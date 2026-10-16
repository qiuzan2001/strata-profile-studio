"""The engine log's timing lines, read by the Studio itself: for a Strata build without the /profile routes (Strata
0.1.44.1 as released).  The engine writes them when its environment has STRATA_PREFILL_TIMING=1 and
STRATA_DECODE_TIMING=1 (Strata's docs/DETAILS.md, "Where a decode window's time goes").

`last_request(lines)` is the newest finished request's breakdown, in the shape of Strata's GET /profile/last
"request" (Strata PR #2661), so the rest of the Studio reads it unchanged.

Ported from Strata's tools/strata_profile.py and serve/profile_page.py (Strata PRs #2660 and #2661):
Copyright (c) 2026 Niko1221 and the Strata contributors.  MIT licence (studiolib/vendor/LICENSE-strata.txt).
"""
from __future__ import annotations

import os
import re

KV_BLOCK_BYTES = 4224.0                            # a missed KV block read from RAM (generate.cpp: the KV streaming line)

# the prompt path's phases (src/prefill/prefill.cpp, kPfNames) grouped by what they wait for; a phase name the engine
# adds later lands in "other"
PROMPT_GROUPS = (
    ("experts over PCIe", ("wait copy", "gather", "host grouping")),
    ("dequant", ("dequant",)),
    ("expert GEMM", ("gemm gate/up", "gemm down", "combine")),
    ("attention", ("qsa proj", "qsa indexer", "qsa select", "qsa attn", "kv stage")),
    ("GDN", ("gdn", "gdn conv+gates", "gdn recurrence", "gdn out proj")),
)
OTHER = "other"
DECODE_GROUPS = ("GPU", "CPU experts", "draft", "adapt wait", "other")


_num = r"(-?\d+(?:\.\d+)?)"


def parse_prefill(line: str) -> dict | None:
    """`strata prefill timing: N tokens, GPU timeline X ms, wall Y ms, host staging Z ms: <phase> <ms> (<pct>%) ...`"""
    m = re.search(r"prefill timing: (\d+) tokens, GPU timeline " + _num + r" ms, wall " + _num + r" ms, host staging "
                  + _num + r" ms:(.*)$", line)
    if not m:
        return None
    phases = {}
    for name, ms, _pct in re.findall(r"\s*([a-z][a-z0-9+/ ]*?) " + _num + r" \(" + _num + r"%\)", m.group(5)):
        phases[name.strip()] = float(ms)
    return {"tokens": int(m.group(1)), "gpu_ms": float(m.group(2)), "wall_ms": float(m.group(3)),
            "host_staging_ms": float(m.group(4)), "phases": phases}


def parse_prefill_host(line: str) -> dict | None:
    """`strata prefill timing: host: chunk setup (...) A ms, waiting for each chunk B ms, after each chunk (...) C ms, PLE D ms`"""
    m = re.search(r"prefill timing: host: chunk setup .*? " + _num + r" ms, waiting for each chunk " + _num
                  + r" ms, after each chunk .*? " + _num + r" ms, PLE " + _num + r" ms", line)
    if not m:
        return None
    return {"chunk_setup_ms": float(m.group(1)), "wait_chunk_ms": float(m.group(2)), "after_chunk_ms": float(m.group(3)),
            "ple_ms": float(m.group(4))}


def parse_decode(line: str) -> dict | None:
    """`strata decode timing: W windows, avg T t, k tokens/window, M ms/window = verify V (GPU-reach wait G + per-layer host
    H [plan p actq a jobs j CPU c] + stage s) + commit/emit e + draft d; per layer-window: CPU experts x (y entries), VRAM
    hits v, PCIe q; adapt wait w join jn, n swaps/window`"""
    m = re.search(r"decode timing: (\d+) windows, avg T " + _num + r", " + _num + r" tokens/window, " + _num
                  + r" ms/window = verify " + _num + r" \(GPU-reach wait " + _num + r" \+ per-layer host " + _num
                  + r" \[plan " + _num + r" actq " + _num + r" jobs " + _num + r" CPU " + _num + r"\] \+ stage " + _num
                  + r"\) \+ commit/emit " + _num + r" \+ draft " + _num, line)
    if not m:
        return None
    f = [float(x) for x in m.groups()]
    d = {"windows": int(f[0]), "avg_T": f[1], "tokens_per_window": f[2], "ms_per_window": f[3], "verify": f[4], "gpu": f[5],
         "host": f[6], "host_plan": f[7], "host_actq": f[8], "host_jobs": f[9], "host_cpu": f[10], "stage": f[11],
         "commit": f[12], "draft": f[13]}
    m2 = re.search(r"CPU experts " + _num + r" \(" + _num + r" entries\), VRAM hits " + _num + r", PCIe " + _num, line)
    if m2:
        d.update(cpu_experts=float(m2.group(1)), cpu_entries=float(m2.group(2)), vram_hits=float(m2.group(3)), pcie=float(m2.group(4)))
    m3 = re.search(r"adapt wait " + _num + r" join " + _num + r", " + _num + r" swaps/window", line)
    if m3:
        d.update(adapt_wait=float(m3.group(1)), adapt_join=float(m3.group(2)), swaps=float(m3.group(3)))
    return d


