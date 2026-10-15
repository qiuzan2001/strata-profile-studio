"""The comparison of two reports: pairs, noise floors, rows and verdicts.  Pure; the page only draws the result.

How a change is judged (also in the README, "How the comparison decides"): the noise floor of a metric is measured,
not assumed, whenever the folder holds reports made with the same settings on the same machine: how far those
reports differ from each other on that metric is how far two runs of one setting wander on this PC.  A change counts
only when it is larger than that.  With no such pair the floors are fixed guesses and the verdict says so.  The three
chat questions of one report are three different prompts, so their spread says nothing about noise and is never used.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .calibparse import same_value
from .reports import settings_short

MIN_REL = 0.03         # calibrate.py's MIN_GAIN: the least a measured floor can be
ASSUMED_TPS = 0.05     # no same-settings pair: tok/s (decode and prompt) and the other relative metrics
ASSUMED_MS = 1.0       # no pair: a window part must move by this many ms
ASSUMED_HIT = 0.02     # no pair: hit rate, in points (a fraction)
MS_PART_MIN = 0.5      # with a pair: a window part's floor is never less than this many ms
SAME_SETTINGS_MAX = 20  # the newest this many same-settings reports of a group are compared pairwise
SIGN_MIN_TURNS = 5     # the sign test needs this many agent turns
DECODE_PARTS = ("GPU", "CPU experts", "draft", "adapt wait", "other")
ASSUMED_NOTE = "assumed floor, no same-settings pair measured yet"


def _trunc1(x: float) -> float:
    """One decimal, cut and not rounded (7.85 shows as 7.8)."""
    return math.floor(x * 10 + 1e-6) / 10


def _pct_text(f: float) -> str:
    return f"{_trunc1(f * 100):.1f}%"


def _round(x, n=4):
    return None if x is None else round(x, n)


def short_of(rep: dict) -> dict:
    return settings_short(rep["settings"]["rows"], True if rep["settings"]["source"] == "captured" else None)


# ----------------------------------------------------------------------------------------- the metrics
def metric_values(rep: dict) -> dict:
    """{(group, key, metric): (value, kind)} for every number the comparison judges.  kind: rel | pts | ms."""
    out = {}
    for d in rep.get("decode") or []:
        k = d["kind"]
        out[("decode", k, "decode tok/s")] = ((d.get("tps") or {}).get("median"), "rel")
        out[("decode", k, "expert cache hit rate (points)")] = (d.get("hit_rate"), "pts")
        out[("decode", k, "ms per window")] = (d.get("ms_per_window"), "rel")
        for part, v in (d.get("window_ms") or {}).items():
            out[("decode", k, f"{part} (ms per window)")] = (v, "ms")
        out[("decode", k, "tokens per window")] = (d.get("tokens_per_window"), "rel")
    for p in rep.get("prompts") or []:
        out[("prompt", p["label"], "prompt tok/s")] = (p.get("prompt_tps"), "rel")
        out[("prompt", p["label"], "GPU time (ms)")] = (p.get("gpu_ms"), "rel")
    for t in rep.get("agent") or []:
        if t.get("turn") is None:
            continue
        out[("agent", t["turn"], "decode tok/s")] = (t.get("decode_tps"), "rel")
        out[("agent", t["turn"], "prompt tok/s")] = (t.get("prompt_tps"), "rel")
        out[("agent", t["turn"], "expert cache hit rate (points)")] = (t.get("hit_rate"), "pts")
        out[("agent", t["turn"], "ms per window")] = (t.get("ms_per_window"), "rel")
    return out


class Noise:
    """The floors, from same-settings reports in the pool (every ok report in the folder)."""

    def __init__(self, a: dict, b: dict, pool: list[dict] | None):
        if not pool:
            pool = [{"rep": a, "short": short_of(a), "order": [0, 0, 0, 1, 0]},
                    {"rep": b, "short": short_of(b), "order": [0, 0, 0, 2, 0]}]
        for e in pool:
            e.setdefault("id", e["rep"].get("id"))
        machines = {self.machine(a), self.machine(b)}
        groups = defaultdict(list)
        for e in pool:
            if self.machine(e["rep"]) in machines:
                groups[(self.machine(e["rep"]), tuple(sorted(e["short"].items())))].append(e)
        self.table: dict = {}          # (group, key, metric) -> {"d", "lo", "hi", "n"}
        for members in groups.values():
            if len(members) < 2:
                continue
            members = sorted(members, key=lambda e: e["order"])[-SAME_SETTINGS_MAX:]
            series = defaultdict(list)
            for e in members:                                   # oldest first
                for key, (v, kind) in metric_values(e["rep"]).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        series[key].append((v, kind, e))
            for key, items in series.items():
                if len(items) < 2:
                    continue
                best = None
                if items[0][1] == "rel":                         # relative to the earlier run, as a change is
                    for i in range(len(items)):
                        for j in range(i + 1, len(items)):
                            if items[i][0] > 0:
                                d = abs(items[j][0] - items[i][0]) / items[i][0]
                                if best is None or d > best[0]:
                                    best = (d, items[i][2], items[j][2])
                else:
                    lo = min(items, key=lambda x: x[0])
                    hi = max(items, key=lambda x: x[0])
                    best = (hi[0] - lo[0], lo[2], hi[2])
                if best is None:
                    continue
                cur = self.table.get(key)
                if cur is None or best[0] > cur["d"]:
                    self.table[key] = {"d": best[0], "lo": best[1], "hi": best[2], "n": len(items)}
        self.pairs: list[dict] = []

    @staticmethod
    def machine(rep: dict):
        m = rep.get("machine") or {}
        return (m.get("gpu"), m.get("model"), m.get("quant"), m.get("context"))

    def floor(self, key: tuple, kind: str) -> dict:
        """{"value": the floor (fraction, points as a fraction, or ms), "assumed": bool, "label", "n"}."""
        hit = self.table.get(key)
        if kind == "rel":
            base, assumed = ASSUMED_TPS, ASSUMED_TPS
            if hit:
                base = max(MIN_REL, hit["d"])
        elif kind == "pts":
            base, assumed = ASSUMED_HIT, ASSUMED_HIT
            if hit:
                base = max(ASSUMED_HIT, hit["d"])
        else:
            base, assumed = ASSUMED_MS, ASSUMED_MS
            if hit:
                base = max(MS_PART_MIN, hit["d"])
        if not hit:
            return {"value": assumed, "assumed": True, "label": None, "n": 0}
        lo, hi = sorted((hit["lo"], hit["hi"]), key=lambda e: e["order"])
        if lo["rep"]["date"] != hi["rep"]["date"]:
            label = f"{lo['rep']['date']} run {lo['order'][3]} and {hi['rep']['date']} run {hi['order'][3]}, same settings"
        else:
            label = f"runs {lo['order'][3]} and {hi['order'][3]}, same settings"
        pair = {"a": lo["id"], "b": hi["id"], "label": label}
        if pair not in self.pairs:
            self.pairs.append(pair)
        return {"value": base, "assumed": False, "label": label, "n": hit["n"]}


# --------------------------------------------------------------------------------------------- the rows
def _state(delta, pct, floor, direction, kind="rel", abs_floor=None) -> str:
    """better / worse / same.  rel: `floor` is a fraction of a; pts: floor in points (a fraction), delta in the
    same unit; ms: `floor` in ms and the change must also exceed MIN_REL of a."""
    if direction is None or delta is None:
        return "same"
    if kind == "rel":
        if pct is None:
            return "same"
        if abs(pct) / 100 <= floor + 1e-12:
            return "same"
    elif kind == "pts":
        if abs(delta) <= floor + 1e-12:
            return "same"
    else:
        if abs(delta) <= floor + 1e-12:
            return "same"
        if pct is not None and abs(pct) / 100 <= MIN_REL:
            return "same"
    moved_up = delta > 0
    return "better" if moved_up == (direction == "higher") else "worse"


def _row(group, key, metric, a, b, direction, fl: dict, kind: str, tinted=True):
    """One table row.  pts rows hold percentages (value x 100), delta in points and the floor in points."""
    scale = 100.0 if kind == "pts" else 1.0
    floor_shown = fl["value"] * scale if kind == "pts" else fl["value"]
    if a is None or b is None:
        return {"group": group, "key": key, "metric": metric, "a": _round(None if a is None else a * scale),
                "b": _round(None if b is None else b * scale), "delta": None, "pct": None, "better": direction,
                "floor": _round(floor_shown), "floor_kind": kind, "state": "missing"}
    d = (b - a) * scale
    pct = (b - a) / a * 100 if a else None
    state = "same"
    if tinted and direction:
        state = _state(b - a, pct, fl["value"], direction, kind)
    return {"group": group, "key": key, "metric": metric, "a": _round(a * scale), "b": _round(b * scale),
            "delta": _round(d), "pct": _round(pct, 2), "better": direction, "floor": _round(floor_shown),
            "floor_kind": kind, "state": state}


def _note(fl: dict) -> str | None:
    if fl["assumed"]:
        return None
    note = f"noise floor from {fl['label']}"
    if fl["n"] > 2:
        note += f"; the widest spread of {fl['n']} runs with the same settings"
    return note


def _verdict_decode(kind, da, db, fl, noise, hl):
    if not da or not db or da["tps"]["median"] is None or db["tps"]["median"] is None:
        return {"group": "decode", "key": kind, "state": "missing", "text": f"{kind}: not in both reports",
                "floor_note": None}
    a, b = da["tps"]["median"], db["tps"]["median"]
    pct = (b - a) / a * 100 if a else None
    F = fl["value"]
    state = _state(b - a, pct, F, "higher")
    if state == "same":
        text = (f"{kind}: no measurable change ({pct:+.1f}%, inside the {_pct_text(F)} "
                f"{'floor' if fl['assumed'] else 'noise floor'})" if pct is not None
                else f"{kind}: no measurable change")
    else:
        word = "faster" if state == "better" else "slower"
        text = (f"{kind}: {abs(pct):.1f}% {word}, {a:.1f} → {b:.1f} tok/s"
                + ("" if fl["assumed"] else f" (noise floor {_pct_text(F)})"))
    wa, wb = da.get("window_ms") or {}, db.get("window_ms") or {}
    biggest = None
    for part in [p for p in DECODE_PARTS if p in wa and p in wb] + [p for p in wa if p in wb and p not in DECODE_PARTS]:
        va, vb = wa[part], wb[part]
        if va is None or vb is None:
            continue
        pf = noise.floor(("decode", kind, f"{part} (ms per window)"), "ms")
        p = (vb - va) / va * 100 if va else None
        if _state(vb - va, p, pf["value"], "lower", "ms") != "same" and (biggest is None or abs(vb - va) > abs(biggest[1])):
            biggest = (part, vb - va)
    if biggest:
        text += f"; largest change: {biggest[0]} {biggest[1]:+.1f} ms per window"
    ha, hb = da.get("hit_rate"), db.get("hit_rate")
    if ha is not None and hb is not None and abs(hb - ha) > hl["value"] + 1e-12:
        text += f"; hit rate {ha * 100:.1f}% → {hb * 100:.1f}%"
    if fl["assumed"]:
        text += f" ({ASSUMED_NOTE})"
    return {"group": "decode", "key": kind, "state": state, "text": text, "floor_note": _note(fl)}


def _verdict_prompt(label, pa, pb, fl):
    if not pa or not pb or pa.get("prompt_tps") is None or pb.get("prompt_tps") is None:
        return {"group": "prompt", "key": label, "state": "missing", "text": f"{label} read: not in both reports",
                "floor_note": None}
    a, b = pa["prompt_tps"], pb["prompt_tps"]
    pct = (b - a) / a * 100 if a else None
    F = fl["value"]
    state = _state(b - a, pct, F, "higher")
    if state == "same":
        text = (f"{label} read: no measurable change ({pct:+.1f}%, inside the {_pct_text(F)} "
                f"{'floor' if fl['assumed'] else 'noise floor'})" if pct is not None
                else f"{label} read: no measurable change")
    else:
        word = "faster" if state == "better" else "slower"
        text = (f"{label} read: {abs(pct):.1f}% {word}, {a:,.0f} → {b:,.0f} tok/s"
                + ("" if fl["assumed"] else f" (noise floor {_pct_text(F)})"))
    if fl["assumed"]:
        text += f" ({ASSUMED_NOTE})"
    return {"group": "prompt", "key": label, "state": state, "text": text, "floor_note": _note(fl)}


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


def compute(a: dict, b: dict, pool: list[dict] | None = None) -> dict:
    """`a` and `b`: normalised reports (reports.normalise).  A is the before, B the after.  `pool`: every ok report
    in the folder as {"rep", "short", "order"} (ReportStore.pool()); the floors come from same-settings pairs in it."""
    noise = Noise(a, b, pool)
    rows: list[dict] = []
    verdicts: list[dict] = []
    da, db = {d["kind"]: d for d in a.get("decode") or []}, {d["kind"]: d for d in b.get("decode") or []}
    for kind in _ordered_keys(a.get("decode"), b.get("decode"), "kind"):
        x, y = da.get(kind), db.get(kind)
        tps_fl = noise.floor(("decode", kind, "decode tok/s"), "rel")
        hit_fl = noise.floor(("decode", kind, "expert cache hit rate (points)"), "pts")
        verdicts.append(_verdict_decode(kind, x, y, tps_fl, noise, hit_fl))
        g = "decode"
        rows.append(_row(g, kind, "decode tok/s", ((x or {}).get("tps") or {}).get("median"),
                         ((y or {}).get("tps") or {}).get("median"), "higher", tps_fl, "rel"))
        if x and y:
            rows.append(_row(g, kind, "expert cache hit rate (points)", x.get("hit_rate"), y.get("hit_rate"),
                             "higher", hit_fl, "pts"))
            rows.append(_row(g, kind, "read over PCIe (points)", x.get("pcie_share"), y.get("pcie_share"), None,
                             noise.floor((g, kind, "expert cache hit rate (points)"), "pts"), "pts", tinted=False))
            rows.append(_row(g, kind, "ms per window", x.get("ms_per_window"), y.get("ms_per_window"), "lower",
                             noise.floor((g, kind, "ms per window"), "rel"), "rel"))
            wa, wb = x.get("window_ms") or {}, y.get("window_ms") or {}
            for part in [p for p in DECODE_PARTS if p in wa or p in wb] + \
                    [p for p in list(wa) + list(wb) if p not in DECODE_PARTS]:
                rows.append(_row(g, kind, f"{part} (ms per window)", wa.get(part), wb.get(part), "lower",
                                 noise.floor((g, kind, f"{part} (ms per window)"), "ms"), "ms"))
            rows.append(_row(g, kind, "tokens per window", x.get("tokens_per_window"), y.get("tokens_per_window"),
                             "higher", noise.floor((g, kind, "tokens per window"), "rel"), "rel"))
    pa, pb = {p["label"]: p for p in a.get("prompts") or []}, {p["label"]: p for p in b.get("prompts") or []}
    for label in _ordered_keys(a.get("prompts"), b.get("prompts"), "label"):
        x, y = pa.get(label), pb.get(label)
        fl = noise.floor(("prompt", label, "prompt tok/s"), "rel")
        verdicts.append(_verdict_prompt(label, x, y, fl))
        rows.append(_row("prompt", label, "prompt tok/s", (x or {}).get("prompt_tps"), (y or {}).get("prompt_tps"),
                         "higher", fl, "rel"))
        if x and y:
            rows.append(_row("prompt", label, "GPU time (ms)", x.get("gpu_ms"), y.get("gpu_ms"), "lower",
                             noise.floor(("prompt", label, "GPU time (ms)"), "rel"), "rel"))
    ta, tb = {t["turn"]: t for t in a.get("agent") or [] if t.get("turn") is not None}, \
        {t["turn"]: t for t in b.get("agent") or [] if t.get("turn") is not None}
    ups = downs = both = 0
    for turn in sorted(set(ta) | set(tb), key=lambda t: (not isinstance(t, (int, float)), t)):
        x, y = ta.get(turn), tb.get(turn)
        g = "agent"
        fl = noise.floor((g, turn, "decode tok/s"), "rel")
        if x and y:
            rows.append(_row(g, turn, "decode tok/s", x.get("decode_tps"), y.get("decode_tps"), "higher", fl, "rel"))
            rows.append(_row(g, turn, "prompt tok/s", x.get("prompt_tps"), y.get("prompt_tps"), "higher",
                             noise.floor((g, turn, "prompt tok/s"), "rel"), "rel"))
            rows.append(_row(g, turn, "expert cache hit rate (points)", x.get("hit_rate"), y.get("hit_rate"),
                             "higher", noise.floor((g, turn, "expert cache hit rate (points)"), "pts"), "pts"))
            rows.append(_row(g, turn, "ms per window", x.get("ms_per_window"), y.get("ms_per_window"), "lower",
                             noise.floor((g, turn, "ms per window"), "rel"), "rel"))
            if x.get("decode_tps") is not None and y.get("decode_tps") is not None:
                both += 1
                ups += y["decode_tps"] > x["decode_tps"]
                downs += y["decode_tps"] < x["decode_tps"]
        else:
            rows.append(_row(g, turn, "decode tok/s", (x or {}).get("decode_tps"), (y or {}).get("decode_tps"),
                             "higher", fl, "rel"))
    if both >= SIGN_MIN_TURNS and (ups == both or downs == both):         # a sign test, inside the floor too
        sentence = f"{both} of {both} agent turns {'faster' if ups == both else 'slower'}"
        v = next((v for v in verdicts if v["group"] == "decode" and v["key"] == "agent turns"), None)
        if v and v["state"] != "missing":
            v["text"] = v["text"].replace(f" ({ASSUMED_NOTE})", "") + f"; {sentence}" + \
                (f" ({ASSUMED_NOTE})" if f" ({ASSUMED_NOTE})" in v["text"] else "")
        else:
            verdicts.append({"group": "agent", "key": "turns", "state": "same", "text": sentence, "floor_note": None})
    floors = {"minimum": MIN_REL, "assumed": {"tps": ASSUMED_TPS, "ms_part": ASSUMED_MS, "hit_points": ASSUMED_HIT},
              "pairs": noise.pairs}
    return {"a": a.get("id"), "b": b.get("id"), "warnings": warnings_for(a, b), "floors": floors,
            "verdicts": verdicts, "rows": rows, "settings": settings_diff(a, b)}
