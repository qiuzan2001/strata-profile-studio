"""A demo Strata: tests/fakestrata.py fed with recorded data, for the page check and for screenshots of every
state without a GPU.  No port of the real Strata is touched.

    python3 tests/demo_strata.py --port 8195 --results /tmp/demo-results [--seed DIR] [--live live.json]
                                 [--hardware hardware.json] [--settings settings.json] [--state STATE]

--seed DIR       copy every profile report folder of DIR into --results first (copies keep their mtimes)
--live FILE      a Studio /api/live answer: its samples become Strata's /profile/history, the newest /profile/last
--hardware FILE  a Studio /api/hardware answer: /metrics' hardware blocks and the engine block
--settings FILE  a Studio /api/settings answer: served back as Strata's /profile/settings
--state          ok (default) | first-run | profile-off | key | unloaded | tuning-elsewhere | decoding
--tune-secs N    how long a tune takes (its output is the real calibration's, tests/fixtures/real_tune.json)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakestrata import FakeStrata                   # noqa: E402

REPORT_PREFIX = "-profile-"


def last_doc(s: dict) -> dict:
    """A Studio sample back into the shape of Strata's /profile/last `request`."""
    return {
        "summary": {k: s.get(k) for k in ("prompt_tokens", "reused", "read", "prompt_ms", "prompt_tok_s", "generated",
                                          "decode_ms", "decode_tok_s", "drafts_accepted", "drafts_offered")},
        "prompt": {"tokens": s.get("prompt_tokens"), "reused": s.get("reused"), "read": s.get("read"),
                   "tok_s": s.get("prompt_tok_s"), "ms": s.get("prompt_ms"),
                   "groups": ({"ms": s["prompt_groups_ms"], "share": {}, "gpu_ms": s.get("prompt_gpu_ms"),
                               "tokens": s.get("read"), "top": s.get("prompt_top") or []}
                              if s.get("prompt_groups_ms") else None), "host": s.get("prompt_host")},
        "decode": {"generated": s.get("generated"), "tok_s": s.get("decode_tok_s"), "ms": s.get("decode_ms"),
                   "groups": ({"ms": s["window_ms"], "share": {}, "ms_per_window": s.get("ms_per_window"),
                               "tokens_per_window": s.get("tokens_per_window"), "windows": s.get("windows"),
                               "cpu_entries": s.get("cpu_entries"), "cpu_experts": s.get("cpu_experts"),
                               "vram_hits": s.get("vram_hits"), "pcie": s.get("pcie_per_window"),
                               "swaps": s.get("swaps")} if s.get("window_ms") else None),
                   "drafts_accepted": s.get("drafts_accepted"), "drafts_offered": s.get("drafts_offered")},
        "hit": ({k: s.get(k) for k in ("hit_rate", "hits", "lookups", "pcie_share", "offloaded", "routed")}
                if s.get("hit_rate") is not None else None),
        "kv": {"kv_hit": s.get("kv_hit"), "block_reads": s.get("kv_block_reads"), "ram_mib": s.get("kv_ram_mib")}
        if s.get("kv_hit") is not None else None,
        "gpu_stages": s.get("gpu_stages"),
    }


def history_entry(i: int, s: dict) -> dict:
    return {"n": i + 1, "seen_at": s.get("seen_at"), "open": False,
            **{k: s.get(k) for k in ("prompt_tokens", "reused", "read", "prompt_tok_s", "prompt_ms", "prompt_gpu_ms",
                                     "generated", "decode_tok_s", "decode_ms", "ms_per_window", "tokens_per_window",
                                     "hit_rate", "pcie_share", "kv_hit", "cpu_experts", "drafts_offered",
                                     "drafts_accepted")},
            "ms": s.get("window_ms"),
            "prompt_top": (s.get("prompt_top") or [[None]])[0][0] if s.get("prompt_top") else None}


def settings_doc(doc: dict) -> dict:
    return {"file": doc.get("file"), "loaded": doc.get("loaded", True),
            "settings": [{"id": r["id"], "name": r["name"], "kind": "env" if r["name"].isupper() else "arg",
                          "loaded_now": r.get("now"), "next_load": r.get("next_load"), "run_config": r.get("run_config"),
                          "calibrated": r.get("calibrated"), "edit": None, "about": r.get("about")}
                         for r in doc.get("rows") or []]}


class DemoStrata(FakeStrata):
    """FakeStrata whose profile runs write copies of real reports (round robin over `sources`)."""

    def __init__(self, results_dir, sources=(), **kw):
        super().__init__(results_dir, **kw)
        self.sources = [Path(p) for p in sources]

    def _finish_run(self, n: int):
        if not self.sources:
            return super()._finish_run(n)
        time.sleep(self.run_secs)
        src = self.sources[(n - 1) % len(self.sources)]
        base = src.name.rsplit("-", 1)[0] if src.name.rsplit("-", 1)[-1].isdigit() else src.name
        suffixes = [int(p.name.rsplit("-", 1)[1]) for p in self.results_dir.iterdir()
                    if p.name.startswith(base + "-") and p.name.rsplit("-", 1)[1].isdigit()]
        dest = self.results_dir / f"{base}-{max(suffixes + [1]) + 1}"
        shutil.copytree(src, dest)
        os.utime(dest / "results.json")                # a new run: it ended now
        results = json.loads((dest / "results.json").read_text())
        with self.lock:
            self.run.update(state="done", returncode=0, elapsed_s=round(time.time() - self.run["started_at"], 1),
                            lines=self.run["lines"] + [f"Report: {dest}/README.md"],
                            report={"path": str(dest), "readme": "# demo", "results": results})
            self.runs_done += 1


