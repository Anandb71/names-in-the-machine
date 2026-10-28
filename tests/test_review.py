import pytest

from conftest import DOC, PIP

from nitm import review, scan

URL = "https://en.wikipedia.org/wiki/Ian_Murdock"


@pytest.fixture
def matches(extractor, snap, tmp_path):
    result = scan.scan(doc_root=str(DOC), pip_roots=[str(PIP)], extractor=extractor, snapshot=snap)
    scan.write_outputs(result, tmp_path / "out")
    import json

    return json.loads((tmp_path / "out" / "matches.json").read_text(encoding="utf-8"))


def find(matches, qid):
    return next(m for m in matches if m["qid"] == qid)


def test_receipts_have_emails_removed_and_methods_merged(matches):
    rows = review.receipt_rows(find(matches, "Q92761"))
    assert all("@" not in r["text"] for r in rows)
    dpkg = next(r for r in rows if r["file"].endswith("dpkg/copyright"))
    assert dpkg["text"] == "Copyright © 1994 Ian Murdock <email removed>"
    base = next(r for r in rows if r["file"].endswith("base-files/copyright"))
    assert base["methods"] == ["byline", "gemma"]


def test_approval_needs_a_reason_and_a_second_source(matches, snap):
    verified, local = review.load_verified(None or "missing.json"), {}
    m = find(matches, "Q92761")
    with pytest.raises(ValueError):
        review.decide(m, snap.people["Q92761"], verified, local, approve=True, reason="", source=URL,
                      reviewer="Anand Biju", snapshot_date="2026-10-27")
    with pytest.raises(ValueError):
        review.decide(m, snap.people["Q92761"], verified, local, approve=True, reason="same person",
                      source="not a url", reviewer="Anand Biju", snapshot_date="2026-10-27")
    entry = review.decide(m, snap.people["Q92761"], verified, local, approve=True, reason="same person",
                          source=URL, reviewer="Anand Biju", snapshot_date="2026-10-27", primary="dpkg/copyright:6")
    assert entry["years"] == "1973–2015" and entry["reviewer"] == "Anand Biju"
    assert entry["receipts"][entry["primary_receipt"]]["line"] == 6
    assert review.publish_problems(entry) == []
    with pytest.raises(ValueError):  # one decision per match
        review.decide(m, snap.people["Q92761"], verified, local, approve=True, reason="again", source=URL,
                      reviewer="Anand Biju", snapshot_date="2026-10-27")


def test_turned_down_matches_stay_local(matches, snap):
    verified, local = {"people": []}, {}
    review.decide(find(matches, "Q133867446"), snap.people["Q133867446"], verified, local, approve=False,
                  reason="homonym", reviewer="Anand Biju", snapshot_date="2026-10-27")
    assert verified["people"] == []
    assert local["decisions"]["Q133867446"]["decision"] == "reject"
    assert review.review_counts(local) == {"rejected": 1, "withdrawn": 0}


def test_gate_failures_cannot_be_approved(matches, snap):
    smith = next(m for m in matches if m["status"] == "left_off")
    with pytest.raises(ValueError):
        review.decide(smith, {}, {"people": []}, {}, approve=True, reason="x", source=URL,
                      reviewer="Anand Biju", snapshot_date="2026-10-27")


@pytest.mark.parametrize("field", ["reviewer", "date", "reason", "second_source", "years", "qid", "name"])
def test_publish_gate_needs_every_field(matches, snap, field):
    entry = review.make_entry(find(matches, "Q550635"), snap.people["Q550635"], reviewer="Anand Biju",
                              reason="same person", source=URL, snapshot_date="2026-10-27")
    assert review.publish_problems(entry) == []
    entry[field] = ""
    assert review.publish_problems(entry)


def test_publish_gate_refuses_an_email_in_a_receipt(matches, snap):
    entry = review.make_entry(find(matches, "Q550635"), snap.people["Q550635"], reviewer="Anand Biju",
                              reason="same person", source=URL, snapshot_date="2026-10-27")
    entry["receipts"][0]["text"] += " <jloup@gzip.org>"
    assert "a receipt still contains an email address" in review.publish_problems(entry)


def test_verify_screen_strips_emails(matches, snap):
    text = review.screen(find(matches, "Q92761"), snap.people["Q92761"], 1, 1)
    assert "@" not in text and "Ian Murdock" in text


def test_queue_has_one_entry_per_person():
    base = {"status": "review", "label": "Thomas Lord", "methods": ["byline"], "link_hits": [], "warnings": [],
            "receipts": [{"file": "diffutils/copyright", "lines": [[9, "GNU `sdiff' was written by Thomas Lord."]],
                          "package": "diffutils", "source": "doc", "method": "byline", "name": "Thomas Lord"}]}
    a = dict(base, qid="Q133867446", key="thomas lord")
    b = dict(base, qid="Q133867446", key="tom lord", methods=["copyright"],
             receipts=[dict(base["receipts"][0], file="libc-bin/copyright", lines=[[5, "Copyright 1993 Tom Lord"]], method="copyright")])
    queue = review.merge_by_qid([a, b, dict(base, qid="Q1", key="x y", status="left_off")])
    assert len(queue) == 1
    assert queue[0]["keys"] == ["thomas lord", "tom lord"]
    assert queue[0]["methods"] == ["byline", "copyright"] and len(queue[0]["receipts"]) == 2


def test_other_peoples_names_are_removed_from_receipts(matches, snap):
    entry = review.make_entry(find(matches, "Q92761"), snap.people["Q92761"], reviewer="Anand Biju",
                              reason="same person", source=URL, snapshot_date="2026-10-27")
    base_files = next(r for r in entry["receipts"] if r["file"].endswith("base-files/copyright"))
    assert base_files["others"] == 1  # Bruno Pelham is named there too
    assert "Ian Murdock" in base_files["text"] and review.NAME_MARK in base_files["text"]
    dpkg = next(r for r in entry["receipts"] if r["file"].endswith("dpkg/copyright"))
    assert dpkg["text"] == "Copyright © 1994 Ian Murdock <email removed>" and dpkg["others"] == 0
    assert "Pelham" not in str(entry)
    assert review.publish_problems(entry) == []


def test_redaction_keeps_the_person_and_removes_co_authors():
    text, n = review.redact_others("1998 Ross Anderson, Eli Biham, Lars Knudsen", {"ross anderson"})
    assert text == "1998 Ross Anderson, <name removed>, <name removed>" and n == 2
