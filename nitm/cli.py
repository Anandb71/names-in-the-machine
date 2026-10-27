"""nitm: find the people who have died whose names still ship in this computer's license files.

    nitm scan                 read the files, extract names, match the snapshot (counts only)
    nitm verify               approve or turn down each match by hand -> verified.json
    nitm still                measure what each verified person's named work still does here
    nitm page                 write the static ofrenda page from verified.json
    nitm eval build|freeze|run    the hand-labelled free-text eval
    nitm snapshot fetch|info  the dated Wikidata snapshot (fetch needs the network, once)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from nitm import __version__, evalset, gemma, page, review, scan, snapshot, sources, still

ROOT = Path(os.environ.get("NITM_HOME") or Path(__file__).resolve().parent.parent)


def _now() -> str:
    return datetime.now(snapshot.IST).strftime("%Y-%m-%d %H:%M IST")


def _n(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


class Log:
    """Print to stdout and, with --log, append the same lines to a run log."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, line: str = "") -> None:
        print(line, flush=True)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def block(self, title: str, data: Any) -> None:
        """A JSON block in the log file only (the terminal gets the readable lines)."""
        if self.path:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(f"--- {title} (json)\n{json.dumps(data, indent=1, ensure_ascii=False)}\n")


def _rules(counts: dict[str, int]) -> str:
    return " · ".join(f"{rule} {_n(n)}" for rule, n in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"


def _extractor(args) -> gemma.Extractor | None:
    if getattr(args, "no_model", False):
        return None
    return gemma.Extractor(model=args.model, cache_path=Path(args.cache) if args.cache else None, timeout=args.timeout)


def _pip_roots(args) -> list[str]:
    roots = [] if args.no_default_pip else sources.default_pip_roots()
    roots += [str(Path(p).resolve()) for p in args.pip_root or []]
    return sources.collapse_roots(roots)


def _reviewer(args) -> str | None:
    """Who is approving: --reviewer, then NITM_REVIEWER, then git's user.name. Never a built-in name."""
    if args.reviewer:
        return args.reviewer
    if os.environ.get("NITM_REVIEWER"):
        return os.environ["NITM_REVIEWER"]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or None


# --- scan -----------------------------------------------------------------------------------


def print_summary(s: dict[str, Any], log: Log, *, snapshot_meta: dict[str, Any] | None) -> None:
    """The scan summary. Counts only: nothing here names a person."""
    f, l, r, g = s["files"], s["lines"], s["regex"], s["gemma"]
    by = r["name_like_strings_by_method"]
    log(f"  files scanned          {_n(f['files_scanned'])}: {_n(f['copyright_files'])} copyright files ({_n(f['dep5_files'])} DEP-5) + {_n(f['metadata_files'])} METADATA files")
    log(f"  lines read             {_n(f['lines_read'])}")
    log(f"  name-like strings      {_n(r['name_like_strings'])} from the regex (Copyright lines {_n(by['copyright'])} · author fields {_n(by['field'])} · free text {_n(by['byline'])})")
    log(f"  regex rejected         {_n(r['rejected_total'])} ({_rules(r['rejected'])})")
    log(f"  license text stripped  {_n(l['license_template_lines_stripped'])} lines + {_n(l['license_template_sentences_stripped'])} sentences, before any model sees them")
    log(f"  free-text credit lines {_n(l['free_text_credit_lines_unique'])} unique ({_n(l['free_text_credit_lines'])} in all)")
    if g["used"]:
        lat = g["latency_seconds"]
        timing = f"latency min/median/max {lat['min']}/{lat['median']}/{lat['max']} s over {lat['n']} fresh calls" if lat else "all answers from cache"
        log(f"  Gemma ({g['model']})     {_n(g['lines_sent'])} lines, one per call · {_n(g['fresh_calls'])} fresh, {_n(g['cached_calls'])} cached, {_n(g['errors'])} errors")
        log(f"                         {_n(g['proposals'])} names proposed · {_n(g['rejected_total'])} rejected ({_rules(g['rejected'])})")
        log(f"                         {timing}")
        for kind, n in g.get("error_kinds", {}).items():
            log(f"                         error ×{n}: {kind}")
    else:
        log("  Gemma                  off (--no-model): free text read by the regex only")
    log(f"  names after validator  {_n(s['names']['unique_names_after_validator'])} unique (regex {_n(r['unique_names_kept'])} · Gemma {_n(g['unique_names_kept'])} · only Gemma {_n(g['unique_names_only_gemma'])})")
    m = s.get("match")
    if m:
        snap = ""
        if snapshot_meta:
            snap = f" (snapshot {str(snapshot_meta.get('retrieved_finished', ''))[:10]}, {_n(snapshot_meta['counts']['people'])} people)"
        log(f"  Wikidata matches       {_n(m['names_matching_a_deceased_entry'])} names match someone who died{snap}")
        log(f"  homonym gate           removed {_n(m['names_removed_by_gate'])} ({_rules(m['names_removed_by_gate_by_reason'])}) · {_n(m['names_passed_gate'])} names go to review ({_n(m['entries_passed_gate_for_review'])} entries, {_n(m['entries_passed_gate_only_via_gemma'])} only via Gemma)")
        log(f"  no death date          {_n(m['names_matching_only_no_death_date_entries'])} names match only people with no death date: left off")
    gemma_wall = f" · {g['wall_seconds']} s free text" if g["used"] else ""
    log(f"  wall time              {s['read_seconds']} s reading{gemma_wall} · {s['wall_seconds']} s total")


def cmd_scan(args) -> int:
    log = Log(args.log)
    extractor = _extractor(args)
    snap = None
    data = Path(args.data)
    if (data / "deceased.json").exists():
        snap = snapshot.load(data)
    else:
        print(f"nitm: no snapshot in {data}; names will not be matched (run `nitm snapshot fetch` once)", file=sys.stderr)
    roots = [] if args.no_pip else _pip_roots(args)
    doc_root = None if args.no_docs else args.doc_root
    log(f"nitm scan · {_now()} · {'regex only' if extractor is None else extractor.model}")
    log(f"  command                nitm {' '.join(sys.argv[1:]) if args.argv is None else ' '.join(args.argv)}")
    log(f"  sources                {doc_root or '(no doc root)'} + {len(roots)} Python roots")
    result = scan.scan(doc_root=doc_root, pip_roots=roots, extractor=extractor, snapshot=snap,
                       progress=lambda m: print(f"  … {m}", file=sys.stderr, flush=True))
    s = scan.summary(result)
    scan.write_outputs(result, Path(args.out))
    if args.json:
        print(json.dumps(s, indent=1))
    print_summary(s, log, snapshot_meta=snap.meta if snap else None)
    log.block("scan summary", s)
    if s.get("match"):
        log(f"  next                   nitm verify   (local working files in {args.out}/, git-ignored)")
    return 0


# --- verify ---------------------------------------------------------------------------------


def _load_review(args):
    matches = json.loads((Path(args.out) / "matches.json").read_text(encoding="utf-8"))
    snap = snapshot.load(Path(args.data))
    verified = review.load_verified(Path(args.verified))
    local = review.load_json(Path(args.local), {"decisions": {}})
    return matches, snap, verified, local


def cmd_verify(args) -> int:
    try:
        matches, snap, verified, local = _load_review(args)
    except FileNotFoundError as exc:
        print(f"nitm: {exc.filename} is missing; run `nitm scan` first", file=sys.stderr)
        return 2
    queue = review.merge_by_qid(matches)
    if args.list:
        decided = review.decided_qids(verified, local)
        for index, match in enumerate(queue, 1):
            mark = "decided" if match["qid"] in decided else "open"
            print(f"({mark}) " + review.screen(match, snap.people[match["qid"]], index, len(queue)))
        return 0
    reviewer = _reviewer(args)
    if not reviewer:
        print("nitm: say who is reviewing: --reviewer NAME (or NITM_REVIEWER, or git config user.name)", file=sys.stderr)
        return 2
    if args.approve or args.reject:
        qid = args.approve or args.reject
        found = [m for m in queue if m["qid"] == qid]
        if not found:
            print(f"nitm: {qid} is not in the review queue", file=sys.stderr)
            return 2
        probe = None
        if args.probe == "none":
            probe = {"probe": "", "arg": ""}  # nothing on this machine can be measured honestly for this card
        elif args.probe:
            kind, _, arg = args.probe.partition(":")
            probe = {"probe": kind, "arg": arg}
        try:
            entry = review.decide(found[0], snap.people[qid], verified, local, approve=bool(args.approve),
                                  reason=args.reason or "", source=args.source, reviewer=reviewer,
                                  snapshot_date=snap.retrieved, probe=probe, primary=args.primary)
        except ValueError as exc:
            print(f"nitm: {exc}", file=sys.stderr)
            return 2
        review.save_json(Path(args.verified), verified)
        review.save_json(Path(args.local), local)
        print(f"saved: {entry['name']} ({entry['years']})" if entry else f"left off: {qid}")
        return 0
    approved, rejected, skipped = review.interactive(
        matches, snap.people, verified, local, reviewer=reviewer, snapshot_date=snap.retrieved,
        ask=input, out=sys.stdout,
    )
    review.save_json(Path(args.verified), verified)
    review.save_json(Path(args.local), local)
    print(f"approved {approved} · left off {rejected} · skipped {skipped}")
    return 0


# --- still ----------------------------------------------------------------------------------


def cmd_still(args) -> int:
    log = Log(args.log)
    path = Path(args.verified)
    verified = review.load_verified(path)
    roots = _pip_roots(args)
    summary = review.load_json(Path(args.out) / "summary.json", None)
    if not args.pip_root and summary and (summary.get("sources") or {}).get("pip_roots"):
        roots = summary["sources"]["pip_roots"]  # count installs where the scan looked, not elsewhere
    log(f"nitm still · {_now()}")
    for entry in verified.get("people", []):
        probe = entry.get("still") or {}
        if not probe.get("probe"):
            continue
        try:
            m = still.measure(probe["probe"], probe.get("arg", ""), pip_roots=roots)
        except ValueError as exc:  # an unknown probe: record it, keep going
            log(f"  {entry['name']}: not measured ({exc})")
            continue
        entry["still"] = m.to_json()
        if m.value is None:
            log(f"  {entry['name']}: not measured ({m.error})")
        else:
            log(f"  {entry['name']}: {m.sentence}")
        log(f"      $ {m.command}")
        log(f"      measured {m.measured_at}")
        review.save_json(path, verified)
    review.save_json(path, verified)
    return 0


# --- page -----------------------------------------------------------------------------------


def cmd_page(args) -> int:
    verified = review.load_verified(Path(args.verified))
    summary = review.load_json(Path(args.out) / "summary.json", None)
    local = review.load_json(Path(args.local), {"decisions": {}})
    meta = None
    if (Path(args.data) / "deceased.json").exists():
        meta = snapshot.load(Path(args.data)).meta
    for name, problems in page.skipped(verified):
        print(f"nitm: not on the page: {name} ({'; '.join(problems)})", file=sys.stderr)
    public = review.text_problems(json.dumps(review.publishable(verified), ensure_ascii=False))
    if public:
        print(f"nitm: page not written: verified.json {public[0]}", file=sys.stderr)
        return 1
    html = page.render(verified, summary=summary, review_counts=review.review_counts(local), snapshot_meta=meta)
    problems = page.check(html)
    if problems:
        for problem in problems:
            print(f"nitm: page not written: {problem}", file=sys.stderr)
        return 1
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"wrote {args.output}: {len(review.publishable(verified))} cards")
    return 0


