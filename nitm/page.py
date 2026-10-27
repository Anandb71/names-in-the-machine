"""The ofrenda: one static HTML file, built only from verified.json.

Every word on a card comes from a template, a verified field (name, years, Wikidata id,
reviewer, date), a receipt line quoted exactly from a file on the machine (emails removed),
or a measurement with the command that produced it. No model writes anything here.

Living people never appear by name: the page shows them only as counts.
"""

from __future__ import annotations

import html
import re
from typing import Any

from nitm.review import publish_problems, publishable
from nitm.text import EMAIL_RE, SPELLED_EMAIL_RE, strip_emails

REPO_URL = "https://github.com/Anandb71/names-in-the-machine"
ISSUES_URL = REPO_URL + "/issues"

FOUND_BY = {
    "copyright": "the Copyright-line regex",
    "field": "the author-field regex",
    "byline": "the free-text regex",
    "gemma": "Gemma 3 1B (free text, held to the validator)",
}


def _found_line(methods: list[str]) -> str:
    labels = [FOUND_BY[m] for m in FOUND_BY if m in methods]
    if not labels:
        return "the scan"
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]

# Words about how or why someone died. None of them may appear anywhere on the page.
DEATH_WORDS = (
    "suicide", "suicidal", "killed", "kill", "murder", "murdered", "overdose", "cancer", "illness", "ill",
    "disease", "sickness", "accident", "accidental", "crash", "hanged", "hanging", "shot", "shooting",
    "drowned", "drowning", "tragic", "tragically", "tragedy", "depression", "depressed", "heart attack",
    "stroke", "pancreatitis", "alcohol", "alcoholism", "drug", "drugs", "prosecution", "prosecuted",
    "indictment", "indicted", "arrest", "arrested", "police", "custody", "autopsy", "coroner", "cause of death",
    "manner of death", "passed away suddenly", "untimely", "too soon", "lost his life", "lost her life",
    "took his own life", "took her own life", "struggle", "battle with",
)
DEATH_WORD_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in DEATH_WORDS) + r")\b", re.I)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def visible_text(page: str) -> str:
    """The words a reader sees: tags, styles and attribute values stripped."""
    text = re.sub(r"<style\b.*?</style>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def check(page: str) -> list[str]:
    """Problems that stop a page from being written. Empty means it may be written."""
    problems = []
    if "@" in page or EMAIL_RE.search(page) or SPELLED_EMAIL_RE.search(page):
        problems.append("the page contains an email address (or an @ of any kind)")
    hit = DEATH_WORD_RE.search(visible_text(page))
    if hit:
        problems.append(f"the page contains a word about how someone died: {hit.group(0)!r}")
    return problems


def _receipt_html(receipt: dict[str, Any]) -> str:
    text = strip_emails(receipt["text"])
    return (
        f'<figure class="receipt"><figcaption>{_e(receipt["file"])}, line {_e(receipt["line"])}</figcaption>'
        f"<pre><code>{_e(text)}</code></pre></figure>"
    )


def _still_html(still: dict[str, Any] | None) -> str:
    if not still or still.get("value") is None or not still.get("sentence"):
        return ""
    return (
        '<div class="still"><p class="still-label">Still working here</p>'
        f'<p>{_e(still["sentence"])}</p>'
        f'<p class="cmd"><code>{_e(still["command"])}</code><br>measured {_e(still["measured_at"][:16].replace("T", " "))} IST</p></div>'
    )


def _more_item(r: dict[str, Any]) -> str:
    where = f'<span class="where">{_e(r["file"])}:{_e(r["line"])}</span>'
    if not r.get("text"):
        n = int(r.get("others") or 0)
        return f'<li>{where}<span class="also"> also names {n} other {"person" if n == 1 else "people"}, so it is not quoted here</span></li>'
    return f'<li>{where}<code>{_e(strip_emails(r["text"]))}</code></li>'


def card(entry: dict[str, Any]) -> str:
    receipts = entry["receipts"]
    files = sorted({r["file"] for r in receipts})
    index = int(entry.get("primary_receipt") or 0)
    primary = receipts[index] if 0 <= index < len(receipts) and receipts[index].get("text") else next(r for r in receipts if r.get("text"))
    rest = [r for r in receipts if r is not primary]
    places = f"{len(files)} file" + ("s" if len(files) != 1 else "")
    more = ""
    if rest:
        items = "".join(_more_item(r) for r in rest)
        more = f'<details><summary>{len(rest)} more line{"s" if len(rest) != 1 else ""} on this machine</summary><ul>{items}</ul></details>'
    found_line = _found_line(entry.get("found_by", []))
    return f"""
<article class="card" id="{_e(entry['qid'])}">
  <div class="candle" aria-hidden="true"><span class="flame"></span><span class="wax"></span></div>
  <h2>{_e(entry['name'])}</h2>
  <p class="years">{_e(entry['years'])}</p>
  <p class="named">Named in {places} on this machine.</p>
  {_receipt_html(primary)}
  {more}
  {_still_html(entry.get('still'))}
  <p class="source">Years from Wikidata {_e(entry['qid'])} (snapshot {_e(entry.get('snapshot', ''))}). Found by {_e(found_line)}. Checked by hand against a second source by {_e(entry['reviewer'])} on {_e(entry['date'])}.</p>
</article>"""


STYLE = """
:root { --night:#15100c; --card:#221911; --line:#4a3417; --marigold:#f4a300; --marigold-deep:#d9730d;
  --candle:#ffe3a3; --text:#f3e6cf; --muted:#b8a487; --code:#0f0b08; }
* { box-sizing: border-box; }
body { margin:0; background: radial-gradient(ellipse at top, #2a1b10 0%, var(--night) 60%); color: var(--text);
  font: 17px/1.55 Georgia, "Iowan Old Style", "Palatino Linotype", serif; }
header, main, footer { max-width: 760px; margin: 0 auto; padding: 0 20px; }
header { padding-top: 56px; text-align: center; }
header h1 { color: var(--marigold); font-weight: normal; letter-spacing: .04em; margin: 0 0 8px; font-size: 2.2em; }
header p { color: var(--muted); margin: 6px 0; }
.garland { color: var(--marigold-deep); letter-spacing: .5em; margin: 22px 0 6px; }
.card { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 0 18px; background: var(--card);
  border: 1px solid var(--line); border-top: 3px solid var(--marigold); border-radius: 6px; padding: 24px 24px 18px 18px;
  margin: 34px 0; box-shadow: 0 0 40px rgba(244,163,0,.06); }
.card > * { grid-column: 2; }
.card > .candle { grid-column: 1; grid-row: 1 / span 3; }
.card h2 { margin: 0; color: var(--candle); font-weight: normal; font-size: 1.6em; }
.years { margin: 2px 0 14px; color: var(--marigold); letter-spacing: .05em; }
.named { margin: 0 0 10px; }
.receipt { margin: 0 0 12px; }
.receipt figcaption, .where { color: var(--muted); font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; }
pre, code { font: 14px/1.45 ui-monospace, Menlo, Consolas, monospace; }
pre { background: var(--code); border-left: 3px solid var(--marigold-deep); padding: 10px 12px; margin: 4px 0 0;
  white-space: pre-wrap; word-break: break-word; color: var(--candle); }
details { margin: 0 0 12px; color: var(--muted); }
details ul { list-style: none; padding: 0; margin: 8px 0 0; }
details li { margin: 0 0 8px; }
details li code { display: block; color: var(--text); white-space: pre-wrap; word-break: break-word; }
details li .also { display: block; font-size: .85em; }
.still { border-top: 1px dashed var(--line); padding-top: 10px; margin-top: 6px; }
.still p { margin: 2px 0; }
.still-label { color: var(--marigold); font-size: .85em; letter-spacing: .08em; text-transform: uppercase; }
.cmd { color: var(--muted); font-size: .85em; }
.cmd code { white-space: pre-wrap; word-break: break-all; }
.source { color: var(--muted); font-size: .85em; margin: 12px 0 0; }
.candle { position: relative; width: 34px; height: 70px; margin-top: 4px; }
.wax { position: absolute; bottom: 0; left: 9px; width: 16px; height: 42px; border-radius: 3px;
  background: linear-gradient(90deg, #e9d3a6, #fff1cf 45%, #d8bd8a); }
.flame { position: absolute; top: 2px; left: 10px; width: 14px; height: 24px; border-radius: 50% 50% 45% 45% / 60% 60% 40% 40%;
  background: radial-gradient(ellipse at 50% 70%, #fff6d5 0%, #ffd36b 35%, #f4a300 60%, rgba(244,163,0,0) 75%);
  box-shadow: 0 0 18px 6px rgba(244,163,0,.25); }
.left-off { border: 1px solid var(--line); border-radius: 6px; padding: 18px 22px; margin: 40px 0; color: var(--muted); }
.left-off h2 { color: var(--text); font-weight: normal; font-size: 1.2em; margin: 0 0 8px; }
.left-off ul { margin: 0; padding-left: 20px; }
footer { color: var(--muted); font-size: .9em; padding-bottom: 60px; }
footer a, .left-off a { color: var(--marigold); }
"""


def _left_off(summary: dict[str, Any] | None, review_counts: dict[str, int] | None) -> str:
    items = []
    match = (summary or {}).get("match") or {}
    no_death = int(match.get("names_matching_only_no_death_date_entries", 0))
    gate = match.get("names_removed_by_gate_by_reason", {})
    no_death += sum(v for k, v in gate.items() if k == "no_death_date")
    if no_death:
        items.append(f"{no_death:,} name{'s' if no_death != 1 else ''} matched someone on Wikidata with no death date recorded. Left off, not guessed.")
    unverifiable = sum(v for k, v in gate.items() if k != "no_death_date")
    if unverifiable:
        items.append(
            f"{unverifiable:,} name{'s' if unverifiable != 1 else ''} matched a person who has died, but with no software "
            "occupation and no link to the software in the receipt, so the match could not be checked. Left off."
        )
    rejected = int((review_counts or {}).get("rejected", 0))
    if rejected:
        items.append(f"{rejected:,} match{'es' if rejected != 1 else ''} turned down at review: the same name, but not the same person, or not sure.")
    names = int(((summary or {}).get("names") or {}).get("unique_names_after_validator", 0))
    if names:
        items.append(f"Everyone else stays a number: {names:,} name-shaped strings passed the validator, and living people are never named here.")
    if not items:
        return ""
    return '<section class="left-off"><h2>Left off</h2><ul>' + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul></section>"


def render(
    verified: dict[str, Any],
    summary: dict[str, Any] | None = None,
    review_counts: dict[str, int] | None = None,
    snapshot_meta: dict[str, Any] | None = None,
) -> str:
    """The whole page. Only entries that pass the publish gate are rendered."""
    entries = publishable(verified)
    files = (summary or {}).get("files") or {}
    scanned = ""
    if files:
        scanned = (
            f"Read {files.get('copyright_files', 0):,} Debian copyright files and "
            f"{files.get('metadata_files', 0):,} Python package records on this machine."
        )
    snap = ""
    if snapshot_meta:
        snap = f"Years come from a Wikidata snapshot taken {_e(str(snapshot_meta.get('retrieved_finished', ''))[:10])} (CC0)."
    cards = "".join(card(e) for e in entries)
    if not cards:
        cards = '<p class="named">No verified entries yet.</p>'
    count = len(entries)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Names in the Machine</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <p class="garland" aria-hidden="true">✿ ✿ ✿ ✿ ✿</p>
  <h1>Names in the Machine</h1>
  <p>An ofrenda for {count} {'person' if count == 1 else 'people'} who have died and whose names still ship inside this computer.</p>
  <p>Their names are here because open-source licenses keep the copyright notice with the code.</p>
  <p>{_e(scanned)}</p>
</header>
<main>
{cards}
{_left_off(summary, review_counts)}
</main>
<footer>
  <p>Every line quoted above is copied exactly from a file on this machine, with email addresses and other people's names removed. {snap} No model wrote any of the text on this page.</p>
  <p><strong>Corrections:</strong> if a card here is wrong, or a family asks, it comes down at once. Open an issue at <a href="{ISSUES_URL}">{_e(ISSUES_URL)}</a>.</p>
  <p>Made with <a href="{REPO_URL}">names-in-the-machine</a>.</p>
</footer>
</body>
</html>
"""


def skipped(verified: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Entries in verified.json that the publish gate keeps off the page, with the reasons."""
    return [(str(e.get("name") or e.get("qid")), publish_problems(e)) for e in verified.get("people", []) if publish_problems(e)]
