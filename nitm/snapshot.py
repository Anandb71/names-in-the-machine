"""A dated, committed snapshot of Wikidata (CC0). Fetched once; the tool runs offline after that.

What is in it:
- data/deceased.json: humans with a death date (P570) who have a software or computer
  science occupation, or who are linked as developer/creator/programmer/designer/author of a
  software item. Labels, aliases, birth and death dates with their precision, occupations,
  software links. Nothing about how anyone died: cause, manner and place of death are
  never requested.
- data/no_death_date.txt: for humans with those occupations or software links and no death
  date, only a short hash of each name. The tool can say "this name matches someone on
  Wikidata with no death date, so it is left off" without carrying a list of living people's
  names.

The query service is shared and often busy. Every query is retried with exponential backoff
and jitter; a batch that keeps failing is split in half, and a single heavy query falls back
to LIMIT/OFFSET pages. Every attempt is written to the fetch log.
"""

from __future__ import annotations

import http.client
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from nitm.text import key_hash, name_key

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "names-in-the-machine/0.1 (https://github.com/Anandb71/names-in-the-machine; "
    "one-time CC0 snapshot of deceased software people for an offline tool)"
)
IST = timezone(timedelta(hours=5, minutes=30))
TRANSIENT_HTTP = (429, 500, 502, 503, 504)

OCCUPATION_ROOTS = {
    "Q5482740": "programmer",
    "Q82594": "computer scientist",
    "Q1709010": "software engineer",
    "Q183888": "software developer",
}
# Properties that link a software item to a person.
LINK_PROPS = {
    "P178": "developer",
    "P170": "creator",
    "P50": "author",
    "P943": "programmer",
    "P287": "designed by",
    "P112": "founded by",
    "P61": "discoverer or inventor",
}
# Too broad for a whole-graph query; still used as links per person (Q_LINKS_IN).
LINK_PROPS_PER_PERSON_ONLY = ("P112", "P61")

