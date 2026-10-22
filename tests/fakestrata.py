"""An in-process stand-in for Strata's HTTP routes, for the tests (and for trying the Studio with no GPU).

It serves /health, /metrics, /profile/last, /profile/status, /profile/run, /profile/tune and /v1/chat/completions,
and optionally /profile/history and /profile/settings, with the answers' shapes read from Strata's serve/profile_page.py.
Every request's method, path, headers and body is recorded in `.requests`.

    python3 tests/fakestrata.py --port 8191 --results /tmp/results     # a Strata that keeps producing requests
"""
from __future__ import annotations

import argparse
import copy
import http.server
import json
import shutil
import threading
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"
RUN_FIXTURES = ("2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s", "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s-2")

TUNE_LINES = [
    "",
    "  Tuning Strata for this PC: the output speed is measured with a few engine settings (the PCIe share, the",
    "  Loading the model for the measurements ...",
    "    round 1/3 PCIe share: 0 71.2  0.2 80.4  0.35 95.1  0.55 88.0  0.75 86.1  0.9 83.0  1 79.9",
    "    round 2/3 PCIe share: 1 79.9  0.9 82.2  0.75 85.0  0.55 87.1  0.35 94.0  0.2 79.9  0 70.2",
    "    round 3/3 PCIe share: 0 71.0  0.2 81.0  0.35 96.2  0.55 88.4  0.75 86.3  0.9 83.9  1 80.5",
    "    PCIe share, median of 3: 0: 71.0 (70.2-71.2), 0.2: 80.4 (79.9-81.0), 0.35: 95.1 (94.0-96.2), "
    "0.55: 88.0 (87.1-88.4), 0.75: 86.1 (85.0-86.3), 0.9: 83.0 (82.2-83.9), 1: 79.9 (79.0-80.5) -> best 0.35",
    "    round 1/3 draft floor: 0.3 90.1  0.5 94.9  0.7 96.0",
    "    round 2/3 draft floor: 0.7 95.8  0.5 94.7  0.3 90.4",
    "    round 3/3 draft floor: 0.3 90.0  0.5 95.0  0.7 96.4",
    "    draft floor, median of 3: 0.3: 90.1 (90.0-90.4), 0.5: 94.9 (94.7-95.0), 0.7: 96.0 (95.8-96.4) -> best 0.7",
    "  Measuring with 7 CPU workers (restarts the engine) ...",
    "    7 workers: 95.0 tok/s",
    "  Measuring with 5 CPU workers (restarts the engine) ...",
    "    5 workers: 93.1 tok/s",
    "  Measuring the expert tier (the engine default) (restarts the engine) ...",
    "    87.0 tok/s",
    "  Measuring the expert tier every 1, 160 swaps, decay 0.97 (restarts the engine) ...",
    "    96.6 tok/s",
    "  [ok] tuned for this PC: --pcie-frac 0.35, --spec-min-p 0.70, --adapt-every 1, --adapt-swaps 160, "
    "--adapt-decay 0.97 (96.6 tok/s)",
]
FAIL_LINES = TUNE_LINES[:3] + [
    "    the engine did not start for the first value: out of memory - this candidate is dropped, "
    "the earlier results are kept",
    "  [!]  the tuning did not finish (the engine did not start): the default settings stay",
]
CHANGES = [
    {"flag": "--pcie-frac", "before": None, "before_loaded": "0.55", "after": "0.35", "changed": True},
    {"flag": "--spec-min-p", "before": "0.5", "before_loaded": "0.50", "after": "0.70", "changed": True},
    {"flag": "--pool-workers", "before": None, "before_loaded": "7", "after": None, "changed": False},
    {"flag": "--adapt-every", "before": None, "before_loaded": None, "after": "1", "changed": True},
    {"flag": "--adapt-swaps", "before": None, "before_loaded": None, "after": "160", "changed": True},
    {"flag": "--adapt-decay", "before": None, "before_loaded": None, "after": "0.97", "changed": True},
]

