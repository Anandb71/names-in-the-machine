"""The free-text eval: hand labels, frozen before Gemma runs, scored against the regex.

The set is every unique free-text credit line the scan found, plus negatives drawn from real
lines on the machine: license template text (James Hacker, Ty Coon, Gnomovision, Essential
Claim) and credit sentences that name no one. Each line is labelled with the full names of
the real people it names, copied exactly. People who never lived are not labels.

The lines and labels name living people, so eval/ stays on the machine (git-ignored). The
log keeps the counts and the sha256 of both files, written when the labels were frozen.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from nitm import sources
from nitm.extract import by_names, is_boilerplate
from nitm.gemma import Extractor
from nitm.scan import ScanResult, latency_stats
from nitm.snapshot import IST
from nitm.text import for_model, name_key, text_hash
from nitm.validate import validate

TRAP_MARKERS = ("james hacker", "ty coon", "gnomovision", "essential claim")


def _row(kind: str, text: str, receipt, occurrences: int = 1) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": for_model(text),
        "file": sources.short_path(receipt.path),
        "line": receipt.first_line,
        "occurrences": occurrences,
    }


def build(result: ScanResult, negatives: int = 40) -> list[dict[str, Any]]:
    """Every unique free-text credit line, plus `negatives` real lines that should name no one."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text, units in sorted(result.unique_units.items()):
        row = _row("unit", text, units[0].receipt, len(units))
        if row["text"] in seen:
            continue
        seen.add(row["text"])
        rows.append(row)

    pool_traps, pool_license, pool_nameless = [], [], []
    for unit in result.license_units:
        clean = for_model(unit.text)
        if clean in seen or not clean:
            continue
        low = clean.lower()
        (pool_traps if any(m in low for m in TRAP_MARKERS) else pool_license).append(unit)
    for unit in result.nameless_units:
        if for_model(unit.text) not in seen:
            pool_nameless.append(unit)

    picked: list[dict[str, Any]] = []

    def add(unit, kind: str) -> bool:
        row = _row(kind, unit.text, unit.receipt)
        if row["text"] in seen or len(row["text"]) > 500:
            return False
        seen.add(row["text"])
        picked.append(row)
        return True

    def ordered(pool):
        return sorted(pool, key=lambda u: text_hash(for_model(u.text)))  # a fixed, arbitrary order

    # One line for each trap marker first, so the GPL's invented people are always in the set.
    for marker in TRAP_MARKERS:
        for unit in ordered(u for u in pool_traps if marker in for_model(u.text).lower()):
            if add(unit, "negative-template"):
                break
    # Then half license template text, half credit sentences that name no one.
    want = (negatives - len(picked)) // 2
    for unit in ordered(pool_license):
        if want <= 0:
            break
        want -= add(unit, "negative-template")
    want = negatives - len(picked)
    for unit in ordered(pool_nameless):
        if want <= 0:
            break
        want -= add(unit, "negative-nameless")
    rows.extend(picked)
    for index, row in enumerate(rows, 1):
        row["id"] = f"e{index:03d}"
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_labels(lines: list[dict[str, Any]], labels: list[dict[str, Any]]) -> list[str]:
    """Every line has exactly one label row, and every labelled name is an exact substring of its line."""
    problems = []
    by_id = {row["id"]: row for row in lines}
    seen = set()
    for label in labels:
        if label["id"] not in by_id:
            problems.append(f"{label['id']}: no such line")
            continue
        if label["id"] in seen:
            problems.append(f"{label['id']}: labelled twice")
        seen.add(label["id"])
        for name in label["names"]:
            if name not in by_id[label["id"]]["text"]:
                problems.append(f"{label['id']}: label is not an exact substring of the line")
    missing = set(by_id) - seen
    if missing:
        problems.append(f"{len(missing)} lines have no label")
    return problems


def freeze(lines_path: Path, labels_path: Path, freeze_path: Path) -> dict[str, Any]:
    lines = read_jsonl(lines_path)
    labels = read_jsonl(labels_path)
    problems = check_labels(lines, labels)
    if problems:
        raise ValueError("labels are not ready: " + "; ".join(problems[:5]))
    record = {
        "frozen_at": datetime.now(IST).isoformat(timespec="seconds"),
        "lines_sha256": sha256(lines_path),
        "labels_sha256": sha256(labels_path),
        "lines": len(lines),
        "lines_by_kind": dict(Counter(row["kind"] for row in lines)),
        "gold_names": sum(len(l["names"]) for l in labels),
        "lines_with_a_person": sum(1 for l in labels if l["names"]),
    }
    Path(freeze_path).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


