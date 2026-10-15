"""/api/settings: the speed settings, from Strata's /profile/settings when it has it, otherwise from /metrics'
engine block, otherwise from --config.  `about` sentences are the Studio's own copy of the Profile tab design's."""
from __future__ import annotations

import json
from pathlib import Path

from .reports import _number_text, arg_value

CONFIG_MAX = 1024 * 1024

# (id, name, kind, key in the engine block, calibrated, about) - the order Strata's tab uses
SPEED_SETTINGS = (
    ("pcie_frac", "--pcie-frac", "arg", "pcie_frac", True,
     "The share of the missed experts the card reads over PCIe instead of the CPU computing them."),
    ("spec_min_p", "--spec-min-p", "arg", "spec_min_p", True,
     "The draft floor: how sure a drafted token must be for the draft to go on."),
    ("spec", "--spec", "arg", None, False, "How many tokens the draft may propose."),
    ("pool_workers", "--pool-workers", "arg", "pool_workers", True,
     "The CPU threads that compute the experts not in VRAM."),
    ("adapt_every", "--adapt-every", "arg", None, True,
     "The adaptive expert tier: how often it swaps experts into VRAM, how many, how fast it forgets."),
    ("adapt_swaps", "--adapt-swaps", "arg", None, True,
     "The adaptive expert tier: how often it swaps experts into VRAM, how many, how fast it forgets."),
    ("adapt_decay", "--adapt-decay", "arg", None, True,
     "The adaptive expert tier: how often it swaps experts into VRAM, how many, how fast it forgets."),
    ("kv_resident", "--kv-resident", "arg", "kv_resident", False,
     "KV positions per layer kept in VRAM; the rest is streamed from RAM."),
    ("max_context", "--max-context", "arg", "context", False,
     "The context the KV cache is sized for; what it does not take is left to the expert cache."),
    ("expert_cache", "--expert-cache", "arg", "expert_cache", False,
     "The VRAM expert cache; auto takes what is left after the rest."),
    ("vram_reserve_mib", "--vram-reserve-mib", "arg", None, False,
     "VRAM left free for other programs; it is taken from the expert cache."),
    ("prefill_cpu_share", "STRATA_PREFILL_CPU_SHARE", "env", None, False,
     "The CPU's share of the experts in short prompt reads."),
    ("prefill_cpu_share_max", "STRATA_PREFILL_CPU_SHARE_MAX", "env", None, False,
     "The largest prompt chunk the CPU share works on (1,024 tokens by default)."),
    ("expert_profile_save", "expert_profile_save", "key", None, False,
     "Saves the adaptive tier's expert ranking so the next start fills the cache with it."),
)
ABOUT = {row[0]: row[5] for row in SPEED_SETTINGS}
# the rows /metrics can fill, in the order the design lists them
METRICS_ROWS = ("pcie_frac", "spec_min_p", "pool_workers", "kv_resident", "max_context", "expert_cache")


def read_config(path) -> tuple[dict | None, str | None]:
    """The run config, read only.  (None, reason) when it cannot be read."""
    if not path:
        return None, None
    try:
        p = Path(path)
        if p.stat().st_size > CONFIG_MAX:
            return None, "The config file is larger than 1 MiB."
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(cfg, dict):
            return None, "The config file is not a JSON object."
        return cfg, None
    except (OSError, ValueError) as e:
        return None, f"The config file could not be read ({type(e).__name__})."


def config_value(cfg: dict | None, name: str, kind: str):
    if not cfg:
        return None
    if kind == "arg":
        return _number_text(arg_value(cfg.get("args"), name))
    if kind == "env":
        env = cfg.get("env")
        return _number_text(env.get(name)) if isinstance(env, dict) else None
    return _number_text(cfg.get(name)) if name in cfg else None


def engine_value(engine: dict | None, key: str | None):
    if not engine or not key:
        return None
    if key == "expert_cache":
        if engine.get("expert_slots") is None:
            return None
        return f"{_number_text(engine.get('expert_slots'))} experts ({_number_text(engine.get('expert_cache_mib'))} MiB)"
    return _number_text(engine.get(key))


def build(profile_settings: dict | None, engine: dict | None, config_path, loaded: bool | None) -> dict:
    """`profile_settings`: Strata's /profile/settings JSON or None; `engine`: /metrics' engine block or None."""
    cfg, cfg_error = read_config(config_path)
    file = Path(config_path).name if config_path else None
    if isinstance(profile_settings, dict) and isinstance(profile_settings.get("settings"), list):
        rows = []
        for r in profile_settings["settings"]:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or r.get("name") or "")
            rows.append({"id": rid, "name": str(r.get("name") or rid), "now": _number_text(r.get("loaded_now")),
                         "next_load": _number_text(r.get("next_load")), "run_config": _number_text(r.get("run_config")),
                         "calibrated": bool(r.get("calibrated")), "about": ABOUT.get(rid) or str(r.get("about") or "")})
        return {"source": "profile/settings", "file": profile_settings.get("file") or file,
                "loaded": bool(profile_settings.get("loaded")), "rows": rows, "note": None}
    if isinstance(engine, dict):
        rows = []
        by_id = {r[0]: r for r in SPEED_SETTINGS}
        for rid in METRICS_ROWS:
            _, name, kind, key, calibrated, about = by_id[rid]
            run = config_value(cfg, name, kind)
            rows.append({"id": rid, "name": name, "now": engine_value(engine, key), "next_load": run,
                         "run_config": run, "calibrated": calibrated, "about": about})
        note = "Strata has no /profile/settings: the values are the loaded engine's, from /metrics."
        if config_path and cfg is None:
            note += f" {cfg_error}"
        return {"source": "metrics", "file": file, "loaded": bool(loaded), "rows": rows, "note": note}
    if cfg is not None:
        rows = []
        for rid, name, kind, key, calibrated, about in SPEED_SETTINGS:
            run = config_value(cfg, name, kind)
            rows.append({"id": rid, "name": name, "now": None, "next_load": run, "run_config": run,
                         "calibrated": calibrated, "about": about})
        return {"source": "config", "file": file, "loaded": False, "rows": rows,
                "note": "Strata does not answer: the values are from the run config file."}
    note = "No settings to show: Strata is unreachable and no --config was given."
    if config_path and cfg_error:
        note = f"No settings to show: Strata is unreachable. {cfg_error}"
    return {"source": "none", "file": file, "loaded": False, "rows": [], "note": note}


def rows_for_meta(profile_settings: dict | None, engine: dict | None) -> list[dict]:
    """The rows stored for a report that carries no config: {flag, config, loaded}."""
    out = []
    if isinstance(profile_settings, dict) and isinstance(profile_settings.get("settings"), list):
        for r in profile_settings["settings"]:
            if isinstance(r, dict) and r.get("name"):
                out.append({"flag": str(r["name"]), "config": _number_text(r.get("run_config")),
                            "loaded": _number_text(r.get("loaded_now"))})
        return out
    if isinstance(engine, dict):
        by_id = {r[0]: r for r in SPEED_SETTINGS}
        for rid in METRICS_ROWS:
            _, name, _, key, _, _ = by_id[rid]
            out.append({"flag": name, "config": None, "loaded": engine_value(engine, key)})
    return out
