"""Match candidate names against the snapshot, then run the homonym gate.

The gate is strict on purpose:
- exact label or alias match only (diacritics folded, middle initials optional);
  no fuzzy matching and no surname-only matching;
- the Wikidata item must be a human (Q5) with a death date (P570) precise to the year;
- it must have a software/CS occupation, or a software link whose name appears in the receipt.

Passing the gate only puts a name in front of a person for review. It never publishes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from nitm.extract import Candidate
from nitm.snapshot import Snapshot
from nitm.text import YEAR_RE, fold, remove_emails


@dataclass
class Match:
    qid: str
    label: str
    key: str
    status: str  # "review" or "left_off"
    reason: str | None = None  # why it was left off
    candidates: list[Candidate] = field(default_factory=list)
    cs_occupations: list[str] = field(default_factory=list)
    link_hits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def methods(self) -> list[str]:
        return sorted({c.method for c in self.candidates})

    @property
    def files(self) -> list[str]:
        return sorted({c.receipt.path for c in self.candidates})


@dataclass
class MatchResult:
    matches: list[Match]
    keys_total: int
    keys_matched: int
    keys_no_death_namesake_only: int
    keys_unmatched: int

    def by_status(self, status: str) -> list[Match]:
        return [m for m in self.matches if m.status == status]

    def left_off_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.matches:
            if m.status == "left_off":
                counts[m.reason or "unknown"] = counts.get(m.reason or "unknown", 0) + 1
        return counts


def best_date(dates: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [d for d in dates or [] if d.get("time") and d.get("precision") is not None]
    preferred = [d for d in usable if d.get("rank") == "preferred"]
    pool = preferred or usable
    if not pool:
        return None
    return max(pool, key=lambda d: d["precision"])


def year_of(date: dict[str, Any] | None) -> int | None:
    if not date or not date.get("time"):
        return None
    match = re.match(r"^(-?\d{1,4})", date["time"])
    return int(match.group(1)) if match else None


def death_year(person: dict[str, Any]) -> int | None:
    date = best_date(person.get("died", []))
    if not date or date["precision"] < 9:
        return None
    return year_of(date)


def receipt_years(candidates: list[Candidate]) -> list[int]:
    years = []
    for c in candidates:
        for _, text in c.receipt.lines:
            years.extend(int(y) for y in YEAR_RE.findall(text))
    return years


def display_years(person: dict[str, Any], earliest_receipt_year: int | None = None) -> tuple[str, bool, str | None]:
    """'1973–2015', or 'died 2021' when the birth date is missing, imprecise or implausible."""
    died = death_year(person)
    if died is None:
        return "", False, "no death year"
    birth = best_date(person.get("born", []))
    born = year_of(birth) if birth and birth["precision"] >= 9 else None
    note = None
    if born is None:
        note = "birth date missing or imprecise on Wikidata"
    elif not 10 <= died - born <= 110:
        note = f"birth year {born} is implausible"
        born = None
    elif earliest_receipt_year is not None and born > earliest_receipt_year - 10:
        note = f"birth year {born} is implausible next to a {earliest_receipt_year} copyright line"
        born = None
    if born is None:
        return f"died {died}", False, note
    return f"{born}–{died}", True, None


def _blob(candidates: list[Candidate]) -> str:
    parts = []
    for c in candidates:
        parts.append(c.receipt.package)
        parts.extend(remove_emails(text) for _, text in c.receipt.lines)
    return fold(" ".join(parts))


def _word_in(label: str, blob: str) -> bool:
    label = fold(label).strip()
    if len(label) < 3:
        return False
    return re.search(r"(?<![\w-])" + re.escape(label) + r"(?![\w-])", blob) is not None


def gate(person: dict[str, Any], candidates: list[Candidate], closure: set[str]) -> tuple[str, str | None, list[str], list[str]]:
    """Return (status, reason, cs occupations, link hits)."""
    if not person.get("human"):
        return "left_off", "not_human", [], []
    if death_year(person) is None:
        return "left_off", "no_death_date", [], []
    cs = [o["label"] or o["qid"] for o in person.get("occupations", []) if o["qid"] in closure]
    blob = _blob(candidates)
    links = sorted({s["label"] for s in person.get("software", []) if _word_in(s["label"], blob)})
    if not cs and not links:
        return "left_off", "unverifiable", [], []
    return "review", None, cs, links


def match_candidates(candidates: list[Candidate], snapshot: Snapshot) -> MatchResult:
    closure = set(snapshot.meta.get("occupations", {}))
    by_key: dict[str, list[Candidate]] = {}
    for c in candidates:
        if len(c.key.split()) >= 2:
            by_key.setdefault(c.key, []).append(c)
    matches: list[Match] = []
    matched = namesake_only = unmatched = 0
    for key in sorted(by_key):
        group = by_key[key]
        people = snapshot.lookup(key)
        if not people:
            if snapshot.has_no_death_namesake(key):
                namesake_only += 1
            else:
                unmatched += 1
            continue
        matched += 1
        passing = []
        for person in people:
            status, reason, cs, links = gate(person, group, closure)
            m = Match(
                qid=person["qid"],
                label=person.get("label") or key,
                key=key,
                status=status,
                reason=reason,
                candidates=list(group),
                cs_occupations=cs,
                link_hits=links,
            )
            matches.append(m)
            if status == "review":
                passing.append(m)
        for m in passing:
            if len(passing) > 1:
                m.warnings.append(f"{len(passing)} people on Wikidata share this name and pass the gate")
            if snapshot.has_no_death_namesake(key):
                m.warnings.append("someone on Wikidata with this name has no death date: check it is the same person")
            person = snapshot.people[m.qid]
            years = receipt_years(m.candidates)
            died = death_year(person)
            if years and died is not None and max(years) > died + 1:
                m.warnings.append(f"a receipt carries the year {max(years)}, after this person's death year {died}")
            _, _, note = display_years(person, min(years) if years else None)
            if note:
                m.warnings.append(note)
            deaths = {year_of(d) for d in person.get("died", []) if d.get("time")}
            if len(deaths) > 1:
                m.warnings.append("Wikidata has more than one death year for this item")
    return MatchResult(
        matches=matches,
        keys_total=len(by_key),
        keys_matched=matched,
        keys_no_death_namesake_only=namesake_only,
        keys_unmatched=unmatched,
    )