def verify_frozen(lines_path: Path, labels_path: Path, freeze_path: Path) -> dict[str, Any]:
    """Refuse to score against labels that changed after they were frozen."""
    if not Path(freeze_path).exists():
        raise ValueError("labels are not frozen yet: run `nitm eval freeze` before any model sees the lines")
    record = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    if sha256(lines_path) != record["lines_sha256"] or sha256(labels_path) != record["labels_sha256"]:
        raise ValueError("eval lines or labels changed after they were frozen")
    return record


def _keys(names: list[str]) -> set[str]:
    return {k for k in (name_key(n) for n in names) if len(k.split()) >= 2}


def score(gold: dict[str, set[str]], predicted: dict[str, set[str]]) -> dict[str, Any]:
    tp = fp = fn = 0
    for line_id, keys in gold.items():
        guess = predicted.get(line_id, set())
        tp += len(keys & guess)
        fp += len(guess - keys)
        fn += len(keys - guess)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 3), "recall": round(recall, 3)}


def run(
    lines: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    extractor: Extractor | None = None,
    progress=None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Score the free-text regex, Gemma and both together. Returns (counts, per-line details)."""
    gold = {l["id"]: _keys(l["names"]) for l in labels}
    systems: dict[str, dict[str, set[str]]] = {"regex": {}, "gemma_raw": {}, "gemma": {}, "regex_plus_gemma": {}}
    rejections: Counter = Counter()
    trap_proposals: Counter = Counter()
    seconds: list[float] = []
    fresh = cached = errors = 0
    details = []
    started = time.monotonic()
    for index, row in enumerate(lines, 1):
        text = row["text"]
        regex_kept, _ = validate(by_names(text), text)
        systems["regex"][row["id"]] = _keys(regex_kept)
        detail = {"id": row["id"], "kind": row["kind"], "text": text, "gold": sorted(gold.get(row["id"], set())),
                  "regex": regex_kept}
        if extractor is not None:
            proposal = extractor.propose(text)
            if proposal.error:
                errors += 1
            elif proposal.cached:
                cached += 1
            else:
                fresh += 1
                seconds.append(proposal.seconds or 0.0)
            kept, rejected = validate(proposal.names, text)
            for r in rejected:
                rejections[r.rule] += 1
                if r.rule == "denylist":
                    trap_proposals[r.name] += 1
            systems["gemma_raw"][row["id"]] = _keys(proposal.names)
            systems["gemma"][row["id"]] = _keys(kept)
            systems["regex_plus_gemma"][row["id"]] = systems["regex"][row["id"]] | systems["gemma"][row["id"]]
            detail.update({"gemma_raw": proposal.raw, "gemma_kept": kept,
                           "gemma_rejected": [[r.name, r.rule] for r in rejected], "seconds": proposal.seconds})
        details.append(detail)
        if progress and (index % 25 == 0 or index == len(lines)):
            progress(f"eval: {index}/{len(lines)} lines")
    counts: dict[str, Any] = {
        "lines": len(lines),
        "lines_by_kind": dict(Counter(row["kind"] for row in lines)),
        "gold_names": sum(len(v) for v in gold.values()),
        "lines_with_a_person": sum(1 for v in gold.values() if v),
        "regex": score(gold, systems["regex"]),
    }
    if extractor is not None:
        regex_hits = {(i, k) for i, keys in systems["regex"].items() for k in keys & gold.get(i, set())}
        gemma_hits = {(i, k) for i, keys in systems["gemma"].items() for k in keys & gold.get(i, set())}
        counts.update(
            {
                "model": extractor.model,
                "gemma_raw": score(gold, systems["gemma_raw"]),
                "gemma": score(gold, systems["gemma"]),
                "regex_plus_gemma": score(gold, systems["regex_plus_gemma"]),
                "true_names_only_gemma_found": len(gemma_hits - regex_hits),
                "true_names_only_regex_found": len(regex_hits - gemma_hits),
                "gemma_rejections": dict(sorted(rejections.items())),
                "gemma_denylist_proposals": dict(sorted(trap_proposals.items())),
                "gemma_calls": {"fresh": fresh, "cached": cached, "errors": errors},
                "gemma_latency_seconds": latency_stats(seconds),
                "wall_seconds": round(time.monotonic() - started, 1),
            }
        )
    return counts, details


def is_template(text: str) -> bool:
    return is_boilerplate(text)
