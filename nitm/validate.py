"""The referee. Every name, from the regex or from Gemma, has to pass these rules.

Each rule has a name, so every rejection can be logged and counted by reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nitm.text import PARTICLES, shape_ok

# People who never lived: the GPL's "How to Apply" example text.
DENYLIST = ("james hacker", "ty coon", "gnomovision", "yoyodyne")

# A name containing one of these words is an organisation or a term, not a person.
ORG_WORDS = frozenset(
    w.lower()
    for w in """
    Inc Incorporated Ltd Limited LLC LLP GmbH AG AB BV SA SAS SARL Oy KK Pty PLC Corp Corporation Company Co
    Foundation Project Projects Team Teams Group Developers Development Contributors Contributor Authors Author
    Maintainers Maintainer Community Members Users License Licence Licenses Policy Claim Claims Essential
    University Universität Universite Institute Institut Consortium Laboratory Laboratories Labs Lab Society
    Association Council Committee Software Systems Technologies Technology Research Networks Network Microsystems
    Computer Computers Computing Graphics Semiconductor Electronics Electronic Digital Media Studio Studios Games
    Press Publishing Communications Services Solutions Enterprises Industries Holdings Partners Associates
    Commission Agency Office Bureau Ministry Department Government Federal National International Organization
    Organisation School College Academy Center Centre Museum Initiative Initiatives Alliance Forum Working
    Common Commons Creative Unicode GNU Mozilla Apache Debian Ubuntu Linux Python Perl Ruby Java Google Microsoft
    Apple IBM Intel Oracle Netscape Canonical Cygnus Hat Free Open Public Standard Standards Specification
    Original Initial Covered Modifications Executable Source Documentation Manual Text Font Fonts Icon Icons
    Theme Data Database Files File Library Libraries Code Program Programs Package Packages Version Copyright
    Trademark Patent Section Act States America Regents Trustees Board Street Road Avenue Ave Floor Suite Place
    Boulevard Box Version Release Edition Notice Agreement Terms Conditions Warranty Liability Disclaimer
    Contract Project's Group's Systems' Engine Toolkit Framework Platform Kernel Desktop Server Client Protocol
    Format Formats Algorithm Encoder Decoder Codec Module Modules Plugin Plugins Extension Extensions Interface
    Driver Drivers Utilities Utility Tools Tool Studio Works Work Vice President Secretary Director Thanks
    Copyrighted Reserved Rights Berkeley California Massachusetts Cambridge Boston
    """.split()
)

BAD_CHARS_RE = re.compile(r"[\d@/\\<>=#_|&~^%$]|https?:|www\.", re.I)

RULES = ("not_in_line", "denylist", "bad_chars", "shape", "org_word")


@dataclass(frozen=True)
class Rejection:
    name: str
    rule: str


def check(name: str, line: str | None = None) -> str | None:
    """Return the name of the first rule this name breaks, or None if it passes.

    `line` is the source line. When given, the name must appear in it exactly
    (this is how Gemma's proposals are held to what the file actually says).
    """
    if line is not None and name not in line:
        return "not_in_line"
    low = name.lower()
    if any(word in low for word in DENYLIST):
        return "denylist"
    if BAD_CHARS_RE.search(name):
        return "bad_chars"
    if not shape_ok(name):
        return "shape"
    tokens = [t.strip(".,'’").lower() for t in name.split()]
    if any(t in ORG_WORDS for t in tokens if t not in PARTICLES):
        return "org_word"
    return None


def validate(names: list[str], line: str | None = None) -> tuple[list[str], list[Rejection]]:
    """Split proposals into kept names and logged rejections. Duplicates are dropped quietly."""
    kept: list[str] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()
    for raw in names:
        name = re.sub(r"['’]s$", "", " ".join(raw.split()))
        if not name or name in seen:
            continue
        seen.add(name)
        rule = check(name, line)
        if rule:
            rejected.append(Rejection(name=name, rule=rule))
        else:
            kept.append(name)
    return kept, rejected
