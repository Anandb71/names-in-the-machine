"""Fixes from the pre-publish review: everything here guards public output."""

import json

import pytest

from conftest import DOC, PIP

from nitm import cli, page, review, scan, still
from nitm.text import death_words, has_email, strip_emails

URL = "https://en.wikipedia.org/wiki/Ian_Murdock"


@pytest.mark.parametrize("shape", [
    "Name Surname, user -at- example.org",
    "Copyright 2001 Name <first.last at example.org>",
    "Name <j at example dot org>",
    "user [at] mail [dot] example [dot] org",
    "user＠example.org",
])
def test_spelled_out_addresses_are_removed(shape):
    shown = strip_emails(shape)
    assert "<email removed>" in shown and not has_email(shown)
    assert "example" not in shown


def test_prose_with_at_is_left_alone():
    assert strip_emails("written at Bell Labs in 1985") == "written at Bell Labs in 1985"


@pytest.mark.parametrize("line, removed", [
    ("Copyright 1999 Ian Murdock AND Jane Q. Placeholder", "Jane Q. Placeholder"),
    ("Copyright 1999 Ian Murdock, JANE PLACEHOLDER", "JANE PLACEHOLDER"),
    ("Copyright 1999 Ian Murdock, Jane Mary Anne Louise Placeholder", "Jane Mary Anne Louise Placeholder"),
    ("Copyright Jane Placeholder; Ian Murdock", "Jane Placeholder"),
])
def test_redaction_errs_toward_removing(line, removed):
    text, n = review.redact_others(line, {"ian murdock"})
    assert removed not in text and "Ian Murdock" in text and n >= 1


def test_redaction_keeps_orgs_and_the_person():
    text, n = review.redact_others("Copyright (c) 1999-2002 by Secret Labs AB / Copyright (c) 1999-2002 by Fredrik Lundh", {"fredrik lundh"})
    assert text.endswith("by Fredrik Lundh") and "Secret Labs AB" in text and n == 0


@pytest.mark.parametrize("text", ["suicides", "overdosed", "sui­cide", "heart / attack", "Tragically", "complications"])
def test_death_words_with_endings_and_tricks(text):
    assert death_words(f"He {text} in 2015")


def test_ordinary_card_words_are_not_death_words():
    assert death_words("Named in 6 files on this machine. Measured on this machine. died 2021") == []


@pytest.fixture
def match(extractor, snap, tmp_path):
    result = scan.scan(doc_root=str(DOC), pip_roots=[str(PIP)], extractor=extractor, snapshot=snap)
    scan.write_outputs(result, tmp_path / "out")
    matches = json.loads((tmp_path / "out" / "matches.json").read_text(encoding="utf-8"))
    return next(m for m in matches if m["qid"] == "Q92761")


@pytest.mark.parametrize("reason", ["confirmed by mail from someone@example.org", "a tragic loss, same person"])
def test_public_reason_is_checked(match, snap, reason):
    with pytest.raises(ValueError):
        review.decide(match, snap.people["Q92761"], {"people": []}, {}, approve=True, reason=reason, source=URL,
                      reviewer="A Reviewer", snapshot_date="2026-10-27")


def test_unknown_probe_is_refused(match, snap):
    with pytest.raises(ValueError):
        review.decide(match, snap.people["Q92761"], {"people": []}, {}, approve=True, reason="same person", source=URL,
                      reviewer="A Reviewer", snapshot_date="2026-10-27", probe={"probe": "rdepend", "arg": "dpkg"})


def test_two_copies_of_a_file_count_as_two(match):
    second = json.loads(json.dumps(match["receipts"][0]))
    second["path"] = "/elsewhere/" + second["path"].lstrip("/")
    rows = review.receipt_rows(dict(match, receipts=match["receipts"] + [second]))
    assert review.file_count(rows) == review.file_count(review.receipt_rows(match)) + 1
    assert any(r["copies"] == 2 for r in rows)


def test_no_built_in_reviewer():
    args = cli.build_parser().parse_args(["verify", "--list"])
    assert args.reviewer is None


def test_still_keeps_going_after_a_bad_probe(tmp_path, capsys):
    verified = {"people": [
        {"name": "A", "still": {"probe": "nonsense", "arg": "x"}},
        {"name": "B", "still": {"probe": "dpkg-log", "arg": "/nonexistent/dpkg.log"}},
    ]}
    path = tmp_path / "verified.json"
    path.write_text(json.dumps(verified))
    assert cli.main(["still", "--verified", str(path), "--out", str(tmp_path / "out"), "--no-default-pip"]) == 0
    saved = json.loads(path.read_text())
    assert saved["people"][1]["still"]["command"].startswith("grep")


def test_page_refuses_a_verified_file_with_an_email(tmp_path, capsys, match, snap):
    verified, local = {"people": []}, {}
    review.decide(match, snap.people["Q92761"], verified, local, approve=True, reason="same person", source=URL,
                  reviewer="A Reviewer", snapshot_date="2026-10-27")
    verified["people"][0]["reviewer"] = "a.reviewer@example.org"
    assert review.publish_problems(verified["people"][0])
    assert page.render(verified).count('<article class="card"') == 0
