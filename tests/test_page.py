"""The publish gate. What may and may not reach the ofrenda page."""

import json
import re

import pytest

from conftest import ANSWERS, DOC, INVENTED, PIP

from nitm import page, review, scan
from nitm.gemma import parse_names

URL = "https://en.wikipedia.org/wiki/"
LIVING = ["Ivor Jessop", "Mel Wexford", "Cal Strand", "Rolf Stanhope", "Pavel Eggleston", "Mina Hartwell",
          "Dara Hayford", "Lyle Towner", "Torvald Granfeld", "Dale MacKinley", "Wade Davenport", "Milo Alan Dorrance",
          "Anders Grunewald", "Jane Placeholder", "Bruno Pelham", "Thomas G. Vane", "Percy Dunmore"]


@pytest.fixture
def world(extractor, snap, tmp_path):
    """Scan the fixtures with the fake Gemma, then approve Murdock, Katz and Lundh by hand."""
    result = scan.scan(doc_root=str(DOC), pip_roots=[str(PIP)], extractor=extractor, snapshot=snap)
    scan.write_outputs(result, tmp_path / "out")
    matches = json.loads((tmp_path / "out" / "matches.json").read_text(encoding="utf-8"))
    verified, local = {"people": []}, {}
    for qid, wiki, primary in (("Q92761", "Ian_Murdock", "dpkg/copyright:6"), ("Q550635", "Phil_Katz", None),
                               ("Q110051708", "Fredrik_Lundh", None)):
        m = next(x for x in matches if x["qid"] == qid)
        review.decide(m, snap.people[qid], verified, local, approve=True, reason="same person: receipt and Wikidata agree",
                      source=URL + wiki, reviewer="Anand Biju", snapshot_date="2026-10-27", primary=primary)
    review.decide(next(x for x in matches if x["qid"] == "Q133867446"), snap.people["Q133867446"], verified, local,
                  approve=False, reason="not sure", reviewer="Anand Biju", snapshot_date="2026-10-27")
    html = page.render(verified, summary=scan.summary(result), review_counts=review.review_counts(local), snapshot_meta=snap.meta)
    return {"result": result, "verified": verified, "html": html, "extractor": extractor}


def test_verified_people_render(world):
    html = world["html"]
    for name in ("Ian Murdock", "Phil Katz", "Fredrik Lundh"):
        assert name in html
    assert "1973–2015" in html and "1962–2000" in html and "died 2021" in html
    assert "Copyright © 1994 Ian Murdock &lt;email removed&gt;" in html
    assert "The deflate format used by zlib was defined by Phil Katz." in html


def test_unverified_entries_never_render(world, snap):
    verified = json.loads(json.dumps(world["verified"]))
    katz = next(e for e in verified["people"] if e["qid"] == "Q550635")
    del katz["second_source"]
    lundh = next(e for e in verified["people"] if e["qid"] == "Q110051708")
    lundh["reviewer"] = ""
    html = page.render(verified)
    assert "Ian Murdock" in html
    assert "Phil Katz" not in html and "Fredrik Lundh" not in html
    assert [name for name, _ in page.skipped(verified)] == ["Phil Katz", "Fredrik Lundh"]


def test_matches_nobody_approved_never_render(world):
    # Aaron Swartz and Thomas Lord pass the gate in this fixture but were not approved here.
    assert "Aaron Swartz" not in world["html"] and "Thomas Lord" not in world["html"]


def test_no_email_address_anywhere_on_the_page(world):
    html = world["html"]
    assert "@" not in page.visible_text(html)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html)
    assert page.check(html) == []


def test_check_catches_an_email_that_slips_in(world):
    assert page.check(world["html"].replace("</main>", "<p>mail imurdock@debian.org</p></main>"))


@pytest.mark.parametrize("word", ["suicide", "tragic", "illness", "overdose", "cancer", "arrested", "heart attack", "accident"])
def test_check_catches_death_circumstance_words(world, word):
    assert page.check(world["html"].replace("</main>", f"<p>{word}</p></main>"))


def test_no_death_circumstance_words_in_the_rendered_page(world):
    assert not page.DEATH_WORD_RE.search(page.visible_text(world["html"]))


def test_living_people_are_counts_never_names(world):
    html = world["html"]
    for name in LIVING:
        assert name not in html, name
    assert "living people are never named here" in html


def test_gemma_never_authors_text_about_a_person(world):
    """Every Gemma answer string stays off the page. The only Gemma-touched text that can appear
    is a validated name, and it appears only because the same words are in the receipt line."""
    html = world["html"]
    visible = page.visible_text(html)
    raws = [row["raw"] for row in world["extractor"]._cache.values()]
    assert INVENTED in raws
    for raw in raws:
        if raw not in ("NONE",) and len(parse_names(raw)) > 1 or raw == INVENTED:
            assert raw not in visible
    for fragment in ("a Debian package", "died in 2015", "This machine contains", "Lenka Torrance", "Essential Claim"):
        assert fragment not in visible
    receipts = " ".join(r["text"] or "" for e in world["verified"]["people"] for r in e["receipts"])
    for raw in raws:
        for name in parse_names(raw):
            if name in visible:
                assert name in receipts, f"{name!r} reached the page without being in a receipt"


def test_cards_are_templates_plus_verified_fields(world):
    allowed = {"qid", "name", "keys", "years", "years_note", "reviewer", "date", "reason", "second_source", "found_by",
               "primary_receipt", "receipts", "still", "snapshot"}
    for entry in world["verified"]["people"]:
        assert set(entry) <= allowed
    assert "the free-text regex and Gemma 3 1B (free text, held to the validator)" in world["html"]  # Katz


def test_page_has_corrections_line_and_left_off_counts(world):
    html = world["html"]
    assert "it comes down at once" in html
    assert "https://github.com/Anandb71/names-in-the-machine/issues" in html
    assert "1 name matched only people on Wikidata with no death date recorded" in html  # Thomas G. Vane
    assert "1 name matched a person who has died, but with no software occupation" in html  # Rory Smythe
    assert "1 match turned down at review." in html


def test_no_model_wrote_the_page(world):
    assert "No model wrote any of the text on this page." in world["html"]
    assert ANSWERS  # the fake model was used for this world


def test_withdrawn_person_leaves_the_page_and_is_counted(world):
    verified = json.loads(json.dumps(world["verified"]))
    local = {}
    assert review.withdraw(verified, local, "Q550635", "too recent to show") == "Phil Katz"
    assert [e["qid"] for e in verified["people"]] == ["Q92761", "Q110051708"]
    assert local["decisions"]["Q550635"]["decision"] == "withdrawn"
    assert review.review_counts(local) == {"rejected": 0, "withdrawn": 1}
    with pytest.raises(ValueError):
        review.withdraw(verified, local, "Q550635", "again")  # already gone
    with pytest.raises(ValueError):
        review.withdraw(verified, local, "Q92761", "  ")  # a reason is required
    html = page.render(verified, review_counts=review.review_counts(local))
    assert "Phil Katz" not in html
    assert "1 approved match withdrawn by the author before publishing." in html


def test_review_status_is_shown_and_never_claims_a_human_review(world):
    verified = json.loads(json.dumps(world["verified"]))
    assert "Review status" not in world["html"]
    verified["review_status"] = "Identity checks were AI-assisted; the author's own review is pending."
    html = page.render(verified)
    assert "Review status:</strong> Identity checks were AI-assisted; the author&#x27;s own review is pending." in html
    assert page.check(html) == []
