"""The standard test: one conversation of the same 6 turns on every PC and in every run, replayed once per arm (one set
of per-request settings).

It is built like a coding agent's session: a long first prompt, then turns that add a few thousand new tokens on top of
the conversation that the engine already holds, with replies of different length. Some replies copy text (like a tool
call or a diff) and some are free text (like an explanation). The prompt text is plain filler, not the user's text.
The Studio never reads or sends the content of the user's prompts. How well the drafts of the engine (suffix draft,
MTP) work on the user's own text is therefore not measured here.

Every conversation starts with a salt, so the engine finds no reused prefix from another pass: the first turn reads
every token new. The later turns reuse the turn before, as a real session does. The history holds fixed text for the
replies, so every arm gets the same prompts whatever it answers. The request is greedy (temperature 0).
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request

WORDS = ("table river window garden pencil engine bridge candle forest mirror ladder summer winter planet silver "
         "button harbor kitchen letter market number orange paper quiet rocket school shadow thunder valley yellow "
         "anchor blanket copper dragon feather glacier hammer island jungle kettle lantern meadow needle oyster").split()
PASSAGE_HEAD = "Copy the following passage word for word. Then stop.\n\n"
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


# The standard test: 6 turns. `read` is the new tokens that the turn adds (the first turn: the whole prompt), `generated`
# the planned reply, `kind` says whether the reply copies text or is free. It is a benchmark, not a claim about the
# user's traffic. The numbers are fixed, so that two tunes can be compared.
STANDARD_TURNS = (
    {"read": 24000, "generated": 128, "kind": "copy"},
    {"read": 1500, "generated": 384, "kind": "free"},
    {"read": 3000, "generated": 192, "kind": "copy"},
    {"read": 1000, "generated": 512, "kind": "copy"},
    {"read": 2000, "generated": 384, "kind": "free"},
    {"read": 800, "generated": 128, "kind": "copy"},
)
STANDARD_NOTE = ("The standard test: one conversation of 6 turns, like a coding agent. The first turn reads 24,000 tokens. "
                 "Each later turn adds 800 to 3,000 new tokens on top of the conversation that Strata already holds, and "
                 "the replies have 128 to 512 tokens (1,728 in all). Some replies copy text, like a diff, and some are "
                 "free text, like an explanation. It is the same on every PC and in every run, so two tunes can be compared.")
FREE_TASK = ("Now write a long, detailed explanation of how a garbage collector works in a programming language, with "
             "examples. Do not stop early.")
REUSE_MIN = 0.5                                         # a later turn must reuse at least this share of its prompt
CLEAN_FIRST = 64                                        # the first turn may find at most this many tokens cached


def standard_turns() -> list[dict]:
    return [dict(t) for t in STANDARD_TURNS]


def shapes_seconds(shapes: list[dict], prompt_tok_s: float | None, decode_tok_s: float | None) -> float | None:
    """The time one pass over the turns takes at the speeds of the log. None when a speed is unknown."""
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

    def chat(self, prompt: str, max_tokens: int, tune: dict | None = None, cancel=None, messages: list | None = None,
             checkpoint: bool = False) -> dict:
        """One request. A `prompt` is a one-shot call (no checkpoint is kept). `messages` is a conversation: with
        `checkpoint` the engine keeps this turn, so that the next turn reuses it."""
        body = {"model": "strata", "stream": True, "max_tokens": max_tokens, "temperature": 0,
                "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False},
                "messages": messages if messages is not None else [{"role": "user", "content": prompt}]}
        if not checkpoint:
            body["strata_checkpoint"] = False
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


def user_turn(turn: dict, i: int, sizer: Sizer, salt: str, prev_reply_tokens: int) -> str:
    """The user message of turn i: filler that makes up the new tokens, then the task. The first turn starts with the
    salt, so no prefix is shared with another pass."""
    if turn["kind"] == "copy":
        passage_words = sizer.words(turn["generated"])
        task = PASSAGE_HEAD + filler(passage_words, f"copy{i}")
        task_tokens = passage_words * sizer.per_word + int(len(PASSAGE_HEAD.split()) * 1.4) + 8
    else:
        task = FREE_TASK
        task_tokens = len(FREE_TASK.split()) * 1.4 + 8
    own = turn["read"] - task_tokens - prev_reply_tokens - (sizer.base_tokens if i == 0 else 0) - 6
    head = f"Reference {salt}: " if i == 0 else ""
    return f"{head}{filler(sizer.words(own), f'{salt}-{i}')}\n\n{task}"


def one_pass(client: Client, turns: list[dict], sizer: Sizer, tune: dict | None, salt: str, cancel=None) -> dict:
    """The conversation once. The score of the pass is `score_ms`: the milliseconds that the engine spent per generated
    token (the settings under test act on the reply, not on the prompt read; per token, because the reply length of
    two passes can differ a little even with the same settings), or the wall milliseconds per token when the engine
    reports no time.
    `cached` is what the first turn found cached (it should be nothing); `reuse_min` is the smallest share of a later
    turn's prompt that the engine reused (a real session reuses nearly all of it)."""
    wall = prompt_ms = decode_ms = 0.0
    read = gen = cached = drafts = accepted = 0
    reuse, errors, h = [], [], hashlib.sha256()
    history: list[dict] = []
    prev_tokens = 0
    for i, turn in enumerate(turns):
        user = {"role": "user", "content": user_turn(turn, i, sizer, salt, prev_tokens)}
        last = i == len(turns) - 1
        r = client.chat("", turn["generated"], tune, cancel, messages=history + [user], checkpoint=not last)
        if r["error"]:
            errors.append(r["error"])
            break
        wall += r["wall_s"]
        t, u = r["timings"], r["usage"]
        n_read, n_cache = int(t.get("prompt_n") or 0), int(t.get("cache_n") or 0)
        read += n_read or int(u.get("prompt_tokens") or 0)
        if i == 0:
            cached = n_cache
        elif n_read + n_cache:
            reuse.append(n_cache / (n_read + n_cache))
        gen += int(u.get("completion_tokens") or t.get("predicted_n") or 0)
        prompt_ms += float(t.get("prompt_ms") or 0.0)
        decode_ms += float(t.get("predicted_ms") or 0.0)
        drafts += int(t.get("draft_n") or 0)
        accepted += int(t.get("draft_n_accepted") or 0)
        h.update(r["text"].encode())
        h.update(b"\x00")
        fixed = filler(sizer.words(turn["generated"]), f"asst{i}")              # fixed text: the same history in every arm
        history += [user, {"role": "assistant", "content": fixed}]
        prev_tokens = int(sizer.words(turn["generated"]) * sizer.per_word)
    per_token = (decode_ms if decode_ms > 0 else wall * 1000) / max(gen, 1)
    return {"score_ms": per_token, "wall_s": wall, "prompt_s": prompt_ms / 1000, "read": read,
            "generated": gen, "cached": cached, "reuse_min": min(reuse) if reuse else None,
            "prompt_tok_s": (read / prompt_ms * 1000) if prompt_ms else None,
            "decode_tok_s": (gen / decode_ms * 1000) if decode_ms else None,
            "drafts_accepted": (accepted / drafts) if drafts else None,
            "hash": h.hexdigest(), "error": errors[0] if errors else None}
