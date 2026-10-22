"""Workload: what the requests that this PC served look like, and which scenario they are closest to.

The engine log has one summary line per request (`strata serve: prompt N tokens = R reused + X read in A ms ...`).
This module reads the tail of that log and adds up the token counts and the times. It reads no prompt text, writes
nothing and needs no GPU.

Two parts:
  * "my traffic": the counts of the last requests (prompt length, the reused share, the reply length, the share of
    time in the prompt read and in the reply, the draft acceptance);
  * the scenario presets: a typical shape for each of five uses. The shapes are assumed, not measured. The page says
    so. The time split of a preset is an estimate from the speeds that this PC measured in its own log.

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

# Typical shapes. Assumed, not measured: `prompt_tokens` and `generated` are the typical size of one request, and
# `reused` is the share of the prompt that an earlier request already read (the prefix cache).
PRESETS = (
    {"id": "chat", "name": "Short chat", "prompt_tokens": 600, "reused": 0.6, "generated": 300,
     "about": "Short questions in one conversation. Each request adds a few lines."},
    {"id": "document", "name": "Long document", "prompt_tokens": 30000, "reused": 0.0, "generated": 500,
     "about": "One long text, read once, with a short answer."},
    {"id": "browser", "name": "Browser agent", "prompt_tokens": 20000, "reused": 0.4, "generated": 800,
     "about": "Many steps. Each step has a new page state and a long reply with thinking."},
    {"id": "coding", "name": "Coding agent", "prompt_tokens": 40000, "reused": 0.8, "generated": 300,
     "about": "A long context that grows, with edits that copy earlier text."},
    {"id": "extract", "name": "Structured extraction", "prompt_tokens": 3000, "reused": 0.3, "generated": 200,
     "about": "A fixed form of output (JSON). Many short, independent requests."},
)


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


def estimate_share(shape: dict, prompt_tok_s: float | None, decode_tok_s: float | None) -> float | None:
    """The share of one request's time in the prompt read, for a shape, from this PC's own measured speeds."""
    if not prompt_tok_s or not decode_tok_s:
        return None
    pre = shape["prompt_tokens"] * (1.0 - shape["reused"]) / prompt_tok_s
    dec = shape["generated"] / decode_tok_s
    return pre / (pre + dec) if pre + dec > 0 else None


def distance(mine: dict, preset: dict) -> float:
    """How far the traffic is from a preset: the sizes on a log scale, the reused share on a plain scale."""
    p = abs(math.log(max(mine["prompt_tokens"]["median"], 1.0) / preset["prompt_tokens"]))
    g = abs(math.log((1.0 + mine["generated"]["median"]) / (1.0 + preset["generated"])))
    r = abs((mine["reused"] or 0.0) - preset["reused"])
    return p + g + 2.0 * r


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
           "presets": [], "levers": [], "closest": None, "note": None,
           "preset_note": "The shapes of the presets are assumed. They are not measured."}
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
    for p in PRESETS:
        item = {**p, "estimated_prompt_share": None, "distance": None}
        if mine:
            item["estimated_prompt_share"] = estimate_share(p, mine["prompt_tok_s"], mine["decode_tok_s"])
            item["distance"] = distance(mine, p)
        out["presets"].append(item)
    if mine:
        out["closest"] = min(out["presets"], key=lambda x: x["distance"])["id"]
    out["levers"] = levers(mine)
    return out
