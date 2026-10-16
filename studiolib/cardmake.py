"""A result card (schema strata-card/1) from one profile report: `studio.py --card` and the Share dialog.

The card has only the fields below, each taken from a known place: nothing from the report is copied as a whole.
Thus the prompts, the host name, the user name, paths, keys, the run config and the engine's start command can never
reach a card (tests/test_cards.py checks the nine real reports for it).
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from . import __version__, compare
from .configwrite import CALIBRATED
from .reports import ReportError, ReportStore, is_defaults
from .settings import CONTROLS, SPEED_SETTINGS
from .state import State

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from cards import render, validate            # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{0,12}$")
LIMIT_OF_PART = {"GPU": "gpu", "CPU experts": "cpu", "draft": "gpu", "adapt wait": "pcie"}
FLAG_DEFAULT = {name: (CONTROLS.get(rid) or {}).get("engine_default") for rid, name, *_ in SPEED_SETTINGS}


class CardError(Exception):
    pass


def _r(x, n=1):
    return None if x is None else round(float(x), n)


def _int(x):
    return None if x is None else int(x)


def check_name(name) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise CardError("The name has at most 12 characters: letters, digits, space, - and _.")
    return name


def _os(text) -> str:
    """"Linux 7.0.0-38-generic" -> "Linux 7.0.0": the OS and its version, without the build suffix."""
    bits = str(text or "").split()
    if not bits:
        return "not reported"
    ver = bits[1].split("-")[0] if len(bits) > 1 else ""
    return re.sub(r"[^\w .+()-]", "", f"{bits[0]} {ver}".strip())[:40]


def _cpu(name) -> str:
    return re.sub(r"\s+\d+-Core Processor$", "", str(name or "not reported")).strip()[:80]


def _safe(text, n):
    """Free text from a report, without the characters that paths, URLs and markup need."""
    return re.sub(r"[/\\@<>`]", " ", str(text))[:n]


def facts_from(engine_log_facts: dict | None, gpu: dict | None) -> dict:
    """The PC facts that are not in a report: the engine's start-up probe (the CPU pool's expert read rate, the
    measured PCIe link) and the GPU reader's link maximum and power limit."""
    probe = (engine_log_facts or {}).get("probe") or {}
    gpu = gpu or {}
    return {"expert_read_gbs": probe.get("cpu_gbs"), "link_gbs": probe.get("link_gbs"),
            "pcie_gen": gpu.get("pcie_gen_max"), "pcie_width": gpu.get("pcie_width"), "power_limit_w": gpu.get("power_limit_w")}


