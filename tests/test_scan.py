"""A whole scan over the real-line fixtures, with a fake Gemma. No model, no network."""

import io
import json
from contextlib import redirect_stdout

from conftest import DOC, PIP

from nitm import cli, scan

LIVING = ["Ivor Jessop", "Mel Wexford", "Cal Strand", "Rolf Stanhope", "Pavel Eggleston", "Mina Hartwell",
          "Anders Grunewald", "Jane Placeholder", "Bruno Pelham", "Thomas G. Vane", "Percy Dunmore"]
EVERYONE = LIVING + ["Ian Murdock", "Phil Katz", "Fredrik Lundh", "Aaron Swartz", "Thomas Lord", "Rory Smythe"]


def run(extractor=None, snap=None):
    return scan.scan(doc_root=str(DOC), pip_roots=[str(PIP)], extractor=extractor, snapshot=snap)


def test_gemma_never_sees_emails_or_license_templates(extractor, snap):
    run(extractor, snap)
    seen = extractor.transport_seen
    assert seen, "free-text lines should reach the model"
    for line in seen:
        assert "@" not in line
        for template in ("James Hacker", "Ty Coon", "Gnomovision", "Yoyodyne"):
            assert template not in line


def test_one_call_per_unique_line(extractor, snap):
    result = run(extractor, snap)
    assert len(extractor.transport_seen) == len(result.unique_units) == result.gemma_lines


def test_gemma_junk_is_rejected_with_a_reason(extractor, snap):
    result = run(extractor, snap)
    rejected = {(r["name"], r["rule"]) for r in result.rejection_log if r["stage"] == "gemma"}
    assert ("Lenka Torrance", "not_in_line") in rejected
    assert ("Essential Claim", "not_in_line") in rejected
    assert ("zlib", "shape") in rejected
    assert ("a Debian package", "not_in_line") in rejected
    kept = {c.name for c in result.candidates if c.method == "gemma"}
    assert "Lenka Torrance" not in kept and "Essential Claim" not in kept


def test_gemma_reaches_a_name_the_regex_misses(extractor, snap):
    result = run(extractor, snap)
    by_method = result.keys_by_method()
    assert "wade davenport" in by_method["gemma"]
    assert "wade davenport" not in by_method.get("byline", set())
    assert scan.summary(result)["gemma"]["unique_names_only_gemma"] == 1


def test_no_model_mode_never_calls_gemma(snap):
    result = run(None, snap)
    assert not result.gemma_used
    assert "phil katz" in result.keys_by_method()["byline"]


def test_funnel(extractor, snap):
    s = scan.summary(run(extractor, snap))
    assert s["files"]["copyright_files"] == 8 and s["files"]["metadata_files"] == 1
    m = s["match"]
    assert m["names_removed_by_gate_by_reason"] == {"unverifiable": 1}  # Rory Smythe
    assert m["names_matching_only_no_death_date_entries"] == 1  # Thomas G. Vane
    assert m["names_passed_gate"] == 5  # Murdock, Katz, Lundh, Swartz, Lord


def test_summary_and_terminal_output_name_no_one(extractor, snap):
    result = run(extractor, snap)
    s = scan.summary(result)
    blob = json.dumps(s)
    out = io.StringIO()
    with redirect_stdout(out):
        cli.print_summary(s, cli.Log(None), snapshot_meta=snap.meta)
    for text in (blob, out.getvalue()):
        assert "@" not in text
        for name in EVERYONE:
            assert name not in text


def test_outputs_go_to_the_local_folder(extractor, snap, tmp_path):
    result = run(extractor, snap)
    scan.write_outputs(result, tmp_path / "out")
    assert {p.name for p in (tmp_path / "out").iterdir()} == {
        "candidates.jsonl", "units.jsonl", "rejections.jsonl", "matches.json", "summary.json"}