REAL = json.loads((Path(__file__).resolve().parent / "fixtures" / "real_tune.json").read_text())
REAL_CHANGES = [
    {"flag": "--pcie-frac", "before": "0.35", "before_loaded": "0.35", "after": None, "changed": True},
    {"flag": "--spec-min-p", "before": "0.70", "before_loaded": "0.70", "after": "0.5", "changed": True},
    {"flag": "--pool-workers", "before": "7", "before_loaded": "7", "after": None, "changed": False},
    {"flag": "--adapt-every", "before": "1", "before_loaded": None, "after": "1", "changed": False},
    {"flag": "--adapt-swaps", "before": "160", "before_loaded": None, "after": "80", "changed": True},
    {"flag": "--adapt-decay", "before": "0.97", "before_loaded": None, "after": "0.97", "changed": False},
]

ENGINE = {"model": "swift-1.5-iq3_s", "max_context": 262144, "context": 262144, "kv_resident": 32768,
          "expert_slots": 3880, "expert_cache_mib": 7586, "pool_workers": 7, "pcie_frac": "0.55", "spec_min_p": "0.50"}


def request_doc(prompt_tokens=2023, generated=24, prompt_ms=1809.0, decode_ms=377.0, hit=0.706, adapt=1.44,
                with_phases=True, with_hit=True):
    """A /profile/last `request` in the shape serve.profile_page.last_request returns."""
    read = prompt_tokens
    return {
        "summary": {"prompt_tokens": prompt_tokens, "reused": 0, "read": read, "prompt_ms": prompt_ms,
                    "prompt_tok_s": round(read / prompt_ms * 1000, 1), "generated": generated, "decode_ms": decode_ms,
                    "decode_tok_s": round(generated / decode_ms * 1000, 1), "drafts_accepted": 12, "drafts_offered": 23},
        "prompt": {"tokens": prompt_tokens, "reused": 0, "read": read, "tok_s": 1118.0, "ms": prompt_ms,
                   "groups": ({"ms": {"experts over PCIe": 696.0, "dequant": 240.0, "expert GEMM": 284.0,
                                      "attention": 87.0, "GDN": 214.0, "other": 91.0},
                               "share": {}, "gpu_ms": 1614.0, "tokens": 2016, "top": [["wait copy", 680.0]]}
                              if with_phases else None), "host": None},
        "decode": {"generated": generated, "tok_s": 63.7, "ms": decode_ms,
                   "groups": {"ms": {"GPU": 9.11, "CPU experts": 16.05, "draft": 1.61, "adapt wait": adapt,
                                     "other": 0.79}, "share": {}, "ms_per_window": 29.0, "tokens_per_window": 1.85,
                              "windows": 13, "cpu_entries": 6.47, "cpu_experts": 4.29},
                   "drafts_accepted": 12, "drafts_offered": 23},
        "hit": {"hit_rate": hit, "pcie_share": 0.091} if with_hit else None,
        "kv": {"kv_hit": 0.9719} if with_hit else None, "gpu_stages": None}


