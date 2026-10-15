"""The comparison of two reports: pairs, noise floors, rows and verdicts.  Pure; the page only draws the result.

How a change is judged is written down in the README ("How the comparison decides"): every report repeats the same
three chat questions, so their spread is the run-to-run noise of this PC and build, and a change counts only when it
is larger than that spread allows.
"""
from __future__ import annotations

import math

from .calibparse import same_value

MIN_MED = 0.03        # calibrate.py's MIN_GAIN: the least a median change must be to count
MIN_ONE = 0.05        # single runs: the same minimum by the square-root-of-two argument
MIN_HIT = 0.02        # hit rate, in points (a fraction)
ONE_FACTOR = 1.7      # sigma = range / 1.7, a change is real at 2 standard deviations of a difference
MS_PART_ABS = 0.5     # a window part must also move by this many ms
DECODE_PARTS = ("GPU", "CPU experts", "draft", "adapt wait", "other")
NOISE_UNKNOWN = "noise unknown (fewer than two chat runs)"


def _trunc1(x: float) -> float:
    """One decimal, cut and not rounded: the page shows the floor as the design's examples do (7.85 -> 7.8)."""
    return math.floor(x * 10 + 1e-6) / 10


def floors_for(a: dict, b: dict) -> dict:
    sa = (a.get("noise") or {}).get("chat_tps_spread")
    sb = (b.get("noise") or {}).get("chat_tps_spread")
    ha = (a.get("noise") or {}).get("chat_hit_range")
    hb = (b.get("noise") or {}).get("chat_hit_range")
    s = max(sa or 0.0, sb or 0.0)
    h_med = max(MIN_HIT, ha or 0.0, hb or 0.0)
    return {"tps_median": round(max(MIN_MED, s), 4), "tps_single": round(max(MIN_ONE, ONE_FACTOR * s), 4),
            "hit_median": round(h_med, 4), "hit_single": round(ONE_FACTOR * h_med, 4), "ms_part_abs": MS_PART_ABS,
            "_unknown": sa is None or sb is None}


def _delta(a, b, scale: float = 1.0):
    if a is None or b is None:
        return None, None
    d = (b - a) * scale
    pct = (b - a) / a * 100 if a else None
    return d, pct


def _round(x, n=4):
    return None if x is None else round(x, n)


def _state(delta, pct, floor, direction, abs_floor=None) -> str:
    """better / worse / same for a relative floor (`floor` as a fraction)."""
    if direction is None or delta is None:
        return "same"
    if pct is None:                                   # a was 0: only an absolute floor can say
        if abs_floor is None or abs(delta) <= abs_floor:
            return "same"
        moved_up = delta > 0
    else:
        if abs(pct) / 100 <= floor + 1e-12:
            return "same"
        if abs_floor is not None and abs(delta) <= abs_floor:
            return "same"
        moved_up = pct > 0
    good_up = direction == "higher"
    return "better" if moved_up == good_up else "worse"


def _row(group, key, metric, a, b, direction, floor, kind="rel", abs_floor=None, scale=1.0, tinted=True):
    """One table row.  Hit rates and shares are passed as fractions and shown in percent (scale 100)."""
    if a is None or b is None:
        return {"group": group, "key": key, "metric": metric, "a": _round(a), "b": _round(b), "delta": None,
                "pct": None, "better": direction, "floor": _round(floor), "floor_kind": kind, "state": "missing"}
    d, pct = _delta(a, b, scale)
    if kind == "pts":                                 # the floor is in points of a fraction; delta is in points
        state = "same"
        if direction and tinted and abs(d) / 100 > floor + 1e-12:
            state = "better" if (d > 0) == (direction == "higher") else "worse"
        shown_floor = floor * 100
    else:
        state = _state(b - a, pct, floor, direction if tinted else None, abs_floor)
        shown_floor = floor
    return {"group": group, "key": key, "metric": metric, "a": _round(a * scale), "b": _round(b * scale),
            "delta": _round(d), "pct": _round(pct, 2), "better": direction, "floor": _round(shown_floor),
            "floor_kind": kind, "state": state}


def _tps_n(rep_kind) -> int:
    n = ((rep_kind or {}).get("tps") or {}).get("n")
    return int(n) if isinstance(n, (int, float)) else 1


