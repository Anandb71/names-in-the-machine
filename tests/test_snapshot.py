import io
import json
import urllib.error
import urllib.parse

import pytest

from conftest import SNAPSHOT

from nitm import snapshot
from nitm.text import key_hash


def http_error(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return urllib.error.HTTPError("https://query.wikidata.org/sparql", code, "error", headers, io.BytesIO(b""))


def ok(rows):
    return {"results": {"bindings": rows}}


def test_retries_504_with_exponential_backoff_and_jitter():
    calls, waits = [], []

    def send(data):
        calls.append(data)
        if len(calls) < 4:
            raise http_error(504)
        return ok([{"p": {"value": "http://www.wikidata.org/entity/Q92761"}}])

    client = snapshot.Client(send=send, sleep=waits.append, pause=0)
    rows = client.query("SELECT ?p WHERE {}", name="t")
    assert len(rows) == 1 and len(calls) == 4
    backoff = waits[:3]
    assert 5 <= backoff[0] <= 6.25 and 10 <= backoff[1] <= 12.5 and 20 <= backoff[2] <= 25
    assert any("HTTP 504" in line for line in client.log)


def test_honours_retry_after_on_429():
    waits = []
    replies = [http_error(429, retry_after=120), ok([])]

    def send(data):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    snapshot.Client(send=send, sleep=waits.append, pause=0).query("q", name="t")
    assert waits[0] == 120


def test_gives_up_after_six_attempts_with_80s_cap():
    waits = []

    def send(data):
        raise http_error(504)

    client = snapshot.Client(send=send, sleep=waits.append, pause=0)
    with pytest.raises(snapshot.FetchError):
        client.query("q", name="t")
    assert len(waits) == 5
    assert max(waits) <= 80 * 1.25


def test_does_not_retry_a_bad_query():
    def send(data):
        raise http_error(400)

    with pytest.raises(urllib.error.HTTPError):
        snapshot.Client(send=send, sleep=lambda s: None, pause=0).query("q", name="t")


def test_batches_that_keep_failing_are_split():
    def send(data):
        query = urllib.parse.parse_qs(data.decode())["query"][0]
        if query.count("wd:Q") > 5:
            raise http_error(504)
        return ok([{"n": {"value": str(query.count("wd:Q"))}}])

    client = snapshot.Client(send=send, sleep=lambda s: None, pause=0)
    rows = snapshot.batched(client, snapshot.Q_NAMES, [f"Q{i}" for i in range(20)], 10, name="names", min_size=2)
    assert sum(int(r["n"]["value"]) for r in rows) == 20
    assert any("splitting a batch of 10" in line for line in client.log)


def test_snapshot_meta_records_queries_date_and_license():
    raw = json.loads((SNAPSHOT / "deceased.json").read_text(encoding="utf-8"))
    meta = raw["meta"]
    assert meta["license"].startswith("CC0")
    assert meta["retrieved_finished"][:4] == "2026"
    assert "deceased_by_occupation" in meta["queries"]
    assert "P509" in meta["not_requested"]  # cause of death is never asked for


def test_no_death_date_file_holds_hashes_not_names():
    lines = [l for l in (SNAPSHOT / "no_death_date.txt").read_text().splitlines() if not l.startswith("#")]
    assert key_hash("thomas vane") in lines
    assert all(len(l) == 12 and all(c in "0123456789abcdef" for c in l) for l in lines)


def test_index_has_no_single_word_keys(snap):
    assert all(len(key.split()) >= 2 for key in snap.index)
    assert snap.lookup("murdock") == []
    assert [p["qid"] for p in snap.lookup("ian murdock")] == ["Q92761"]
