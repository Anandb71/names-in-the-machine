"""Find names in the lines we read.

Three kinds of line:
- copyright lines ("Copyright © 1994 Ian Murdock", or any line of a DEP-5 Copyright field),
  read by a plain regex;
- person fields (METADATA Author, DEP-5 Upstream-Contact, "Upstream Author:" lists),
  also read by regex;
- free-text credit lines ("The deflate format used by zlib was defined by Phil Katz."),
  which are handed to Gemma one at a time.

License template text (the GPL's "How to Apply" section with its James Hacker and Ty Coon,
MPL definitions, and so on) is stripped before any of this, so Gemma never sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nitm.sources import Line, SourceFile, field_value
from nitm.text import URL_RE, YEAR_RE, name_key, name_runs, remove_emails, text_hash

COPYRIGHT_MARK_RE = re.compile(r"\bcopyright\b|©|\(c\)", re.I)
COPYRIGHT_TEMPLATE_RE = re.compile(
    r"copyright\s+(?:notices?|holders?|owners?|laws?|act|interest|licen[cs]es?|statements?|protection|status|"
    r"claims?|and\s+licen[cs]e|and\s+permission|files?|information|disclaimer|infringement)\b"
    r"|\babove\s+copyright|\bcopyrighted\b|\bcopyrights\b|<year>|name\s+of\s+author|\byear\s+name\b",
    re.I,
)
YEAR_SPAN_RE = re.compile(
    r"\b(?:1[89]\d\d|20\d\d)(?:\s*[-–,/]\s*(?:1[89]\d\d|20\d\d|\d\d|present|now)\b)*", re.I
)
HOLDER_SPLIT_RE = re.compile(r"[,;&/+]|\band\b|\bby\b|\bet\.?\s*al\.?", re.I)
PERSON_SPLIT_RE = re.compile(r"[,;&/]|\band\b", re.I)

DEP5_COPYRIGHT_FIELDS = frozenset({"Copyright"})
DEP5_PERSON_FIELDS = frozenset(
    {"Upstream-Contact", "Upstream-Author", "Upstream-Authors", "Author", "Authors", "Maintainer", "Maintainers"}
)
DEP5_SKIP_FIELDS = frozenset({"Format", "Files", "Files-Excluded", "Files-Included", "Source", "Upstream-Name", "License"})

HEADING_RE = re.compile(r"^\s*([A-Z][A-Za-z()/ -]{1,40}):(?:\s|$)")
COPYRIGHT_HEADING_RE = re.compile(r"^\s*copyright(?:\s*\(c\))?(?:\s+holders?)?\s*:\s*(.*)$", re.I)
PERSON_HEADING_RE = re.compile(
    r"^\s*(?:upstream[\s-]+|original\s+)?(?:authors?|author\(s\)|maintainers?|maintainer\(s\)|"
    r"developers?|contributors?|contributor\(s\)|programmers?)\s*:\s*(.*)$",
    re.I,
)
LICENSE_HEADING_RE = re.compile(r"^\s*licen[cs]e\s*:", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-*•+o]\s+|\d+[.)]\s+|\[\d+\]\s+)")

CREDIT_CUE_RE = re.compile(
    r"(?i:\b(?:written|wrote|defined|developed|created|designed|implemented|maintained|maintainers?|"
    r"contributed|contributions?|contributors?|ported|originally|authored|authors?|invented|conceived|"
    r"coded|rewritten|programmed|modified|packaged|debianized|debianised|adapted|translated|thanks?|"
    r"credits?|work\s+of|based\s+on|derived\s+from)\b)"
    r"|\bby\s+(?=[A-ZÀ-Ý])"
)

# Template text from license texts. A sentence containing any of these is stripped
# before Gemma sees it.
BOILERPLATE_MARKERS = (
    "gnomovision",
    "james hacker",
    "ty coon",
    "yoyodyne",
    "name of author",
    "<year>",
    "signature of",
    "one line to give",
    "this program is free software",
    "you can redistribute it",
    "without any warranty",
    "general public license",
    "lesser general public",
    "library general public",
    "mozilla public license",
    "permission is hereby granted",
    "the software is provided",
    "redistribution and use in source",
    "licensed under the apache",
    "portions created by",
    "initial developer",
    "original code",
    "covered code",
    "contributor version",
    "electronic distribution mechanism",
    "essential claim",
    "w3c policy",
    "president of vice",
    "foo bar",
)

# Legal template sentences (MPL, CC, Apache, BSD, GCC exception, US government clauses).
# Checked per sentence only, so a credit line that merely mentions a license survives.
LICENSE_SENTENCE_RE = re.compile(
    r"\"[^\"]{1,40}\"\s+(?:means|shall\s+mean|refers\s+to)\b"
    r"|\b(?:covered\s+software|contributor\s+(?:version|grant|represents|apis)|contributor's\s+modifications|"
    r"each\s+contributor|patent\s+claims|patent\s+licen[cs]es?|licensed\s+(?:material|rights)|adapted\s+material|"
    r"public\s+license|creative\s+commons|ccpl|licensee|licensor|hereby\s+grants?|hereby\s+granted|"
    r"royalty-free|non-exclusive|notwithstanding|restricted\s+rights|government\s+use|"
    r"commercial\s+computer\s+software|compilation\s+process|independent\s+module|gpl-compatible|"
    r"special\s+exception|this\s+licen[cs]e|licen[cs]e\s+agreement|permission\s+to\s+use,\s+copy|"
    r"endorse\s+or\s+promote|in\s+no\s+event\s+shall|merchantability|without\s+specific\s+prior)\b",
    re.I,
)

ABBREVIATIONS = frozenset(
    {"inc", "ltd", "co", "corp", "dr", "mr", "mrs", "ms", "jr", "sr", "st", "vs", "etc", "al", "no", "e.g", "i.e", "cf", "approx"}
)
MAX_UNIT_CHARS = 500


@dataclass(frozen=True)
class Receipt:
    path: str
    package: str
    source: str
    lines: tuple[tuple[int, str], ...]  # (line number, raw text) exactly as in the file

    @property
    def first_line(self) -> int:
        return self.lines[0][0]


@dataclass(frozen=True)
class Candidate:
    name: str
    key: str
    method: str  # "copyright", "field" or "gemma"
    receipt: Receipt


@dataclass
class Unit:
    """One free-text credit line (a sentence, re-joined if the file wrapped it)."""

    text: str
    receipt: Receipt

    @property
    def key(self) -> str:
        return text_hash(self.text)


@dataclass
class FileResult:
    path: str
    package: str
    source: str
    dep5: bool
    copyright_lines: list[Line] = field(default_factory=list)
    person_lines: list[Line] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    # Kept only so the eval can draw negatives from real lines on the machine.
    license_units: list[Unit] = field(default_factory=list)
    nameless_units: list[Unit] = field(default_factory=list)
    boilerplate_lines: int = 0
    boilerplate_sentences: int = 0
    cue_without_names: int = 0
    too_long: int = 0


def is_boilerplate(text: str) -> bool:
    """License template text with invented names or blanks in it (GPL "How to Apply" and friends)."""
    low = text.lower()
    return any(marker in low for marker in BOILERPLATE_MARKERS)


def is_license_sentence(text: str) -> bool:
    return is_boilerplate(text) or bool(LICENSE_SENTENCE_RE.search(text))


def is_copyright_statement(text: str) -> bool:
    if not COPYRIGHT_MARK_RE.search(text) or COPYRIGHT_TEMPLATE_RE.search(text):
        return False
    if YEAR_RE.search(text):
        return True
    return bool(re.search(r"(?:©|\([cC]\))\s*(?:by\s+)?[A-Z]", text))


def copyright_holders(text: str) -> list[str]:
    """The Copyright-line regex: strip marks, years, emails and asides, then keep name-shaped runs."""
    s = remove_emails(text)
    s = URL_RE.sub(" ", s)
    s = re.sub(r"\bcopyright\b\s*:?", " ", s, flags=re.I)
    s = re.sub(r"©|\([cC]\)|\bcopr\.", " ", s)
    s = re.sub(r"\ball rights reserved\b\.?", " ", s, flags=re.I)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = YEAR_SPAN_RE.sub(" , ", s)
    names = []
    for chunk in HOLDER_SPLIT_RE.split(s):
        names.extend(run for run in name_runs(chunk) if len(run.split()) >= 2)
    return names


def person_field_names(text: str) -> list[str]:
    s = remove_emails(text)
    s = URL_RE.sub(" ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    names = []
    for chunk in PERSON_SPLIT_RE.split(s):
        names.extend(run for run in name_runs(chunk) if len(run.split()) >= 2)
    return names


BYLINE_END_RE = re.compile(r"(?<!\b[A-Z])[.;:(]\s|[.;:(]$|\bfor\b|\bin\s+\d")


def by_names(text: str) -> list[str]:
    """The free-text regex: 'written by Firstname Lastname', 'thanks to ...'.

    This is the whole of what plain code reads in free text. Gemma is measured against it.
    """
    names = []
    for match in re.finditer(r"\bby\s+|\bthanks\s+to\s+", text, re.I):
        rest = remove_emails(text[match.end() :])
        rest = BYLINE_END_RE.split(rest, maxsplit=1)[0]
        for chunk in PERSON_SPLIT_RE.split(rest):
            if not chunk.strip():
                continue  # the gap in an Oxford comma: "Lyle Towner, and Pavel Eggleston"
            runs = name_runs(chunk)
            if not runs:
                break
            names.extend(run for run in runs if len(run.split()) >= 2)
    return names


def has_name_pair(text: str) -> bool:
    return any(len(run.split()) >= 2 for run in name_runs(remove_emails(URL_RE.sub(" ", text))))


def _split_sentences(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for match in re.finditer(r"[.!?](?=\s+[\"'`(A-ZÀ-Ý]|\s*$)", text):
        end = match.end()
        before = text[start:match.start()].split()
        last = before[-1].lower().strip("(\"'`") if before else ""
        if len(last) == 1 or last in ABBREVIATIONS or re.fullmatch(r"(?:[^\W\d_]\.)+[^\W\d_]", last):
            continue  # an initial ("J. Bernard", "D.J. Bernard") or an abbreviation, not a sentence end
        spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _units_from_segment(segment: list[tuple[Line, str]], result: FileResult) -> None:
    if not segment:
        return
    text = ""
    spans: list[tuple[int, int, Line]] = []
    for line, value in segment:
        value = " ".join(value.split())
        if text:
            text += " "
        start = len(text)
        text += value
        spans.append((start, len(text), line))
    for s, e in _split_sentences(text):
        sentence = text[s:e].strip()
        if not sentence or not CREDIT_CUE_RE.search(sentence):
            continue
        covered = tuple((line.lineno, line.text) for a, b, line in spans if a < e and b > s)
        receipt = Receipt(path=result.path, package=result.package, source=result.source, lines=covered)
        if is_license_sentence(sentence):
            result.boilerplate_sentences += 1
            result.license_units.append(Unit(text=sentence, receipt=receipt))
            continue
        if not has_name_pair(sentence):
            result.cue_without_names += 1
            result.nameless_units.append(Unit(text=sentence, receipt=receipt))
            continue
        if len(sentence) > MAX_UNIT_CHARS:
            result.too_long += 1
            continue
        result.units.append(Unit(text=sentence, receipt=receipt))


def extract_file(sf: SourceFile) -> FileResult:
    """Sort every line of a file into copyright lines, person-field lines and free-text units."""
    result = FileResult(path=sf.path, package=sf.package, source=sf.source, dep5=sf.dep5)
    if sf.source == "pip":
        result.person_lines.extend(l for l in sf.lines if l.text.strip())
        return result

    mode = "free"  # free, copyright, person or license
    segment: list[tuple[Line, str]] = []
    last_paragraph = None
    last_field = None
    block_seen = False  # a person or copyright block ends at the next paragraph once it has content
    holder_continues = False

    def flush():
        nonlocal segment
        _units_from_segment(segment, result)
        segment = []

    for line in sf.lines:
        value = field_value(line) if sf.dep5 else line.text.strip()
        new_paragraph = line.paragraph != last_paragraph
        new_field = sf.dep5 and line.field != last_field
        if new_paragraph or new_field:
            flush()
            holder_continues = False
            if sf.dep5:
                mode = "free"
            elif mode in ("person", "copyright") and block_seen:
                mode = "free"
        last_paragraph, last_field = line.paragraph, line.field

        if not value:
            flush()
            continue
        if sf.dep5 and line.field in DEP5_COPYRIGHT_FIELDS:
            result.copyright_lines.append(line)
            continue
        if sf.dep5 and line.field in DEP5_PERSON_FIELDS:
            result.person_lines.append(line)
            continue
        if is_boilerplate(value):
            result.boilerplate_lines += 1
            result.license_units.append(Unit(text=" ".join(value.split()), receipt=receipt_for_line(result, line)))
            flush()
            continue

        statement = is_copyright_statement(value)
        if statement or holder_continues:
            mark = COPYRIGHT_MARK_RE.search(value)
            tail = value[: mark.start()].strip() if mark else ""
            if (
                statement and not holder_continues and segment and tail
                and re.search(r"[.!?]$", tail) and not re.search(r"[.!?:]\s*$", segment[-1][1])
            ):
                # This line opens with the end of a sentence the previous line started
                # ("...Tanya Lenz, and Christine / van Vredendaal.  Copyright 2023 ..."): keep it whole.
                segment.append((line, value))
            flush()
            result.copyright_lines.append(line)
            holder_continues = bool(re.search(r"(?:,|\band|&)\s*$", value, re.I))
            continue

        if sf.dep5 and line.field in DEP5_SKIP_FIELDS:
            continue

        heading = HEADING_RE.match(value)
        if heading:
            flush()
            person = PERSON_HEADING_RE.match(value)
            copyright_heading = COPYRIGHT_HEADING_RE.match(value)
            if person:
                mode, block_seen = "person", False
                if person.group(1).strip():
                    result.person_lines.append(line)
                    block_seen = True
                continue
            if copyright_heading:
                mode, block_seen = "copyright", False
                rest = copyright_heading.group(1).strip()
                if rest and YEAR_RE.search(rest):
                    result.copyright_lines.append(line)
                    block_seen = True
                continue
            if LICENSE_HEADING_RE.match(value):
                mode = "license"
                continue
            mode = "free"

        if mode == "person":
            result.person_lines.append(line)
            block_seen = True
            continue
        if mode == "copyright":
            if YEAR_RE.search(value):
                result.copyright_lines.append(line)
                block_seen = True
            continue
        if mode == "license":
            continue

        if BULLET_RE.match(value) or (segment and segment[-1][1].rstrip().endswith(":")):
            flush()
        segment.append((line, value))
    flush()
    return result


def regex_candidates(result: FileResult) -> list[tuple[str, str, Line]]:
    """(name, method, line) for every name-shaped string the regex finds in this file."""
    out = []
    for line in result.copyright_lines:
        value = field_value(line) if line.dep5 else line.text
        for name in copyright_holders(value):
            out.append((name, "copyright", line))
    for line in result.person_lines:
        value = field_value(line) if line.dep5 else line.text
        if line.source == "pip":
            value = value.split(":", 1)[1] if ":" in value else value
        else:
            heading = PERSON_HEADING_RE.match(value)
            if heading:
                value = heading.group(1)
        for name in person_field_names(value):
            out.append((name, "field", line))
    return out


def receipt_for_line(result: FileResult, line: Line) -> Receipt:
    return Receipt(path=result.path, package=result.package, source=result.source, lines=((line.lineno, line.text),))


def key_of(name: str) -> str:
    return name_key(name)
