"""Profile reports on disk: the folder scan, results.json normalisation and the settings each report ran with.

Read only.  A report is a folder `<date>-profile-<gpu>-<model>[-N]` that tools/strata_profile.py wrote into
bench/results; the Studio never writes there.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .requests import report_requests

REPORT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-profile-[a-z0-9.-]+$")
MAX_REPORTS = 500
MAX_RESULTS_BYTES = 16 * 1024 * 1024
UNREADABLE = "results.json could not be read"
NOTE_MAX = 2000

# the flags a report's settings table lists, in order: (flag, key in machine.engine_info, how it is shown)
SETTING_FLAGS = (
    ("--pcie-frac", "pcie_frac"), ("--spec-min-p", "spec_min_p"), ("--pool-workers", "pool_workers"),
    ("--adapt-every", None), ("--adapt-swaps", None), ("--adapt-decay", None),
    ("--kv-resident", "kv_resident"), ("--max-context", "context"), ("--expert-cache", "expert_cache"),
    ("--spec", None), ("--vram-reserve-mib", None),
)
ENV_FLAGS = ("STRATA_PREFILL_CPU_SHARE", "STRATA_PREFILL_CPU_SHARE_MAX")


class ReportError(Exception):
    """A report that does not exist (404) or cannot be read (404, `unreadable`)."""

    def __init__(self, message: str, unreadable: bool = False):
        super().__init__(message)
        self.unreadable = unreadable


def arg_value(args, flag):
    """calibrate.py's rule: the item after `flag`, or None."""
    args = list(args or [])
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def _get(d, *path):
    """d[path[0]][path[1]]... with None for anything missing or not a dict on the way."""
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _str(x):
    return None if x is None else str(x)


def _list(x):
    return x if isinstance(x, list) else []


def _dict(x):
    return x if isinstance(x, dict) else {}


def _number_text(v):
    """A setting's value as text: 7 -> "7", 0.55 -> "0.55", "0.55" -> "0.55"; None stays None."""
    if v is None or isinstance(v, bool):
        return None if v is None else str(v)
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


# ----------------------------------------------------------------------------------------------- settings rows
def settings_rows(machine: dict) -> list[dict] | None:
    """The settings table of a report from `machine.config.args`/`env` and `machine.engine_info`; None when the
    report has neither block."""
    cfg = machine.get("config") if isinstance(machine.get("config"), dict) else None
    info = machine.get("engine_info") if isinstance(machine.get("engine_info"), dict) else None
    if cfg is None and info is None:
        return None
    args = cfg.get("args") if cfg else None
    rows = []
    for flag, key in SETTING_FLAGS:
        loaded = None
        if info:
            if key == "expert_cache":
                if info.get("expert_slots") is not None:
                    loaded = f"{_number_text(info.get('expert_slots'))} experts ({_number_text(info.get('expert_cache_mib'))} MiB)"
            elif key:
                loaded = _number_text(info.get(key))
        rows.append({"flag": flag, "config": _number_text(arg_value(args, flag)) if args is not None else None,
                     "loaded": loaded})
    env = cfg.get("env") if cfg and isinstance(cfg.get("env"), dict) else None
    if env is not None:
        for name in ENV_FLAGS:
            rows.append({"flag": name, "config": _number_text(env.get(name)), "loaded": None})
    return rows


def settings_short(rows, has_args: bool | None = None) -> dict:
    """Four chips: PCIe share, draft floor, workers, and the expert tier ("engine default" or every/swaps/decay).
    `has_args`: whether the run config's arguments are known (default: when any row has a config value)."""
    by = {r["flag"]: r for r in rows or []}
    if has_args is None:
        has_args = any(r.get("config") is not None for r in rows or [])

    def shown(flag):
        r = by.get(flag) or {}
        return r.get("loaded") if r.get("loaded") is not None else r.get("config")
    adapt = None
    if rows and has_args:
        parts = [by.get(f, {}).get("config") for f in ("--adapt-every", "--adapt-swaps", "--adapt-decay")]
        adapt = "engine default" if not any(p is not None for p in parts) else \
            "/".join(p if p is not None else "default" for p in parts)
    return {"pcie_frac": shown("--pcie-frac"), "spec_min_p": shown("--spec-min-p"),
            "pool_workers": shown("--pool-workers"), "adapt": adapt}


