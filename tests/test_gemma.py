import json
import urllib.error

from conftest import fake_gemma

from nitm import gemma


def test_one_line_per_call_temperature_zero(tmp_path):
    transport = fake_gemma({"Phil Katz": "Phil Katz"})
    ex = gemma.Extractor(cache_path=tmp_path / "c.jsonl", transport=transport)
    payload = ex.payload("The deflate format used by zlib was defined by Phil Katz.")
    assert payload["options"]["temperature"] == 0
    assert payload["model"] == "gemma3:1b"
    assert payload["prompt"].count("Line: ") == 3  # two shots and the one line
    assert payload["prompt"].endswith("Line: The deflate format used by zlib was defined by Phil Katz.\nPeople:")


def test_cached_by_line_hash(tmp_path):
    transport = fake_gemma({"Phil Katz": "Phil Katz"})
    cache = tmp_path / "c.jsonl"
    ex = gemma.Extractor(cache_path=cache, transport=transport)
    line = "The deflate format used by zlib was defined by Phil Katz."
    first = ex.propose(line)
    second = ex.propose(line)
    assert first.names == second.names == ["Phil Katz"]
    assert not first.cached and second.cached
    assert len(transport.seen) == 1
    # A new process reads the cache file and does not call the model again.
    again = gemma.Extractor(cache_path=cache, transport=transport).propose(line)
    assert again.cached and len(transport.seen) == 1
    row = json.loads(cache.read_text().splitlines()[0])
    assert row["key"] == ex.cache_key(line) and row["raw"] == "Phil Katz"


def test_parse_names():
    assert gemma.parse_names("NONE") == []
    assert gemma.parse_names("People: Anna Kowalski; Rui Duarte") == ["Anna Kowalski", "Rui Duarte"]
    assert gemma.parse_names("Mina Hartwell, Dara Hayford and Pavel Eggleston") == ["Mina Hartwell", "Dara Hayford", "Pavel Eggleston"]
    assert gemma.parse_names("") == []


def test_model_down_is_an_error_not_a_guess(tmp_path):
    def broken(payload):
        raise urllib.error.URLError("connection refused")

    proposal = gemma.Extractor(transport=broken).propose("written by Thomas Lord.")
    assert proposal.error and proposal.names == []


def test_example_names_in_the_shots_are_not_in_any_fixture():
    from conftest import DOC

    text = " ".join(p.read_text(encoding="utf-8") for p in DOC.glob("*/copyright"))
    for line, answer in gemma.SHOTS:
        for name in answer.split("; "):
            if name != "NONE":
                assert name not in text
