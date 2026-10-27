import pytest

from nitm.validate import DENYLIST, check, validate

KATZ = "The deflate format used by zlib was defined by Phil Katz."
HACKER = "'Gnomovision' (which makes passes at compilers) written by James Hacker."


def test_exact_substring_rule():
    assert check("Phil Katz", KATZ) is None
    assert check("Philip Katz", KATZ) == "not_in_line"
    assert check("phil katz", KATZ) == "not_in_line"


def test_invented_relationship_never_passes():
    line = "These files were written by Ian Murdock and Bruno Pelham ."
    kept, rejected = validate(["Ian Murdock", "a Debian package", "died in 2015"], line)
    assert kept == ["Ian Murdock"]
    assert {r.rule for r in rejected} == {"not_in_line"}


@pytest.mark.parametrize("name", ["James Hacker", "Ty Coon", "Gnomovision"])
def test_denylist_people_who_never_lived(name):
    assert name.lower() in DENYLIST
    assert check(name) == "denylist"


def test_james_hacker_is_denylisted_even_though_it_is_in_the_line():
    assert check("James Hacker", HACKER) == "denylist"


@pytest.mark.parametrize(
    "name",
    ["Free Software Foundation", "Secret Labs AB", "Silicon Graphics", "Essential Claim", "Mozilla Contributors", "Debian Project"],
)
def test_org_and_term_stoplist(name):
    assert check(name) == "org_word"


@pytest.mark.parametrize("name", ["Ian Murdock2", "imurdock@debian.org", "http://zlib.net Phil", "Phil_Katz"])
def test_no_digits_emails_or_urls(name):
    assert check(name) == "bad_chars"


@pytest.mark.parametrize("name", ["Murdock", "Phil", "The Deflate Format Used By", "phil katz", "Andrew M."])
def test_two_to_four_capitalised_tokens(name):
    assert check(name) == "shape"


def test_validate_drops_duplicates_and_possessives():
    line = "GNU tar, heavily based on Jon Gilfoyle's public domain version"
    kept, rejected = validate(["Jon Gilfoyle's", "Jon Gilfoyle"], line)
    assert kept == ["Jon Gilfoyle"]
    assert rejected == []
