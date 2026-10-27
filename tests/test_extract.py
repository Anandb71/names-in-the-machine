"""Extraction from real lines (tests/fixtures/doc is copied from /usr/share/doc on the build machine)."""

from conftest import DOC

from nitm import sources
from nitm.extract import by_names, copyright_holders, extract_file, regex_candidates
from nitm.text import for_model


def parsed(package: str):
    path = DOC / package / "copyright"
    return extract_file(sources.parse_copyright(str(path), path.read_text(encoding="utf-8")))


def names(result):
    return [(name, method) for name, method, _ in regex_candidates(result)]


def test_murdock_copyright_line_dpkg_line_6():
    result = parsed("dpkg")
    found = [(name, line.lineno) for name, _, line in regex_candidates(result)]
    assert ("Ian Murdock", 6) in found
    line = next(l for l in result.copyright_lines if l.lineno == 6)
    assert line.text == " Copyright © 1994 Ian Murdock <imurdock@debian.org>"


def test_copyright_holders_strip_marks_years_and_emails():
    assert copyright_holders("Copyright © 1994-1999, 2007-2008 Ivor Jessop <ijessop@example.org>") == ["Ivor Jessop"]
    assert copyright_holders("Copyright (c) 1999-2002 by Fredrik Lundh") == ["Fredrik Lundh"]


def test_katz_is_free_text_not_a_copyright_line():
    result = parsed("zlib1g")
    assert not any("Katz" in l.text for l in result.copyright_lines)
    units = [u for u in result.units if "Phil Katz" in u.text]
    assert len(units) == 1
    assert units[0].text == "The deflate format used by zlib was defined by Phil Katz."
    assert units[0].receipt.first_line == 10


def test_free_text_regex_reads_bylines():
    assert by_names("The deflate format used by zlib was defined by Phil Katz.") == ["Phil Katz", "Phil Katz"]
    assert by_names("GNU `diff' was written by Mina Hartwell, Dara Hayford, Rolf Stanhope, Lyle Towner, and Pavel Eggleston.") == [
        "Mina Hartwell", "Dara Hayford", "Rolf Stanhope", "Lyle Towner", "Pavel Eggleston"]
    assert by_names("The sntrup761 code is based on public domain code written by Daniel J. Bernard, Tanya Lenz.") == [
        "Daniel J. Bernard", "Tanya Lenz"]


def test_free_text_regex_misses_name_first_sentences():
    # This is the gap Gemma is measured on.
    assert by_names("Wade Davenport designed and implemented the unified output format.") == []


def test_lundh_receipt():
    result = parsed("libpython3.13-minimal")
    assert ("Fredrik Lundh", "copyright") in names(result)


def test_lane_dep5_copyright_field():
    result = parsed("ffmpeg")
    assert ("Thomas G. Vane", "copyright") in names(result)


def test_gpl_template_people_are_stripped_before_anything_reads_them():
    result = parsed("openjdk-21-jre-headless")
    everything = [u.text for u in result.units] + [n for n, _, _ in regex_candidates(result)]
    assert not any("Hacker" in t or "Coon" in t or "Gnomovision" in t for t in everything)
    stripped = " ".join(u.text for u in result.license_units)
    assert "James Hacker" in stripped and "Ty Coon" in stripped


def test_dep5_field_split_kills_wrap_junk():
    # acl lines 7-8: "Copyright © 1999-2001,2007-2009 Anders Grunewald" then "License: GPL-2+".
    # Read as one run of text, a regex finds "Anders Grunewald License".
    result = parsed("acl")
    found = [n for n, _ in names(result)]
    assert "Anders Grunewald" in found
    assert not any("License" in n for n in found)


def test_org_line_reaches_the_validator_as_an_org():
    result = parsed("diffutils")
    assert ("Free Software Foundation", "copyright") in names(result)  # rejected later by the stoplist


def test_wrapped_comment_is_rejoined_into_sentences():
    result = parsed("diffutils")
    texts = [for_model(u.text) for u in result.units]
    assert "GNU `diff' was written by Mina Hartwell, Dara Hayford, Rolf Stanhope, Lyle Towner, and Pavel Eggleston." in texts
    assert "Wade Davenport designed and implemented the unified output format." in texts
    unit = next(u for u in result.units if u.text.startswith("GNU `diff'"))
    assert [n for n, _ in unit.receipt.lines] == [6, 7]


def test_metadata_author_email_field():
    path = str(next((DOC.parent / "pip").glob("*.dist-info")) / "METADATA")
    sf = sources.parse_metadata(path, open(path, encoding="utf-8").read())
    result = extract_file(sf)
    assert ("Aaron Swartz", "field") in names(result)
    assert sources.short_path(path) == "html2text-2025.4.15.dist-info/METADATA"


def test_the_scan_never_reads_itself():
    from nitm.sources import SELF_TESTS, iter_metadata_files

    repo = str(DOC.parent.parent.parent)
    files = list(iter_metadata_files([repo]))
    assert not any(f.path.startswith(SELF_TESTS) for f in files)
    assert not any(f.package == "names-in-the-machine" for f in files)
    # A fixture root given on purpose (as the tests do) is still read.
    assert len(list(iter_metadata_files([str(DOC.parent / "pip")]))) == 1
