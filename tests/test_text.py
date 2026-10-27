from nitm.text import EMAIL_MARK, for_model, has_email, key_hash, name_key, shape_ok, strip_emails


def test_strip_emails_real_dpkg_line():
    line = " Copyright © 1994 Ian Murdock <imurdock@debian.org>"
    shown = strip_emails(line)
    assert shown == f" Copyright © 1994 Ian Murdock {EMAIL_MARK}"
    assert "@" not in shown


def test_strip_emails_bare_and_spelled_out():
    assert "@" not in strip_emails("contact jane@example.org today")
    assert "example" not in strip_emails("sergi at calcurco dot org")
    assert "example" not in strip_emails("foo [at] example (dot) org")
    # Prose with a bare "at" stays as it is.
    assert strip_emails("Developed at MIT.") == "Developed at MIT."


def test_for_model_removes_emails_entirely():
    text = "These files were written by Ian Murdock <imurdock@debian.org> and Bruno Pelham <bpelham@example.org>."
    assert for_model(text) == "These files were written by Ian Murdock and Bruno Pelham ."
    assert not has_email(for_model(text))


def test_name_key_folds_diacritics_and_drops_middle_initials():
    assert name_key("Martin Storsjö") == name_key("Martin Storsjo") == "martin storsjo"
    assert name_key("Thomas G. Vane") == name_key("Thomas Vane") == "thomas vane"
    assert name_key("Harold L Hunt II") == "harold hunt"
    assert name_key("Jon Gilfoyle's") == "jon gilfoyle"


def test_name_key_keeps_initials_when_they_are_all_there_is():
    assert name_key("D.J. Bernard") == "d j bernard"


def test_shape_two_to_four_tokens():
    assert shape_ok("Ian Murdock")
    assert shape_ok("Fabian M. Di Nardo")
    assert shape_ok("Kasper S. KELLERMAN")
    assert shape_ok("ISHIDA Mutsuki")
    assert shape_ok("D.J. Bernard")
    assert not shape_ok("Murdock")
    assert not shape_ok("Andrew M.")
    assert not shape_ok("One Two Three Four Five")
    assert not shape_ok("ian murdock")
    assert not shape_ok("GNU GPL")


def test_key_hash_is_stable_and_short():
    assert key_hash("thomas vane") == key_hash("thomas vane")
    assert len(key_hash("thomas vane")) == 12
    assert key_hash("thomas vane") != key_hash("tom lane")
