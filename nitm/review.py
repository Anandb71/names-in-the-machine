"""Review. Nothing reaches the page unless it was approved here.

`nitm verify` walks every match that passed the gate, shows the receipts (emails removed),
the Wikidata item, the years, the occupation and any warnings, and asks y / n / skip.
An approval needs a reason and a second-source URL. Approvals go to verified.json, which is
public. Rejections go to review.local.json, which stays on this machine: a rejected match is
often a living person who shares a name with someone who died.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

from nitm.match import display_years
from nitm.sources import short_path
from nitm.extract import by_names, copyright_holders, person_field_names
from nitm.text import URL_RE as TEXT_URL_RE
from nitm.text import death_words, has_email, name_key, name_runs, strip_emails
from nitm.validate import check

IST = timezone(timedelta(hours=5, minutes=30))
URL_RE = re.compile(r"^https?://\S+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED = ("qid", "name", "years", "reviewer", "date", "reason", "second_source")


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def load_json(path: Path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_verified(path: Path) -> dict[str, Any]:
    data = load_json(path, None)
    if not data:
        return {"about": "Only entries in this file can appear on the page. Each was approved with `nitm verify`, "
                         "with a reason and a second source.", "people": []}
    return data


PROBES = ("rdepends", "dpkg-log", "importers", "dist-count")


def text_problems(text: str) -> list[str]:
    """What may never appear in public text: email addresses and words about how someone died."""
    problems = []
    if "@" in text or has_email(text):
        problems.append("contains an email address")
    hits = death_words(text)
    if hits:
        problems.append(f"contains a word about how someone died ({hits[0]!r})")
    return problems


def publish_problems(entry: dict[str, Any]) -> list[str]:
    """Why an entry may not be published. An empty list means it may."""
    problems = []
    for key in REQUIRED:
        if not str(entry.get(key) or "").strip():
            problems.append(f"missing {key}")
    if entry.get("date") and not DATE_RE.match(str(entry["date"])):
        problems.append("date is not YYYY-MM-DD")
    if entry.get("second_source") and not URL_RE.match(str(entry["second_source"])):
        problems.append("second_source is not a URL")
    for key in ("reason", "reviewer"):
        problems += [f"{key} {p}" for p in text_problems(str(entry.get(key) or ""))]
    receipts = entry.get("receipts") or []
    if not any(r.get("text") for r in receipts):
        problems.append("no receipt that can be quoted")
    own = {name_key(str(entry.get("name") or ""))} | set(entry.get("keys") or [])
    for receipt in receipts:
        text = receipt.get("text")
        if not text:
            continue
        if has_email(text) or "@" in text:
            problems.append("a receipt still contains an email address")
        if others_named(text, own):
            problems.append("a quoted receipt names someone else")
    return problems


NAME_MARK = "<name removed>"
FIELD_WORDS = frozenset({"copyright", "copyrighted", "author", "authors", "written", "upstream", "maintainer",
                         "maintainers", "contributed", "contributor", "contributors"})


def _other_names(text: str, own_keys: set[str]) -> list[str]:
    """Name-shaped strings in a shown line that are not this person.

    This errs toward removing too much: anything shaped like a name counts, even a run the
    extraction validator would reject for its shape (all capitals, five words), unless it is an
    org or a term, contains digits or a URL, or is one of the GPL's invented people.
    """
    clean = TEXT_URL_RE.sub(" ", text)
    found: list[str] = []

    def consider(run: str) -> None:
        tokens = run.split()
        while tokens and tokens[0].lower().rstrip(":") in FIELD_WORDS:
            tokens = tokens[1:]
        if len(tokens) < 2:
            return
        candidate = " ".join(tokens)
        if name_key(candidate) in own_keys or candidate not in text:
            return
        if check(candidate) in (None, "shape"):
            found.append(candidate)

    for chunk in re.split(r"[,;&/()<>]|\band\b", clean, flags=re.I):
        for run in name_runs(chunk):
            consider(run)
    for name in copyright_holders(clean) + person_field_names(clean) + by_names(clean):
        consider(name)
    return found


def others_named(text: str, own_keys: set[str]) -> int:
    """How many other people a shown receipt line names."""
    return len({name_key(run) for run in _other_names(text, own_keys)})


def redact_others(text: str, own_keys: set[str]) -> tuple[str, int]:
    """Replace other people's names with a marker, the way emails are replaced. Living people
    appear only as counts, so a card names only its own person."""
    runs = sorted(set(_other_names(text, own_keys)), key=len, reverse=True)
    for run in runs:
        text = text.replace(run, NAME_MARK)
    return text, len({name_key(run) for run in runs})


def public_receipts(rows: list[dict[str, Any]], own_keys: set[str]) -> list[dict[str, Any]]:
    """Receipts as stored in verified.json: emails and other people's names removed."""
    out = []
    for row in rows:
        text, others = redact_others(row["text"], own_keys)
        if others_named(text, own_keys) or has_email(text):
            text = None  # something the markers could not cover: keep the file and line, not the text
        out.append(dict(row, text=text, others=others))
    return out


