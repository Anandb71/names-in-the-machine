"""The commands end to end, on the fixtures. No model, no network."""

import json

from conftest import DOC, PIP, SNAPSHOT

from nitm import cli

NAMES = ["Ian Murdock", "Phil Katz", "Rolf Stanhope", "Jane Placeholder", "Thomas G. Vane", "Bruno Pelham"]


def common(tmp_path):
    return ["--data", str(SNAPSHOT), "--out", str(tmp_path / "out")]


def scan(tmp_path, capsys):
    code = cli.main(["scan", "--no-model", "--doc-root", str(DOC), "--no-default-pip", "--pip-root", str(PIP),
                     "--log", str(tmp_path / "run.txt")] + common(tmp_path))
    assert code == 0
    return capsys.readouterr().out


def test_scan_prints_counts_and_no_names(tmp_path, capsys):
    out = scan(tmp_path, capsys)
    assert "files scanned          9: 8 copyright files" in out
    assert "Gemma                  off (--no-model)" in out
    log = (tmp_path / "run.txt").read_text()
    for text in (out, log):
        assert "@" not in text
        for name in NAMES:
            assert name not in text


def test_verify_then_page(tmp_path, capsys):
    scan(tmp_path, capsys)
    review = ["--verified", str(tmp_path / "verified.json"), "--local", str(tmp_path / "local.json"), "--reviewer", "A Reviewer"]
    assert cli.main(["verify", "--approve", "Q92761", "--reason", "same person"] + review + common(tmp_path)) == 2
    assert cli.main(["verify", "--approve", "Q92761", "--reason", "same person", "--source",
                     "https://en.wikipedia.org/wiki/Ian_Murdock", "--primary", "dpkg/copyright:6",
                     "--probe", "dpkg-log:/var/log/dpkg.log"] + review + common(tmp_path)) == 0
    assert cli.main(["verify", "--reject", "Q133867446", "--reason", "not sure"] + review + common(tmp_path)) == 0
    verified = json.loads((tmp_path / "verified.json").read_text())
    assert [p["name"] for p in verified["people"]] == ["Ian Murdock"]
    assert "Q133867446" not in (tmp_path / "verified.json").read_text()
    html_path = tmp_path / "ofrenda.html"
    assert cli.main(["page", "-o", str(html_path)] + review[:4] + common(tmp_path)) == 0
    html = html_path.read_text()
    assert "Ian Murdock" in html and "@" not in html
    assert "Thomas Lord" not in html


def test_eval_run_refuses_unfrozen_labels(tmp_path, capsys):
    lines, labels = tmp_path / "lines.jsonl", tmp_path / "labels.jsonl"
    lines.write_text('{"id": "e1", "kind": "unit", "text": "written by Thomas Lord."}\n')
    labels.write_text('{"id": "e1", "names": ["Thomas Lord"]}\n')
    args = ["--lines", str(lines), "--labels", str(labels), "--freeze", str(tmp_path / "FROZEN.json")]
    assert cli.main(["eval", "run", "--no-model"] + args) == 2
    assert cli.main(["eval", "freeze"] + args) == 0
    assert cli.main(["eval", "run", "--no-model", "--details", str(tmp_path / "d.jsonl")] + args) == 0
    assert "precision 1.000 · recall 1.000" in capsys.readouterr().out


def test_withdraw_removes_an_approved_entry(tmp_path, capsys):
    scan(tmp_path, capsys)
    review = ["--verified", str(tmp_path / "verified.json"), "--local", str(tmp_path / "local.json"), "--reviewer", "A Reviewer"]
    assert cli.main(["verify", "--approve", "Q92761", "--reason", "same person", "--source",
                     "https://en.wikipedia.org/wiki/Ian_Murdock", "--probe", "none"] + review + common(tmp_path)) == 0
    assert cli.main(["verify", "--withdraw", "Q92761"] + review + common(tmp_path)) == 2  # needs a reason
    assert cli.main(["verify", "--withdraw", "Q92761", "--reason", "author's choice"] + review + common(tmp_path)) == 0
    assert json.loads((tmp_path / "verified.json").read_text())["people"] == []
    assert json.loads((tmp_path / "local.json").read_text())["decisions"]["Q92761"]["decision"] == "withdrawn"