class FakeStrata:
    def __init__(self, results_dir, key=None, profile=True, history=False, settings=False, tune_available=True,
                 tune_outcome="done", reload_503s=1, report_inside=True, run_secs=0.05, tune_secs=0.2,
                 tune_busy_409s=0, host="127.0.0.1", port=0, status_changes=True, run_fail_on=()):
        self.results_dir = Path(results_dir)
        self.key, self.profile, self.history_on, self.settings_on = key, profile, history, settings
        self.unload_answer = "unloaded"              # POST /unload: unloaded | not loaded | busy | unsupported
        self.tune_available, self.tune_outcome = tune_available, tune_outcome
        self.reload_503s, self.report_inside = reload_503s, report_inside
        self.run_secs, self.tune_secs, self.tune_busy_409s = run_secs, tune_secs, tune_busy_409s
        self.status_changes = status_changes
        self.run_fail_on = set(run_fail_on)          # run numbers (1, 2, ...) that end failed
        self.lock = threading.RLock()
        self.requests: list[dict] = []
        self.loaded = True
        self.model = "swift-1.5-iq3_s"
        self.last = request_doc()
        self.history_entries: list[dict] = []
        self.metrics_requests: list[dict] = []
        self.hardware: dict = {}                     # /metrics hardware, hardware_static and history (telemetry)
        self.hardware_static: dict = {}
        self.hw_history: dict = {}
        self.live_state = {"state": "idle"}
        self.engine = dict(ENGINE)
        self.run = {"state": "idle", "lines": [], "started_at": None, "elapsed_s": None, "returncode": None,
                    "report": None, "note": None}
        self.tune = {"state": "idle", "lines": [], "started_at": None, "elapsed_s": None, "returncode": None,
                     "report": None, "note": None}
        self.runs_done = 0
        self.tune_done_pending_reload = 0
        # the streamed chat: (prompt tok/s, reply tok/s) for the request's strata_tune; the tunes whose reply text differs
        self.chat_speeds = lambda tune: (2000.0, 80.0)
        self.chat_differs = lambda tune: False
        self.conv_cache_on = True                     # the engine keeps a checkpoint of a turn for the next turn
        self._conv = []                               # the prompts of the kept checkpoints
        self.settings_doc = {"file": "strata-swift-1.5-iq3_s.json", "loaded": True, "settings": [
            {"id": "pcie_frac", "name": "--pcie-frac", "kind": "arg", "loaded_now": "0.55", "next_load": None,
             "run_config": None, "calibrated": True, "edit": None, "about": "x"},
            {"id": "spec_min_p", "name": "--spec-min-p", "kind": "arg", "loaded_now": "0.50", "next_load": "0.5",
             "run_config": "0.5", "calibrated": True, "edit": None, "about": "x"},
            {"id": "pool_workers", "name": "--pool-workers", "kind": "arg", "loaded_now": "7", "next_load": None,
             "run_config": None, "calibrated": True, "edit": None, "about": "x"}]}
        self._run_count = 0
        self._stop = threading.Event()
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, status, doc, headers=None):
                body = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _record(self, body):
                with fake.lock:
                    fake.requests.append({"method": self.command, "path": self.path.split("?")[0],
                                          "query": self.path.partition("?")[2],
                                          "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

            def _authorized(self):
                if not fake.key:
                    return True
                if self.headers.get("Authorization", "") == f"Bearer {fake.key}":
                    return True
                self._reply(401, {"error": {"type": "authentication_error", "message": "missing or wrong API key"}})
                return False

            def do_GET(self):
                self._record(None)
                path = self.path.split("?")[0].rstrip("/")
                if path in ("/health", "/api/health"):
                    return self._reply(200, {"status": "ok", "max_context": 262144, "model": fake.model, "images": False,
                                             "api_key": bool(fake.key), "image_generation": False,
                                             "loaded": fake.loaded, "service": "strata"})
                if not self._authorized():
                    return
                if path == "/metrics":
                    with fake.lock:
                        return self._reply(200, {"engine": dict(fake.engine), "live": dict(fake.live_state),
                                                 "requests": list(fake.metrics_requests),
                                                 "hardware": dict(fake.hardware),
                                                 "hardware_static": dict(fake.hardware_static),
                                                 "history": copy.deepcopy(fake.hw_history), "time": time.time()})
                if path.startswith("/profile") and not fake.profile:
                    return self._reply(404, {"error": {"message": "not found"}})
                if path == "/profile/last":
                    with fake.lock:
                        return self._reply(200, {"request": copy.deepcopy(fake.last), "log": True,
                                                 "loaded": fake.loaded, "model": fake.model, "cost": ""})
                if path == "/profile/status":
                    return self._reply(200, fake.status_doc())
                if path == "/profile/history" and fake.history_on:
                    with fake.lock:
                        return self._reply(200, {"max": 50, "engine_run": 1, "requests": list(fake.history_entries)})
                if path == "/profile/settings" and fake.settings_on:
                    return self._reply(200, fake.settings_doc)
                self._reply(404, {"error": {"message": "not found"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode() or "null")
                except ValueError:
                    body = None
                self._record(body)
                path = self.path.split("?")[0].rstrip("/")
                if not self._authorized():
                    return
                if path in ("/profile/run", "/profile/tune"):
                    if not fake.profile:
                        return self._reply(404, {"error": {"message": "not found"}})
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        return self._reply(415, {"error": {"message": "send application/json"}})
                    origin = (self.headers.get("Origin") or "").rstrip("/")
                    if origin and origin.split("://", 1)[-1] != self.headers.get("Host", ""):
                        return self._reply(403, {"error": {"message": "only from Strata's own page"}})
                    if path == "/profile/run":
                        return fake.post_run(self)
                    return fake.post_tune(self)
                if path == "/v1/chat/completions":
                    return fake.post_chat(self, body)
                if path in ("/unload", "/load"):            # Strata 0.1.44.1: JSON only, and no foreign Origin
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        return self._reply(415, {"error": {"message": "send application/json"}})
                    origin = (self.headers.get("Origin") or "").rstrip("/")
                    if origin and origin.split("://", 1)[-1] != self.headers.get("Host", ""):
                        return self._reply(403, {"error": {"message": "the model can be loaded or unloaded only from "
                                                                      "Strata's own page"}})
                    with fake.lock:
                        if path == "/load":
                            fake.loaded = True
                            return self._reply(200, {"status": "loaded"})
                        answer = fake.unload_answer
                        if answer == "busy":
                            return self._reply(409, {"status": "busy"})
                        if answer in ("unloaded", "not loaded"):
                            fake.loaded = False
                    return self._reply(200, {"status": answer})
                self._reply(404, {"error": {"message": "not found"}})

        self.httpd = http.server.ThreadingHTTPServer((host, port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    # --------------------------------------------------------------------------------------- lifecycle
    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    # ------------------------------------------------------------------------------------------ status
    def status_doc(self) -> dict:
        with self.lock:
            tune = None
            if self.tune_available:
                tune = copy.deepcopy(self.tune)
                if tune["started_at"] and tune["state"] == "running":
                    tune["elapsed_s"] = round(time.time() - tune["started_at"], 1)
            run = copy.deepcopy(self.run)
            if run["started_at"] and run["state"] == "running":
                run["elapsed_s"] = round(time.time() - run["started_at"], 1)
            return {**run, "tune": tune,
                    "tune_unavailable": None if self.tune_available else "setup offers the tuning on NVIDIA only"}

    def post_run(self, h):
        with self.lock:
            if self.run["state"] == "running" or self.tune["state"] == "running":
                return h._reply(409, {"error": {"message": "a run or the tuning is already going"}})
            self._run_count += 1
            n = self._run_count
            self.run = {"state": "running", "lines": ["strata_profile: starting"], "started_at": time.time(),
                        "elapsed_s": None, "returncode": None, "report": None, "note": None}
        threading.Thread(target=self._finish_run, args=(n,), daemon=True).start()
        h._reply(200, {"status": "started"})

    def _finish_run(self, n: int):
        time.sleep(self.run_secs)
        if n in self.run_fail_on:
            with self.lock:
                self.run.update(state="failed", returncode=2, lines=self.run["lines"] + ["the run failed: no answer"],
                                elapsed_s=round(time.time() - self.run["started_at"], 1))
            return
        fixture = RUN_FIXTURES[(n - 1) % len(RUN_FIXTURES)]
        parent = self.results_dir if self.report_inside else self.results_dir.parent / "elsewhere"
        parent.mkdir(parents=True, exist_ok=True)
        name = f"2026-10-16-profile-fake-gpu-{n}"
        dest = parent / name
        shutil.copytree(FIXTURES / fixture, dest, dirs_exist_ok=True)
        results = json.loads((dest / "results.json").read_text())
        with self.lock:
            self.run.update(state="done", returncode=0, elapsed_s=round(time.time() - self.run["started_at"], 1),
                            lines=self.run["lines"] + [f"Report: {dest}/README.md (home folders ...)"],
                            report={"path": str(dest), "readme": "# fake", "results": results})
            self.runs_done += 1

    def post_tune(self, h):
        with self.lock:
            if self.tune_busy_409s > 0:
                self.tune_busy_409s -= 1
                return h._reply(409, {"error": {"message": "busy: a run is going"}})
            if not self.tune_available:
                return h._reply(409, {"error": {"message": "setup offers the tuning on NVIDIA only"}})
            if self.run["state"] == "running" or self.tune["state"] == "running":
                return h._reply(409, {"error": {"message": "busy"}})
            self.tune = {"state": "running", "lines": [], "started_at": time.time(), "elapsed_s": None,
                         "returncode": None, "report": None, "note": None}
            self.loaded = False
        threading.Thread(target=self._run_tune, daemon=True).start()
        h._reply(200, {"status": "started"})

    def _run_tune(self):
        if self.tune_outcome == "real":              # the real run's output: the sweeps' winners were not kept
            script = REAL["lines"]
        else:
            script = TUNE_LINES if self.tune_outcome == "done" else FAIL_LINES
        pause = self.tune_secs / max(1, len(script))
        for line in script:
            time.sleep(pause)
            with self.lock:
                self.tune["lines"].append(line)
        with self.lock:
            t = self.tune
            t["elapsed_s"] = round(time.time() - t["started_at"], 1)
            if self.tune_outcome == "real":
                rep = {"summary": REAL["summary"], "tok_s": REAL["tok_s"], "changes": copy.deepcopy(REAL_CHANGES)}
                t.update(state="done", returncode=0, report=rep)
                self.tune_done_pending_reload = self.reload_503s
            elif self.tune_outcome == "done":
                rep = {"summary": "--pcie-frac 0.35, --spec-min-p 0.70, --adapt-every 1, --adapt-swaps 160, "
                                  "--adapt-decay 0.97 (96.6 tok/s)", "tok_s": 96.6}
                if self.status_changes:
                    rep["changes"] = copy.deepcopy(CHANGES)
                t.update(state="done", returncode=0, report=rep)
                self.tune_done_pending_reload = self.reload_503s
            else:
                t.update(state="failed", returncode=1, report=None)
                self.tune_done_pending_reload = 0
            self.loaded = False

    def post_chat(self, h, body=None):
        with self.lock:
            if self.tune["state"] == "running":
                return h._reply(503, {"error": {"message": "the model is busy (tuning)"}}, {"Retry-After": "0"})
            if not self.loaded and self.tune_done_pending_reload > 0:
                self.tune_done_pending_reload -= 1
                return h._reply(503, {"error": {"message": "loading"}}, {"Retry-After": "0"})
            self.loaded = True
        body = body if isinstance(body, dict) else {}
        if not body.get("stream"):
            with self.lock:
                self.last = request_doc(prompt_tokens=18, generated=8, prompt_ms=210.0, decode_ms=95.0, adapt=7.0)
            return h._reply(200, {"id": "x", "choices": [{"message": {"role": "assistant", "content": "OK"}}]})
        # a streamed request, as the replay sends it: 1.3 tokens a word. The reply copies the passage of the last user
        # message, or (a free task) is made of numbered words. A turn that keeps a checkpoint is reused by the next turn
        # whose prompt starts with it.
        msgs = body.get("messages") or [{}]
        full = "".join(f"{m.get('role')}:{m.get('content')}\n" for m in msgs)
        last = str(msgs[-1].get("content") or "")
        tune = body.get("strata_tune") or {}
        tokens = lambda text: 15 + round(len(text.split()) * 1.3)
        ptoks = tokens(full)
        cache_n = 0
        with self.lock:
            if self.conv_cache_on:
                cache_n = max([tokens(c) for c in self._conv if full.startswith(c)] or [0])
                if body.get("strata_checkpoint") is not False:
                    self._conv.append(full)
        head = "Copy the following passage word for word. Then stop.\n\n"
        if head in last:
            reply = last.split(head, 1)[1].split()
        elif "garbage collector" in last:
            reply = [f"w{i}" for i in range(2000)]
        else:
            reply = ["OK"]
        reply = reply[:max(1, int(int(body.get("max_tokens") or 16) / 1.3))]
        if self.chat_differs(tune):
            reply = reply + ["extra"]
        ctoks = round(len(reply) * 1.3)
        p_tps, d_tps = self.chat_speeds(tune)
        pms, dms = max(0.1, (ptoks - cache_n) / p_tps * 1000), ctoks / d_tps * 1000
        with self.lock:
            self.last = request_doc(prompt_tokens=ptoks, generated=ctoks, prompt_ms=pms, decode_ms=dms, adapt=7.0)
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True

        def sse(doc):
            try:
                h.wfile.write(b"data: " + (doc if isinstance(doc, bytes) else json.dumps(doc).encode()) + b"\n\n")
            except OSError:                                  # the client went away (a cancelled replay)
                pass
        for w in reply:
            sse({"choices": [{"delta": {"content": w + " "}}]})
        sse({"choices": [], "usage": {"prompt_tokens": ptoks, "completion_tokens": ctoks},
             "timings": {"prompt_n": ptoks - cache_n, "cache_n": cache_n, "prompt_ms": round(pms, 1), "predicted_n": ctoks,
                         "predicted_ms": round(dms, 1), "draft_n": 10, "draft_n_accepted": 8}})
        sse(b"[DONE]")

    # ---------------------------------------------------------------------------------- test helpers
    def set_last(self, doc):
        with self.lock:
            self.last = doc

    def requests_to(self, path: str):
        with self.lock:
            return [r for r in self.requests if r["path"] == path]


def main(argv=None):
    ap = argparse.ArgumentParser(description="A fake Strata for trying the Studio with no GPU.")
    ap.add_argument("--port", type=int, default=8191)
    ap.add_argument("--results", required=True, help="the folder fake profile runs write their reports into")
    ap.add_argument("--key", default=None)
    ap.add_argument("--no-profile", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--settings", action="store_true")
    ap.add_argument("--tune-secs", type=float, default=20.0)
    a = ap.parse_args(argv)
    fake = FakeStrata(a.results, key=a.key, profile=not a.no_profile, history=a.history, settings=a.settings,
                      run_secs=4.0, tune_secs=a.tune_secs, port=a.port, reload_503s=2).start()
    print(f"fake Strata on {fake.url}", flush=True)
    n = 0
    try:
        while True:                                        # a new request every few seconds, as if somebody chats
            time.sleep(3)
            n += 1
            doc = request_doc(prompt_tokens=300 + 400 * (n % 7), generated=24 + (n % 5) * 20,
                              decode_ms=400.0 + 30 * (n % 4), hit=0.62 + 0.04 * (n % 6), adapt=1.0 + 0.4 * (n % 3))
            with fake.lock:
                fake.last = doc
                fake.metrics_requests.insert(0, {"time": time.time(), "duration_s": 2.0, "prompt_tokens": 300 + 400 * (n % 7),
                                                 "reused": 0, "prompt_read": 300 + 400 * (n % 7), "prompt_ms": 400.0,
                                                 "decode_ms": 400.0, "decode_tok_s": 60.0 + n % 9,
                                                 "output_tokens": 24, "hit_rate": 0.7, "pcie_share": 0.09})
                del fake.metrics_requests[12:]
                if fake.history_on:
                    s = doc["summary"]
                    fake.history_entries.append({"n": n, "seen_at": time.time(), "open": False,
                                                 "prompt_tokens": s["prompt_tokens"], "reused": 0, "read": s["read"],
                                                 "prompt_tok_s": s["prompt_tok_s"], "prompt_ms": s["prompt_ms"],
                                                 "generated": s["generated"], "decode_tok_s": s["decode_tok_s"],
                                                 "decode_ms": s["decode_ms"], "ms": doc["decode"]["groups"]["ms"],
                                                 "hit_rate": 0.7})
                    del fake.history_entries[:-50]
    except KeyboardInterrupt:
        fake.stop()


if __name__ == "__main__":
    main()
