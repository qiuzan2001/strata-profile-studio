"""Replay the shape of the user's requests against Strata, one arm (one set of per-request settings) at a time.

The shape is what the engine log holds: the tokens read, the tokens generated. The replay builds a prompt of that
many tokens from plain filler text and asks the model to copy a passage, so the reply has the length of the logged
reply. The text is not the user's text. The Studio never reads or sends the content of the user's prompts. What the
replay keeps from the user's traffic is the size of the work, not its content. How well the drafts of the engine
(suffix draft, MTP) work on the user's own text is therefore not measured here.

Every prompt starts with a salt, so the engine finds no reused prefix: the replay reads every token new.
The request is greedy (temperature 0) and sets `strata_checkpoint: false`, so the replay leaves no checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request

from . import workload

WORDS = ("table river window garden pencil engine bridge candle forest mirror ladder summer winter planet silver "
         "button harbor kitchen letter market number orange paper quiet rocket school shadow thunder valley yellow "
         "anchor blanket copper dragon feather glacier hammer island jungle kettle lantern meadow needle oyster").split()
PASSAGE_HEAD = "Copy the following passage word for word. Then stop.\n\n"
MAX_READ = 30000                                        # a replayed request reads at most this many new tokens
MAX_GENERATED = 400                                     # and generates at most this many
OVERHEAD_WORDS = 800                                    # the length of the probe that finds the tokens per word


class ReplayError(Exception):
    pass


def filler(words: int, salt: str) -> str:
    """`words` words of plain text. The same salt gives the same text, and a new salt a text with no common prefix."""
    n = len(WORDS)
    seed = int(hashlib.sha256(salt.encode()).hexdigest()[:8], 16)
    out = []
    for i in range(max(0, words)):
        out.append(WORDS[(seed + i * 7 + (i // n) * 3) % n])
    return " ".join(out)


def pick_shapes(reqs: list[dict], k: int = 3, max_read: int = MAX_READ, max_generated: int = MAX_GENERATED) -> list[dict]:
    """k shapes from the logged requests: spread over the requests by the number of tokens read. A shape is
    {"read": tokens, "generated": tokens}. Both are capped, and `capped` says so."""
    rows = []
    for r in reqs:
        read = r.get("read_tokens")
        gen = r.get("generated")
        if not isinstance(read, int) or not isinstance(gen, int) or read < 1 or gen < 1:
            continue
        rows.append((read, gen))
    if not rows:
        return []
    rows.sort()
    k = max(1, min(k, len(rows)))
    picks = [rows[round(i * (len(rows) - 1) / (k - 1))] if k > 1 else rows[len(rows) // 2] for i in range(k)]
    out = []
    for read, gen in picks:
        out.append({"read": min(read, max_read), "generated": min(gen, max_generated),
                    "capped": read > max_read or gen > max_generated})
    return out


def shapes_seconds(shapes: list[dict], prompt_tok_s: float | None, decode_tok_s: float | None) -> float | None:
    """The time one pass over the shapes takes at the speeds of the log. None when a speed is unknown."""
    if not prompt_tok_s or not decode_tok_s:
        return None
    return sum(s["read"] / prompt_tok_s + s["generated"] / decode_tok_s for s in shapes)


class Client:
    """A streamed chat request with the standard library. `chat` returns the text and the engine's counts."""

    def __init__(self, base_url: str, key: str | None = None, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def health(self) -> dict | None:
        """GET /health as a dict, or None when Strata does not answer."""
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=5) as r:
                d = json.loads(r.read().decode())
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def chat(self, prompt: str, max_tokens: int, tune: dict | None = None, cancel=None) -> dict:
        body = {"model": "strata", "stream": True, "max_tokens": max_tokens, "temperature": 0,
                "strata_checkpoint": False, "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": prompt}]}
        if tune:
            body["strata_tune"] = dict(tune)
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        t0 = time.perf_counter()
        text, usage, timings, err = [], None, None, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for raw in r:
                    if cancel is not None and cancel.is_set():
                        err = "cancelled"
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    d = line[5:].strip()
                    if d == "[DONE]":
                        break
                    try:
                        j = json.loads(d)
                    except ValueError:
                        continue
                    if j.get("error"):
                        err = str(j["error"])[:300]
                    for ch in j.get("choices") or []:
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            text.append(piece)
                    if j.get("usage"):
                        usage = j["usage"]
                    if j.get("timings"):
                        timings = j["timings"]
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
        except Exception as e:                           # noqa: BLE001  (a refused connection, a timeout, a cut stream)
            err = f"{type(e).__name__}: {e}"[:300]
        joined = "".join(text)
        return {"wall_s": time.perf_counter() - t0, "text": joined, "usage": usage or {}, "timings": timings or {},
                "error": err}