# ------------------------------------------------------------------------------------------- normalisation
def chat_runs(results: dict) -> list[dict]:
    out = []
    for r in _list(_get(results, "chat", "requests")):
        r = _dict(r)
        out.append({"decode_tps": _num(r.get("decode_tps")), "hit_rate": _num(_get(r, "engine", "hit", "hit_rate"))})
    return out


def machine_gpu(machine: dict):
    gpu = machine.get("gpu")
    if isinstance(gpu, list):
        return ", ".join(str(g) for g in gpu) or None
    return _str(gpu)


def normalise(raw, report_id: str, path: str, meta: dict | None = None) -> dict:
    """The report as the page and the comparison read it.  Every key can be None or empty; none is an error."""
    raw = _dict(raw)
    machine = _dict(raw.get("machine"))
    results = _dict(raw.get("results"))
    params = _dict(raw.get("params"))
    rows = settings_rows(machine)
    source = "report"
    if rows is None and meta and isinstance(meta.get("rows"), list):
        rows, source = [r for r in meta["rows"] if isinstance(r, dict)], "captured"
    if rows is None:
        rows, source = [], "none"
    prompts = []
    for label, p in _dict(results.get("prompts")).items():
        p = _dict(p)
        prompts.append({"label": label, "tokens": _num(p.get("prompt_tokens")), "prompt_tps": _num(p.get("prompt_tps")),
                        "gpu_ms": _num(_get(p, "groups", "gpu_ms")), "groups_ms": _dict(_get(p, "groups", "ms")) or None,
                        "groups_share": _dict(_get(p, "groups", "share")) or None})
    decode = []
    for kind, d in _dict(results.get("decode")).items():
        d = _dict(d)
        tps = _dict(d.get("decode_tps"))
        decode.append({"kind": kind, "context": _num(d.get("context")),
                       "tps": {"median": _num(tps.get("median")), "min": _num(tps.get("min")),
                               "max": _num(tps.get("max")), "n": _num(tps.get("n"))},
                       "hit_rate": _num(_get(d, "hit", "hit_rate")), "pcie_share": _num(_get(d, "hit", "pcie_share")),
                       "ms_per_window": _num(_get(d, "groups", "ms_per_window")),
                       "tokens_per_window": _num(_get(d, "groups", "tokens_per_window")),
                       "window_ms": _dict(_get(d, "groups", "ms")) or None,
                       "cpu_experts": _num(_get(d, "groups", "cpu_experts")),
                       "cpu_entries": _num(_get(d, "groups", "cpu_entries"))})
    agent = []
    for t in _list(_get(results, "agent", "turns")):
        t = _dict(t)
        hit = _num(_get(t, "hit", "hit_rate"))
        if hit is None:
            hit = _num(_get(t, "engine", "hit", "hit_rate"))
        agent.append({"turn": t.get("turn"), "prompt_tokens": _num(t.get("prompt_tokens")),
                      "reused": _num(t.get("reused_tokens")), "read": _num(t.get("read_tokens")),
                      "prompt_tps": _num(t.get("prompt_tps")), "decode_tps": _num(t.get("decode_tps")),
                      "hit_rate": hit, "ms_per_window": _num(_get(t, "groups", "ms_per_window")),
                      "window_ms": _dict(_get(t, "groups", "ms")) or None})
    runs = chat_runs(results)
    return {"id": report_id, "path": path, "date": _str(raw.get("date")) or report_id[:10],
            "machine": {"gpu": machine_gpu(machine), "gpu_vram_mib": _num(machine.get("gpu_vram_mib")),
                        "cpu": _str(machine.get("cpu")), "ram_gib": _num(machine.get("ram_gib")),
                        "model": _str(machine.get("model")), "quant": _str(machine.get("quant")),
                        "context": _num(machine.get("context")), "strata_version": _str(machine.get("strata_version")),
                        "engine_version": _str(machine.get("engine_version"))},
            "environment": environment(machine),
            "params": {"long": _num(params.get("long")), "verify_profile": params.get("verify_profile")},
            "elapsed_s": _num(raw.get("elapsed_s")),
            "settings": {"source": source, "rows": rows},
            "prompts": prompts, "decode": decode, "agent": agent, "chat_runs": runs,
            "requests": report_requests(results),
            "findings": [str(f) for f in _list(results.get("findings"))],
            "skipped": _dict(results.get("skipped")),
            "failures": [f if isinstance(f, (str, dict)) else str(f) for f in _list(results.get("failures"))],
            "note": "", "tune": None}


