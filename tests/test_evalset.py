import os

import pytest

from conftest import DOC, fake_gemma

from nitm import evalset, gemma, scan


def test_score():
    gold = {"a": {"phil katz"}, "b": {"ian murdock", "bruno pelham"}, "c": set()}
    guess = {"a": {"phil katz"}, "b": {"ian murdock"}, "c": {"james hacker"}}
    assert evalset.score(gold, guess) == {"tp": 2, "fp": 1, "fn": 1, "precision": 0.667, "recall": 0.667}


def test_labels_must_be_exact_substrings_and_complete():
    lines = [{"id": "e1", "text": "written by Thomas Lord."}, {"id": "e2", "text": "nothing"}]
    assert evalset.check_labels(lines, [{"id": "e1", "names": ["Thomas Lord"]}, {"id": "e2", "names": []}]) == []
    assert evalset.check_labels(lines, [{"id": "e1", "names": ["Tom Lord"]}, {"id": "e2", "names": []}])
    assert evalset.check_labels(lines, [{"id": "e1", "names": ["Thomas Lord"]}])


def test_freeze_then_any_change_is_refused(tmp_path):
    lines, labels, frozen = tmp_path / "lines.jsonl", tmp_path / "labels.jsonl", tmp_path / "FROZEN.json"
    evalset.write_jsonl(lines, [{"id": "e1", "kind": "unit", "text": "written by Thomas Lord."}])
    evalset.write_jsonl(labels, [{"id": "e1", "names": ["Thomas Lord"]}])
    with pytest.raises(ValueError):
        evalset.verify_frozen(lines, labels, frozen)  # not frozen yet
    evalset.freeze(lines, labels, frozen)
    assert evalset.verify_frozen(lines, labels, frozen)["gold_names"] == 1
    evalset.write_jsonl(labels, [{"id": "e1", "names": []}])
    with pytest.raises(ValueError):
        evalset.verify_frozen(lines, labels, frozen)


def test_build_includes_the_template_traps():
    result = scan.scan(doc_root=str(DOC), pip_roots=None)
    rows = evalset.build(result, negatives=6)
    texts = [r["text"] for r in rows]
    assert any("James Hacker" in t for t in texts)
    assert any("Ty Coon" in t for t in texts)
    assert all("@" not in t for t in texts)
    assert 3 <= len([r for r in rows if r["kind"] != "unit"]) <= 6  # as many as the fixture pools hold


def test_run_scores_regex_gemma_and_both(tmp_path):
    lines = [
        {"id": "e1", "kind": "unit", "text": "GNU `sdiff' was written by Thomas Lord."},
        {"id": "e2", "kind": "unit", "text": "Wade Davenport designed and implemented the unified output format."},
        {"id": "e3", "kind": "negative-template", "text": "'Gnomovision' (which makes passes at compilers) written by James Hacker."},
    ]
    labels = [{"id": "e1", "names": ["Thomas Lord"]}, {"id": "e2", "names": ["Wade Davenport"]}, {"id": "e3", "names": []}]
    transport = fake_gemma({"Thomas Lord": "Thomas Lord", "Wade Davenport": "Wade Davenport", "James Hacker": "James Hacker"})
    counts, details = evalset.run(lines, labels, gemma.Extractor(transport=transport))
    assert counts["regex"] == {"tp": 1, "fp": 0, "fn": 1, "precision": 1.0, "recall": 0.5}
    assert counts["regex_plus_gemma"]["recall"] == 1.0
    assert counts["gemma_raw"]["fp"] == 1  # James Hacker, before the validator
    assert counts["gemma"]["fp"] == 0  # after it
    assert counts["gemma_denylist_proposals"] == {"James Hacker": 1}
    assert counts["true_names_only_gemma_found"] == 1


def test_run_without_a_model():
    counts, _ = evalset.run([{"id": "e1", "kind": "unit", "text": "written by Thomas Lord."}], [{"id": "e1", "names": ["Thomas Lord"]}])
    assert counts["regex"]["recall"] == 1.0 and "gemma" not in counts