def parse_gpu_stages(line: str) -> dict | None:
    """`strata decode GPU stages (ms/window): GDN layers: <name> <ms> ... | QSA layers: ... | total T ms/window over W windows`"""
    if "decode GPU stages (ms/window):" not in line:
        return None
    body = line.split("(ms/window):", 1)[1]
    out = {"gdn": {}, "qsa": {}, "total": None, "windows": None}
    for part in body.split("|"):
        part = part.strip()
        if part.startswith("GDN layers:") or part.startswith("QSA layers:"):
            key = "gdn" if part.startswith("GDN") else "qsa"
            for name, ms in re.findall(r"(\S+(?: \S+)*?) " + _num + r"(?= |$)", part.split(":", 1)[1]):
                out[key][name.strip()] = float(ms)
        else:
            m = re.search(r"total " + _num + r" ms/window over (\d+) windows", part)
            if m:
                out["total"], out["windows"] = float(m.group(1)), int(m.group(2))
    return out


def parse_summary(line: str) -> dict | None:
    """`strata serve: prompt N tokens = R reused + X read in A ms (B tok/s), G generated in C ms (D tok/s), drafts accepted
    a of b, c checkpoints`"""
    m = re.search(r"serve: prompt (\d+) tokens = (\d+) reused \+ (\d+)(?: of \d+)? read in " + _num + r" ms \(" + _num
                  + r" tok/s\), (\d+) generated in " + _num + r" ms \(" + _num + r" tok/s\), drafts accepted (\d+) of (\d+)", line)
    if not m:
        return None
    return {"prompt_tokens": int(m.group(1)), "reused": int(m.group(2)), "read": int(m.group(3)), "prompt_ms": float(m.group(4)),
            "prompt_tok_s": float(m.group(5)), "generated": int(m.group(6)), "decode_ms": float(m.group(7)),
            "decode_tok_s": float(m.group(8)), "drafts_accepted": int(m.group(9)), "drafts_offered": int(m.group(10))}


def parse_hit_rate(line: str) -> dict | None:
    """`strata serve: decode expert cache hit rate: P% (H hits / L lookups)[; O more read by the GPU over PCIe or from another
    GPU (Q% of all R routed)]`"""
    m = re.search(r"decode expert cache hit rate: " + _num + r"% \((\d+) hits / (\d+) lookups\)", line)
    if not m:
        return None
    d = {"hit_rate": round(float(m.group(1)) / 100.0, 4), "hits": int(m.group(2)), "lookups": int(m.group(3)), "pcie_share": 0.0,
         "offloaded": 0}
    m2 = re.search(r"; (\d+) more read by the GPU over PCIe or from another GPU \(" + _num + r"% of all (\d+) routed\)", line)
    if m2:
        d.update(offloaded=int(m2.group(1)), pcie_share=round(float(m2.group(2)) / 100.0, 4), routed=int(m2.group(3)))
    return d


def parse_kv(line: str) -> dict | None:
    """`strata serve: KV streaming: P% of N block reads hit VRAM, M MiB read from RAM`"""
    m = re.search(r"KV streaming: " + _num + r"% of (\d+) block reads hit VRAM, " + _num + r" MiB read from RAM", line)
    if not m:
        return None
    return {"kv_hit": round(float(m.group(1)) / 100.0, 4), "block_reads": int(m.group(2)), "ram_mib": float(m.group(3)),
            "misses": int(round(float(m.group(3)) * 1048576.0 / KV_BLOCK_BYTES))}


def kv_request(now: dict | None, prev: dict | None) -> dict | None:
    """The KV streaming line counts since the engine started; this request's own share is the difference from the
    line before it (None without both, or without a read in between)."""
    if not now or not prev:
        return None
    reads = now["block_reads"] - prev["block_reads"]
    misses = now["misses"] - prev["misses"]
    if reads <= 0 or misses < 0:
        return None
    return {"kv_hit": round(1.0 - misses / reads, 4), "block_reads": reads, "ram_mib": round(now["ram_mib"] - prev["ram_mib"], 1)}


PARSERS = (("prefill", parse_prefill), ("prefill_host", parse_prefill_host), ("decode", parse_decode),
           ("gpu_stages", parse_gpu_stages), ("summary", parse_summary), ("hit", parse_hit_rate), ("kv", parse_kv))


def engine_facts(lines) -> dict:
    """The engine's figures for one request from the log lines written during it: the last line of each kind (a
    request reads its prompt once and decodes once; the summary lines follow).  Keys absent when no line matched."""
    out: dict = {}
    for line in lines:
        for key, fn in PARSERS:
            d = fn(line)
            if d is not None:
                out[key] = d
                break
    return out


