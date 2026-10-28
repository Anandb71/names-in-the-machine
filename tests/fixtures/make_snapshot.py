"""Cut tests/fixtures/snapshot from the real snapshot in data/. Run by hand; the tests only read the result.

    python tests/fixtures/make_snapshot.py
"""

from __future__ import annotations

import json
from pathlib import Path

from nitm.text import key_hash, name_key

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "snapshot"

# Ian Murdock, Phil Katz, Fredrik Lundh, Aaron Swartz, Thomas Lord: real records from data/deceased.json.
KEEP = ["Q92761", "Q550635", "Q110051708", "Q302817", "Q133867446"]

# Rory Smythe (Q990000001) is an invented person, not in the real snapshot: a basketball coach with no
# software occupation and no link to software. He stands in for a real homonym, to show that a matching
# name alone is never enough. The qid is made up.
RORY_SMYTHE = {
    "qid": "Q990000001",
    "label": "Rory Smythe",
    "aliases": [],
    "human": True,
    "born": [{"time": "1948-12-12", "precision": 11, "rank": "normal"}],
    "died": [{"time": "2009-06-04", "precision": 11, "rank": "normal"}],
    "occupations": [{"qid": "Q3665646", "label": "basketball player"}, {"qid": "Q5137571", "label": "basketball coach"}],
    "software": [],
}

# Tom Vane (Q990000002) is invented too: alias "Thomas G. Vane", no death date, so he is never listed.
# Only the hashes of the name keys are stored, as in the real data/no_death_date.txt.
NO_DEATH_DATE_NAMES = ["Tom Vane", "Thomas G. Vane"]


def main() -> None:
    raw = json.loads((ROOT / "data" / "deceased.json").read_text(encoding="utf-8"))
    people = {p["qid"]: p for p in raw["people"]}
    meta = dict(raw["meta"])
    meta["fixture"] = "cut from data/deceased.json by tests/fixtures/make_snapshot.py; Rory Smythe added as a homonym"
    rows = [people[q] for q in KEEP] + [RORY_SMYTHE]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "deceased.json").write_text(json.dumps({"meta": meta, "people": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    hashes = sorted({key_hash(name_key(n)) for n in NO_DEATH_DATE_NAMES})
    (OUT / "no_death_date.txt").write_text("# fixture: hashed name keys only\n" + "\n".join(hashes) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} people, {len(hashes)} hashes")


if __name__ == "__main__":
    main()
