"""Workload: what the requests that this PC served look like.

The engine log has one summary line per request (`strata serve: prompt N tokens = R reused + X read in A ms ...`).
This module reads the tail of that log and adds up the token counts and the times. It reads no prompt text, writes
nothing and needs no GPU.

Two parts:
  * "my traffic": the counts of the last requests (prompt length, the reused share, the reply length, the share of
    time in the prompt read and in the reply, the draft acceptance);
  * the levers: ideas for this traffic.

The levers are ideas for the user, not measurements. Each one says what it is based on.
"""
from __future__ import annotations

import math
import os
import statistics

from . import timinglog

TAIL_BYTES = 8 * 1024 * 1024
DEFAULT_LAST = 100
MAX_LAST = 1000

def tail_lines(path: str, tail: int = TAIL_BYTES) -> list[str]:
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        pos = max(0, size - tail)
        f.seek(pos)
        data = f.read(tail)
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if pos > 0 and lines else lines          # the tail starts inside a line


def requests_of(lines) -> list[dict]:
    """The summary line of each request, oldest first."""
    out = []
    for line in lines:
        s = timinglog.parse_summary(line)
        if s:
            out.append(s)
    return out


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def summarize(reqs: list[dict]) -> dict | None:
    """The counts of a list of requests, or None for no request."""
    if not reqs:
        return None
    prompt = [r["prompt_tokens"] for r in reqs]
    gen = [r["generated"] for r in reqs]
    read = sum(r["read"] for r in reqs)
    reused = sum(r["reused"] for r in reqs)
    total_prompt = sum(prompt)
    pre_ms = sum(r["prompt_ms"] for r in reqs)
    dec_ms = sum(r["decode_ms"] for r in reqs)
    dec_tok = sum(r["generated"] for r in reqs if r["decode_ms"] > 0)
    offered = sum(r["drafts_offered"] for r in reqs)
    accepted = sum(r["drafts_accepted"] for r in reqs)
    both = pre_ms + dec_ms
    return {
        "requests": len(reqs),
        "prompt_tokens": {"median": statistics.median(prompt), "p90": _quantile(prompt, 0.9)},
        "read_tokens": {"median": statistics.median(r["read"] for r in reqs)},
        "reused": reused / total_prompt if total_prompt else None,
        "generated": {"median": statistics.median(gen), "p90": _quantile(gen, 0.9)},
        "prompt_tok_s": read / pre_ms * 1000.0 if pre_ms > 0 and read > 0 else None,
        "decode_tok_s": dec_tok / dec_ms * 1000.0 if dec_ms > 0 and dec_tok > 0 else None,
        "prompt_s": pre_ms / 1000.0,
        "decode_s": dec_ms / 1000.0,
        "prompt_share": pre_ms / both if both > 0 else None,
        "drafts_accepted": accepted / offered if offered else None,
    }


def levers(mine: dict | None) -> list[dict]:
    """Ideas for this traffic. `basis` says where each one comes from. None of them is a measured gain."""
    if not mine:
        return []
    out = []
    share = mine["prompt_share"]
    if share is not None and share < 0.5:
        out.append({"kind": "changes output", "basis": "your log: the reply takes most of the time",
                    "text": "Shorten the thinking. A thinking budget cuts the reply length. It can change the answers, so check the results."})
        acc = mine["drafts_accepted"]
        if acc is not None:
            out.append({"kind": "same output", "basis": f"your log: {round(acc * 100)}% of the drafts were accepted",
                        "text": "Test the draft settings (--suffix-draft, --lookup-chain). They do not change the output. "
                                "A workload that copies text may gain."})
    elif share is not None:
        out.append({"kind": "same output", "basis": "your log: the prompt read takes most of the time",
                    "text": "Test the prompt chunk (--prefill). It does not change the output."})
    if mine["reused"] is not None and mine["reused"] < 0.5:
        out.append({"kind": "client", "basis": f"your log: {round(mine['reused'] * 100)}% of each prompt is reused",
                    "text": "Put the text that does not change at the start of the prompt. A longer shared start is read only once."})
    if mine["generated"]["median"] and mine["generated"]["median"] >= 500:
        out.append({"kind": "client", "basis": f"your log: the median reply has {round(mine['generated']['median'])} tokens",
                    "text": "Ask for shorter replies where the task allows it."})
    return out


def document(path: str | None, last: int = DEFAULT_LAST, min_prompt: int = 0) -> dict:
    """The answer of /api/workload."""
    last = max(1, min(int(last), MAX_LAST))
    min_prompt = max(0, int(min_prompt))
    out = {"path": path, "readable": False, "found": 0, "last": last, "min_prompt": min_prompt, "mine": None,
           "levers": [], "note": None}
    if not path:
        out["note"] = "The Studio does not know the engine log. Start it with --engine-log or --config."
        return out
    try:
        lines = tail_lines(path)
    except OSError as e:
        out["note"] = f"The engine log cannot be read: {e.strerror or e}."
        return out
    out["readable"] = True
    allreq = requests_of(lines)
    out["found"] = len(allreq)
    reqs = [r for r in allreq if r["prompt_tokens"] >= min_prompt][-last:]
    mine = summarize(reqs)
    out["mine"] = mine
    if mine is None:
        out["note"] = "The engine log has no request that matches."
    out["levers"] = levers(mine)
    return out