# ------------------------------------------------------------------------------------------------------ grouping
def prompt_groups(pf: dict | None) -> dict | None:
    """The prompt read's GPU time by group, in ms, and the share of the timeline each has (the phases not in any group
    are "other").  None without a prefill line."""
    if not pf or not pf.get("phases"):
        return None
    phases = dict(pf["phases"])
    ms = {}
    for name, members in PROMPT_GROUPS:
        ms[name] = sum(phases.pop(p, 0.0) for p in members)
    ms[OTHER] = sum(phases.values())
    total = pf.get("gpu_ms") or sum(ms.values()) or 1.0
    return {"ms": ms, "share": {k: v / total for k, v in ms.items()}, "gpu_ms": pf.get("gpu_ms"), "tokens": pf.get("tokens"),
            "top": sorted(pf["phases"].items(), key=lambda kv: -kv[1])[:5]}


def decode_groups(dc: dict | None) -> dict | None:
    """The decode window's milliseconds by what the host waited for: the card (GPU-reach wait), the experts the CPU
    pool computed (the per-layer host time: plan, activation quantization, jobs, CPU), the draft, the adaptive tier's
    wait, and the rest (commit, stage, what the line does not itemize).  None without a decode line."""
    if not dc:
        return None
    gpu, host, draft = dc.get("gpu", 0.0), dc.get("host", 0.0), dc.get("draft", 0.0)
    adapt = dc.get("adapt_wait", 0.0)
    total = dc.get("ms_per_window", 0.0)
    other = max(0.0, total - gpu - host - draft - adapt)
    ms = {"GPU": gpu, "CPU experts": host, "draft": draft, "adapt wait": adapt, "other": other}
    return {"ms": ms, "share": {k: v / total if total else 0.0 for k, v in ms.items()}, "ms_per_window": total,
            "tokens_per_window": dc.get("tokens_per_window"), "windows": dc.get("windows"), "cpu_entries": dc.get("cpu_entries"),
            "cpu_experts": dc.get("cpu_experts"),
            "vram_hits": dc.get("vram_hits"), "pcie": dc.get("pcie"), "swaps": dc.get("swaps")}


def last_request(lines: list[str]) -> dict | None:
    """The breakdown of the last finished request among `lines` (engine log lines, oldest first), or None.

    A request's `strata serve: prompt ...` summary line ends its run: the prompt and decode timing lines come before
    it, the hit rate and KV streaming lines after it.  So the last summary's request is the lines between the summary
    before it and the end: its own lines before the summary, the hit rate and KV lines after.  The KV streaming line
    counts since the engine started, so the request's own share is the difference from the line before it.  A
    cancelled request writes no summary, so its timing lines count with the next request's (a prompt the next request
    reused whole then shows the cancelled read's phases): the page is a view of the log, not a ledger."""
    idx = [i for i, line in enumerate(lines) if parse_summary(line)]
    if not idx:
        return None
    s = idx[-1]
    p = idx[-2] if len(idx) > 1 else -1
    own = engine_facts(lines[p + 1:s + 1])
    after = engine_facts(lines[s + 1:])
    summary = own.get("summary") or {}
    out = {"summary": summary,
           "prompt": {"tokens": summary.get("prompt_tokens"), "reused": summary.get("reused"), "read": summary.get("read"),
                      "tok_s": summary.get("prompt_tok_s"), "ms": summary.get("prompt_ms"),
                      "groups": prompt_groups(own.get("prefill")), "host": own.get("prefill_host")},
           "decode": {"generated": summary.get("generated"), "tok_s": summary.get("decode_tok_s"), "ms": summary.get("decode_ms"),
                      "groups": decode_groups(own.get("decode")), "drafts_accepted": summary.get("drafts_accepted"),
                      "drafts_offered": summary.get("drafts_offered")},
           "hit": after.get("hit"), "kv": kv_request(after.get("kv"), own.get("kv")),
           "gpu_stages": own.get("gpu_stages")}
    if out["prompt"]["groups"]:
        out["prompt"]["groups"]["top"] = [list(t) for t in out["prompt"]["groups"]["top"]]
    return out


READ_MAX = 262144                 # the engine log's tail that is read for the last request (its lines are a few KB)


class LastRequest:
    """GET /profile/last: the last request's breakdown from the engine log's tail, parsed again only when the log grew."""

    def __init__(self):
        self.cached = (None, None)                      # (key, value), one assignment: handlers read it concurrently

    def read(self, path, start) -> dict | None:
        """`start`: where this engine run's lines begin in the log (None: no run yet - a lazy start - so nothing)."""
        if not path or start is None:
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        key = (path, start, size)
        cached_key, cached_value = self.cached
        if key == cached_key:
            return cached_value
        pos = max(int(start), size - READ_MAX, 0)
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read(size - pos)
        except OSError:
            return None
        if pos > int(start):                            # the tail starts inside a line: drop that line
            data = data[data.find(b"\n") + 1:]
        end = data.rfind(b"\n") + 1                    # whole lines only
        value = last_request(data[:end].decode("utf-8", "replace").splitlines())
        self.cached = (key, value)
        return value
