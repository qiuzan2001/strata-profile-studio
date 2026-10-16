"""The cards repository's Action, for one issue: add a card, or remove one for the person who shared it.

    python3 submit.py --event "$GITHUB_EVENT_PATH" [--data data] [--url https://<owner>.github.io/strata-cards]

An issue with the label "card": the first fenced JSON block of the issue is the card.  It must pass validate.py.
At most LIMIT cards per GitHub login in 24 hours.  The same data gives the same id, so a card that is already on
the site is not added again.  Duplicates of the same PC, settings and day are merged by build.py.

An issue with the label "remove": the first card id in the issue is removed when the issue's author shared it.
Anybody else can open a pull request that deletes data/<id>.json.

The login is not written into a card.  data/submitters.json keeps a hash of it with the time and the card id, for
the limit and for removals.  The outcome goes to $GITHUB_OUTPUT (changed=true|false) and message.txt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import validate                                        # noqa: E402

LIMIT = 10
DAY = 24 * 3600
FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", re.S)
ID_RE = re.compile(r"\b[a-z2-7]{10}\b")


def who(login: str) -> str:
    return hashlib.sha256(("strata-cards:" + login.lower()).encode("utf-8")).hexdigest()[:16]


def card_from(body: str):
    m = FENCE.search(body or "")
    text = m.group(1) if m else (body or "").strip()
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, f"The issue has no card JSON in a fenced block (```json ... ```), or the JSON is not valid ({e.msg})."


def handle(event: dict, data: Path, url: str, now: float | None = None) -> tuple[bool, str]:
    """(changed, message) for one issue event."""
    now = now or time.time()
    issue = event.get("issue") or {}
    labels = {(lab or {}).get("name") for lab in issue.get("labels") or []}
    login = ((issue.get("user") or {}).get("login")) or ""
    body = issue.get("body") or ""
    ledger_path = data / "submitters.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    me = who(login)
    base = url.rstrip("/") + "/"
    if "remove" in labels:
        m = ID_RE.search(body)
        if not m:
            return False, "The issue names no card id (10 letters and digits). Nothing was removed."
        cid = m.group(0)
        mine = [e for e in ledger.get(me, []) if e[1] == cid]
        if not mine:
            return False, (f"Only the person who shared card {cid} can remove it with an issue. Anybody can open a "
                           f"pull request that deletes data/{cid}.json.")
        f = data / f"{cid}.json"
        if f.exists():
            f.unlink()
        ledger[me] = [e for e in ledger[me] if e[1] != cid]
        ledger_path.write_text(json.dumps(ledger, indent=1) + "\n", encoding="utf-8")
        return True, f"Card {cid} is removed. The site updates in about one minute."
    if "card" not in labels:
        return False, "The issue has no label \"card\" or \"remove\". Nothing was done."
    card, err = card_from(body)
    if err:
        return False, err + " Nothing was saved."
    errs = validate.validate(card)
    if errs:
        return False, "The card is not valid. Nothing was saved.\n\n" + "\n".join(f"- {e}" for e in errs[:8])
    cid = card["id"]
    if (data / f"{cid}.json").exists():
        return False, f"This card is on the site already: {base}c/{cid}/"
    recent = [e for e in ledger.get(me, []) if now - e[0] < DAY]
    if len(recent) >= LIMIT:
        return False, f"The limit is {LIMIT} cards per person in 24 hours. Open the issue again tomorrow. Nothing was saved."
    data.mkdir(parents=True, exist_ok=True)
    (data / f"{cid}.json").write_text(json.dumps(card, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger[me] = recent + [[int(now), cid]]
    ledger_path.write_text(json.dumps(ledger, indent=1) + "\n", encoding="utf-8")
    return True, f"Thank you. The card: {base}c/{cid}/ (the site updates in about one minute). Check: schema and ranges."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add or remove a card for one issue event.")
    ap.add_argument("--event", required=True)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--url", default="https://qiuzan2001.github.io/strata-cards")
    ap.add_argument("--message", default="message.txt")
    a = ap.parse_args(argv)
    event = json.loads(Path(a.event).read_text(encoding="utf-8"))
    changed, message = handle(event, Path(a.data), a.url)
    Path(a.message).write_text(message + "\n", encoding="utf-8")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    print(("changed: " if changed else "unchanged: ") + message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