def make_card(rep: dict, pool: list, *, run=None, tune=None, facts=None, name="") -> dict:
    """The card of one normalized report (ReportStore.get).  `pool`: ReportStore.pool() for the noise floors.
    `tune`: the Studio's tune record whose after-report is this report.  `facts`: facts_from(...)."""
    facts = facts or {}
    m, e = rep.get("machine") or {}, rep.get("environment") or {}
    if not rep.get("decode"):
        raise CardError("The report has no decode measurement.")
    noise = compare.Noise(rep, rep, pool)
    decode = []
    for d in rep["decode"][:3]:
        f = noise.floor(("decode", d["kind"], "decode tok/s"), "rel")
        w = d.get("window_ms")
        decode.append({
            "kind": d["kind"], "context": int(d.get("context") or 1), "tps": _r(d["tps"]["median"]), "min": _r(d["tps"]["min"]),
            "max": _r(d["tps"]["max"]), "n": int(d["tps"].get("n") or 1), "floor": round(f["value"], 4),
            "floor_assumed": bool(f["assumed"]), "hit_rate": _r(d.get("hit_rate"), 3), "pcie_share": _r(d.get("pcie_share"), 3),
            "ms_per_window": _r(d.get("ms_per_window"), 2), "tokens_per_window": _r(d.get("tokens_per_window"), 2),
            "window_ms": {k: _r(w.get(k) or 0, 2) for k in render.PARTS} if w else None})
    prefill = []
    for p in (rep.get("prompts") or [])[:8]:
        if not p.get("prompt_tps"):
            continue
        key = next((k for k in noise.table if k[0] == "prompt" and k[1] == p["label"] and "tok/s" in k[2]), None)
        f = noise.floor(key, "rel") if key else {"value": compare.ASSUMED_TPS, "assumed": True}
        prefill.append({"label": _safe(p["label"].replace(" (long)", ""), 16), "tokens": int(p["tokens"]), "tps": _r(p["prompt_tps"]),
                        "floor": round(f["value"], 4), "floor_assumed": bool(f["assumed"]),
                        "read_s": _r(p["tokens"] / p["prompt_tps"])})
    settings = []
    for row in ((rep.get("settings") or {}).get("rows") or [])[:40]:
        if row.get("config") is None:
            continue
        flag = row["flag"]
        default = FLAG_DEFAULT.get(flag)
        settings.append({"flag": flag, "value": _safe(row["config"], 40), "default": _safe(default, 60) if default else None,
                         "calibrated": flag in CALIBRATED})
    chat = next((d for d in decode if d["kind"] == "chat"), decode[0])
    limit = None
    if chat["window_ms"]:
        part = max((k for k in chat["window_ms"] if k != "other"), key=lambda k: chat["window_ms"][k])
        limit = LIMIT_OF_PART.get(part)
    vram = m.get("gpu_vram_mib")
    gen, width = facts.get("pcie_gen"), facts.get("pcie_width")
    card = {
        "schema": "strata-card/1",
        "date": str(rep.get("date")),
        "studio": __version__,
        "strata": {"version": _safe(m.get("strata_version") or "not reported", 20).replace(" ", "-"),
                   "commit": (e.get("strata_commit") or None),
                   "engine": m.get("engine_version") or None,
                   "backend": (f"CUDA {e['cuda']}" if e.get("cuda") else e.get("backend")) or None,
                   "driver": _safe(e["gpu_driver"], 30) if e.get("gpu_driver") else None},
        "os": _os(e.get("os")),
        "model": {"name": _safe(m.get("model") or "not reported", 60), "quant": _safe(m.get("quant") or "not reported", 20),
                  "context": int(m.get("context") or 512), "kv": _safe(e["kv"], 12) if e.get("kv") else None,
                  "kv_resident": _int(e.get("kv_resident"))},
        "pc": {
            "cpu": {"name": _safe(_cpu(m.get("cpu")), 80), "cores": _int(e.get("cpu_cores")), "threads": _int(e.get("cpu_threads")),
                    "pool_workers": _int(e.get("pool_workers")), "expert_read_gbs": _r(facts.get("expert_read_gbs"))},
            "ram": {"gib": _r(m.get("ram_gib"))},
            "pcie": {"gen": _int(gen) if gen else None, "width": _int(width) if width else None,
                     "measured_gbs": _r(facts.get("link_gbs"))},
            "gpu": {"name": _safe(m.get("gpu") or "not reported", 80), "vram_mib": int(vram) if vram else 1024,
                    "power_limit_w": _r(facts.get("power_limit_w")), "expert_cache_mib": _int(e.get("expert_cache_mib")),
                    "expert_slots": _int(e.get("expert_slots"))},
            "limit": limit},
        "decode": decode,
        "prefill": prefill,
        "agent_turns": [{"turn": int(a["turn"]), "tps": _r(a["decode_tps"]), "prompt_tokens": int(a.get("prompt_tokens") or 0)}
                        for a in (rep.get("agent") or [])[:32] if a.get("decode_tps")],
        "settings": settings,
        "tune": ({"calibration_tok_s": _r(tune.get("tok_s")), "minutes": _r((tune.get("tune_s") or 0) / 60) if tune.get("tune_s") else None,
                  "kept": _safe(tune.get("summary") or "", 240), "before_run": tune.get("before_run")} if tune else None),
        "noise": {"pairs": len(noise.pairs), "labels": sorted({_safe(p["label"], 80) for p in noise.pairs})[:6]},
        "run": run,
        "defaults": bool(is_defaults(rep)),
    }
    card["id"] = validate.card_id(card)
    card["name"] = check_name(name)
    errs = validate.validate(card)
    if errs:
        raise CardError("The card is not valid: " + " · ".join(errs[:3]))
    return card


def run_numbers(store: ReportStore) -> dict:
    """{report id: its run number}, as the Studio counts them (the oldest report is run 1)."""
    ok = [r for r in store.scan()["reports"] if r.get("ok")]
    return {r["id"]: len(ok) - i for i, r in enumerate(ok)}


def tune_for(state: State | None, rid: str, runs: dict):
    if state is None:
        return None
    t = next((t for t in reversed(state.tunes()) if t.get("after_report") == rid), None)
    if not t:
        return None
    t = dict(t)
    t["before_run"] = runs.get(t.get("before_report"))
    return t


def card_of_store(store: ReportStore, rid: str, *, state=None, facts=None, name="") -> dict:
    try:
        rep = store.get(rid)
    except ReportError as e:
        raise CardError(str(e))
    runs = run_numbers(store)
    return make_card(rep, store.pool(), run=runs.get(rid), tune=tune_for(state, rid, runs), facts=facts, name=name)


def card_of_path(path, *, state=None, facts=None, name="") -> dict:
    """The card of a report folder, or of its results.json.  The other reports in the same results folder give the
    noise floor.  Reads only."""
    p = Path(path).expanduser()
    if p.name == "results.json":
        p = p.parent
    if not (p / "results.json").is_file():
        raise CardError(f"{path} is not a profile report (a folder with results.json).")
    tmp = State(Path(tempfile.mkdtemp(prefix="studio-card-")) / "state.json")
    tmp.load()
    store = ReportStore(p.parent, tmp)
    return card_of_store(store, p.name, state=state, facts=facts, name=name)
