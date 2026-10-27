"""One thing each person's named work still does on this machine, measured locally.

Every measurement runs the exact shell command that the card prints next to the number,
so anyone can paste it and check.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from nitm.sources import SELF_TESTS

IST = timezone(timedelta(hours=5, minutes=30))
Runner = Callable[[str], str]

DEP_TYPES = "--no-suggests --no-recommends --no-conflicts --no-breaks --no-replaces --no-enhances"


@dataclass
class Measurement:
    probe: str
    arg: str
    value: int | None
    sentence: str
    command: str
    measured_at: str
    error: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def shell(command: str) -> str:
    proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=300)
    if proc.returncode not in (0, 1):  # grep -c exits 1 on zero matches
        raise RuntimeError(proc.stderr.strip() or f"exit {proc.returncode}")
    return proc.stdout


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="minutes")


def _int(output: str) -> int:
    match = re.search(r"\d+", output)
    if not match:
        raise ValueError(f"no number in output: {output[:80]!r}")
    return int(match.group(0))


def rdepends_command(package: str) -> str:
    q = shlex.quote(package)
    return (
        f"apt-cache rdepends --installed {DEP_TYPES} {q} "
        f"| tail -n +3 | tr -d ' |' | sort -u | grep -vx {q} | wc -l"
    )


def dpkg_log_command(path: str = "/var/log/dpkg.log") -> str:
    return f"grep -cE '^\\S+ \\S+ (install|upgrade|remove|purge) ' {shlex.quote(path)}"


def importers_command(module: str, root: str) -> str:
    return (
        f"grep -rlE --include='*.py' '^\\s*(import|from) {re.escape(module)}([ .,]|$)' {shlex.quote(root)} | wc -l"
    )


def dist_count_command(name: str, roots: list[str]) -> str:
    joined = " ".join(shlex.quote(r) for r in roots)
    pattern = shlex.quote(f"Name: {name}")
    own = shlex.quote(SELF_TESTS + "/*")  # this tool's test fixtures are not installs
    return (
        f"find {joined} -path '*.dist-info/METADATA' -not -path '*/node_modules/*' -not -path '*/.cache/*' "
        f"-not -path {own} 2>/dev/null | xargs -r grep -lix {pattern} | wc -l"
    )


def measure(probe: str, arg: str, *, run: Runner = shell, pip_roots: list[str] | None = None) -> Measurement:
    if probe == "rdepends":
        command = rdepends_command(arg)
        template = "{value} installed packages on this machine depend on {arg}."
        one = "1 installed package on this machine depends on {arg}."
    elif probe == "dpkg-log":
        path = arg or "/var/log/dpkg.log"
        command = dpkg_log_command(path)
        template = path + " on this machine records {value} package installs, upgrades and removals."
        one = path + " on this machine records 1 package install, upgrade or removal."
    elif probe == "importers":
        module, _, root = arg.partition("@")
        root = root or "/usr/lib/python3"
        command = importers_command(module, root)
        template = "{value} Python files under " + root + " import " + module + "."
        one = "1 Python file under " + root + " imports " + module + "."
    elif probe == "dist-count":
        command = dist_count_command(arg, pip_roots or ["/usr/lib/python3"])
        template = "{value} Python environments read by this scan have {arg} installed."
        one = "1 Python environment read by this scan has {arg} installed."
    else:
        raise ValueError(f"unknown probe {probe!r}")
    try:
        value = _int(run(command))
        sentence = (one if value == 1 else template).format(value=f"{value:,}", arg=arg)
        return Measurement(probe, arg, value, sentence, command, _now())
    except Exception as exc:  # a failed probe is shown as "not measured", never guessed
        return Measurement(probe, arg, None, "", command, _now(), error=str(exc))