def _by(items, key):
    return {i[key]: i for i in items or [] if i.get(key) is not None}


def _fmt_floor(f: float) -> str:
    return f"{_trunc1(f * 100):.1f}%"


def _verdict_decode(kind, da, db, F, H, floor_text, unknown):
    key = kind
    if not da or not db or da["tps"]["median"] is None or db["tps"]["median"] is None:
        return {"group": "decode", "key": key, "state": "missing", "text": f"{kind}: not in both reports"}
    a, b = da["tps"]["median"], db["tps"]["median"]
    _, pct = _delta(a, b)
    state = _state(b - a, pct, F, "higher")
    if state == "same":
        text = (f"{kind}: no measurable change ({pct:+.1f}%, inside the {floor_text} noise floor)"
                if pct is not None else f"{kind}: no measurable change")
    else:
        word = "faster" if state == "better" else "slower"
        text = f"{kind}: {abs(pct):.1f}% {word}, {a:.1f} → {b:.1f} tok/s (noise floor {floor_text})"
    wa, wb = da.get("window_ms") or {}, db.get("window_ms") or {}
    biggest = None
    for part in [p for p in DECODE_PARTS if p in wa and p in wb] + [p for p in wa if p in wb and p not in DECODE_PARTS]:
        va, vb = wa[part], wb[part]
        if va is None or vb is None:
            continue
        d, p = _delta(va, vb)
        if _state(d, p, F, "lower", MS_PART_ABS) != "same" and (biggest is None or abs(d) > abs(biggest[1])):
            biggest = (part, d)
    if biggest:
        text += f"; largest change: {biggest[0]} {biggest[1]:+.1f} ms per window"
    ha, hb = da.get("hit_rate"), db.get("hit_rate")
    if ha is not None and hb is not None and abs(hb - ha) > H + 1e-12:
        text += f"; hit rate {ha * 100:.1f}% → {hb * 100:.1f}%"
    if unknown:
        text += f"; {NOISE_UNKNOWN}"
    return {"group": "decode", "key": key, "state": state, "text": text}


def _verdict_prompt(label, pa, pb, F, floor_text, unknown):
    if not pa or not pb or pa.get("prompt_tps") is None or pb.get("prompt_tps") is None:
        return {"group": "prompt", "key": label, "state": "missing", "text": f"{label} read: not in both reports"}
    a, b = pa["prompt_tps"], pb["prompt_tps"]
    _, pct = _delta(a, b)
    state = _state(b - a, pct, F, "higher")
    if state == "same":
        text = (f"{label} read: no measurable change ({pct:+.1f}%, inside the {floor_text} noise floor)"
                if pct is not None else f"{label} read: no measurable change")
    else:
        word = "faster" if state == "better" else "slower"
        text = f"{label} read: {abs(pct):.1f}% {word}, {a:,.0f} → {b:,.0f} tok/s (noise floor {floor_text})"
    if unknown:
        text += f"; {NOISE_UNKNOWN}"
    return {"group": "prompt", "key": label, "state": state, "text": text}


def _ordered_keys(a_items, b_items, key):
    keys = [i[key] for i in a_items or [] if i.get(key) is not None]
    keys += [i[key] for i in b_items or [] if i.get(key) is not None and i[key] not in keys]
    return keys


def warnings_for(a: dict, b: dict) -> list[str]:
    ma, mb = a.get("machine") or {}, b.get("machine") or {}
    out = []
    for key, text in (("gpu", "different GPU"), ("model", "different model"), ("context", "different context"),
                      ("strata_version", "different Strata version")):
        if ma.get(key) is not None and mb.get(key) is not None and ma[key] != mb[key]:
            out.append(text)
    return out


def settings_diff(a: dict, b: dict) -> list[dict]:
    def shown(rep):
        out = {}
        for r in (rep.get("settings") or {}).get("rows") or []:
            out[r["flag"]] = r.get("loaded") if r.get("loaded") is not None else r.get("config")
        return out
    sa, sb = shown(a), shown(b)
    flags = list(sa) + [f for f in sb if f not in sa]
    return [{"flag": f, "a": sa.get(f), "b": sb.get(f),
             "changed": not (sa.get(f) is None and sb.get(f) is None) and not same_value(sa.get(f), sb.get(f))}
            for f in flags]