Q_OCCUPATIONS = """SELECT DISTINCT ?occ ?occLabel WHERE {
  VALUES ?root { %(roots)s }
  ?occ wdt:P279* ?root .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

Q_DECEASED_BY_OCCUPATION = """SELECT DISTINCT ?p WHERE {
  VALUES ?occ { %(occs)s }
  ?p wdt:P106 ?occ ; wdt:P570 ?died .
}"""

Q_DECEASED_BY_LINK = """SELECT DISTINCT ?p ?sw WHERE {
  ?sw wdt:%(prop)s ?p .
  ?p wdt:P31 wd:Q5 ; wdt:P570 ?died .
  ?sw wdt:P31/wdt:P279* wd:Q7397 .
}"""

Q_NO_DEATH_BY_OCCUPATION = """SELECT DISTINCT ?p WHERE {
  VALUES ?occ { %(occs)s }
  ?p wdt:P106 ?occ ; wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?p wdt:P570 ?died }
}"""

Q_NO_DEATH_BY_LINK = """SELECT DISTINCT ?p WHERE {
  ?sw wdt:%(prop)s ?p .
  ?p wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?p wdt:P570 ?died }
  ?sw wdt:P31/wdt:P279* wd:Q7397 .
}"""

Q_NAMES = """SELECT ?p ?kind ?name WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?kind { rdfs:label skos:altLabel }
  ?p ?kind ?name .
  FILTER(LANG(?name) IN ("en", "mul"))
}"""

Q_FACTS = """SELECT ?p ?prop ?value ?valueLabel WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?prop { wdt:P31 wdt:P106 }
  ?p ?prop ?value .
  OPTIONAL { ?value rdfs:label ?valueLabel . FILTER(LANG(?valueLabel) = "en") }
}"""

Q_DATES = """SELECT ?p ?which ?time ?precision ?rank WHERE {
  VALUES ?p { %(qids)s }
  VALUES (?which ?claim ?valueProp) { ("born" p:P569 psv:P569) ("died" p:P570 psv:P570) }
  ?p ?claim ?statement .
  ?statement wikibase:rank ?rank .
  OPTIONAL { ?statement ?valueProp ?node . ?node wikibase:timeValue ?time ; wikibase:timePrecision ?precision . }
}"""

# Per-person links, kept cheap: no labels and no reverse P50 (that one returns every
# scholarly article a computer scientist wrote). P50 links to software come from
# Q_DECEASED_BY_LINK instead, which only matches software items.
Q_LINKS_IN = """SELECT ?p ?prop ?sw WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?prop { wdt:P178 wdt:P170 wdt:P943 wdt:P287 wdt:P112 wdt:P61 }
  ?sw ?prop ?p .
}"""

Q_LINKS_OUT = """SELECT ?p ?sw WHERE {
  VALUES ?p { %(qids)s }
  ?p wdt:P800 ?sw .
}"""

Q_LABELS = """SELECT ?sw ?swLabel WHERE {
  VALUES ?sw { %(qids)s }
  ?sw rdfs:label ?swLabel . FILTER(LANG(?swLabel) = "en")
}"""


class FetchError(RuntimeError):
    """A query kept failing after every retry."""


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def _chunks(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


@dataclass
class Client:
    """A polite SPARQL client: descriptive User-Agent, a pause between queries, and
    exponential backoff with jitter (5s, 10s, 20s, 40s, 80s) on 429/5xx and timeouts."""

    endpoint: str = ENDPOINT
    pause: float = 1.0
    attempts: int = 6
    base_delay: float = 5.0
    max_delay: float = 80.0
    timeout: float = 120.0
    log: list[str] = field(default_factory=list)
    on_log: Callable[[str], None] | None = None
    sleep: Callable[[float], None] = time.sleep
    send: Callable[[bytes], dict[str, Any]] | None = None  # tests swap the network out
    rng: random.Random = field(default_factory=random.Random)
    queries: int = 0
    rows: int = 0
    failures: int = 0

    def note(self, message: str) -> None:
        stamped = f"{datetime.now(IST).strftime('%H:%M:%S')} {message}"
        self.log.append(stamped)
        if self.on_log:
            self.on_log(stamped)

    def _send(self, data: bytes) -> dict[str, Any]:
        if self.send is not None:
            return self.send(data)
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def query(self, sparql: str, *, name: str = "query", attempts: int | None = None) -> list[dict[str, Any]]:
        attempts = attempts or self.attempts
        data = urllib.parse.urlencode({"query": sparql, "format": "json"}).encode()
        delay = self.base_delay
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            retry_after = None
            try:
                body = self._send(data)
                rows = body["results"]["bindings"]
                self.queries += 1
                self.rows += len(rows)
                self.note(f"{name}: {len(rows)} rows in {time.monotonic() - started:.1f}s (attempt {attempt})")
                self.sleep(self.pause)
                return rows
            except urllib.error.HTTPError as exc:
                if exc.code not in TRANSIENT_HTTP:
                    self.note(f"{name}: HTTP {exc.code}, not retried")
                    raise
                problem = f"HTTP {exc.code}"
                retry_after = _retry_after(exc)
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError,
                    http.client.HTTPException, json.JSONDecodeError, KeyError) as exc:
                problem = type(exc).__name__
            self.failures += 1
            elapsed = time.monotonic() - started
            if attempt == attempts:
                self.note(f"{name}: {problem} after {elapsed:.1f}s on attempt {attempt}/{attempts}; giving up")
                raise FetchError(f"{name}: {problem} after {attempts} attempts")
            wait = min(delay, self.max_delay)
            wait += self.rng.uniform(0, wait * 0.25)
            if retry_after:
                wait = max(wait, retry_after)
            self.note(f"{name}: {problem} after {elapsed:.1f}s on attempt {attempt}/{attempts}; waiting {wait:.1f}s")
            self.sleep(wait)
            delay *= 2
        raise FetchError(f"{name}: no attempts made")


def batched(
    client: Client,
    template: str,
    items: list[str],
    size: int,
    *,
    name: str,
    slot: str = "qids",
    min_size: int = 8,
    first_attempts: int = 3,
) -> list[dict[str, Any]]:
    """Run a VALUES query over items in batches. A batch that keeps failing is split in half;
    only a batch of min_size or fewer gets the full retry budget before giving up."""
    rows: list[dict[str, Any]] = []
    queue = list(_chunks(items, size))
    while queue:
        batch = queue.pop(0)
        small = len(batch) <= min_size
        try:
            rows.extend(
                client.query(template % {slot: _values(batch)}, name=f"{name} [{len(batch)}]",
                             attempts=None if small else first_attempts)
            )
        except FetchError:
            if small:
                raise
            half = len(batch) // 2
            client.note(f"{name}: splitting a batch of {len(batch)} into {half} + {len(batch) - half}")
            queue[:0] = [batch[:half], batch[half:]]
    return rows


def paged(client: Client, sparql: str, *, name: str, page: int = 10_000) -> list[dict[str, Any]]:
    """LIMIT/OFFSET paging for one heavy query (sorted, so pages are stable)."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        got = client.query(f"{sparql}\nORDER BY ?p\nLIMIT {page}\nOFFSET {offset}", name=f"{name} offset {offset}")
        rows.extend(got)
        if len(got) < page:
            return rows
        offset += page


