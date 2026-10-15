"""One request in one shape, wherever it comes from: Strata's /profile/last, /profile/history, /metrics, or a
report's results.json.  The page draws a request the same way in every view; a field a source does not carry is None.

The prompt phase groups and the decode window parts follow tools/strata_profile.py (PROMPT_GROUPS, decode_groups), so a
live request and a report request split their time the same way.
"""
from __future__ import annotations

# the prompt path's phases grouped by what they wait for (tools/strata_profile.py PROMPT_GROUPS); others are "other"
PROMPT_GROUPS = (
    ("experts over PCIe", ("wait copy", "gather", "host grouping")),
    ("dequant", ("dequant",)),
    ("expert GEMM", ("gemm gate/up", "gemm down", "combine")),
    ("attention", ("qsa proj", "qsa indexer", "qsa select", "qsa attn", "kv stage")),
    ("GDN", ("gdn", "gdn conv+gates", "gdn recurrence", "gdn out proj")),
)
LONG_PROMPT = 8000          # a fresh read at least this long is a "long prompt"

REQUEST_KEYS = (
    "prompt_tokens", "reused", "read", "prompt_tok_s", "prompt_ms", "prompt_gpu_ms", "prompt_groups_ms", "phases",
    "prompt_top", "prompt_host", "host_staging_ms",
    "generated", "decode_tok_s", "decode_ms", "ms_per_window", "tokens_per_window", "window_ms", "windows",
    "cpu_experts", "cpu_entries", "vram_hits", "pcie_per_window", "swaps",
    "hit_rate", "hits", "lookups", "pcie_share", "offloaded", "routed",
    "kv_hit", "kv_block_reads", "kv_ram_mib",
    "drafts_offered", "drafts_accepted", "ttft_s", "total_s", "gpu_stages",
)


def _d(x):
    return x if isinstance(x, dict) else {}


