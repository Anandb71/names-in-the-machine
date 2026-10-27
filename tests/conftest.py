"""Shared fixtures. Nothing here calls a model or the network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nitm import gemma, snapshot

FIXTURES = Path(__file__).parent / "fixtures"
DOC = FIXTURES / "doc"
PIP = FIXTURES / "pip"
SNAPSHOT = FIXTURES / "snapshot"

# A raw answer that invents a relationship, as gemma3:1b did in the red-team probe.
INVENTED = "Ian Murdock, a Debian package, died in 2015. This machine contains the package of Ian Murdock."


def fake_gemma(answers: dict[str, str] | None = None, default: str = "NONE"):
    """A stand-in for Ollama. Answers by substring of the line; records every prompt it saw."""
    answers = answers or {}
    seen: list[str] = []

    def transport(payload):
        line = payload["prompt"].rsplit("Line: ", 1)[1].split("\nPeople:", 1)[0]
        seen.append(line)
        for needle, answer in answers.items():
            if needle in line:
                return {"response": answer, "eval_count": 5}
        return {"response": default, "eval_count": 1}

    transport.seen = seen
    return transport


# What the fake model says about each fixture line. Real names where a reader would find
# them, plus junk of the kinds the real model produced: an invented relationship, an org,
# and a name that is not in the line at all.
ANSWERS = {
    "defined by Phil Katz": "Phil Katz",
    "written by Percy Dunmore": "Percy Dunmore; zlib",
    "Wade Davenport designed": "Wade Davenport",
    "These files were written by Ian Murdock": INVENTED,
    "written by Thomas Lord": "Thomas Lord",
    "written by Rory Smythe": "Rory Smythe",
    "GNU `diff' was written": "Mina Hartwell; Dara Hayford; Rolf Stanhope; Lyle Towner; Pavel Eggleston; Essential Claim",
    "Torvald Granfeld": "Torvald Granfeld; Dale MacKinley; Lenka Torrance",
}


@pytest.fixture
def extractor(tmp_path):
    transport = fake_gemma(ANSWERS)
    ex = gemma.Extractor(cache_path=tmp_path / "gemma.jsonl", transport=transport)
    ex.transport_seen = transport.seen
    return ex


@pytest.fixture
def snap():
    return snapshot.load(SNAPSHOT)


@pytest.fixture
def workdir(tmp_path):
    """A scratch copy of the snapshot, so a test can write next to it."""
    data = tmp_path / "data"
    shutil.copytree(SNAPSHOT, data)
    return tmp_path


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