def compute(a: dict, b: dict) -> dict:
    """`a` and `b`: normalised reports (reports.normalise).  A is the before, B the after."""
    fl = floors_for(a, b)
    unknown = fl.pop("_unknown")
    rows: list[dict] = []
    verdicts: list[dict] = []
    da, db = _by(a.get("decode"), "kind"), _by(b.get("decode"), "kind")
    for kind in _ordered_keys(a.get("decode"), b.get("decode"), "kind"):
        x, y = da.get(kind), db.get(kind)
        single = _tps_n(x) < 2 or _tps_n(y) < 2
        F = fl["tps_single"] if single else fl["tps_median"]
        H = fl["hit_single"] if single else fl["hit_median"]
        verdicts.append(_verdict_decode(kind, x, y, F, H, _fmt_floor(F), unknown))
        g = "decode"
        rows.append(_row(g, kind, "decode tok/s", ((x or {}).get("tps") or {}).get("median"),
                         ((y or {}).get("tps") or {}).get("median"), "higher", F))
        if x and y:
            rows.append(_row(g, kind, "expert cache hit rate (points)", x.get("hit_rate"), y.get("hit_rate"),
                             "higher", H, "pts", scale=100.0))
            rows.append(_row(g, kind, "read over PCIe (points)", x.get("pcie_share"), y.get("pcie_share"),
                             None, H, "pts", scale=100.0, tinted=False))
            rows.append(_row(g, kind, "ms per window", x.get("ms_per_window"), y.get("ms_per_window"), "lower", F))
            wa, wb = x.get("window_ms") or {}, y.get("window_ms") or {}
            for part in [p for p in DECODE_PARTS if p in wa or p in wb] + \
                    [p for p in list(wa) + list(wb) if p not in DECODE_PARTS]:
                rows.append(_row(g, kind, f"{part} (ms per window)", wa.get(part), wb.get(part), "lower", F,
                                 abs_floor=MS_PART_ABS))
            rows.append(_row(g, kind, "tokens per window", x.get("tokens_per_window"), y.get("tokens_per_window"),
                             "higher", F))
    pa, pb = _by(a.get("prompts"), "label"), _by(b.get("prompts"), "label")
    F1 = fl["tps_single"]
    for label in _ordered_keys(a.get("prompts"), b.get("prompts"), "label"):
        x, y = pa.get(label), pb.get(label)
        verdicts.append(_verdict_prompt(label, x, y, F1, _fmt_floor(F1), unknown))
        rows.append(_row("prompt", label, "prompt tok/s", (x or {}).get("prompt_tps"), (y or {}).get("prompt_tps"),
                         "higher", F1))
        if x and y:
            rows.append(_row("prompt", label, "GPU time (ms)", x.get("gpu_ms"), y.get("gpu_ms"), "lower", F1))
    ta, tb = _by(a.get("agent"), "turn"), _by(b.get("agent"), "turn")
    for turn in sorted(set(ta) | set(tb), key=lambda t: (not isinstance(t, (int, float)), t)):
        x, y = ta.get(turn), tb.get(turn)
        key = turn
        g = "agent"
        if x and y:
            rows.append(_row(g, key, "decode tok/s", x.get("decode_tps"), y.get("decode_tps"), "higher", F1))
            rows.append(_row(g, key, "prompt tok/s", x.get("prompt_tps"), y.get("prompt_tps"), "higher", F1))
            rows.append(_row(g, key, "expert cache hit rate (points)", x.get("hit_rate"), y.get("hit_rate"),
                             "higher", fl["hit_single"], "pts", scale=100.0))
            rows.append(_row(g, key, "ms per window", x.get("ms_per_window"), y.get("ms_per_window"), "lower", F1))
        else:
            rows.append(_row(g, key, "decode tok/s", (x or {}).get("decode_tps"), (y or {}).get("decode_tps"),
                             "higher", F1))
    return {"a": a.get("id"), "b": b.get("id"), "warnings": warnings_for(a, b), "floors": fl,
            "verdicts": verdicts, "rows": rows, "settings": settings_diff(a, b)}
