"""The poller: talks to Strata on the cadences of the design, keeps the Live ring, and says what Strata can do.

One thread calls `step()` about ten times a second; each kind of call runs only when it is due.  While Strata is
unreachable (or needs a key) the only call is /health.  The flow runner and the HTTP handlers share this object:
everything they read goes through its lock.
"""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

from . import requests as requests_mod, settings as settings_mod
from .requests import REQUEST_KEYS, kind_of
from .strata import StrataError

LIVE_MAX = 300
FRESH_UPDATE_S = 15.0       # a sample touched this recently is re-sent to a page that asks for "since"
SEEN_METRICS_MAX = 600

MSG_UNREACHABLE = ("Strata does not answer at {url}. Start it (./start.sh or START-HERE.bat). Retrying every 5 s.")
MSG_KEY = "Strata needs an API key: start the Studio with --key KEY or STRATA_KEY=KEY."
MSG_PROFILE_OFF = ("Profiling is off in Strata: add \"profile\": true to strata-<model>.json (or start "
                   "serve/server.py with --profile) and restart Strata.")
MSG_MISMATCH = ("Strata wrote its report to {parent}, but the Studio reads {dir}: restart the Studio with "
                "--results {parent}.")

SAMPLE_KEYS = REQUEST_KEYS
HARDWARE_HISTORY = 60


def _d(x):
    return x if isinstance(x, dict) else {}


def _stamp(prompt_tokens, reused, generated, prompt_ms, decode_ms):
    def r(v):
        return None if v is None else int(round(v))
    return (prompt_tokens, reused, generated, r(prompt_ms), r(decode_ms))


sample_from_last = requests_mod.from_last
sample_from_history = requests_mod.from_history
sample_from_metrics = requests_mod.from_metrics