def build(a) -> DemoStrata:
    results = Path(a.results)
    results.mkdir(parents=True, exist_ok=True)
    sources = []
    if a.seed and a.state != "no-reports":
        for p in sorted(Path(a.seed).iterdir()):
            if REPORT_PREFIX in p.name and (p / "results.json").is_file() and not (results / p.name).exists():
                shutil.copytree(p, results / p.name)
        sources = sorted(p for p in results.iterdir() if REPORT_PREFIX in p.name)[-2:]
    fake = DemoStrata(results, sources=sources, key="demo-key" if a.state == "key" else None,
                      profile=a.state != "profile-off", history=True, settings=bool(a.settings),
                      tune_outcome="real", tune_secs=a.tune_secs, run_secs=a.run_secs, reload_503s=2,
                      host="127.0.0.1", port=a.port)
    samples = []
    if a.live and a.state not in ("first-run", "profile-off"):
        samples = [s for s in json.loads(Path(a.live).read_text())["samples"] if not s.get("late")]
        samples.sort(key=lambda s: s["seen_at"])
    fake.history_entries = [history_entry(i, s) for i, s in enumerate(samples[-50:])]
    fake.last = last_doc(samples[-1]) if samples else None
    if a.state == "profile-off" and a.live:
        for s in json.loads(Path(a.live).read_text())["samples"][-30:]:
            fake.metrics_requests.insert(0, {"time": s["seen_at"] - 3, "duration_s": 3.0, "prompt_tokens": s["prompt_tokens"],
                                             "reused": s["reused"], "prompt_read": s["read"], "prompt_ms": s["prompt_ms"],
                                             "decode_ms": s["decode_ms"], "decode_tok_s": s["decode_tok_s"],
                                             "output_tokens": s["generated"], "hit_rate": s["hit_rate"],
                                             "pcie_share": s["pcie_share"]})
    if a.hardware:
        hw = json.loads(Path(a.hardware).read_text())
        fake.hardware, fake.hardware_static, fake.hw_history = hw["now"], hw["static"], hw["history"]
        fake.engine.update({k: v for k, v in hw["engine"].items() if v is not None})
    if a.settings:
        fake.settings_doc = settings_doc(json.loads(Path(a.settings).read_text()))
    if a.state == "unloaded":
        fake.loaded = False
    return fake


def animate(fake: DemoStrata, decoding: bool, stop: threading.Event):
    """Moves the hardware readings once a second; `decoding`: as if a long decode were running."""
    base = dict(fake.hardware)
    t0 = time.time()
    while not stop.wait(1.0):
        t = time.time() - t0
        wave = 0.5 + 0.5 * math.sin(t / 3)
        with fake.lock:
            hw = fake.hardware
            if decoding:
                hw.update(gpu_util=38 + 10 * wave, cpu=61 + 8 * wave, gpu_power=168 + 20 * wave, gpu_temp=58,
                          gpu_pcie_gen=4, gpu_pcie_rx_mb=1650 + 500 * wave, gpu_pcie_tx_mb=90 + 20 * wave)
                fake.live_state = {"state": "generating", "tok_s": round(76 + 6 * wave, 1)}
            else:
                hw.update({k: base.get(k) for k in ("gpu_util", "cpu", "gpu_power", "gpu_pcie_rx_mb", "gpu_pcie_tx_mb")})
            for k in ("gpu_util", "cpu", "gpu_pcie_rx_mb", "gpu_power"):
                seq = fake.hw_history.setdefault(k, [])
                seq.append(hw.get(k))
                del seq[:-60]


def main(argv=None):
    ap = argparse.ArgumentParser(description="A demo Strata fed with recorded data (no GPU).")
    ap.add_argument("--port", type=int, default=8195)
    ap.add_argument("--results", required=True)
    ap.add_argument("--seed")
    ap.add_argument("--live")
    ap.add_argument("--hardware")
    ap.add_argument("--settings")
    ap.add_argument("--state", default="ok")
    ap.add_argument("--tune-secs", type=float, default=40.0)
    ap.add_argument("--run-secs", type=float, default=6.0)
    a = ap.parse_args(argv)
    fake = build(a).start()
    stop = threading.Event()
    threading.Thread(target=animate, args=(fake, a.state == "decoding", stop), daemon=True).start()
    if a.state == "tuning-elsewhere":                  # a tune started from Strata's own page, not the Studio
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(f"{fake.url}/profile/tune", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}))
    print(f"demo Strata on {fake.url} ({a.state})", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        fake.stop()


if __name__ == "__main__":
    main()