def _people_by_occupation(client: Client, template: str, occs: list[str], *, name: str) -> set[str]:
    """Run an occupation query in chunks; split chunks that fail; page a single occupation that fails."""
    found: set[str] = set()
    queue = list(_chunks(occs, 60))
    while queue:
        batch = queue.pop(0)
        sparql = template % {"occs": _values(batch)}
        try:
            rows = client.query(sparql, name=f"{name} [{len(batch)} occupations]", attempts=3)
        except FetchError:
            if len(batch) > 1:
                half = len(batch) // 2
                client.note(f"{name}: splitting {len(batch)} occupations into {half} + {len(batch) - half}")
                queue[:0] = [batch[:half], batch[half:]]
                continue
            client.note(f"{name}: paging {batch[0]} with LIMIT/OFFSET")
            rows = paged(client, sparql, name=f"{name} {batch[0]}")
        found.update(_qid(r["p"]["value"]) for r in rows)
    return found


def _date(row: dict[str, Any]) -> dict[str, Any] | None:
    if "time" not in row:
        return None
    raw = row["time"]["value"]
    return {"time": raw[:10] if not raw.startswith("-") else raw[:11], "precision": int(row["precision"]["value"])}


def fetch(data_dir: Path, client: Client | None = None, progress=print) -> dict[str, Any]:
    """Run every query and write the snapshot files. Needs the network; run it once."""
    client = client or Client()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(IST)
    incomplete: list[str] = []

    rows = client.query(Q_OCCUPATIONS % {"roots": _values(OCCUPATION_ROOTS)}, name="occupations")
    occupations = {_qid(r["occ"]["value"]): r.get("occLabel", {}).get("value", "") for r in rows}
    occ_list = sorted(occupations)
    progress(f"occupation closure: {len(occupations)} occupations")

    deceased = _people_by_occupation(client, Q_DECEASED_BY_OCCUPATION, occ_list, name="deceased by occupation")
    by_occupation = len(deceased)
    progress(f"deceased with a CS/software occupation: {by_occupation}")

    linked: set[str] = set()
    links: set[tuple[str, str, str]] = set()  # (person, software item, property)
    for prop in LINK_PROPS:
        if prop in LINK_PROPS_PER_PERSON_ONLY:
            continue
        try:
            rows = client.query(Q_DECEASED_BY_LINK % {"prop": prop}, name=f"deceased by link {prop}")
        except FetchError as exc:
            incomplete.append(f"deceased by link {prop}: {exc}")
            progress(f"link query {prop} failed: {exc}")
            continue
        for r in rows:
            linked.add(_qid(r["p"]["value"]))
            links.add((_qid(r["p"]["value"]), _qid(r["sw"]["value"]), prop))
    deceased |= linked
    progress(f"deceased linked to a software item: {len(linked)} (total now {len(deceased)})")

    people: dict[str, dict[str, Any]] = {
        q: {"qid": q, "label": None, "aliases": [], "human": False, "born": [], "died": [], "occupations": [], "software": []}
        for q in sorted(deceased)
    }
    qids = sorted(people)
    for r in batched(client, Q_NAMES, qids, 150, name="names"):
        person = people[_qid(r["p"]["value"])]
        name = r["name"]["value"]
        if r["kind"]["value"].endswith("label") and r["name"].get("xml:lang") == "en":
            person["label"] = name
        elif name not in person["aliases"]:
            person["aliases"].append(name)
    progress(f"names: {len(qids)} people")
    for r in batched(client, Q_FACTS, qids, 150, name="facts"):
        person = people[_qid(r["p"]["value"])]
        value = _qid(r["value"]["value"])
        if r["prop"]["value"].endswith("P31"):
            if value == "Q5":
                person["human"] = True
        else:
            entry = {"qid": value, "label": r.get("valueLabel", {}).get("value", "")}
            if entry not in person["occupations"]:
                person["occupations"].append(entry)
    progress("facts: done")
    for r in batched(client, Q_DATES, qids, 150, name="dates"):
        person = people[_qid(r["p"]["value"])]
        rank = r["rank"]["value"].rsplit("#", 1)[-1].replace("Rank", "").lower()
        if rank == "deprecated":
            continue
        date = _date(r)
        target = person["born"] if r["which"]["value"] == "born" else person["died"]
        item = {"time": date["time"], "precision": date["precision"], "rank": rank} if date else {"time": None, "precision": None, "rank": rank}
        if item not in target:
            target.append(item)
    progress("dates: done")
    for r in batched(client, Q_LINKS_IN, qids, 100, name="links in"):
        links.add((_qid(r["p"]["value"]), _qid(r["sw"]["value"]), _qid(r["prop"]["value"])))
    for r in batched(client, Q_LINKS_OUT, qids, 150, name="links out"):
        links.add((_qid(r["p"]["value"]), _qid(r["sw"]["value"]), "P800"))
    items = sorted({sw for _, sw, _ in links})
    labels = {_qid(r["sw"]["value"]): r["swLabel"]["value"] for r in batched(client, Q_LABELS, items, 300, name="link labels")}
    for person_qid, sw, prop in sorted(links):
        if person_qid in people and sw in labels:  # an item with no English label cannot be found in a receipt
            entry = {"qid": sw, "label": labels[sw], "via": prop}
            if entry not in people[person_qid]["software"]:
                people[person_qid]["software"].append(entry)
    progress(f"software links: {len(links)} links to {len(items)} items")

    for person in people.values():
        if not person["label"] and person["aliases"]:
            person["label"] = person["aliases"][0]
        person["aliases"] = sorted(a for a in person["aliases"] if a != person["label"])
        person["occupations"].sort(key=lambda o: o["qid"])
        person["software"].sort(key=lambda s: (s["via"], s["qid"]))
        person["born"].sort(key=lambda d: (d["time"] or "", d["precision"] or 0))
        person["died"].sort(key=lambda d: (d["time"] or "", d["precision"] or 0))

    living = _people_by_occupation(client, Q_NO_DEATH_BY_OCCUPATION, occ_list, name="no death date by occupation")
    living_by_occupation = len(living)
    for prop in LINK_PROPS:
        if prop in LINK_PROPS_PER_PERSON_ONLY:
            continue
        try:
            rows = client.query(Q_NO_DEATH_BY_LINK % {"prop": prop}, name=f"no death date by link {prop}", attempts=4)
        except FetchError as exc:
            incomplete.append(f"no death date by link {prop}: {exc}")
            progress(f"no-death-date link query {prop} failed: {exc}")
            continue
        living.update(_qid(r["p"]["value"]) for r in rows)
    living -= deceased
    progress(f"humans with those occupations or links and no death date: {len(living)}")
    hashes: set[str] = set()
    for r in batched(client, Q_NAMES, sorted(living), 400, name="no-death-date names"):
        key = name_key(r["name"]["value"])
        if len(key.split()) >= 2:
            hashes.add(key_hash(key))
    progress(f"no-death-date name hashes: {len(hashes)}")

    finished = datetime.now(IST)
    meta = {
        "source": "Wikidata, https://www.wikidata.org",
        "license": "CC0 1.0 (Wikidata structured data)",
        "endpoint": ENDPOINT,
        "user_agent": USER_AGENT,
        "retrieved_started": started.isoformat(timespec="seconds"),
        "retrieved_finished": finished.isoformat(timespec="seconds"),
        "occupation_roots": OCCUPATION_ROOTS,
        "occupations": dict(sorted(occupations.items())),
        "link_properties": LINK_PROPS,
        "link_properties_per_person_only": list(LINK_PROPS_PER_PERSON_ONLY),
        "counts": {
            "occupations": len(occupations),
            "deceased_by_occupation": by_occupation,
            "deceased_by_software_link": len(linked),
            "people": len(people),
            "people_human": sum(1 for p in people.values() if p["human"]),
            "no_death_date_by_occupation": living_by_occupation,
            "no_death_date_people": len(living),
            "no_death_date_name_hashes": len(hashes),
            "queries": client.queries,
            "rows_fetched": client.rows,
            "failed_attempts": client.failures,
        },
        "incomplete": incomplete,
        "queries": {
            "occupations": Q_OCCUPATIONS,
            "deceased_by_occupation": Q_DECEASED_BY_OCCUPATION,
            "deceased_by_link": Q_DECEASED_BY_LINK,
            "no_death_by_occupation": Q_NO_DEATH_BY_OCCUPATION,
            "no_death_by_link": Q_NO_DEATH_BY_LINK,
            "names": Q_NAMES,
            "facts": Q_FACTS,
            "dates": Q_DATES,
            "links_in": Q_LINKS_IN,
            "links_out": Q_LINKS_OUT,
            "link_labels": Q_LABELS,
        },
        "how_the_queries_were_run": (
            "%(roots)s, %(occs)s and %(qids)s are VALUES lists: the occupation roots, the occupation closure "
            "in chunks of 60, and person QIDs in batches (150 for names/facts/dates/links out, 100 for links in, "
            "300 for link labels, 400 for no-death-date names). %(prop)s is each link property except those in link_properties_per_person_only. "
            "Failing batches were split in half; a failing single occupation was paged with ORDER BY ?p LIMIT/OFFSET."
        ),
        "not_requested": "cause of death (P509), manner of death (P1196), place of death (P20) and every other property not listed in these queries",
    }
    snapshot = {"meta": meta, "people": [people[q] for q in qids]}
    (data_dir / "deceased.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    header = [
        "# names-in-the-machine: hashed name keys of Wikidata humans with a software/CS occupation or software link and no death date",
        f"# retrieved {finished.isoformat(timespec='seconds')} from {ENDPOINT} (CC0). sha256('nitm-v1:' + name_key)[:12]",
        "# Only hashes are stored, so this file is not a list of living people's names.",
    ]
    (data_dir / "no_death_date.txt").write_text("\n".join(header + sorted(hashes)) + "\n", encoding="utf-8")
    return meta


