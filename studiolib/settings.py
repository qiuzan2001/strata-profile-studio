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

# How each setting is edited by hand.  Ranges come from the engine (src/program/generate.cpp: its --help text, its
# option defaults and its clamps) or Strata's prefill code; where neither states a range, the control is a plain
# number field and the hint says so.  "default" removes the flag (the engine's own default applies).
CONTROLS = {
    "pcie_frac": {"control": "range", "min": 0.0, "max": 1.0, "step": 0.05, "specials": ["default"],
                  "engine_default": "auto (0.55 for native packs, then the start-up PCIe probe)",
                  "source": "the engine clamps a PCIe share to 0-1"},
    "spec_min_p": {"control": "range", "min": 0.0, "max": 1.0, "step": 0.05, "specials": ["default"],
                   "engine_default": "0.0", "source": "a probability (0-1); setup writes 0.5"},
    "spec": {"control": "number", "min": 0, "step": 1, "specials": ["default"], "engine_default": "0 (CUDA)",
             "source": "no range in the engine's help; setup writes 4, --suffix-draft grows the window to at most 8"},
    "pool_workers": {"control": "number", "min": 0, "step": 1, "specials": ["default"],
                     "engine_default": "0: every physical core except the host's",
                     "source": "no upper bound in the engine's help"},
    "adapt_every": {"control": "number", "min": 0, "step": 1, "specials": ["default"], "engine_default": "4",
                    "source": "no range in the engine's help; 0 turns the adaptive tier off"},
    "adapt_swaps": {"control": "number", "min": 0, "step": 1, "specials": ["default"], "engine_default": "96",
                    "source": "no range in the engine's help; 0 turns the adaptive tier off"},
    "adapt_decay": {"control": "number", "min": 0.0, "step": 0.01, "specials": ["default"], "engine_default": "0.7",
                    "source": "no range in the engine's help (a multiplier of the usage counts)"},
    "kv_resident": {"control": "number", "min": 0, "step": 1024, "specials": ["default"], "engine_default": "0: all KV in VRAM",
                    "source": "the engine's help: 0, or at least 20480 cells", "or_min": 20480},
    "max_context": {"control": "number", "min": 1, "step": 1024, "specials": ["default"], "engine_default": "4096",
                    "source": "no upper bound in the engine's help"},
    "expert_cache": {"control": "number", "min": 0, "step": 1, "specials": ["auto", "default"], "engine_default": "0 (setup writes auto)",
                     "source": "a slot count, or auto: what is left after the rest"},
    "vram_reserve_mib": {"control": "number", "min": 0, "step": 64, "specials": ["default"], "engine_default": "700 MiB",
                         "source": "no upper bound in the engine's help"},
    "prefill_cpu_share": {"control": "range", "min": 0.0, "max": 1.0, "step": 0.05, "specials": ["auto", "default"],
                          "engine_default": "auto on CUDA with one GPU", "source": "Strata's prefill clamps it to 0-1"},
    "prefill_cpu_share_max": {"control": "number", "min": 1024, "step": 256, "specials": ["default"],
                              "engine_default": "3072", "source": "Strata's prefill: at least 1024"},
    "expert_profile_save": {"control": "text", "specials": ["default"], "engine_default": "off",
                            "source": "a file path; the engine saves its learned profile there every 10 min"},
}


def check_value(rid: str, value) -> str | None:
    """None when `value` is valid for the setting `rid`, else the reason.  `value` None means "default"."""
    spec = CONTROLS.get(rid)
    if spec is None:
        return f"{rid} cannot be edited here."
    if value is None:
        return None
    v = str(value).strip()
    if v in spec.get("specials", []) and v != "default":
        return None
    if spec["control"] == "text":
        return None if v else "Give a path, or choose default."
    try:
        num = float(v)
    except ValueError:
        return f"{v} is not a number."
    if spec.get("step", 1) == 1 and spec["control"] == "number" and num != int(num):
        return f"{v} is not a whole number."
    lo, hi = spec.get("min"), spec.get("max")
    if spec.get("or_min") and num != 0 and num < spec["or_min"]:
        return f"Use 0 or at least {spec['or_min']}."
    if lo is not None and num < lo:
        return f"The least value is {lo}."
    if hi is not None and num > hi:
        return f"The largest value is {hi}."
    return None
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
                         "calibrated": bool(r.get("calibrated")), "about": ABOUT.get(rid) or str(r.get("about") or ""),
                         "control": CONTROLS.get(rid)})
        return {"source": "profile/settings", "file": profile_settings.get("file") or file,
                "loaded": bool(profile_settings.get("loaded")), "rows": rows, "note": None}
    if isinstance(engine, dict):
        rows = []
        by_id = {r[0]: r for r in SPEED_SETTINGS}
        for rid in METRICS_ROWS:
            _, name, kind, key, calibrated, about = by_id[rid]
            run = config_value(cfg, name, kind)
            rows.append({"id": rid, "name": name, "now": engine_value(engine, key), "next_load": run,
                         "run_config": run, "calibrated": calibrated, "about": about, "control": CONTROLS.get(rid)})
        note = "Strata has no /profile/settings: the values are the loaded engine's, from /metrics."
        if config_path and cfg is None:
            note += f" {cfg_error}"
        return {"source": "metrics", "file": file, "loaded": bool(loaded), "rows": rows, "note": note}
    if cfg is not None:
        rows = []
        for rid, name, kind, key, calibrated, about in SPEED_SETTINGS:
            run = config_value(cfg, name, kind)
            rows.append({"id": rid, "name": name, "now": None, "next_load": run, "run_config": run,
                         "calibrated": calibrated, "about": about, "control": CONTROLS.get(rid)})
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
