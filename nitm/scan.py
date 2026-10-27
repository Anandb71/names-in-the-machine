"""One full pass over the machine: read, extract, validate, match. Every count is recorded."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nitm import sources
from nitm.extract import Candidate, FileResult, Receipt, Unit, by_names, extract_file, regex_candidates
from nitm.gemma import Extractor
from nitm.match import MatchResult, match_candidates
from nitm.snapshot import Snapshot
from nitm.text import for_model, name_key, strip_emails
from nitm.validate import Rejection, validate

REGEX_METHODS = ("copyright", "field", "byline")
METHOD_LABELS = {
    "copyright": "Copyright-line regex",
    "field": "author-field regex",
    "byline": "free-text regex",
    "gemma": "Gemma, free text",
}


@dataclass
class ScanResult:
    copyright_files: int = 0
    dep5_files: int = 0
    metadata_files: int = 0
    lines_read: int = 0
    copyright_lines: int = 0
    person_lines: int = 0
    boilerplate_lines: int = 0
    boilerplate_sentences: int = 0
    cue_without_names: int = 0
    too_long: int = 0
    units: list[Unit] = field(default_factory=list)
    license_units: list[Unit] = field(default_factory=list)
    nameless_units: list[Unit] = field(default_factory=list)
    regex_strings: Counter = field(default_factory=Counter)  # name-like strings, by method
    regex_rejections: Counter = field(default_factory=Counter)
    gemma_used: bool = False
    gemma_lines: int = 0
    gemma_fresh: int = 0
    gemma_cached: int = 0
    gemma_errors: int = 0
    gemma_error_kinds: Counter = field(default_factory=Counter)
    gemma_proposals: int = 0
    gemma_rejections: Counter = field(default_factory=Counter)
    gemma_seconds: list[float] = field(default_factory=list)
    gemma_wall_seconds: float = 0.0
    rejection_log: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    match: MatchResult | None = None
    read_seconds: float = 0.0
    wall_seconds: float = 0.0
    model: str | None = None
    doc_root: str | None = None
    pip_roots: list[str] = field(default_factory=list)

    @property
    def unique_units(self) -> dict[str, list[Unit]]:
        grouped: dict[str, list[Unit]] = {}
        for unit in self.units:
            grouped.setdefault(unit.text, []).append(unit)
        return grouped

    def keys_by_method(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for c in self.candidates:
            out.setdefault(c.method, set()).add(c.key)
        return out


def _log_rejections(result: ScanResult, stage: str, rejected: list[Rejection], receipt: Receipt, times: int = 1) -> None:
    for r in rejected:
        if stage == "gemma":
            result.gemma_rejections[r.rule] += 1
        else:
            result.regex_rejections[r.rule] += times
        result.rejection_log.append(
            {"stage": stage, "rule": r.rule, "name": r.name, "file": sources.short_path(receipt.path), "line": receipt.first_line}
        )


def _absorb(result: ScanResult, fr: FileResult) -> None:
    result.copyright_lines += len(fr.copyright_lines)
    result.person_lines += len(fr.person_lines)
    result.boilerplate_lines += fr.boilerplate_lines
    result.boilerplate_sentences += fr.boilerplate_sentences
    result.cue_without_names += fr.cue_without_names
    result.too_long += fr.too_long
    result.units.extend(fr.units)
    result.license_units.extend(fr.license_units)
    result.nameless_units.extend(fr.nameless_units)
    for name, method, line in regex_candidates(fr):
        result.regex_strings[method] += 1
        kept, rejected = validate([name])
        receipt = Receipt(path=fr.path, package=fr.package, source=fr.source, lines=((line.lineno, line.text),))
        _log_rejections(result, method, rejected, receipt)
        for name in kept:
            result.candidates.append(Candidate(name=name, key=name_key(name), method=method, receipt=receipt))


def _add(result: ScanResult, names: list[str], method: str, units: list[Unit]) -> None:
    for name in names:
        for unit in units:
            result.candidates.append(Candidate(name=name, key=name_key(name), method=method, receipt=unit.receipt))


def read_free_text(result: ScanResult, extractor: Extractor | None, progress=None) -> None:
    """Each unique free-text credit line is read once by the regex and, if a model is set, once by Gemma.

    Both read the line with email addresses removed. Gemma's names have to be exact substrings
    of that line and pass every validator rule, or they are dropped and the reason is logged.
    """
    grouped = result.unique_units
    total = len(grouped)
    started = time.monotonic()
    for index, (text, units) in enumerate(sorted(grouped.items()), 1):
        clean = for_model(text)
        found = by_names(clean)
        result.regex_strings["byline"] += len(found) * len(units)
        kept, rejected = validate(found, clean)
        _log_rejections(result, "byline", rejected, units[0].receipt, times=len(units))
        _add(result, kept, "byline", units)
        if extractor is None:
            continue
        result.gemma_lines += 1
        proposal = extractor.propose(clean)
        if proposal.error:
            result.gemma_errors += 1
            result.gemma_error_kinds[proposal.error[:80]] += 1
            continue
        if proposal.cached:
            result.gemma_cached += 1
        else:
            result.gemma_fresh += 1
            result.gemma_seconds.append(proposal.seconds or 0.0)
        result.gemma_proposals += len(proposal.names)
        kept, rejected = validate(proposal.names, clean)
        _log_rejections(result, "gemma", rejected, units[0].receipt)
        _add(result, kept, "gemma", units)
        if progress and (index % 10 == 0 or index == total):
            progress(f"gemma: {index}/{total} credit lines")
    if extractor is not None:
        result.gemma_used = True
        result.model = extractor.model
        result.gemma_wall_seconds = time.monotonic() - started


def scan(
    doc_root: str | None = sources.DOC_ROOT,
    pip_roots: list[str] | None = None,
    extractor: Extractor | None = None,
    snapshot: Snapshot | None = None,
    progress=None,
) -> ScanResult:
    started = time.monotonic()
    result = ScanResult(doc_root=doc_root, pip_roots=list(pip_roots or []))
    if doc_root:
        for sf in sources.iter_copyright_files(doc_root):
            result.copyright_files += 1
            result.dep5_files += int(sf.dep5)
            result.lines_read += len(sf.lines)
            _absorb(result, extract_file(sf))
    if pip_roots:
        for sf in sources.iter_metadata_files(pip_roots):
            result.metadata_files += 1
            result.lines_read += len(sf.lines)
            _absorb(result, extract_file(sf))
    result.read_seconds = time.monotonic() - started
    if progress:
        progress(f"read {result.copyright_files} copyright files and {result.metadata_files} METADATA files")

    read_free_text(result, extractor, progress)

    if snapshot is not None:
        result.match = match_candidates(result.candidates, snapshot)
    result.wall_seconds = time.monotonic() - started
    return result


def latency_stats(seconds: list[float]) -> dict[str, float] | None:
    if not seconds:
        return None
    return {
        "n": len(seconds),
        "min": round(min(seconds), 2),
        "median": round(statistics.median(seconds), 2),
        "max": round(max(seconds), 2),
        "total": round(sum(seconds), 1),
    }


def summary(result: ScanResult) -> dict[str, Any]:
    """Counts only. Nothing in here names a person."""
    by_method = result.keys_by_method()
    regex_keys: set[str] = set().union(*(by_method.get(m, set()) for m in REGEX_METHODS))
    gemma_keys = by_method.get("gemma", set())
    all_keys = regex_keys | gemma_keys
    out: dict[str, Any] = {
        "sources": {"doc_root": result.doc_root, "pip_roots": result.pip_roots},
        "files": {
            "copyright_files": result.copyright_files,
            "dep5_files": result.dep5_files,
            "metadata_files": result.metadata_files,
            "files_scanned": result.copyright_files + result.metadata_files,
            "lines_read": result.lines_read,
        },
        "lines": {
            "copyright_lines": result.copyright_lines,
            "person_field_lines": result.person_lines,
            "license_template_lines_stripped": result.boilerplate_lines,
            "license_template_sentences_stripped": result.boilerplate_sentences,
            "credit_sentences_without_a_name": result.cue_without_names,
            "credit_sentences_too_long": result.too_long,
            "free_text_credit_lines": len(result.units),
            "free_text_credit_lines_unique": len(result.unique_units),
        },
        "regex": {
            "name_like_strings": sum(result.regex_strings.values()),
            "name_like_strings_by_method": {m: result.regex_strings.get(m, 0) for m in REGEX_METHODS},
            "rejected": dict(sorted(result.regex_rejections.items())),
            "rejected_total": sum(result.regex_rejections.values()),
            "unique_names_kept": len(regex_keys),
            "unique_names_kept_by_method": {m: len(by_method.get(m, set())) for m in REGEX_METHODS},
        },
        "gemma": {
            "used": result.gemma_used,
            "model": result.model,
            "lines_sent": result.gemma_lines,
            "fresh_calls": result.gemma_fresh,
            "cached_calls": result.gemma_cached,
            "errors": result.gemma_errors,
            "error_kinds": dict(result.gemma_error_kinds),
            "proposals": result.gemma_proposals,
            "rejected": dict(sorted(result.gemma_rejections.items())),
            "rejected_total": sum(result.gemma_rejections.values()),
            "unique_names_kept": len(gemma_keys),
            "unique_names_only_gemma": len(gemma_keys - regex_keys),
            "latency_seconds": latency_stats(result.gemma_seconds),
            "wall_seconds": round(result.gemma_wall_seconds, 1),
        },
        "names": {"unique_names_after_validator": len(all_keys)},
        "read_seconds": round(result.read_seconds, 1),
        "wall_seconds": round(result.wall_seconds, 1),
    }
    if result.match is not None:
        m = result.match
        review = m.by_status("review")
        review_keys = {x.key for x in review}
        matched_keys = {x.key for x in m.matches}
        gate_reasons: Counter = Counter()
        for key in matched_keys - review_keys:
            reasons = sorted({x.reason or "unknown" for x in m.matches if x.key == key})
            gate_reasons["+".join(reasons)] += 1
        out["match"] = {
            "names_matching_a_deceased_entry": m.keys_matched,
            "name_entry_pairs": len(m.matches),
            "pairs_left_off_by_gate": m.left_off_counts(),
            "names_removed_by_gate": len(matched_keys - review_keys),
            "names_removed_by_gate_by_reason": dict(sorted(gate_reasons.items())),
            "names_passed_gate": len(review_keys),
            "entries_passed_gate_for_review": len(review),
            "entries_passed_gate_only_via_gemma": sum(1 for x in review if x.methods == ["gemma"]),
            "names_matching_only_no_death_date_entries": m.keys_no_death_namesake_only,
            "names_not_in_snapshot": m.keys_unmatched,
        }
    return out


def _receipt_json(receipt: Receipt) -> dict[str, Any]:
    return {
        "path": receipt.path,
        "file": sources.short_path(receipt.path),
        "package": receipt.package,
        "source": receipt.source,
        "lines": [[n, t] for n, t in receipt.lines],
    }


def write_outputs(result: ScanResult, out_dir: Path) -> None:
    """Local working files. They name living people, so out/ is git-ignored."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "candidates.jsonl", "w", encoding="utf-8") as handle:
        for c in result.candidates:
            handle.write(json.dumps({"name": c.name, "key": c.key, "method": c.method, "receipt": _receipt_json(c.receipt)}, ensure_ascii=False) + "\n")
    with open(out_dir / "units.jsonl", "w", encoding="utf-8") as handle:
        for text, units in sorted(result.unique_units.items()):
            handle.write(json.dumps({"text": text, "occurrences": [_receipt_json(u.receipt) for u in units]}, ensure_ascii=False) + "\n")
    with open(out_dir / "rejections.jsonl", "w", encoding="utf-8") as handle:
        for row in result.rejection_log:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if result.match is not None:
        rows = []
        for m in result.match.matches:
            rows.append(
                {
                    "qid": m.qid,
                    "label": m.label,
                    "key": m.key,
                    "status": m.status,
                    "reason": m.reason,
                    "methods": m.methods,
                    "cs_occupations": m.cs_occupations,
                    "link_hits": m.link_hits,
                    "warnings": m.warnings,
                    "receipts": [
                        dict(_receipt_json(c.receipt), method=c.method, name=c.name) for c in m.candidates
                    ],
                }
            )
        (out_dir / "matches.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary(result), indent=1) + "\n", encoding="utf-8")


def display_receipt(receipt: Receipt) -> str:
    lines = " / ".join(strip_emails(t.strip()) for _, t in receipt.lines)
    return f"{sources.short_path(receipt.path)}:{receipt.first_line}  {lines}"