class Poller:
    def __init__(self, client, timing, store, results_dir, state=None, config_path=None, clock=time.time):
        self.client, self.timing, self.reports, self.clock = client, timing, store, clock
        self.state = state
        self.results_dir = Path(results_dir)
        self.config_path = config_path
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        # reachability
        self.status = "unreachable"                  # unreachable | key_needed | profile_off | ok
        self.message = None
        self.checked_at = None
        self.health: dict = {}
        self.key_flag = False                        # a 401 came back and no good answer since
        self.profile: bool | None = None             # None: not known yet
        self.routes = {"history": False, "settings": False}
        self.status_raw: dict | None = None
        self.status_at = None
        self.engine: dict | None = None
        self.engine_at = None
        self.hardware: dict = {}                     # /metrics: hardware (now), hardware_static, history, live
        self.hardware_at = None
        self.mismatch: str | None = None
        self._need_probe = True
        # live ring
        self.ring: list[dict] = []
        self._by_stamp: dict = {}
        self._seq = 0
        self._after_load = False
        self._loaded_prev: bool | None = None
        self._seen_metrics: dict = {}
        self._last_seen = False
        self.tag: str | None = None                  # set by the flow
        # observations
        self.estimates = {"profile_s": None, "tune_s": None}
        self.studio_run = False                      # a run the Studio started is going (meta capture at its end)
        self.tune_by_studio = False
        self._settings_cache = (0.0, None)
        self._due = {"health": 0.0, "last": 0.0, "history": 0.0, "status": 0.0, "metrics": 0.0, "probe": 0.0,
                     "scan": 0.0}
        self.scan_cache = (0.0, None)

    # ------------------------------------------------------------------------------------------ thread
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="poller", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:                         # noqa: BLE001 - the loop must not die on one bad answer
                pass
            self._stop.wait(self.timing.tick)

    def _is_due(self, name: str, every: float, now: float) -> bool:
        if now >= self._due[name]:
            self._due[name] = now + every
            return True
        return False

    # ------------------------------------------------------------------------------------------- step
    def step(self):
        now = self.clock()
        if self._is_due("health", self.timing.health, now):
            self._check_health()
        if self._is_due("scan", self.timing.scan, now):
            self.scan_reports(force=True)
        if self.status in ("unreachable", "key_needed"):
            return                                       # only /health until Strata answers and the key works
        if self._need_probe or self._is_due("probe", self.timing.probe, now):
            self._need_probe = False
            self._due["probe"] = now + self.timing.probe
            self._probe()
        if self.status in ("unreachable", "key_needed"):
            return
        if self.profile:
            if self._is_due("last", self.timing.last, now):
                self._fetch_last()
            if self.routes["history"] and self._is_due("history", self.timing.history, now):
                self._fetch_history()
            if self._is_due("status", self.timing.status_busy if self._busy() else self.timing.status_idle, now):
                self.refresh_status()
        every = self.timing.metrics_off if not self.profile else self.timing.metrics_on
        every = min(every, self.timing.hardware)
        if self._is_due("metrics", every, now):
            self._fetch_metrics()

    def _busy(self) -> bool:
        raw = self.status_raw or {}
        return _d(raw).get("state") == "running" or _d(raw.get("tune")).get("state") == "running"

    # ---------------------------------------------------------------------------------------- health
    def _derive(self):
        """status and message from what is known."""
        with self.lock:
            if self.status == "unreachable":
                self.message = MSG_UNREACHABLE.format(url=self.client.base_url) if self.checked_at else None
                return
            if self.key_flag:
                self.status, self.message = "key_needed", MSG_KEY
            elif self.profile is False:
                self.status, self.message = "profile_off", MSG_PROFILE_OFF
            else:
                self.status, self.message = "ok", self.mismatch

    def _check_health(self):
        now = self.clock()
        try:
            r = self.client.call("health")
        except StrataError as e:
            with self.lock:
                self.checked_at = now
                if e.kind == "key_needed":
                    self.key_flag = True
                    self.status = "key_needed"
                else:
                    if self.status != "unreachable":
                        self._need_probe = True
                    self.status = "unreachable"
                    self.health = {}
                self._derive()
            return
        with self.lock:
            self.checked_at = now
            if r.status != 200 or not isinstance(r.data, dict):
                self.status, self.health = "unreachable", {}
                self._derive()
                return
            was_unreachable = self.status == "unreachable"
            h = r.data
            self.health = {"model": h.get("model"), "loaded": bool(h.get("loaded")),
                           "max_context": h.get("max_context"), "api_key": bool(h.get("api_key")),
                           "profile": h.get("profile") if "profile" in h else None}
            loaded = self.health["loaded"]
            if self._loaded_prev is False and loaded:
                self._after_load = True
            self._loaded_prev = loaded
            if self.health["api_key"] and not self.client.has_key:
                self.key_flag = True
            elif not self.health["api_key"]:
                self.key_flag = False
            if self.status == "unreachable":
                self.status = "ok"
            if was_unreachable:
                self._need_probe = True
            self._derive()

    # ---------------------------------------------------------------------------------------- probe
    def _auth_failed(self):
        with self.lock:
            self.key_flag = True
            self.status = "key_needed"
            self._derive()

    def _auth_ok(self):
        with self.lock:
            if self.key_flag and (self.client.has_key or not self.health.get("api_key")):
                self.key_flag = False
                self._derive()

    def _probe(self):
        """Which routes does this Strata have?  /profile/status 404 means profiling is off."""
        try:
            r = self.client.call("status")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return
        self._auth_ok()
        if r.status == 404:
            with self.lock:
                self.profile = False
                self.routes = {"history": False, "settings": False}
                self.status_raw = None
                self._derive()
            return
        if r.status != 200 or not isinstance(r.data, dict):
            return
        self._note_status(r.data)
        routes = {}
        for name in ("history", "settings"):
            try:
                routes[name] = self.client.call(name).status == 200
            except StrataError:
                routes[name] = False
        with self.lock:
            self.routes = routes
            self._derive()

    # --------------------------------------------------------------------------------------- status
    def refresh_status(self) -> dict | None:
        """GET /profile/status now (the flow polls through here too).  Returns the answer, or None."""
        try:
            r = self.client.call("status")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return None
        self._auth_ok()
        if r.status == 404:
            with self.lock:
                self.profile = False
                self.status_raw = None
                self._derive()
            return None
        if r.status != 200 or not isinstance(r.data, dict):
            return None
        return self._note_status(r.data)

    def _note_status(self, data: dict) -> dict:
        with self.lock:
            prev = self.status_raw or {}
            self.status_raw, self.status_at = data, self.clock()
            self.profile = True
            run, tune = data, _d(data.get("tune"))
            prev_run, prev_tune = prev.get("state"), _d(prev.get("tune")).get("state")
            if prev_run == "running" and run.get("state") in ("done", "failed"):
                if run.get("state") == "done" and isinstance(run.get("elapsed_s"), (int, float)):
                    self.estimates["profile_s"] = run["elapsed_s"]
                self.scan_cache = (0.0, None)
            if prev_tune == "running" and tune.get("state") == "done" and isinstance(tune.get("elapsed_s"), (int, float)):
                self.estimates["tune_s"] = tune["elapsed_s"]
            mismatch = None
            rp = _d(run.get("report")).get("path")
            if rp:
                parent = Path(rp).resolve().parent if Path(rp).is_absolute() else None
                if parent is not None and parent != self.results_dir.resolve():
                    mismatch = MSG_MISMATCH.format(parent=parent, dir=self.results_dir)
            self.mismatch = mismatch
            studio_run_ended = (self.studio_run and prev_run == "running" and run.get("state") in ("done", "failed"))
        self._derive()
        if studio_run_ended:
            self.studio_run = False
            if run.get("state") == "done" and rp:
                self.capture_meta(Path(rp).name)
        return data

    def tune_status(self) -> dict:
        with self.lock:
            return copy.deepcopy(_d(self.status_raw))

    # --------------------------------------------------------------------------------------- samples
    def _tag(self) -> str | None:
        if self.tag:
            return self.tag
        raw = _d(self.status_raw)
        if _d(raw.get("tune")).get("state") == "running":
            return "tune"
        if raw.get("state") == "running":
            return "profile"
        return None

    def _append(self, fields: dict, stamp, source: str, seen_at: float, open_: bool, tag=None, insert_sorted=False):
        self._seq += 1
        s = {"seq": self._seq, "seen_at": seen_at, "source": source, "tag": tag, "open": open_,
             "after_load": False, "late": False}
        for k in SAMPLE_KEYS:
            s[k] = fields.get(k)
        s["_stamp"], s["_updated"] = stamp, self.clock()
        if insert_sorted:
            i = len(self.ring)
            while i > 0 and self.ring[i - 1]["seen_at"] > seen_at:
                i -= 1
            self.ring.insert(i, s)
        else:
            self.ring.append(s)
        self._by_stamp[stamp] = s
        while len(self.ring) > LIVE_MAX:
            old = self.ring.pop(0)
            if self._by_stamp.get(old["_stamp"]) is old:
                del self._by_stamp[old["_stamp"]]
        return s

    def _fetch_last(self):
        try:
            r = self.client.call("last")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return
        if r.status == 404:
            with self.lock:
                self.profile = False
                self._derive()
            return
        if r.status != 200 or not isinstance(r.data, dict):
            return
        self._auth_ok()
        req = r.data.get("request")
        if not isinstance(req, dict):
            return
        fields = sample_from_last(req)
        stamp = _stamp(fields["prompt_tokens"], fields["reused"], fields["generated"], fields["prompt_ms"],
                       fields["decode_ms"])
        now = self.clock()
        with self.lock:
            existing = self._by_stamp.get(stamp)
            if existing is not None:
                changed = False
                for k in SAMPLE_KEYS:
                    if fields[k] is not None and existing[k] != fields[k]:
                        existing[k] = fields[k]
                        changed = True
                if existing["source"] == "history" and fields["prompt_groups_ms"]:
                    existing["source"] = "last"
                if existing["open"] and existing["hit_rate"] is not None:
                    existing["open"] = False
                    changed = True
                if changed:
                    existing["_updated"] = now
                return
            s = self._append(fields, stamp, "last", now, fields["hit_rate"] is None, tag=self._tag())
            if not self._last_seen:                  # the first answer is a request from before the Studio started
                s["late"] = True
            self._last_seen = True
            if self._after_load:
                s["after_load"], self._after_load = True, False

    def _fetch_history(self):
        try:
            r = self.client.call("history")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return
        if r.status != 200 or not isinstance(r.data, dict):
            if r.status == 404:
                with self.lock:
                    self.routes["history"] = False
            return
        entries = [e for e in r.data.get("requests") or [] if isinstance(e, dict)]
        with self.lock:
            for e in entries:
                f = sample_from_history(e)
                stamp = _stamp(f["prompt_tokens"], f["reused"], f["generated"], f["prompt_ms"], f["decode_ms"])
                if stamp in self._by_stamp:
                    continue
                seen = e.get("seen_at") if isinstance(e.get("seen_at"), (int, float)) else self.clock()
                self._append(f, stamp, "history", seen, False, tag=self._tag() if seen >= self.clock() - 3 else None,
                             insert_sorted=True)

    def _fetch_metrics(self):
        try:
            r = self.client.call("metrics")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return
        if r.status != 200 or not isinstance(r.data, dict):
            return
        self._auth_ok()
        with self.lock:
            if isinstance(r.data.get("engine"), dict):
                self.engine, self.engine_at = r.data["engine"], self.clock()
            self._note_hardware(r.data)
            if self.profile:
                return                                   # profile on: Live comes from /profile/last
            reqs = [x for x in r.data.get("requests") or [] if isinstance(x, dict)]
            for x in reversed(reqs):                     # Strata lists the newest first; the ring wants oldest first
                t = x.get("time")
                if t is None or t in self._seen_metrics:
                    continue
                self._seen_metrics[t] = True
                if len(self._seen_metrics) > SEEN_METRICS_MAX:
                    self._seen_metrics.pop(next(iter(self._seen_metrics)))
                dur = x.get("duration_s") if isinstance(x.get("duration_s"), (int, float)) else 0
                seen = t + dur if isinstance(t, (int, float)) else self.clock()
                f = sample_from_metrics(x)
                s = self._append(f, ("metrics", t), "metrics", seen, False, tag=None, insert_sorted=True)
                if self._after_load:
                    s["after_load"], self._after_load = True, False

    def _note_hardware(self, data: dict):
        hist = {}
        for k, v in _d(data.get("history")).items():
            if isinstance(v, list):
                hist[str(k)] = [x if isinstance(x, (int, float)) and not isinstance(x, bool) else None
                                for x in v[-HARDWARE_HISTORY:]]
        self.hardware = {"now": _d(data.get("hardware")), "static": _d(data.get("hardware_static")), "history": hist,
                         "live": _d(data.get("live")), "time": data.get("time")}
        self.hardware_at = self.clock()

    def hardware_doc(self) -> dict:
        with self.lock:
            hw = copy.deepcopy(self.hardware)
            eng = copy.deepcopy(_d(self.engine))
            at = self.hardware_at
        live = _d(hw.get("live"))
        return {"at": at, "now": hw.get("now") or {}, "static": hw.get("static") or {},
                "history": hw.get("history") or {},
                "live": {k: live.get(k) for k in ("state", "tok_s", "tok_s_mean", "prompt_read", "prompt_total")
                         if k in live},
                "engine": {k: eng.get(k) for k in ("expert_slots", "expert_cache_mib", "arena_mib", "vram_free_mib",
                                                   "kv_resident", "pool_workers", "pcie_frac", "context", "kv",
                                                   "spec", "mtp_max")}}

    # ------------------------------------------------------------------------------------ read side
    def live(self, since: int = 0) -> dict:
        now = self.clock()
        with self.lock:
            out = []
            for s in self.ring:
                if s["seq"] > since or s["open"] or now - s["_updated"] <= FRESH_UPDATE_S:
                    x = {k: v for k, v in s.items() if not k.startswith("_")}
                    x["kind"] = kind_of(x["prompt_tokens"], x["reused"], x["generated"])
                    out.append(x)
            return {"max": LIVE_MAX, "last_seq": self._seq if self.ring else 0, "samples": copy.deepcopy(out)}

    def first_sample_with_tag(self, tag: str, after_seq: int = 0) -> dict | None:
        with self.lock:
            for s in self.ring:
                if s["tag"] == tag and s["seq"] > after_seq:
                    return {k: copy.deepcopy(v) for k, v in s.items() if not k.startswith("_")}
        return None

    def newest_seq(self) -> int:
        with self.lock:
            return self._seq

    def set_tag(self, tag: str | None):
        with self.lock:
            self.tag = tag

    # ---------------------------------------------------------------------------------- settings
    def get_settings(self, force: bool = False) -> dict | None:
        """/profile/settings, cached for 10 s; None when Strata has no such route or does not answer."""
        now = self.clock()
        with self.lock:
            at, val = self._settings_cache
            if not force and val is not None and now - at < self.timing.settings_cache:
                return val
        if self.status in ("unreachable", "key_needed") or not self.routes.get("settings"):
            return None
        try:
            r = self.client.call("settings")
        except StrataError as e:
            if e.kind == "key_needed":
                self._auth_failed()
            return None
        if r.status != 200 or not isinstance(r.data, dict):
            return None
        with self.lock:
            self._settings_cache = (now, r.data)
        return r.data

    def get_engine(self) -> dict | None:
        """The /metrics engine block (cached; a fresh call when it is older than 30 s)."""
        with self.lock:
            if self.engine is not None and self.engine_at and self.clock() - self.engine_at < 30:
                return self.engine
        if self.status in ("unreachable", "key_needed"):
            return self.engine
        self._fetch_metrics_engine()
        return self.engine

    def _fetch_metrics_engine(self):
        try:
            r = self.client.call("metrics")
        except StrataError:
            return
        if r.status == 200 and isinstance(r.data, dict) and isinstance(r.data.get("engine"), dict):
            with self.lock:
                self.engine, self.engine_at = r.data["engine"], self.clock()

    def capture_meta(self, report_id: str):
        """Remember the settings in effect for a run the Studio started, for a report that records none."""
        state = self.state
        if state is None:
            return
        rows = settings_mod.rows_for_meta(self.get_settings(force=True), self.get_engine())
        if not rows:
            return
        source = "profile/settings" if self.routes.get("settings") else "metrics"
        state.set_meta(report_id, {"captured_at": self.clock(), "source": source, "rows": rows})

    # ------------------------------------------------------------------------------------- reports
    def scan_reports(self, force: bool = False) -> dict:
        now = self.clock()
        with self.lock:
            at, val = self.scan_cache
            if val is not None and not force and now - at < self.timing.scan:
                return val
        val = self.reports.scan()
        with self.lock:
            self.scan_cache = (now, val)
        return val

    # ------------------------------------------------------------------------------------- state
    def strata_block(self, flow_running_tune: bool = False) -> dict:
        """The `strata` part of GET /api/state."""
        with self.lock:
            raw = _d(self.status_raw)
            tune = raw.get("tune")
            if self.status in ("unreachable", "key_needed"):
                reason = "Strata does not answer." if self.status == "unreachable" else "Strata needs an API key."
                avail = False
            elif self.profile is False:
                avail, reason = False, "Profiling is off in Strata."
            elif tune is None and self.status_raw is not None:
                avail = False
                reason = raw.get("tune_unavailable") or "Strata cannot tune on this PC."
            elif self.status_raw is None:
                avail, reason = False, "Strata's state is not known yet."
            else:
                avail, reason = True, None
            tune_d = _d(tune)
            return {
                "status": self.status, "checked_at": self.checked_at, "message": self.message,
                "model": self.health.get("model"), "loaded": self.health.get("loaded"),
                "max_context": self.health.get("max_context"), "api_key": self.health.get("api_key"),
                "profile": self.profile, "routes": dict(self.routes),
                "run": {"state": raw.get("state") or "idle", "elapsed_s": raw.get("elapsed_s")},
                "tune": {"available": avail, "unavailable_reason": reason, "state": tune_d.get("state") or "idle",
                         "elapsed_s": tune_d.get("elapsed_s"),
                         "started_by_studio": bool(self.tune_by_studio and tune_d.get("state") == "running")},
            }