ENV_KEYS = (("gpu_driver", "machine"), ("cuda", "machine"), ("cpu_cores", "machine"), ("cpu_threads", "machine"),
            ("os", "machine"), ("python", "machine"), ("strata_commit", "machine"), ("parallel_slots", "machine"),
            ("launch", "machine"), ("kv", "engine_info"), ("kv_resident", "engine_info"),
            ("expert_slots", "engine_info"), ("expert_cache_mib", "engine_info"), ("spec", "engine_info"),
            ("mtp_max", "engine_info"), ("vram_free_mib", "engine_info"), ("arena_mib", "engine_info"),
            ("pool_workers", "engine_info"))


def environment(machine: dict) -> dict:
    """The rest of the machine block: driver, CUDA, cores, OS, commit, launch line and the engine's sizes."""
    info = _dict(machine.get("engine_info"))
    out = {}
    for key, where in ENV_KEYS:
        v = (machine if where == "machine" else info).get(key)
        out[key] = v if isinstance(v, (int, float, str)) and not isinstance(v, bool) else None
    return out


def headline(rep: dict) -> dict:
    """The few numbers a run is ranked by: decode tok/s and hit rate per workload, prompt tok/s per read, decode
    tok/s per agent turn."""
    return {"decode": {d["kind"]: d["tps"]["median"] for d in rep["decode"]},
            "hit": {d["kind"]: d["hit_rate"] for d in rep["decode"]},
            "window": {d["kind"]: d["ms_per_window"] for d in rep["decode"]},
            "prompt": {p["label"]: p["prompt_tps"] for p in rep["prompts"]},
            "agent": [t["decode_tps"] for t in rep["agent"]]}


def list_entry(rep: dict, order: list, short: dict, ctx) -> dict:
    """One card of the report list from a normalised report and its settings chips."""
    chat = next((d for d in rep["decode"] if d["kind"] == "chat"), None)
    m = rep["machine"]
    return {"id": rep["id"], "date": rep["date"], "order": order, "gpu": m["gpu"], "model": m["model"],
            "quant": m["quant"], "context": m["context"], "strata_version": m["strata_version"],
            "elapsed_s": rep["elapsed_s"], "chat_tps": chat["tps"]["median"] if chat else None, "ok": True,
            "headline": headline(rep), "mtime": order[4] if len(order) > 4 else None,
            "error": None, "settings_short": short,
            "tune": ctx.tune_role(rep["id"]) if ctx else None, "note": ctx.note(rep["id"]) if ctx else ""}


class NoContext:
    """Stands in for the state when the store is used alone (tests, tools)."""

    def tune_role(self, report_id):
        return None

    def note(self, report_id):
        return ""

    def meta(self, report_id):
        return None