def describe(meta: dict[str, Any]) -> str:
    """data/README.md: where the snapshot came from, when, how big, and every query, verbatim."""
    counts = meta["counts"]
    lines = [
        "# The Wikidata snapshot",
        "",
        f"Source: {meta['source']}. License: {meta['license']}.",
        f"Retrieved from `{meta['endpoint']}` between {meta['retrieved_started']} and {meta['retrieved_finished']},",
        "with `nitm snapshot fetch`. The tool reads these files offline and never queries Wikidata at run time.",
        "",
        "- `deceased.json`: humans with a death date (P570) who have a software or computer-science occupation, or who",
        "  are linked as developer, creator, author, programmer or designer of a software item. For each: label,",
        "  aliases, birth and death dates with their precision and rank, occupations, and linked items.",
        "- `no_death_date.txt`: people with those occupations or links and no death date, stored only as short",
        "  hashes of their name keys, so this repository carries no list of living people's names.",
        "",
        "## Counts",
        "",
        "| | |",
        "|---|---|",
    ]
    lines += [f"| {key.replace('_', ' ')} | {value:,} |" for key, value in counts.items()]
    lines += [
        "",
        f"Not requested: {meta['not_requested']}.",
        "",
        f"Incomplete parts: {', '.join(meta.get('incomplete') or []) or 'none'}.",
        "",
        "## Queries",
        "",
        meta["how_the_queries_were_run"],
        "",
        f"Occupation roots: {', '.join(f'{q} ({label})' for q, label in meta['occupation_roots'].items())}.",
        f"Link properties: {', '.join(f'{p} ({label})' for p, label in meta['link_properties'].items())}.",
        "",
    ]
    for name, query in meta["queries"].items():
        lines += [f"### {name}", "", "```sparql", query, "```", ""]
    return "\n".join(lines)


