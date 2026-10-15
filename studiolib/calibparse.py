"""The calibration output parser: Strata's tune prints its progress as text lines (setup.calibrate_config and
tools/calibrate.py through the tune's stdout); this turns those lines into the candidates the Tune view draws.

Pure: lines in, a dict out, no clock and no I/O.  A line that matches nothing is ignored, so a wording change in
Strata costs a chart, never an error.
"""
from __future__ import annotations

import re
import statistics

SWEEP_STEPS = {"PCIe share": "pcie_frac", "draft floor": "spec_min_p"}
STEP_ORDER = (("pcie_frac", "PCIe share", "sweep"), ("spec_min_p", "draft floor", "sweep"),
              ("pool_workers", "CPU workers", "restart"), ("adapt", "expert tier", "restart"))
SUMMARY_FLAG_STEP = {"--pcie-frac": "pcie_frac", "--spec-min-p": "spec_min_p", "--pool-workers": "pool_workers"}

RE_ROUND = re.compile(r"^\s*round (\d+)/(\d+) (PCIe share|draft floor): (.*)$")
RE_PAIR = re.compile(r"(\S+) ([\d.]+)")
RE_MEDIAN = re.compile(r"^\s*(PCIe share|draft floor), median of (\d+): (.*) -> best (\S+)\s*$")
RE_MEDIAN_ITEM = re.compile(r"([\d.]+): ([\d.]+) \(([\d.]+)-([\d.]+)\)")
RE_WORKERS_START = re.compile(r"^\s*Measuring with (\d+) CPU workers \(restarts the engine\) \.\.\.$")
RE_WORKERS_RATE = re.compile(r"^\s*(\d+) workers: ([\d.]+) tok/s$")
RE_TIER_START = re.compile(r"^\s*Measuring the expert tier (?:\(the engine default\)|every (\S+), (\S+) swaps, "
                           r"decay (\S+)) \(restarts the engine\) \.\.\.$")
RE_TIER_RATE = re.compile(r"^\s*([\d.]+) tok/s$")
RE_START_FAILED = re.compile(r"^\s*the engine did not start for (.+?): (.*) - this candidate is dropped, "
                             r"the earlier results are kept$")