class Sizer:
    """Turns a number of tokens into a number of words, from one probe of the model's own count."""

    def __init__(self, client: Client, cancel=None):
        self.client = client
        self.cancel = cancel
        self.base_tokens = 0                              # the tokens of the chat template around an empty prompt
        self.per_word = 1.3

    def calibrate(self):
        a = self.client.chat("x " + filler(0, "p0"), 1, cancel=self.cancel)
        b = self.client.chat("x " + filler(OVERHEAD_WORDS, "p1"), 1, cancel=self.cancel)
        for r in (a, b):
            if r["error"] or not r["usage"].get("prompt_tokens"):
                raise ReplayError(r["error"] or "Strata did not report the prompt tokens (usage.prompt_tokens)")
        self.base_tokens = a["usage"]["prompt_tokens"]
        self.per_word = max(0.3, (b["usage"]["prompt_tokens"] - self.base_tokens) / OVERHEAD_WORDS)

    def words(self, tokens: float) -> int:
        """The words that make `tokens` tokens of plain text."""
        return max(1, int(max(0, tokens) / self.per_word))


def prompt_for(shape: dict, sizer: Sizer, salt: str) -> tuple[str, int]:
    """(the prompt text, max_tokens). The passage to copy is `generated` tokens long; the filler makes up the rest."""
    passage_words = sizer.words(shape["generated"])
    passage = filler(passage_words, "passage")
    head_tokens = int(len(PASSAGE_HEAD.split()) * 1.4) + 8
    ctx_words = sizer.words(shape["read"] - sizer.base_tokens - passage_words * sizer.per_word - head_tokens - 6)
    ctx = f"Reference {salt}: " + filler(ctx_words, salt)       # the salt comes first, so no prefix is shared
    return f"{ctx}\n\n{PASSAGE_HEAD}{passage}", shape["generated"]


def one_pass(client: Client, shapes: list[dict], sizer: Sizer, tune: dict | None, salt: str, cancel=None) -> dict:
    """Every shape once. The score of the pass is `score_s`: the seconds of the engine's own clock (prompt and reply),
    or the wall seconds when the engine reports none. `cached` counts the prompt tokens that the engine reused
    after all: a pass with cached tokens is not a clean read."""
    wall = prompt_ms = decode_ms = 0.0
    read = gen = cached = drafts = accepted = 0
    errors, h = [], hashlib.sha256()
    for i, shape in enumerate(shapes):
        prompt, max_tokens = prompt_for(shape, sizer, f"{salt}-{i}")
        r = client.chat(prompt, max_tokens, tune, cancel)
        if r["error"]:
            errors.append(r["error"])
            break
        wall += r["wall_s"]
        t, u = r["timings"], r["usage"]
        read += int(t.get("prompt_n") or u.get("prompt_tokens") or 0)
        cached += int(t.get("cache_n") or 0)
        gen += int(u.get("completion_tokens") or t.get("predicted_n") or 0)
        prompt_ms += float(t.get("prompt_ms") or 0.0)
        decode_ms += float(t.get("predicted_ms") or 0.0)
        drafts += int(t.get("draft_n") or 0)
        accepted += int(t.get("draft_n_accepted") or 0)
        h.update(r["text"].encode())
        h.update(b"\x00")
    engine_s = (prompt_ms + decode_ms) / 1000
    return {"score_s": engine_s if engine_s > 0 else wall, "wall_s": wall, "read": read, "generated": gen,
            "cached": cached, "prompt_tok_s": (read / prompt_ms * 1000) if prompt_ms else None,
            "decode_tok_s": (gen / decode_ms * 1000) if decode_ms else None,
            "drafts_accepted": (accepted / drafts) if drafts else None,
            "hash": h.hexdigest(), "error": errors[0] if errors else None}


def from_log(path, last: int = 100, min_prompt: int = 2000) -> list[dict]:
    """The logged requests that the shapes come from, with `read_tokens` (new tokens, not reused)."""
    reqs = workload.requests_of(workload.tail_lines(path))[-last:]
    out = []
    for r in reqs:
        if r.get("prompt_tokens", 0) < min_prompt:
            continue
        out.append({**r, "read_tokens": r["read"]})
    return out