@dataclass
class Snapshot:
    meta: dict[str, Any]
    people: dict[str, dict[str, Any]]
    index: dict[str, list[str]]
    no_death_hashes: set[str]

    @property
    def retrieved(self) -> str:
        return str(self.meta.get("retrieved_finished", ""))[:10]

    def lookup(self, key: str) -> list[dict[str, Any]]:
        return [self.people[q] for q in self.index.get(key, [])]

    def has_no_death_namesake(self, key: str) -> bool:
        return key_hash(key) in self.no_death_hashes


def build_index(people: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for qid, person in people.items():
        names = [person.get("label") or ""] + list(person.get("aliases") or [])
        for name in names:
            key = name_key(name)
            if len(key.split()) < 2:
                continue  # no single-word aliases: no surname-only or nickname matches
            bucket = index.setdefault(key, [])
            if qid not in bucket:
                bucket.append(qid)
    return index


def load(data_dir: Path) -> Snapshot:
    data_dir = Path(data_dir)
    raw = json.loads((data_dir / "deceased.json").read_text(encoding="utf-8"))
    people = {p["qid"]: p for p in raw["people"]}
    hashes: set[str] = set()
    hash_file = data_dir / "no_death_date.txt"
    if hash_file.exists():
        hashes = {l.strip() for l in hash_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
    return Snapshot(meta=raw["meta"], people=people, index=build_index(people), no_death_hashes=hashes)