# --- eval -----------------------------------------------------------------------------------


def cmd_eval_build(args) -> int:
    if Path(args.freeze).exists() and not args.force:
        print(f"nitm: {args.freeze} exists; the eval is frozen (use --force to rebuild and re-label)", file=sys.stderr)
        return 2
    result = scan.scan(doc_root=args.doc_root, pip_roots=None, extractor=None, snapshot=None)
    rows = evalset.build(result, args.negatives)
    evalset.write_jsonl(Path(args.lines), rows)
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    print(f"wrote {args.lines}: {len(rows)} lines ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})")
    print(f"label them by hand ({ROOT / 'docs' / 'eval-labelling.md'}) into {args.labels}, then `nitm eval freeze`")
    return 0


def cmd_eval_freeze(args) -> int:
    log = Log(args.log)
    try:
        record = evalset.freeze(Path(args.lines), Path(args.labels), Path(args.freeze))
    except (ValueError, FileNotFoundError) as exc:
        print(f"nitm: {exc}", file=sys.stderr)
        return 2
    log(f"nitm eval freeze · {record['frozen_at']}")
    log(f"  lines                  {_n(record['lines'])} ({', '.join(f'{k} {v}' for k, v in sorted(record['lines_by_kind'].items()))})")
    log(f"  gold names             {_n(record['gold_names'])} on {_n(record['lines_with_a_person'])} lines")
    log(f"  lines sha256           {record['lines_sha256']}")
    log(f"  labels sha256          {record['labels_sha256']}")
    return 0


