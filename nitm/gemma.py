"""Gemma 3 1B proposes person names from one free-text credit line at a time.

It is a proposer, nothing more. It never writes card text, dates or anything about a
person. Every proposal goes through nitm.validate before it counts.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nitm.text import text_hash

Transport = Callable[[dict[str, Any]], dict[str, Any]]

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:1b"
DEFAULT_TIMEOUT = 300
PROMPT_VERSION = "names-v1"
NUM_PREDICT = 48

SYSTEM = (
    "You read one line from a software copyright or credits file and list the people it names. "
    "Copy each person's name exactly as written in the line. Separate names with semicolons. "
    "Companies, projects, universities and foundations are not people. "
    "If the line names no person, reply NONE."
)

# Two shots. The example names are invented and appear in no file, so an echoed
# example can never pass the exact-substring rule.
SHOTS = (
    ("The JPEG decoder was written by Anna Kowalski and Rui Duarte.", "Anna Kowalski; Rui Duarte"),
    ("This product includes software developed by the Example Project.", "NONE"),
)


def build_prompt(line: str) -> str:
    parts = []
    for text, answer in SHOTS:
        parts.append(f"Line: {text}\nPeople: {answer}")
    parts.append(f"Line: {line}\nPeople:")
    return "\n\n".join(parts)


def parse_names(raw: str) -> list[str]:
    """Turn Gemma's reply into a list of proposed names. Says nothing about whether they are right."""
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    text = re.sub(r"^\s*(?:people|names?)\s*:\s*", "", text, flags=re.I).strip()
    if not text or re.fullmatch(r"[\W_]*none[\W_]*", text, re.I):
        return []
    pieces = re.split(r"\s*;\s*", text) if ";" in text else re.split(r"\s*,\s*|\s+and\s+", text)
    names = []
    for piece in pieces:
        piece = piece.strip().strip("\"'`*.").strip()
        piece = re.sub(r"^(?:and|&)\s+", "", piece)
        if piece and piece.upper() != "NONE":
            names.append(piece)
    return names


@dataclass
class Proposal:
    names: list[str]
    raw: str
    seconds: float | None  # wall time of a fresh call; None when served from cache
    cached: bool
    error: str | None = None


@dataclass
class Extractor:
    model: str = DEFAULT_MODEL
    host: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", DEFAULT_HOST))
    cache_path: Path | None = None
    timeout: int = DEFAULT_TIMEOUT
    transport: Transport | None = None
    calls: int = 0
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cache_path and Path(self.cache_path).exists():
            for row in Path(self.cache_path).read_text(encoding="utf-8").splitlines():
                if row.strip():
                    entry = json.loads(row)
                    self._cache[entry["key"]] = entry

    def cache_key(self, line: str) -> str:
        return text_hash(f"{self.model}|{PROMPT_VERSION}|{line}")

    def payload(self, line: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "system": SYSTEM,
            "prompt": build_prompt(line),
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "seed": 0, "num_predict": NUM_PREDICT, "stop": ["\n"]},
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(payload)
        url = self.host.rstrip("/") + "/api/generate"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def propose(self, line: str) -> Proposal:
        key = self.cache_key(line)
        if key in self._cache:
            entry = self._cache[key]
            return Proposal(names=parse_names(entry["raw"]), raw=entry["raw"], seconds=None, cached=True)
        started = time.monotonic()
        try:
            body = self._post(self.payload(line))
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            seconds = time.monotonic() - started
            return Proposal(names=[], raw="", seconds=seconds, cached=False,
                            error=f"{type(exc).__name__} after {seconds:.0f}s: {exc}")
        seconds = time.monotonic() - started
        self.calls += 1
        raw = str(body.get("response", ""))
        entry = {
            "key": key,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "line": line,
            "raw": raw,
            "seconds": round(seconds, 3),
            "eval_count": body.get("eval_count"),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._cache[key] = entry
        if self.cache_path:
            Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return Proposal(names=parse_names(raw), raw=raw, seconds=seconds, cached=False)
