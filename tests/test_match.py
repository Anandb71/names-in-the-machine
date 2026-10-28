"""The homonym gate. Exact names only, a human with a death date, and a software link."""

import copy

from conftest import DOC

from nitm.extract import Candidate, Receipt
from nitm.match import death_year, display_years, gate, match_candidates
from nitm.text import name_key


def candidate(name: str, line: str, package: str = "dpkg", method: str = "copyright") -> Candidate:
    receipt = Receipt(path=f"/usr/share/doc/{package}/copyright", package=package, source="doc", lines=((1, line),))
    return Candidate(name=name, key=name_key(name), method=method, receipt=receipt)


def statuses(result):
    return {(m.label, m.status, m.reason) for m in result.matches}


def test_murdock_goes_to_review(snap):
    result = match_candidates([candidate("Ian Murdock", " Copyright © 1994 Ian Murdock <imurdock@debian.org>")], snap)
    assert statuses(result) == {("Ian Murdock", "review", None)}


def test_vane_is_left_off_for_no_death_date(snap):
    vane = candidate("Thomas G. Vane", "Copyright: 1991-1996, Thomas G. Vane", package="ffmpeg")
    result = match_candidates([vane], snap)
    assert result.matches == []  # never offered for review
    assert result.keys_no_death_namesake_only == 1


def test_no_fuzzy_matching(snap):
    near = [candidate("Ian Murdoch", "x"), candidate("Iain Murdock", "x"), candidate("Phil Katzz", "x")]
    result = match_candidates(near, snap)
    assert result.matches == [] and result.keys_matched == 0


def test_no_surname_only_matching(snap):
    result = match_candidates([candidate("Murdock", "Copyright 1994 Murdock")], snap)
    assert result.matches == [] and result.keys_total == 0


def test_middle_initial_optional_and_diacritics_folded(snap):
    assert [p["qid"] for p in snap.lookup(name_key("Ian A. Murdock"))] == ["Q92761"]
    assert [p["qid"] for p in snap.lookup(name_key("Ïan Murdöck"))] == ["Q92761"]


def test_homonym_without_software_link_is_unverifiable(snap):
    # diffutils: "GNU `diff3' was written by Rory Smythe." The Wikidata Rory Smythe who died
    # was a basketball player and coach: no software occupation, no link to diffutils.
    smith = candidate("Rory Smythe", "GNU `diff3' was written by Rory Smythe.", package="diffutils", method="byline")
    result = match_candidates([smith], snap)
    assert statuses(result) == {("Rory Smythe", "left_off", "unverifiable")}
    assert result.by_status("review") == []


def test_not_human_is_left_off(snap):
    person = copy.deepcopy(snap.people["Q92761"])
    person["human"] = False
    status, reason, _, _ = gate(person, [candidate("Ian Murdock", "x")], set(snap.meta["occupations"]))
    assert (status, reason) == ("left_off", "not_human")


def test_death_date_must_be_precise_to_the_year(snap):
    person = copy.deepcopy(snap.people["Q92761"])
    for d in person["died"]:
        d["precision"] = 7  # century
    assert death_year(person) is None
    status, reason, _, _ = gate(person, [candidate("Ian Murdock", "x")], set(snap.meta["occupations"]))
    assert (status, reason) == ("left_off", "no_death_date")


def test_software_link_named_in_the_receipt_passes_without_occupation(snap):
    person = copy.deepcopy(snap.people["Q92761"])
    person["occupations"] = []
    person["software"] = [{"qid": "Q7593", "label": "Debian", "via": "P112"}]
    in_receipt = candidate("Ian Murdock", "Debian package by Ian Murdock")
    elsewhere = candidate("Ian Murdock", "Copyright 1994 Ian Murdock", package="libfoo")
    closure = set(snap.meta["occupations"])
    assert gate(person, [in_receipt], closure)[0] == "review"
    assert gate(person, [elsewhere], closure)[:2] == ("left_off", "unverifiable")


def test_lundh_shows_death_year_only(snap):
    years, show_birth, note = display_years(snap.people["Q110051708"], earliest_receipt_year=1999)
    assert years == "died 2021" and not show_birth
    assert note


def test_years_for_murdock_and_katz(snap):
    assert display_years(snap.people["Q92761"])[0] == "1973–2015"
    assert display_years(snap.people["Q550635"])[0] == "1962–2000"


def test_receipt_year_after_death_is_flagged(snap):
    late = candidate("Ian Murdock", "Copyright 1994, 2019 Ian Murdock")
    result = match_candidates([late], snap)
    assert any("after this person's death year" in w for w in result.matches[0].warnings)