def _score_line(name: str, s: dict[str, Any]) -> str:
    return f"  {name:<22} precision {s['precision']:.3f} · recall {s['recall']:.3f}   (tp {s['tp']} · fp {s['fp']} · fn {s['fn']})"


def cmd_eval_run(args) -> int:
    log = Log(args.log)
    try:
        record = evalset.verify_frozen(Path(args.lines), Path(args.labels), Path(args.freeze))
    except (ValueError, FileNotFoundError) as exc:
        print(f"nitm: {exc}", file=sys.stderr)
        return 2
    lines = evalset.read_jsonl(Path(args.lines))
    labels = evalset.read_jsonl(Path(args.labels))
    extractor = _extractor(args)
    counts, details = evalset.run(lines, labels, extractor, progress=lambda m: print(f"  … {m}", file=sys.stderr, flush=True))
    log(f"nitm eval run · {_now()} · {'regex only' if extractor is None else extractor.model}")
    log(f"  labels frozen          {record['frozen_at']} (labels sha256 {record['labels_sha256'][:16]}…)")
    log(f"  lines                  {_n(counts['lines'])} ({', '.join(f'{k} {v}' for k, v in sorted(counts['lines_by_kind'].items()))})")
    log(f"  gold names             {_n(counts['gold_names'])} on {_n(counts['lines_with_a_person'])} lines")
    log(_score_line("regex only", counts["regex"]))
    if extractor is not None:
        log(_score_line("Gemma, unvalidated", counts["gemma_raw"]))
        log(_score_line("Gemma, validated", counts["gemma"]))
        log(_score_line("regex + Gemma", counts["regex_plus_gemma"]))
        log(f"  true names only Gemma found {counts['true_names_only_gemma_found']} · only the regex found {counts['true_names_only_regex_found']}")
        log(f"  Gemma rejections       {_rules(counts['gemma_rejections'])}")
        if counts["gemma_denylist_proposals"]:
            log(f"  denylisted proposals   {', '.join(f'{k} ×{v}' for k, v in counts['gemma_denylist_proposals'].items())}")
        lat = counts["gemma_latency_seconds"]
        calls = counts["gemma_calls"]
        log(f"  Gemma calls            {calls['fresh']} fresh · {calls['cached']} cached · {calls['errors']} errors")
        if lat:
            log(f"  latency (fresh calls)  min {lat['min']} s · median {lat['median']} s · max {lat['max']} s (n {lat['n']})")
    log.block("eval counts", counts)
    if args.details:
        evalset.write_jsonl(Path(args.details), details)
    return 0