RE_TUNED = re.compile(r"tuned for this PC: (.*?)(?: \(([\d.]+) tok/s\))?\s*$")
RE_SETTING = re.compile(r"(--[a-z-]+) ([^,\s]+)")
RE_FAILED = re.compile(r"^\s*\[!\]\s+the tuning did not finish \((.*)\): the default settings stay$")
DEFAULTS_TEXT = "the default settings are already the fastest here"


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def same_value(a, b) -> bool:
    """"0.70" equals "0.7": values are compared as floats, as text when they are no numbers."""
    fa, fb = _float(a), _float(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 1e-9
    return a == b


def _entry(values: dict, key: str) -> dict:
    return values.setdefault(key, {"value": key, "rounds": [], "median": None, "min": None, "max": None})


def _sorted_values(step: dict) -> list:
    """Sweeps: by number.  Restarts: in the order they were measured (the engine's own candidate first)."""
    vals = list(step["_values"].values())
    if step["kind"] == "sweep":
        vals.sort(key=lambda v: (_float(v["value"]) is None, _float(v["value"]) or 0.0))
    return vals


def parse(lines, defaults: dict | None = None) -> dict:
    """The calibration's candidates and outcome from `lines`.

    `defaults`: what the engine ran with before the tune ({"pcie_frac": "0.55", "spec_min_p": "0.50", ...}); the
    sweeps' default candidates come from it.  The workers' default is the first count measured, the tier's is
    "default"."""
    defaults = defaults or {}
    steps = {k: {"key": k, "label": label, "kind": kind, "default": None, "best": None, "kept": None, "_values": {}}
             for k, label, kind in STEP_ORDER}
    steps["adapt"]["default"] = "default"
    failed_starts: list[dict] = []
    summary = tok_s = error = outcome = None
    summary_settings: dict[str, str] = {}
    worker = None            # the worker count being measured
    tier = None              # the tier candidate being measured ("default" or "every/swaps/decay")
    for raw in lines or []:
        line = str(raw).rstrip("\r\n")
        m = RE_ROUND.match(line)
        if m:
            step = steps[SWEEP_STEPS[m.group(3)]]
            for val, rate in RE_PAIR.findall(m.group(4)):
                r = _float(rate)
                if r is None:
                    continue
                _entry(step["_values"], val)["rounds"].append(r)
            continue
        m = RE_MEDIAN.match(line)
        if m:
            step = steps[SWEEP_STEPS[m.group(1)]]
            for val, med, lo, hi in RE_MEDIAN_ITEM.findall(m.group(3)):
                e = _entry(step["_values"], val)
                e["median"], e["min"], e["max"] = _float(med), _float(lo), _float(hi)
            step["best"] = m.group(4)
            continue
        m = RE_WORKERS_START.match(line)
        if m:
            worker, tier = m.group(1), None
            _entry(steps["pool_workers"]["_values"], worker)
            if steps["pool_workers"]["default"] is None:
                steps["pool_workers"]["default"] = worker
            continue
        m = RE_WORKERS_RATE.match(line)
        if m:
            _entry(steps["pool_workers"]["_values"], m.group(1))["median"] = _float(m.group(2))
            worker = None
            continue
        m = RE_TIER_START.match(line)
        if m:
            tier = "default" if m.group(1) is None else f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
            worker = None
            _entry(steps["adapt"]["_values"], tier)
            continue
        m = RE_TIER_RATE.match(line)
        if m and tier is not None:
            _entry(steps["adapt"]["_values"], tier)["median"] = _float(m.group(1))
            tier = None
            continue
        m = RE_START_FAILED.match(line)
        if m:
            failed_starts.append({"what": m.group(1), "reason": m.group(2)})
            worker = tier = None
            continue
        m = RE_TUNED.search(line)
        if m:
            summary = m.group(1).strip()
            tok_s = _float(m.group(2))
            summary_settings = {f: v for f, v in RE_SETTING.findall(summary)}
            outcome = "defaults" if summary.startswith(DEFAULTS_TEXT) else "tuned"
            continue
        m = RE_FAILED.match(line)
        if m:
            outcome, error = "failed", m.group(1)

    out_steps = []
    for key, label, kind in STEP_ORDER:
        st = steps[key]
        values = _sorted_values(st)
        for v in values:                              # a sweep's medians while the summary has not come yet
            rs = v["rounds"]
            if v["median"] is None and rs:
                v["median"], v["min"], v["max"] = statistics.median(rs), min(rs), max(rs)
        default = st["default"]
        if key in defaults and defaults[key] is not None:
            default = str(defaults[key])
        value_keys = [v["value"] for v in values]
        for vk in value_keys:                          # print the default as the sweep printed it ("0.55")
            if default is not None and same_value(vk, default):
                default = vk
                break
        best = st["best"]
        if best is None and kind == "restart":
            rated = [v for v in values if v["median"] is not None]
            if rated:
                best = max(rated, key=lambda v: v["median"])["value"]
        kept = None
        if outcome in ("tuned", "defaults"):
            if key == "adapt":
                parts = [summary_settings.get(f) for f in ("--adapt-every", "--adapt-swaps", "--adapt-decay")]
                kept = "/".join(parts) if all(parts) else "default"
            else:
                flag = next(f for f, k in SUMMARY_FLAG_STEP.items() if k == key)
                kept = summary_settings.get(flag, default)
            for vk in value_keys:
                if kept is not None and same_value(vk, kept):
                    kept = vk
                    break
        out_steps.append({"key": key, "label": label, "kind": kind, "default": default, "best": best, "kept": kept,
                          "values": values})
    return {"steps": out_steps, "failed_starts": failed_starts, "summary": summary, "tok_s": tok_s,
            "outcome": outcome, "error": error}


def merge_lines(stored: list[str], new: list[str], cap: int = 4000) -> list[str]:
    """`new` is Strata's newest 400 lines (a deque); `stored` is what the Studio has seen.  Joins them by the
    longest overlap between stored's tail and new's head, so lines that left Strata's deque are not lost."""
    new = [str(x) for x in (new or [])]
    if not stored:
        return new[-cap:]
    if not new:
        return stored
    top = min(len(stored), len(new))
    for k in range(top, 0, -1):
        if stored[-k:] == new[:k]:
            return (stored + new[k:])[-cap:]
    return (stored + new)[-cap:]
