"""Sending a result card to the cards site (the Share dialog's destinations 1 and 2).

The cards site is a public repository with GitHub Pages (`CARDS_REPO`, `--cards-repo`).  Its Action reads one fenced
JSON block from an issue with the label "card" (cards/site/submit.py), checks the card, adds it, and answers the issue
with the card's address.  Two ways to open that issue:

  1. `gh issue create` on this PC, when gh is installed and logged in.  The Studio then reads the issue's comments
     for up to 3 minutes and shows the card's address from the Action's answer.
  2. A link that opens the issue form with the card in it (cards/site/.github/ISSUE_TEMPLATE/card.yml, field "card").
     The person opens it in the browser and clicks Create.  At most 8,000 characters.

Nothing is sent before the person selects the button in the dialog.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse

from cards.render import CARDS_REPO, gpu_short, site_url

FORM_TEMPLATE = "card.yml"
FORM_FIELD = "card"                                     # the id of the form's JSON field (card.yml)
LINK_MAX = 8000
GH_CACHE_S = 60
POLL_S = 180.0
POLL_EVERY_S = 5.0
CARD_URL_RE = re.compile(r"https://\S+?/c/[a-z2-7]{10}/")
ISSUE_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/(\d+)")


def title_of(card) -> str:
    return f"{gpu_short(card)} · {card['model']['name']}"


def issue_body(card) -> str:
    """The issue body that the Action reads: the form's heading and one fenced JSON block (submit.py's FENCE)."""
    return "### Card JSON\n\n```json\n" + json.dumps(card, indent=1, ensure_ascii=False) + "\n```\n"


def form_link(card, repo: str = CARDS_REPO) -> dict:
    """{"url", "length"} of the link that opens the filled issue form, or {"url": None, "reason"} when it would be
    longer than LINK_MAX."""
    base = f"https://github.com/{repo}/issues/new?"
    for text in (json.dumps(card, indent=1, ensure_ascii=False), json.dumps(card, separators=(",", ":"), ensure_ascii=False)):
        url = base + urllib.parse.urlencode({"template": FORM_TEMPLATE, "title": title_of(card), FORM_FIELD: text},
                                            quote_via=urllib.parse.quote)
        if len(url) <= LINK_MAX:
            return {"url": url, "length": len(url), "reason": None}
    return {"url": None, "length": len(url),
            "reason": f"The link would have {len(url):,} characters, more than {LINK_MAX:,}. Use the files on this PC, "
                      "and paste card.json into the issue form."}


def _run(cmd, timeout=30, stdin=None):
    """subprocess.run with text output; never a shell."""
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          input=stdin, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class GhShare:
    """Destination 1: gh on this PC.  `run(cmd, timeout)` is subprocess.run-like (tests replace it)."""

    def __init__(self, repo: str = CARDS_REPO, run=_run, clock=time.time, sleep=time.sleep, poll_s=POLL_S,
                 poll_every_s=POLL_EVERY_S):
        self.repo, self.run, self.clock, self.sleep = repo, run, clock, sleep
        self.poll_s, self.poll_every_s = poll_s, poll_every_s
        self.lock = threading.Lock()
        self._auth = (0.0, None)
        self.job = {"state": "idle"}

    # ------------------------------------------------------------------------------------- is gh there
    def status(self) -> dict:
        """{"ok": bool, "reason"}: gh installed and logged in, cached for 60 s."""
        at, doc = self._auth
        if doc is not None and self.clock() - at < GH_CACHE_S:
            return doc
        if self.run is _run and not shutil.which("gh"):
            doc = {"ok": False, "reason": "gh (the GitHub command line) is not installed on this PC."}
        else:
            try:
                r = self.run(["gh", "auth", "status", "--hostname", "github.com"], timeout=15)
                ok = r.returncode == 0
                doc = {"ok": ok, "reason": None if ok else "gh is not logged in on this PC. Run gh auth login in a terminal, then open this dialog again."}
            except (OSError, subprocess.SubprocessError):
                doc = {"ok": False, "reason": "gh (the GitHub command line) does not run on this PC."}
        self._auth = (self.clock(), doc)
        return doc

    # ------------------------------------------------------------------------------------- the share
    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.job)

    def start(self, card) -> dict:
        with self.lock:
            if self.job.get("state") in ("sending", "waiting"):
                return dict(self.job)
            self.job = {"state": "sending", "card_id": card["id"], "issue_url": None, "card_url": None,
                        "message": "Opening the issue ...", "started_at": self.clock()}
        threading.Thread(target=self._share, args=(card,), daemon=True, name="share-gh").start()
        return self.snapshot()

    def _set(self, **kw):
        with self.lock:
            self.job.update(kw)

    def _share(self, card):
        fd, path = tempfile.mkstemp(prefix="strata-card-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(issue_body(card))
            try:
                r = self.run(["gh", "issue", "create", "--repo", self.repo, "--label", "card", "--title", title_of(card),
                              "--body-file", path], timeout=60)
            except (OSError, subprocess.SubprocessError) as e:
                self._set(state="failed", message=f"gh did not run ({type(e).__name__}). Nothing was sent.")
                return
            m = ISSUE_URL_RE.search(r.stdout or "")
            if r.returncode != 0 or not m:
                why = ((r.stderr or r.stdout or "").strip().splitlines() or ["no answer"])[-1][:200]
                self._set(state="failed", message=f"gh could not open the issue: {why}")
                return
            issue_url, number = m.group(0), m.group(1)
            self._set(state="waiting", issue_url=issue_url,
                      message="The issue is open. The check of the cards site answers in it, in about one minute.")
            end = self.clock() + self.poll_s
            while self.clock() < end:
                self.sleep(self.poll_every_s)
                answer = self._answer(number)
                if answer:
                    url = CARD_URL_RE.search(answer)
                    if url:
                        self._set(state="done", card_url=url.group(0), message=answer.strip()[:400])
                    else:
                        self._set(state="refused", message=answer.strip()[:600])
                    return
            self._set(state="timeout", message="The check did not answer in 3 minutes. The card address comes in the "
                                               "issue.")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _answer(self, number: str) -> str | None:
        """The newest comment of the Action (github-actions[bot]) on the issue, or None."""
        try:
            r = self.run(["gh", "api", f"repos/{self.repo}/issues/{number}/comments"], timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        try:
            comments = json.loads(r.stdout or "[]")
        except ValueError:
            return None
        bot = [c for c in comments if isinstance(c, dict) and str((c.get("user") or {}).get("login", "")).startswith("github-actions")]
        return bot[-1].get("body") if bot else None


DEMO_OFF = "The demo shares no card: its data is the recorded data of one PC."


def targets(card, repo: str, gh: GhShare, off: str | None = None) -> dict:
    """What the dialog shows for destinations 1 and 2.  `off`: why neither can send (the demo)."""
    if off:
        return {"repo": repo, "site": site_url(repo), "title": title_of(card), "gh": {"ok": False, "reason": off},
                "link": {"url": None, "length": 0, "reason": off}}
    return {"repo": repo, "site": site_url(repo), "title": title_of(card), "gh": gh.status(),
            "link": form_link(card, repo)}