# --- snapshot -------------------------------------------------------------------------------


def cmd_snapshot_fetch(args) -> int:
    logs = Path(args.logs)
    logs.mkdir(parents=True, exist_ok=True)
    now = datetime.now(snapshot.IST)
    log_path = logs / f"snapshot_fetch_{now.date().isoformat()}.txt"
    with open(log_path, "a", encoding="utf-8") as log:
        def write(message: str) -> None:
            log.write(message + "\n")
            log.flush()
            print(message, file=sys.stderr, flush=True)

        write(f"# nitm snapshot fetch, started {now.isoformat(timespec='seconds')}")
        write(f"# command: nitm {' '.join(sys.argv[1:])}")
        write(f"# endpoint: {snapshot.ENDPOINT}")
        write(f"# user agent: {snapshot.USER_AGENT}")
        client = snapshot.Client(on_log=write)
        try:
            meta = snapshot.fetch(Path(args.data), client=client, progress=lambda m: write(f"== {m}"))
        except Exception as exc:  # the log keeps the failure; nothing is written to data/
            write(f"# FAILED: {type(exc).__name__}: {exc}")
            return 1
        write(f"# finished {meta['retrieved_finished']}")
        for key, value in meta["counts"].items():
            write(f"# {key}: {value}")
        for problem in meta["incomplete"]:
            write(f"# incomplete: {problem}")
        write(f"# wrote {Path(args.data) / 'deceased.json'} and {Path(args.data) / 'no_death_date.txt'}")
    return 0


