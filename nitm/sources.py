"""Read the files where names live: Debian copyright files and Python dist-info METADATA.

Every line we keep remembers its file, line number and raw text, so a name can always
be traced back to the exact place it sits on the machine.
"""

from __future__ import annotations

import glob
import os
import re
import site
from dataclasses import dataclass, field
from pathlib import Path

DOC_ROOT = "/usr/share/doc"
FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):(.*)$")
METADATA_PERSON_FIELDS = ("Author", "Author-email", "Maintainer", "Maintainer-email")
# Caches hold unpacked copies of wheels, not installed packages.
PRUNE_DIRS = frozenset({".git", "node_modules", "__pycache__", ".hg", ".svn", ".cache", ".Trash"})
# The scan never reads itself: not its test fixtures, not its own install record.
SELF_NAME = "names-in-the-machine"
SELF_TESTS = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "tests"))


@dataclass(frozen=True)
class Line:
    path: str
    lineno: int
    text: str
    package: str
    source: str  # "doc" or "pip"
    field: str | None = None  # DEP-5 field or METADATA header this line belongs to
    paragraph: int = 0
    dep5: bool = False


@dataclass
class SourceFile:
    path: str
    package: str
    source: str
    dep5: bool
    lines: list[Line] = field(default_factory=list)


def is_dep5(lines: list[str]) -> bool:
    for raw in lines[:5]:
        if raw.strip():
            return bool(re.match(r"^Format(?:-Specification)?:\s*\S*(?:copyright-format|dep5|dep-5)", raw, re.I))
    return False


def parse_copyright(path: str, text: str, package: str | None = None) -> SourceFile:
    """Split a copyright file into lines tagged with paragraph and (for DEP-5) field."""
    raw_lines = text.splitlines()
    dep5 = is_dep5(raw_lines)
    package = package or Path(path).parent.name
    parsed = SourceFile(path=path, package=package, source="doc", dep5=dep5)
    paragraph = 0
    current_field: str | None = None
    in_blank = True
    for lineno, raw in enumerate(raw_lines, 1):
        if not raw.strip():
            if not in_blank:
                paragraph += 1
            in_blank = True
            current_field = None
            continue
        in_blank = False
        if dep5:
            match = FIELD_RE.match(raw)
            if match and not raw[:1].isspace():
                current_field = match.group(1)
        parsed.lines.append(
            Line(
                path=path,
                lineno=lineno,
                text=raw,
                package=package,
                source="doc",
                field=current_field if dep5 else None,
                paragraph=paragraph,
                dep5=dep5,
            )
        )
    return parsed


def field_value(line: Line) -> str:
    """The value part of a DEP-5 line: drop 'Field:' on the first line and the ' .' blank marker."""
    text = line.text
    if line.dep5 and line.field and not text[:1].isspace():
        match = FIELD_RE.match(text)
        if match:
            text = match.group(2)
    if text.strip() == ".":
        return ""
    return text.strip()


def iter_copyright_files(doc_root: str = DOC_ROOT):
    for path in sorted(glob.glob(os.path.join(doc_root, "*", "copyright"))):
        if not os.path.isfile(path):
            continue
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield parse_copyright(path, text)


def parse_metadata(path: str, text: str) -> SourceFile:
    """Read the header block of a dist-info METADATA file and keep the person fields."""
    name = None
    kept: list[Line] = []
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            break
        match = FIELD_RE.match(raw)
        if match and not raw[:1].isspace():
            current = match.group(1)
            if current == "Name":
                name = match.group(2).strip()
        if current in METADATA_PERSON_FIELDS:
            kept.append(Line(path=path, lineno=lineno, text=raw, package="", source="pip", field=current))
    if not name:
        name = Path(path).parent.name.split("-")[0]
    lines = [
        Line(path=l.path, lineno=l.lineno, text=l.text, package=name, source="pip", field=l.field) for l in kept
    ]
    return SourceFile(path=path, package=name, source="pip", dep5=False, lines=lines)


def default_pip_roots() -> list[str]:
    roots: set[str] = set()
    try:
        roots.update(site.getsitepackages())
    except AttributeError:
        pass
    try:
        roots.add(site.getusersitepackages())
    except AttributeError:
        pass
    for pattern in ("/usr/lib/python3/dist-packages", "/usr/lib/python3*/site-packages", "/usr/local/lib/python3*/dist-packages"):
        roots.update(glob.glob(pattern))
    return sorted(r for r in roots if os.path.isdir(r))


def collapse_roots(roots: list[str]) -> list[str]:
    """Drop roots that sit inside another root, so nothing is walked or counted twice."""
    kept: list[str] = []
    for root in sorted({os.path.realpath(r) for r in roots if os.path.isdir(r)}, key=len):
        if not any(root == k or root.startswith(k.rstrip("/") + "/") for k in kept):
            kept.append(root)
    return sorted(kept)


def iter_metadata_paths(roots: list[str]):
    """Walk each root for *.dist-info/METADATA. Does not follow symlinks."""
    seen: set[str] = set()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            keep = []
            for name in dirnames:
                if name in PRUNE_DIRS or os.path.realpath(os.path.join(dirpath, name)) == SELF_TESTS:
                    continue
                if name.endswith(".dist-info"):
                    meta = os.path.join(dirpath, name, "METADATA")
                    if os.path.isfile(meta):
                        real = os.path.realpath(meta)
                        if real not in seen:
                            seen.add(real)
                            yield meta
                    continue
                keep.append(name)
            dirnames[:] = keep


def iter_metadata_files(roots: list[str]):
    for path in iter_metadata_paths(roots):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                head = handle.read(64_000)
        except OSError:
            continue
        parsed = parse_metadata(path, head)
        if parsed.package.lower() == SELF_NAME:
            continue
        yield parsed


def short_path(path: str) -> str:
    """How a receipt path is shown: dpkg/copyright, or html2text-2025.4.15.dist-info/METADATA."""
    if path.startswith(DOC_ROOT + "/"):
        return path[len(DOC_ROOT) + 1 :]
    parts = Path(path).parts
    if len(parts) >= 2 and parts[-1] == "copyright":
        return "/".join(parts[-2:])  # <package>/copyright, whatever the doc root
    for index, part in enumerate(parts):
        if part.endswith(".dist-info"):
            return "/".join(parts[index:])
    return path