def publishable(verified: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in verified.get("people", []) if not publish_problems(entry)]


def receipt_rows(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Receipts as stored in verified.json: short path, line number, text without emails.

    One row per short path, line and text. `copies` counts the files on the machine that hold
    that same line (one METADATA file installed in two environments is two copies), and
    `methods` lists every method that found the name there.
    """
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for r in match["receipts"]:
        key = (r.get("path") or r["file"], r["lines"][0][0])
        if key not in seen:
            text = " / ".join(" ".join(strip_emails(t).split()) for _, t in r["lines"])
            seen[key] = {"file": r["file"], "line": r["lines"][0][0], "text": text, "package": r["package"],
                         "source": r["source"], "methods": set()}
        seen[key]["methods"].add(r["method"])
    grouped: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in seen.values():
        g = (row["file"], row["line"], row["text"])
        if g not in grouped:
            grouped[g] = dict(row, copies=0, methods=set())
        grouped[g]["copies"] += 1
        grouped[g]["methods"] |= row["methods"]
    out = list(grouped.values())
    for row in out:
        row["methods"] = sorted(row["methods"])
    out.sort(key=lambda row: (row["source"] != "doc", row["methods"] == ["gemma"], row["file"], row["line"]))
    return out


def file_count(receipts: list[dict[str, Any]]) -> int:
    """How many files on the machine name the person: each short path, times its copies."""
    per_file: dict[str, int] = {}
    for r in receipts:
        per_file[r["file"]] = max(per_file.get(r["file"], 0), int(r.get("copies") or 1))
    return sum(per_file.values())


def find_receipt(receipts: list[dict[str, Any]], spec: str | None) -> int:
    """Index of the receipt named like 'dpkg/copyright:6'; the first quotable one when spec is empty."""
    if not spec:
        return next((i for i, r in enumerate(receipts) if r.get("text", True)), 0)
    file, _, line = spec.rpartition(":")
    for index, r in enumerate(receipts):
        if r["file"] == file and str(r["line"]) == line:
            if r.get("text", True) is None:
                raise ValueError(f"{spec} names other people too, so it cannot be the quoted receipt")
            return index
    raise ValueError(f"no receipt at {spec}")


def suggested_probe(match: dict[str, Any]) -> dict[str, str]:
    doc = [r for r in match["receipts"] if r["source"] == "doc"]
    if doc:
        return {"probe": "rdepends", "arg": doc[0]["package"]}
    return {"probe": "dist-count", "arg": match["receipts"][0]["package"]}


def make_entry(
    match: dict[str, Any],
    person: dict[str, Any],
    *,
    reviewer: str,
    reason: str,
    source: str,
    snapshot_date: str,
    probe: dict[str, str] | None = None,
    primary: int | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    rows = receipt_rows(match)
    years_found = [int(y) for r in rows for y in re.findall(r"\b(?:19|20)\d\d\b", r["text"])]
    years, show_birth, note = display_years(person, min(years_found) if years_found else None)
    name = person.get("label") or match["label"]
    receipts = public_receipts(rows, own_keys(match, name))
    if primary is None:
        primary = find_receipt(receipts, None)
    return {
        "qid": match["qid"],
        "name": name,
        "keys": sorted(own_keys(match, name)),
        "years": years,
        "years_note": note,
        "reviewer": reviewer,
        "date": date or today_ist(),
        "reason": reason,
        "second_source": source,
        "found_by": sorted({m for r in receipts for m in r["methods"]}),
        "primary_receipt": primary,
        "receipts": receipts,
        "still": probe or suggested_probe(match),
        "snapshot": snapshot_date,
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def screen(match: dict[str, Any], person: dict[str, Any], index: int, total: int) -> str:
    receipts = receipt_rows(match)
    years, _, note = display_years(person, None)
    lines = [
        f"[{index}/{total}] {person.get('label')} · {match['qid']} · {years or 'no year'}",
        f"  occupations: {', '.join(o['label'] or o['qid'] for o in person.get('occupations', [])) or 'none'}",
        f"  gate: {', '.join(match['cs_occupations']) or 'no CS occupation'}"
        + (f"; software link in receipt: {', '.join(match['link_hits'])}" if match["link_hits"] else ""),
        f"  found by: {', '.join(match['methods'])} · {_plural(len(receipts), 'receipt')} in {_plural(file_count(receipts), 'file')}",
    ]
    for r in receipts[:6]:
        lines.append(f"    {r['file']}:{r['line']}  {r['text'][:110]}  [{', '.join(r['methods'])}]")
    if len(receipts) > 6:
        lines.append(f"    ... and {len(receipts) - 6} more")
    for warning in match.get("warnings", []):
        lines.append(f"  ! {warning}")
    if note and note not in match.get("warnings", []):
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def merge_by_qid(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The review queue: one entry per Wikidata item that passed the gate, even when two name
    forms reached it ("Thomas Lord" and "Tom Lord")."""
    merged: dict[str, dict[str, Any]] = {}
    for m in matches:
        if m["status"] != "review":
            continue
        if m["qid"] not in merged:
            merged[m["qid"]] = dict(m, receipts=list(m["receipts"]), keys=[m["key"]],
                                    link_hits=list(m["link_hits"]), warnings=list(m["warnings"]))
            continue
        entry = merged[m["qid"]]
        entry["receipts"].extend(m["receipts"])
        entry["keys"].append(m["key"])
        entry["methods"] = sorted(set(entry["methods"]) | set(m["methods"]))
        entry["link_hits"] = sorted(set(entry["link_hits"]) | set(m["link_hits"]))
        entry["warnings"] = list(dict.fromkeys(entry["warnings"] + m["warnings"]))
    return list(merged.values())


def own_keys(match: dict[str, Any], name: str) -> set[str]:
    return set(match.get("keys") or [match["key"]]) | {name_key(name)}


def decided_qids(verified: dict[str, Any], local: dict[str, Any]) -> set[str]:
    return {p["qid"] for p in verified.get("people", [])} | set(local.get("decisions", {}))


def interactive(
    matches: list[dict[str, Any]],
    snapshot_people: dict[str, dict[str, Any]],
    verified: dict[str, Any],
    local: dict[str, Any],
    *,
    reviewer: str,
    snapshot_date: str,
    ask: Callable[[str], str],
    out: TextIO,
) -> tuple[int, int, int]:
    queue = [m for m in merge_by_qid(matches) if m["qid"] not in decided_qids(verified, local)]
    approved = rejected = skipped = 0
    for index, match in enumerate(queue, 1):
        person = snapshot_people[match["qid"]]
        print(screen(match, person, index, len(queue)), file=out)
        answer = ask("  verify? [y]es / [n]o / [s]kip: ").strip().lower()
        if answer.startswith("y"):
            reason = ask("  reason (why this is the same person): ").strip()
            source = ask("  second source URL: ").strip()
            if not reason or not URL_RE.match(source):
                print("  not saved: an approval needs a reason and a second-source URL", file=out)
                skipped += 1
                continue
            if text_problems(reason):
                print(f"  not saved: the reason {text_problems(reason)[0]}; it is public, so reword it", file=out)
                skipped += 1
                continue
            entry = make_entry(match, person, reviewer=reviewer, reason=reason, source=source, snapshot_date=snapshot_date)
            verified.setdefault("people", []).append(entry)
            approved += 1
            print(f"  saved: {entry['name']} ({entry['years']})", file=out)
        elif answer.startswith("n"):
            reason = ask("  reason (homonym / not sure / other): ").strip() or "not sure"
            local.setdefault("decisions", {})[match["qid"]] = {"decision": "reject", "reason": reason, "date": today_ist()}
            rejected += 1
            print("  left off", file=out)
        else:
            skipped += 1
            print("  skipped", file=out)
    return approved, rejected, skipped


def show_receipt_path(path: str) -> str:
    return short_path(path)


def decide(
    match: dict[str, Any],
    person: dict[str, Any],
    verified: dict[str, Any],
    local: dict[str, Any],
    *,
    approve: bool,
    reason: str,
    reviewer: str,
    snapshot_date: str,
    source: str | None = None,
    probe: dict[str, str] | None = None,
    primary: str | None = None,
) -> dict[str, Any] | None:
    """The non-interactive form of one verify answer. Same rules as the prompt."""
    if match["status"] != "review":
        raise ValueError(f"{match['qid']} did not pass the gate; it cannot be approved")
    if match["qid"] in decided_qids(verified, local):
        raise ValueError(f"{match['qid']} already has a decision")
    if not reason.strip():
        raise ValueError("a decision needs a reason")
    if not approve:
        local.setdefault("decisions", {})[match["qid"]] = {"decision": "reject", "reason": reason, "date": today_ist()}
        return None
    if not source or not URL_RE.match(source):
        raise ValueError("an approval needs a second-source URL")
    problems = text_problems(reason)
    if problems:
        raise ValueError(f"the reason {problems[0]}; it is public, so reword it")
    if probe and probe.get("probe") and probe["probe"] not in PROBES:
        raise ValueError(f"unknown probe {probe['probe']!r}; use one of {', '.join(PROBES)} or none")
    name = person.get("label") or match["label"]
    receipts = public_receipts(receipt_rows(match), own_keys(match, name))
    entry = make_entry(match, person, reviewer=reviewer, reason=reason, source=source, snapshot_date=snapshot_date,
                       probe=probe, primary=find_receipt(receipts, primary))
    verified.setdefault("people", []).append(entry)
    return entry


def review_counts(local: dict[str, Any]) -> dict[str, int]:
    decisions = local.get("decisions", {})
    return {"rejected": sum(1 for d in decisions.values() if d.get("decision") == "reject")}