def cmd_snapshot_info(args) -> int:
    snap = snapshot.load(Path(args.data))
    meta = snap.meta
    if args.markdown:
        print(snapshot.describe(meta))
        return 0
    print(f"Wikidata snapshot, {meta['license']}")
    print(f"  retrieved   {meta['retrieved_started']} to {meta['retrieved_finished']}")
    for key, value in meta["counts"].items():
        print(f"  {key:<34}{_n(value)}")
    print(f"  incomplete  {', '.join(meta.get('incomplete') or []) or 'nothing'}")
    print(f"  name index  {_n(len(snap.index))} keys")
    return 0


# --- parser ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nitm", description="Find the people who have died whose names still ship in your license files.")
    parser.add_argument("--version", action="version", version=f"nitm {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def paths(p, *, out=False, data=False, verified=False, local=False):
        if out:
            p.add_argument("--out", default=str(ROOT / "out"), help="local working files (git-ignored)")
        if data:
            p.add_argument("--data", default=str(ROOT / "data"), help="the Wikidata snapshot")
        if verified:
            p.add_argument("--verified", default=str(ROOT / "verified.json"))
        if local:
            p.add_argument("--local", default=str(ROOT / "review.local.json"), help="turned-down matches (stays on this machine)")

    def model(p):
        p.add_argument("--no-model", action="store_true", help="regex only; never call Gemma")
        p.add_argument("--model", default=gemma.DEFAULT_MODEL)
        p.add_argument("--cache", default=str(ROOT / "cache" / "gemma.jsonl"), help="Gemma answers, cached by line hash")
        p.add_argument("--timeout", type=int, default=gemma.DEFAULT_TIMEOUT)

    def pip(p):
        p.add_argument("--pip-root", action="append", help="also walk this directory for *.dist-info/METADATA (repeatable)")
        p.add_argument("--no-default-pip", action="store_true", help="skip this interpreter's site-packages and the system ones")

    s = sub.add_parser("scan", help="read license files, extract names, match the snapshot")
    s.add_argument("--doc-root", default=sources.DOC_ROOT)
    s.add_argument("--no-docs", action="store_true", help="skip /usr/share/doc")
    s.add_argument("--no-pip", action="store_true", help="skip Python METADATA files")
    s.add_argument("--json", action="store_true", help="also print the summary as JSON")
    s.add_argument("--log", help="append the summary to this run log")
    paths(s, out=True, data=True)
    model(s)
    pip(s)
    s.set_defaults(func=cmd_scan, argv=None)

    v = sub.add_parser("verify", help="approve or turn down each match by hand")
    paths(v, out=True, data=True, verified=True, local=True)
    v.add_argument("--reviewer", help="who is approving (default: NITM_REVIEWER, then git config user.name)")
    v.add_argument("--list", action="store_true", help="show the review queue and stop")
    v.add_argument("--approve", metavar="QID", help="approve one match without the prompt")
    v.add_argument("--reject", metavar="QID", help="turn down one match without the prompt")
    v.add_argument("--reason")
    v.add_argument("--source", help="second-source URL (needed to approve)")
    v.add_argument("--probe", help="still-working probe, e.g. rdepends:zlib1g, dpkg-log:/var/log/dpkg.log, importers:re@/usr/lib/python3.13, or none")
    v.add_argument("--primary", help="the receipt to show first, e.g. dpkg/copyright:6")
    v.set_defaults(func=cmd_verify)

    st = sub.add_parser("still", help="measure one thing on this machine for each verified card")
    paths(st, verified=True, out=True)
    st.add_argument("--log", help="append the measurements to this run log")
    pip(st)
    st.set_defaults(func=cmd_still)

    pg = sub.add_parser("page", help="write the static ofrenda page")
    paths(pg, out=True, data=True, verified=True, local=True)
    pg.add_argument("-o", "--output", default="ofrenda.html")
    pg.set_defaults(func=cmd_page)

    ev = sub.add_parser("eval", help="the labelled free-text eval")
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)

    def eval_paths(p):
        p.add_argument("--lines", default=str(ROOT / "eval" / "lines.jsonl"))
        p.add_argument("--labels", default=str(ROOT / "eval" / "labels.jsonl"))
        p.add_argument("--freeze", default=str(ROOT / "eval" / "FROZEN.json"))

    eb = ev_sub.add_parser("build", help="write the eval lines from this machine")
    eval_paths(eb)
    eb.add_argument("--doc-root", default=sources.DOC_ROOT)
    eb.add_argument("--negatives", type=int, default=40)
    eb.add_argument("--force", action="store_true")
    eb.set_defaults(func=cmd_eval_build)
    ef = ev_sub.add_parser("freeze", help="record the sha256 of the lines and labels")
    eval_paths(ef)
    ef.add_argument("--log", help="append the freeze record to this log")
    ef.set_defaults(func=cmd_eval_freeze)
    er = ev_sub.add_parser("run", help="score the regex, Gemma, and both")
    eval_paths(er)
    model(er)
    er.add_argument("--log", help="append the scores to this run log")
    er.add_argument("--details", default=str(ROOT / "out" / "eval_details.jsonl"), help="per-line results (names living people; git-ignored)")
    er.set_defaults(func=cmd_eval_run)

    sn = sub.add_parser("snapshot", help="the dated Wikidata snapshot")
    sn_sub = sn.add_subparsers(dest="snapshot_command", required=True)
    sf = sn_sub.add_parser("fetch", help="query Wikidata once and write data/ (needs the network)")
    sf.add_argument("--data", default=str(ROOT / "data"))
    sf.add_argument("--logs", default=str(ROOT / "logs"))
    sf.set_defaults(func=cmd_snapshot_fetch)
    si = sn_sub.add_parser("info", help="what is in the snapshot")
    si.add_argument("--data", default=str(ROOT / "data"))
    si.add_argument("--markdown", action="store_true", help="print data/README.md")
    si.set_defaults(func=cmd_snapshot_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "argv"):
        args.argv = argv
    return args.func(args)