# ------------------------------------------------------------------------------------------------- the store
class ReportStore:
    """The results folder.  `scan()` lists, `get()` returns one normalised report; both cache by (path, mtime,
    size), so a scan every 10 s costs stats, not parses."""

    def __init__(self, results_dir, ctx=None):
        self.dir = Path(results_dir)
        self.ctx = ctx or NoContext()
        self._lock = threading.Lock()
        self._parsed: dict = {}                       # id -> (key, (normalised without state, settings chips))

    def exists(self) -> bool:
        return self.dir.is_dir()

    # --- paths
    def folder(self, report_id: str) -> Path | None:
        """The report's folder, only when the id is well formed and the folder lies directly in --results."""
        if not isinstance(report_id, str) or not REPORT_ID_RE.match(report_id) or ".." in report_id:
            return None
        p = self.dir / report_id
        try:
            if p.is_symlink() or not p.is_dir():
                return None
            if p.resolve().parent != self.dir.resolve():
                return None
        except OSError:
            return None
        return p

    def _results_file(self, folder: Path):
        f = folder / "results.json"
        try:
            if f.is_symlink() or not f.is_file():
                return None, None
            if f.resolve().parent != folder.resolve():
                return None, None
            return f, f.stat()
        except OSError:
            return None, None

    def _load(self, report_id: str, folder: Path, f: Path, st):
        """The normalised report (without state), or None when results.json is too large or does not parse."""
        key = (str(f), st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._parsed.get(report_id)
        if hit and hit[0] == key:
            return hit[1]
        rep = None
        short = None
        if st.st_size <= MAX_RESULTS_BYTES:
            try:
                with open(f, "rb") as fh:
                    raw = json.loads(fh.read(MAX_RESULTS_BYTES + 1).decode("utf-8-sig"))
                if isinstance(raw, dict):
                    rep = normalise(raw, report_id, str(folder))
                    short = settings_short(rep["settings"]["rows"])
            except (OSError, ValueError):
                rep = None
        with self._lock:
            self._parsed[report_id] = (key, (rep, short))
        return rep, short

    # --- the list
    def scan(self) -> dict:
        out = {"dir": str(self.dir), "exists": self.exists(), "reports": []}
        if not out["exists"]:
            return out
        found = {}
        try:
            names = os.listdir(self.dir)
        except OSError:
            return out
        for name in names:
            if not REPORT_ID_RE.match(name):
                continue
            folder = self.folder(name)
            if folder is None:
                continue
            f, st = self._results_file(folder)
            if f is None:
                continue
            found[name] = (folder, f, st)
        entries = []
        for name, (folder, f, st) in found.items():
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})-profile-.+?(?:-(\d+))?$", name)
            n = 1
            if m and m.group(4):
                base = name[: -(len(m.group(4)) + 1)]
                if base in found and int(m.group(4)) >= 2:
                    n = int(m.group(4))
            dm = re.match(r"^(\d{4})-(\d{2})-(\d{2})", name)
            order = [int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), n, st.st_mtime]
            entries.append((order, name))
        entries.sort(key=lambda e: e[0], reverse=True)
        with self._lock:                               # forget what left the folder
            for gone in [k for k in self._parsed if k not in found]:
                del self._parsed[gone]
        for order, name in entries[:MAX_REPORTS]:
            folder, f, st = found[name]
            rep, short = self._load(name, folder, f, st)
            if rep is None:
                out["reports"].append({"id": name, "date": name[:10], "order": order, "gpu": None, "model": None,
                                       "quant": None, "context": None, "strata_version": None, "elapsed_s": None,
                                       "chat_tps": None, "ok": False, "error": UNREADABLE, "headline": None,
                                       "mtime": order[4],
                                       "settings_short": {"pcie_frac": None, "spec_min_p": None, "pool_workers": None,
                                                          "adapt": None},
                                       "tune": self.ctx.tune_role(name), "note": self.ctx.note(name)})
                continue
            rep, short = self._with_meta(rep, short)
            out["reports"].append(list_entry(rep, order, short, self.ctx))
        return out

    def _with_meta(self, rep: dict, short: dict) -> tuple[dict, dict]:
        """The settings captured at run time, for a report that carries no config of its own."""
        if rep["settings"]["source"] != "none":
            return rep, short
        meta = self.ctx.meta(rep["id"])
        if meta and isinstance(meta.get("rows"), list):
            rows = [r for r in meta["rows"] if isinstance(r, dict)]
            rep = dict(rep)
            rep["settings"] = {"source": "captured", "rows": rows}
            short = settings_short(rows, True)
        return rep, short

    # --- one report
    def get(self, report_id: str) -> dict:
        folder = self.folder(report_id)
        if folder is None:
            raise ReportError("There is no such report.")
        f, st = self._results_file(folder)
        if f is None:
            raise ReportError("There is no such report.")
        rep, short = self._load(report_id, folder, f, st)
        if rep is None:
            raise ReportError(UNREADABLE, unreadable=True)
        rep = dict(self._with_meta(rep, short)[0])
        rep["note"] = self.ctx.note(report_id)
        rep["tune"] = self.ctx.tune_role(report_id)
        return rep

    def pool(self) -> list[dict]:
        """Every ok report in the folder as {"id", "rep", "short", "order"}: what the comparison measures its noise
        floor from."""
        out = []
        for r in self.scan()["reports"]:
            if not r["ok"]:
                continue
            folder = self.folder(r["id"])
            f, st = self._results_file(folder) if folder else (None, None)
            if f is None:
                continue
            rep, short = self._load(r["id"], folder, f, st)
            if rep is None:
                continue
            rep, short = self._with_meta(rep, short)
            out.append({"id": r["id"], "rep": rep, "short": short, "order": r["order"]})
        return out

    def ok_ids(self) -> list[str]:
        return [r["id"] for r in self.scan()["reports"] if r["ok"]]
