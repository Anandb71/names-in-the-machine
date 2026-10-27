"""Text helpers shared by every stage: emails, name shape and name keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata

EMAIL_MARK = "<email removed>"

# Plain addresses, with or without angle brackets.
EMAIL_RE = re.compile(r"<?\b[\w.+'%-]+@[\w-]+(?:\.[\w-]+)+\b>?")
# Spelled-out addresses: "foo [at] bar.org", "foo (at) bar (dot) org", "foo at bar dot org".
# A bare " at " only counts when a spelled " dot " follows, so prose like "at MIT." is left alone.
SPELLED_EMAIL_RE = re.compile(
    r"<?\b[\w.+-]+\s*(?:\[at\]|\(at\)|\{at\}|<at>)\s*[\w-]+"
    r"(?:(?:\.(?=\w)|\s*\[dot\]\s*|\s*\(dot\)\s*|\s*\{dot\}\s*|\s*<dot>\s*)[\w-]+)+\b>?"
    r"|\b[\w.+-]+\s+at\s+[\w-]+(?:\s+dot\s+[\w-]+)+\b",
    re.I,
)
URL_RE = re.compile(r"(?:https?|ftp)://\S+|\bwww\.\S+", re.I)
YEAR_RE = re.compile(r"\b(?:1[89]\d\d|20\d\d)\b")

PARTICLES = frozenset(
    {"van", "von", "der", "den", "de", "del", "della", "da", "di", "du", "la", "le", "dos", "das", "ten", "ter", "bin", "ibn", "al", "i", "y"}
)
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "dr", "mr", "mrs", "ms", "prof"})
_EDGE_PUNCT = "\"'`’‘“”()[]{}<>,;:!?*"
_INITIAL_RE = re.compile(r"^(?:[^\W\d_]\.){1,3}$|^[^\W\d_]$")


def strip_emails(text: str) -> str:
    """Replace every email address with a marker. Used on everything we display."""
    text = EMAIL_RE.sub(EMAIL_MARK, text)
    return SPELLED_EMAIL_RE.sub(EMAIL_MARK, text)


def has_email(text: str) -> bool:
    return bool(EMAIL_RE.search(text))


def remove_emails(text: str) -> str:
    """Delete addresses entirely, for parsing (not display)."""
    text = EMAIL_RE.sub(" ", text)
    return SPELLED_EMAIL_RE.sub(" ", text)


def for_model(text: str) -> str:
    """What Gemma (and the free-text regex) reads: the line with every email address removed."""
    return " ".join(remove_emails(text).split())


def clean_token(token: str) -> str:
    token = token.strip(_EDGE_PUNCT)
    if token.endswith(".") and not _INITIAL_RE.match(token):
        token = token.rstrip(".")
    return re.sub(r"['’]s$", "", token)


def is_initial(token: str) -> bool:
    return bool(_INITIAL_RE.match(token)) and token[:1].isupper()


def is_mixed_word(token: str) -> bool:
    """A capitalised word like Murdock, Jean-loup, O'Brien or McKenzie."""
    if len(token) < 2 or not token[0].isupper() or not any(ch.islower() for ch in token):
        return False
    return all(ch.isalpha() or ch in "-'’" for ch in token)


def is_caps_word(token: str) -> bool:
    """An all-caps surname like KELLERMAN or ISHIDA. Only counts next to a normal word."""
    return len(token) >= 2 and token.isupper() and all(ch.isalpha() or ch == "-" for ch in token)


def is_name_word(token: str) -> bool:
    return is_mixed_word(token) or is_caps_word(token)


def is_name_token(token: str) -> bool:
    return is_name_word(token) or is_initial(token)


def name_runs(text: str) -> list[str]:
    """Maximal runs of name tokens (particles allowed inside a run)."""
    tokens = [clean_token(t) for t in text.split()]
    raw = text.split()
    runs: list[list[str]] = []
    current: list[str] = []
    for index, token in enumerate(tokens):
        broken = raw[index].rstrip().endswith((",", ";", ":")) or (
            raw[index].endswith(".") and not is_initial(token) and len(token) > 1
        )
        if token and is_name_token(token):
            current.append(token)
        elif token.lower() in PARTICLES and current:
            current.append(token)
        else:
            if current:
                runs.append(current)
            current = []
            continue
        if broken:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    out = []
    for run in runs:
        while run and run[-1].lower() in PARTICLES:
            run = run[:-1]
        if run:
            out.append(" ".join(run))
    return out


def shape_ok(name: str) -> bool:
    """2 to 4 capitalised tokens or initials, one of them normal case.

    Either two full words ("Ian Murdock", "ISHIDA Mutsuki"), or initials followed by a
    surname ("D.J. Bernard"). A trailing initial ("Andrew M.") is not a full name.
    """
    tokens = name.split()
    core = [t for t in tokens if t.lower() not in PARTICLES or t[:1].isupper()]
    if not 2 <= len(core) <= 4 or len(tokens) > 6:
        return False
    if not all(is_name_token(t) for t in core):
        return False
    if not is_name_word(core[-1]) and not is_name_word(core[0]):
        return False
    if not any(is_mixed_word(t) for t in core):
        return False
    words = sum(1 for t in core if is_name_word(t))
    return words >= 2 or (words == 1 and is_mixed_word(core[-1]))


def fold(text: str) -> str:
    """Casefold and strip diacritics: Jürgen -> jurgen."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def name_key(name: str) -> str:
    """Exact-match key. Diacritics folded, punctuation dropped, middle initials optional."""
    text = fold(name).replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"['`]", "", text)
    text = re.sub(r"[.\-,_]", " ", text)
    tokens = [t for t in text.split() if t not in SUFFIXES]
    full = [t for t in tokens if len(t) > 1]
    if len(full) >= 2:
        tokens = full
    return " ".join(tokens)


def key_hash(key: str) -> str:
    """Short hash of a name key. Used so living people are never stored by name."""
    return hashlib.sha256(("nitm-v1:" + key).encode("utf-8")).hexdigest()[:12]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