def _n(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def empty() -> dict:
    return {k: None for k in REQUEST_KEYS}


def kind_of(prompt_tokens, reused, generated) -> str:
    """short | follow-up | long prompt | prompt only, from the request's shape (Strata does not say what it was)."""
    if isinstance(generated, (int, float)) and generated <= 1:
        return "prompt only"
    if isinstance(reused, (int, float)) and reused > 0:
        return "follow-up"
    if isinstance(prompt_tokens, (int, float)) and prompt_tokens >= LONG_PROMPT:
        return "long prompt"
    return "short"


def prompt_groups(phases: dict | None) -> dict | None:
    """{group: ms} from the prompt phases (every phase not in a group counts as "other")."""
    if not isinstance(phases, dict) or not phases:
        return None
    rest = {k: v for k, v in phases.items() if _n(v) is not None}
    out = {}
    for name, members in PROMPT_GROUPS:
        out[name] = round(sum(rest.pop(p, 0.0) for p in members), 3)
    out["other"] = round(sum(rest.values()), 3)
    return out


def window_parts(dc: dict | None) -> dict | None:
    """The decode window's ms by what the host waited for (tools/strata_profile.py decode_groups)."""
    dc = _d(dc)
    total = _n(dc.get("ms_per_window"))
    if total is None:
        return None
    gpu, host, draft = _n(dc.get("gpu")) or 0.0, _n(dc.get("host")) or 0.0, _n(dc.get("draft")) or 0.0
    adapt = _n(dc.get("adapt_wait")) or 0.0
    other = max(0.0, total - gpu - host - draft - adapt)
    return {"GPU": gpu, "CPU experts": host, "draft": draft, "adapt wait": adapt, "other": round(other, 4)}


def _top(phases: dict | None, n=5):
    if not isinstance(phases, dict):
        return None
    items = [(k, v) for k, v in phases.items() if _n(v) is not None]
    return [[k, v] for k, v in sorted(items, key=lambda kv: -kv[1])[:n]] or None


def _hit_into(out: dict, hit: dict):
    hit = _d(hit)
    for k in ("hit_rate", "hits", "lookups", "pcie_share", "offloaded", "routed"):
        if _n(hit.get(k)) is not None:
            out[k] = hit[k]


def _decode_into(out: dict, dc: dict, groups: dict | None = None):
    """Fields of a decode line (`engine.decode`) or of decode groups (/profile/last)."""
    dc = _d(dc)
    g = _d(groups)
    out["ms_per_window"] = _n(dc.get("ms_per_window")) if dc else _n(g.get("ms_per_window"))
    out["tokens_per_window"] = _n(dc.get("tokens_per_window")) if dc else _n(g.get("tokens_per_window"))
    out["windows"] = _n(dc.get("windows")) if dc else _n(g.get("windows"))
    out["window_ms"] = window_parts(dc) if dc else (_d(g.get("ms")) or None)
    src = dc or g
    out["cpu_experts"] = _n(src.get("cpu_experts"))
    out["cpu_entries"] = _n(src.get("cpu_entries"))
    out["vram_hits"] = _n(src.get("vram_hits"))
    out["pcie_per_window"] = _n(src.get("pcie"))
    out["swaps"] = _n(src.get("swaps"))


def from_last(req: dict) -> dict:
    """/profile/last's `request` (serve/profile_page.last_request)."""
    req = _d(req)
    s, p, dc = _d(req.get("summary")), _d(req.get("prompt")), _d(req.get("decode"))
    pg, dg = _d(p.get("groups")), _d(dc.get("groups"))
    out = empty()
    out.update(prompt_tokens=_n(s.get("prompt_tokens")), reused=_n(s.get("reused")), read=_n(s.get("read")),
               prompt_tok_s=_n(s.get("prompt_tok_s")), prompt_ms=_n(s.get("prompt_ms")),
               prompt_gpu_ms=_n(pg.get("gpu_ms")), prompt_groups_ms=_d(pg.get("ms")) or None,
               prompt_top=[list(t) for t in pg.get("top") or [] if isinstance(t, (list, tuple)) and len(t) == 2] or None,
               prompt_host=_d(p.get("host")) or None,
               generated=_n(s.get("generated")), decode_tok_s=_n(s.get("decode_tok_s")), decode_ms=_n(s.get("decode_ms")),
               drafts_offered=_n(s.get("drafts_offered")), drafts_accepted=_n(s.get("drafts_accepted")),
               gpu_stages=_d(req.get("gpu_stages")) or None)
    _decode_into(out, None, dg)
    _hit_into(out, req.get("hit"))
    kv = _d(req.get("kv"))
    out.update(kv_hit=_n(kv.get("kv_hit")), kv_block_reads=_n(kv.get("block_reads")), kv_ram_mib=_n(kv.get("ram_mib")))
    return out


def from_history(e: dict) -> dict:
    """One /profile/history entry (serve/profile_page.compact)."""
    e = _d(e)
    out = empty()
    for k in ("prompt_tokens", "reused", "read", "prompt_tok_s", "prompt_ms", "prompt_gpu_ms", "generated",
              "decode_tok_s", "decode_ms", "ms_per_window", "tokens_per_window", "cpu_experts", "hit_rate",
              "pcie_share", "kv_hit", "drafts_offered", "drafts_accepted"):
        out[k] = _n(e.get(k))
    out["window_ms"] = _d(e.get("ms")) or None
    top = e.get("prompt_top")
    out["prompt_top"] = [[top, None]] if isinstance(top, str) and top else None
    return out


def from_metrics(r: dict) -> dict:
    """One /metrics `requests` entry (profiling off: speeds and the hit rate, no breakdown)."""
    r = _d(r)
    read, pms = _n(r.get("prompt_read")), _n(r.get("prompt_ms"))
    out = empty()
    out.update(prompt_tokens=_n(r.get("prompt_tokens")), reused=_n(r.get("reused")), read=read,
               prompt_tok_s=round(read / pms * 1000, 1) if read is not None and pms else None, prompt_ms=pms,
               generated=_n(r.get("engine_generated")) if r.get("engine_generated") is not None else _n(r.get("output_tokens")),
               decode_tok_s=_n(r.get("decode_tok_s")), decode_ms=_n(r.get("decode_ms")), hit_rate=_n(r.get("hit_rate")),
               pcie_share=_n(r.get("pcie_share")), drafts_offered=_n(r.get("drafts_offered")),
               drafts_accepted=_n(r.get("drafts_accepted")))
    return out


def from_report(item: dict, decode_override: dict | None = None) -> dict:
    """One request of a results.json (a chat request, a prompt read or an agent turn): the API's own fields and the
    engine's lines (`engine`).  `decode_override`: the report's merged decode block for the long read."""
    item = _d(item)
    eng = _d(item.get("engine"))
    s = _d(eng.get("summary")) or _d(item.get("summary"))
    pf = _d(eng.get("prefill"))
    out = empty()
    out.update(prompt_tokens=_n(item.get("prompt_tokens")) if item.get("prompt_tokens") is not None else _n(s.get("prompt_tokens")),
               reused=_n(item.get("reused_tokens")) if item.get("reused_tokens") is not None else _n(s.get("reused")),
               read=_n(item.get("read_tokens")) if item.get("read_tokens") is not None else _n(s.get("read")),
               prompt_tok_s=_n(item.get("prompt_tps")) if item.get("prompt_tps") is not None else _n(s.get("prompt_tok_s")),
               prompt_ms=_n(item.get("prompt_ms")) if item.get("prompt_ms") is not None else _n(s.get("prompt_ms")),
               prompt_gpu_ms=_n(pf.get("gpu_ms")), phases=_d(pf.get("phases")) or None,
               prompt_host=_d(eng.get("prefill_host")) or _d(item.get("prefill_host")) or None,
               host_staging_ms=_n(pf.get("host_staging_ms")),
               generated=_n(item.get("generated_tokens")) if item.get("generated_tokens") is not None else _n(s.get("generated")),
               decode_tok_s=_n(item.get("decode_tps")) if item.get("decode_tps") is not None else _n(s.get("decode_tok_s")),
               decode_ms=_n(item.get("decode_ms")) if item.get("decode_ms") is not None else _n(s.get("decode_ms")),
               drafts_offered=_n(item.get("drafts_offered")) if item.get("drafts_offered") is not None else _n(s.get("drafts_offered")),
               drafts_accepted=_n(item.get("drafts_accepted")) if item.get("drafts_accepted") is not None else _n(s.get("drafts_accepted")),
               ttft_s=_n(item.get("ttft_s")), total_s=_n(item.get("total_s")),
               gpu_stages=_d(eng.get("gpu_stages")) or None)
    out["prompt_groups_ms"] = prompt_groups(out["phases"])
    out["prompt_top"] = _top(out["phases"])
    _decode_into(out, _d(decode_override) or _d(eng.get("decode")))
    _hit_into(out, eng.get("hit"))
    kv = _d(eng.get("kv_request")) or _d(eng.get("kv"))
    out.update(kv_hit=_n(kv.get("kv_hit")), kv_block_reads=_n(kv.get("block_reads")), kv_ram_mib=_n(kv.get("ram_mib")))
    return out


def report_requests(results: dict) -> list[dict]:
    """Every measured request of a report, in the order the profile tool sends them: the chat questions, the prompt
    reads, the agent turns.  Each: {"id", "label", "group", "turn", "kind", ...REQUEST_KEYS}."""
    results = _d(results)
    out = []
    for i, r in enumerate((_d(results.get("chat")).get("requests") or [])):
        if isinstance(r, dict):
            out.append({"id": f"chat-{i + 1}", "label": f"chat q{i + 1}", "group": "chat", "turn": None, **from_report(r)})
    for label, p in _d(results.get("prompts")).items():
        if isinstance(p, dict):
            slug = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
            while "--" in slug:
                slug = slug.replace("--", "-")
            out.append({"id": f"read-{slug}", "label": f"{label} read", "group": "prompt", "turn": None, **from_report(p)})
    for t in (_d(results.get("agent")).get("turns") or []):
        if isinstance(t, dict):
            turn = t.get("turn")
            out.append({"id": f"agent-{turn}", "label": f"agent turn {turn}", "group": "agent", "turn": turn,
                        **from_report(t)})
    for r in out:
        r["kind"] = kind_of(r["prompt_tokens"], r["reused"], r["generated"])
    return out
