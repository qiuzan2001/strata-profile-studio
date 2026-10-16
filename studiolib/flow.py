"""The tune flow: profile, tune, reload, profile, compare, as a state machine in one thread of studio.py.

Closing the browser does not affect it.  The page only reads `snapshot()`.  Strata's own routes do the work; this
module starts them, waits for them and writes down what happened.  Nothing here edits a Strata setting: the only
thing that changes the run config is Strata's own POST /profile/tune.
"""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

from . import calibparse
from .calibparse import same_value
from .poller import MSG_KEY, MSG_MISMATCH, MSG_PROFILE_OFF
from .reports import arg_value
from .settings import read_config
from .stock import MSG_RESTART_TUNE
from .strata import StrataError

STAGES = (("check", "Check Strata"), ("before", "Profile before"), ("tune", "Tune"), ("reload", "Load the model"),
          ("after", "Profile after"), ("compare", "Compare"))
TAGS = {"before": "profile-before", "tune": "tune", "reload": "reload", "after": "profile-after"}
SETTING_FLAGS = ("--pcie-frac", "--spec-min-p", "--pool-workers", "--adapt-every", "--adapt-swaps", "--adapt-decay")
MAX_LINES = 2000
LINES_TAIL = 400
RELOAD_BODY_TEXT = "Reply with OK."


class FlowError(Exception):
    """A request the flow cannot take: 409 (one is running) or 412 (a precondition fails)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class _Abandoned(Exception):
    """The Studio is shutting down (or a test simulates a restart): leave the persisted flow as it is."""


class _Stop(Exception):
    """The flow ends here: `state` (failed | cancelled) with the stage that failed and why."""

    def __init__(self, state: str, stage: str | None, message: str):
        super().__init__(message)
        self.state, self.stage, self.message = state, stage, message


def _d(x):
    return x if isinstance(x, dict) else {}


class Flow:
    def __init__(self, client, poller, reports, state, timing, results_dir, config_path=None, clock=time.time):
        self.client, self.poller, self.reports, self.state, self.timing = client, poller, reports, state, timing
        self.results_dir = Path(results_dir)
        self.config_path = config_path
        self.clock = clock
        self.lock = threading.RLock()
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self.rec: dict | None = None             # the current or last flow's record
        self._cancel = False

    # ----------------------------------------------------------------------------------- public API
    def running(self) -> bool:
        with self.lock:
            return bool(self.rec) and self.rec["state"] == "running"

    def start(self, long: int | None = None) -> str:
        with self.lock:
            if self.running():
                raise FlowError(409, "A tune is already running.")
        reason = self.check()
        if reason:
            raise FlowError(412, reason)
        now = self.clock()
        ident = "tune-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        existing = {t.get("id") for t in self.state.tunes()}
        n = 2
        base = ident
        while ident in existing:
            ident, n = f"{base}-{n}", n + 1
        with self.lock:
            self.rec = self._new_record(ident, now, long)
            self._cancel = False
        self._launch(self._run_all)
        return ident

    def cancel(self) -> bool:
        """Ends the flow after the running profile run finishes (Strata cannot stop a run from here)."""
        with self.lock:
            if not self.rec or self.rec["state"] != "running" or self.rec["stage"] not in ("check", "before"):
                return False
            self._cancel = True
            return True

    def retry_after(self) -> str:
        with self.lock:
            rec = self.rec
            if self.running() or not rec or rec["state"] != "failed" or rec["failed_stage"] != "after":
                raise FlowError(409, "There is no failed \"after\" profile to run again.")
        reason = self.check()
        if reason:
            raise FlowError(412, reason)
        with self.lock:
            rec["state"], rec["stage"], rec["error"], rec["ended_at"] = "running", "after", None, None
            rec["failed_stage"] = None
            for st in rec["stages"]:
                if st["key"] in ("after", "compare"):
                    st.update(state="pending", started_at=None, elapsed_s=None, message=None)
            self._cancel = False
        self._launch(self._run_after_only)
        return rec["id"]

    def abandon(self):
        """Stop the thread without ending the flow (a restart of the Studio, as seen from the persisted file)."""
        self._abort.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)

    def resume(self) -> None:
        """Continue the flow a previous Studio process left in the state file (called once at start)."""
        saved = self.state.active_flow()
        if not saved or not isinstance(saved.get("stages"), list):
            return
        with self.lock:
            self.rec = saved
            self.rec["state"] = "running"
            self._cancel = False
        stage = saved.get("stage")
        st = self.poller.refresh_status()
        tune_state = _d(_d(st).get("tune")).get("state")
        if stage == "tune" and tune_state == "running":
            self.poller.tune_by_studio = True
            self._launch(lambda: self._run_all(resume="tune"))
        elif stage == "tune" and tune_state == "done":
            self._launch(lambda: self._run_all(resume="reload"))
        else:
            try:
                self._end("failed", stage, "interrupted: the Studio was restarted")
            except _Abandoned:
                pass

    def check(self) -> str | None:
        """Stage 1's conditions.  None when all hold, otherwise the first reason, as a sentence."""
        t = self.timing
        try:
            h = self.client.call("health", timeout=t.check_timeout)
        except StrataError as e:
            if e.kind == "key_needed":
                return MSG_KEY
            return f"Strata does not answer at {self.client.base_url}."
        if h.status != 200 or not isinstance(h.data, dict):
            return f"Strata's /health answered {h.status}."
        try:
            s = self.client.call("status", timeout=t.check_timeout)
        except StrataError as e:
            return MSG_KEY if e.kind == "key_needed" else f"Strata does not answer at {self.client.base_url}."
        if s.status == 404:
            return MSG_PROFILE_OFF
        if s.status != 200 or not isinstance(s.data, dict):
            return f"Strata's /profile/status answered {s.status}."
        data = s.data
        if data.get("tune") is None:
            return str(data.get("tune_unavailable") or "Strata cannot tune on this PC.")
        if data.get("state") == "running":
            return "A profile run is already going in Strata."
        if _d(data.get("tune")).get("state") == "running":
            return "Strata is already tuning."
        if not self.results_dir.is_dir():
            return (f"The folder {self.results_dir} does not exist. Start the Studio with --results pointing at "
                    f"Strata's bench/results.")
        return None

    # --------------------------------------------------------------------------------------- record
    def _new_record(self, ident: str, now: float, long) -> dict:
        return {"id": ident, "state": "running", "stage": "check", "started_at": now, "ended_at": None,
                "long": long, "lines": [], "candidates": calibparse.parse([]), "summary": None, "tok_s": None,
                "before_report": None, "after_report": None, "settings": None, "reload": None, "error": None,
                "compare": None, "failed_stage": None, "outcome": None, "tune_s": None, "tune_lines": [],
                "stages": [{"key": k, "label": label, "state": "pending", "started_at": None, "elapsed_s": None,
                            "timeout_s": self._timeout_for(k), "message": None} for k, label in STAGES],
                "_cap": {}, "note": ""}

    def _timeout_for(self, key: str):
        t = self.timing
        return {"check": t.check_timeout, "before": t.before_timeout, "tune": t.tune_timeout,
                "reload": t.reload_timeout, "after": t.after_timeout, "compare": None}[key]

    def _stage(self, key: str) -> dict:
        return next(s for s in self.rec["stages"] if s["key"] == key)

    def _begin(self, key: str):
        with self.lock:
            st = self._stage(key)
            st.update(state="running", started_at=self.clock(), elapsed_s=None, message=None)
            self.rec["stage"] = key
        self.poller.set_tag(TAGS.get(key))
        self._persist()

    def _done(self, key: str, message: str | None = None):
        with self.lock:
            st = self._stage(key)
            st["state"] = "done"
            st["elapsed_s"] = round(self.clock() - (st["started_at"] or self.clock()), 1)
            st["message"] = message
        self._persist()

    def _fail(self, key: str, message: str, state: str = "failed"):
        with self.lock:
            st = self._stage(key)
            st["state"] = "failed"
            st["elapsed_s"] = round(self.clock() - (st["started_at"] or self.clock()), 1)
            st["message"] = message
        raise _Stop(state, key, message)

    def _skip_rest(self, after_key: str):
        with self.lock:
            seen = False
            for st in self.rec["stages"]:
                if st["key"] == after_key:
                    seen = True
                    continue
                if seen and st["state"] == "pending":
                    st["state"] = "skipped"

    def _persist(self):
        with self.lock:
            if not self.rec or self.rec["state"] != "running":
                return
            data = copy.deepcopy(self.rec)
        data["tune_lines"] = data["tune_lines"][-LINES_TAIL:]
        data["lines"] = data["lines"][-LINES_TAIL:]
        self.state.set_active_flow(data)

    def _wait(self, seconds: float):
        if self._abort.wait(max(0.0, seconds)):
            raise _Abandoned()

    def _launch(self, fn):
        self._abort.clear()
        self._thread = threading.Thread(target=self._guard, args=(fn,), name="tune-flow", daemon=True)
        self._thread.start()

    def _guard(self, fn):
        try:
            fn()
        except _Abandoned:
            return
        except _Stop as stop:
            try:
                self._end(stop.state, stop.stage, stop.message)
            except _Abandoned:
                pass
        except Exception as e:                       # noqa: BLE001 - a bug here must end the flow, not hang it
            try:
                self._end("failed", self.rec.get("stage") if self.rec else None,
                          f"The flow stopped on an unexpected error ({type(e).__name__}).")
            except _Abandoned:
                pass

    # ------------------------------------------------------------------------------------------ run
    def _run_all(self, resume: str | None = None):
        if resume is None:
            self._stage_check()
            self._stage_profile("before")
            self._maybe_cancel("before")
            self._stage_tune()
        elif resume == "tune":
            self._stage_tune(resume=True)
        elif resume == "reload":                      # the tune ended while the Studio was down
            tune = _d(_d(self.poller.refresh_status()).get("tune"))
            self._absorb_tune_lines(tune)
            self._finish_tune(tune)
        tune_failed = self.rec["_cap"].get("tune_failed")
        if self.poller.stock and not tune_failed:
            self._stock_restart()
        self._stage_reload()
        if tune_failed:
            self._skip_rest("reload")
            raise _Stop("failed", "tune", tune_failed)
        self._stage_profile("after")
        self._stage_compare()

    def _stock_restart(self):
        """Stock mode: a release server reads its run config at start, and the Studio cannot restart a server that it
        did not start.  The flow stops here, and "Run the profile after again" runs stages 5 and 6 after the
        restart of Strata."""
        with self.lock:
            st = self._stage("reload")
            st.update(state="skipped", message=MSG_RESTART_TUNE)
        raise _Stop("failed", "after", f"{MSG_RESTART_TUNE} Then select Run the profile after again.")

    def _run_after_only(self):
        self._stage_profile("after")
        self._stage_compare()

    def _maybe_cancel(self, stage: str):
        if self._cancel:
            self._skip_rest(stage)
            raise _Stop("cancelled", None, "Cancelled. Strata's profile run had to finish first.")

    # --- stage 1
    def _stage_check(self):
        self._begin("check")
        reason = self.check()
        if reason:
            self._skip_rest("check")
            self._fail("check", reason)
        self._cap_config("config_before")
        self._done("check")
        self._maybe_cancel("check")

    # --- stages 2 and 5
    def _stage_profile(self, key: str):
        self._begin(key)
        timeout = self.timing.before_timeout if key == "before" else self.timing.after_timeout
        prev = _d(self.poller.refresh_status())
        prev_started = prev.get("started_at")
        long = self.rec.get("long")
        try:
            r = self.client.call("run", query={"long": int(long)} if long else None, body={})
        except StrataError as e:
            self._skip_rest(key)
            self._fail(key, MSG_KEY if e.kind == "key_needed" else "Strata stopped answering when the run was asked for.")
        if r.status != 200:
            self._skip_rest(key)
            self._fail(key, r.message)
        deadline = self.clock() + timeout
        while True:
            self._wait(self.timing.flow_poll)
            st = self.poller.refresh_status()
            if st is not None and not (st.get("started_at") == prev_started and st.get("state") != "running"):
                with self.lock:
                    self.rec["lines"] = [str(x) for x in (st.get("lines") or [])][-MAX_LINES:]
                if st.get("state") == "done":
                    break
                if st.get("state") == "failed":
                    self._skip_rest(key)
                    last = next((str(x) for x in reversed(st.get("lines") or []) if str(x).strip()), "")
                    self._fail(key, f"The profile run failed (exit {st.get('returncode')}). {last}".strip())
            if self.clock() > deadline:
                self._skip_rest(key)
                self._fail(key, f"The profile run did not end within {int(timeout)} s. The Studio stopped waiting.")
        rid = self._report_id(key, _d(st.get("report")))
        with self.lock:
            self.rec["before_report" if key == "before" else "after_report"] = rid
        if key == "before":
            self.rec["_cap"]["settings_before"] = self.poller.get_settings(force=True)
        self.poller.capture_meta(rid)
        self._done(key, rid)

    def _report_id(self, key: str, report: dict) -> str:
        path = report.get("path")
        if not path:
            self._skip_rest(key)
            self._fail(key, "The run ended but Strata reported no report folder.")
        p = Path(path)
        parent = p.resolve().parent if p.is_absolute() else None
        if parent is None or parent != self.results_dir.resolve():
            self._skip_rest(key)
            self._fail(key, MSG_MISMATCH.format(parent=p.parent, dir=self.results_dir))
        if self.reports.folder(p.name) is None:
            self._skip_rest(key)
            self._fail(key, f"The report {p.name} is not in {self.results_dir}.")
        return p.name

    # --- stage 3
    def _stage_tune(self, resume: bool = False):
        t = self.timing
        if resume:                                    # the stage began in the earlier process: keep its clock
            self.poller.set_tag(TAGS["tune"])
            with self.lock:
                self.rec["stage"] = "tune"
            prev_started = object()                   # nothing is stale: the tune is the one that was started
            deadline = (self._stage("tune")["started_at"] or self.clock()) + t.tune_timeout
        else:
            self._begin("tune")
            prev = _d(_d(self.poller.refresh_status()).get("tune"))
            prev_started = prev.get("started_at")
            for attempt in range(t.busy_retries + 1):  # a 409 "busy" is tried again, three times
                try:
                    r = self.client.call("tune", body={})
                except StrataError as e:
                    self._skip_rest("tune")
                    self._fail("tune", MSG_KEY if e.kind == "key_needed"
                               else "Strata stopped answering when the tuning was asked for.")
                if r.status == 409 and attempt < t.busy_retries:
                    self._wait(t.busy_gap)
                    continue
                break
            if r.status != 200:
                self._skip_rest("tune")
                self._fail("tune", r.message)
            self.poller.tune_by_studio = True
            deadline = self.clock() + t.tune_timeout
        while True:
            self._wait(t.flow_poll)
            tune = _d(_d(self.poller.refresh_status()).get("tune"))
            if tune and not (tune.get("started_at") == prev_started and tune.get("state") != "running"):
                self._absorb_tune_lines(tune)
                if tune.get("state") in ("done", "failed"):
                    break
            if self.clock() > deadline:
                self._skip_rest("tune")
                self._fail("tune", "Strata's tuning is still running. The Studio stopped waiting. "
                                   "It cannot be stopped from here.")
        self._finish_tune(tune)

    def _absorb_tune_lines(self, tune: dict):
        new_lines = [str(x) for x in (tune.get("lines") or [])]
        with self.lock:
            merged = calibparse.merge_lines(self.rec["tune_lines"], new_lines, MAX_LINES)
            self.rec["tune_lines"] = merged
            self.rec["lines"] = merged
            cand = calibparse.parse(merged, self._was_from_settings())
            self.rec["candidates"] = cand
            self.rec["summary"], self.rec["tok_s"] = cand["summary"], cand["tok_s"]

    def _finish_tune(self, tune: dict):
        """The tune ended (done or failed): record what it said and close the stage."""
        cap = self.rec["_cap"]
        cand = self.rec["candidates"]
        if tune.get("state") == "failed":
            why = cand.get("error") or next((ln.strip() for ln in reversed(self.rec["tune_lines"]) if "[!]" in ln),
                                            None) or f"The tuning failed (exit {tune.get('returncode')})."
            cap["tune_failed"] = str(why)
            with self.lock:
                st = self._stage("tune")
                st["state"] = "failed"
                st["elapsed_s"] = round(self.clock() - (st["started_at"] or self.clock()), 1)
                st["message"] = str(why)
                self.rec["tune_s"] = st["elapsed_s"]
            self._persist()
            return
        rep = _d(tune.get("report"))
        if isinstance(rep.get("changes"), list):
            cap["tune_changes"] = rep["changes"]
        if rep.get("tok_s") is not None and self.rec["tok_s"] is None:
            self.rec["tok_s"] = rep.get("tok_s")
        if rep.get("summary") and not self.rec["summary"]:
            self.rec["summary"] = rep.get("summary")
        self._cap_config("config_after")
        self._done("tune", self.rec["summary"])
        with self.lock:
            self.rec["tune_s"] = self._stage("tune")["elapsed_s"]
        self.poller.tune_by_studio = False

    def _was_from_settings(self) -> dict:
        """What the engine ran with before this tune (the calibration's `was`): the loaded values from
        /profile/settings captured after stage 2, else the before report's settings table."""
        cap = self.rec["_cap"]
        out: dict = {}
        rows = {r.get("name"): r for r in _d(cap.get("settings_before")).get("settings") or [] if isinstance(r, dict)}

        def val(row):
            if not row:
                return None
            v = row.get("loaded_now") if row.get("loaded_now") is not None else row.get("run_config")
            return None if v is None else str(v)
        for flag, key in (("--pcie-frac", "pcie_frac"), ("--spec-min-p", "spec_min_p"), ("--pool-workers", "pool_workers")):
            if val(rows.get(flag)) is not None:
                out[key] = val(rows[flag])
        tier = [val(rows.get(f)) for f in ("--adapt-every", "--adapt-swaps", "--adapt-decay")] if rows else None
        rep = None
        if self.rec.get("before_report") and (len(out) < 3 or tier is None):
            try:
                rep = self.reports.get(self.rec["before_report"])
            except Exception:                        # noqa: BLE001 - the report may be gone
                rep = None
        if rep:
            by = {r["flag"]: r for r in rep["settings"]["rows"]}
            for flag, key in (("--pcie-frac", "pcie_frac"), ("--spec-min-p", "spec_min_p"),
                              ("--pool-workers", "pool_workers")):
                r = by.get(flag)
                v = r and (r.get("loaded") if r.get("loaded") is not None else r.get("config"))
                if key not in out and v is not None:
                    out[key] = str(v)
            if tier is None:
                tier = [(by.get(f) or {}).get("config") for f in ("--adapt-every", "--adapt-swaps", "--adapt-decay")]
        if tier is not None and (rep or rows):
            out["adapt"] = "/".join(tier) if all(tier) else "default"
        return out

    # --- stage 4
    def _stage_reload(self):
        self._begin("reload")
        t = self.timing
        model = self.poller.health.get("model")
        if not model:
            try:
                model = _d(self.client.call("health", timeout=t.check_timeout).data).get("model")
            except StrataError:
                model = None
        body = {"model": model or "default", "messages": [{"role": "user", "content": RELOAD_BODY_TEXT}],
                "max_tokens": 8, "temperature": 0, "stream": False}
        start = self.clock()
        seq0 = self.poller.newest_seq()
        deadline = start + t.reload_timeout
        attempts = 0
        loaded = False
        with self.lock:
            self.rec["lines"] = ["Loading the model again with one short request ..."]
        while self.clock() < deadline:
            attempts += 1
            try:
                r = self.client.call("chat", body=body, timeout=min(t.reload_attempt_timeout, deadline - self.clock()))
            except StrataError as e:
                if e.kind == "key_needed":
                    self._fail_reload("the key was refused.")
                self._wait(min(t.retry_after_default, max(0.0, deadline - self.clock())))
                continue
            if r.status == 200:
                loaded = True
                break
            if r.status == 503:                       # the model is loading, or the GPU is busy: wait and ask again
                try:
                    gap = float(r.headers.get("retry-after", t.retry_after_default))
                except ValueError:
                    gap = t.retry_after_default
                self._wait(min(gap, max(0.0, deadline - self.clock())))
                continue
            self._fail_reload(r.message)
        if not loaded:
            self._fail_reload("it did not answer in time.")
        seconds = round(self.clock() - start, 1)
        wait_until = self.clock() + t.sample_wait
        while True:                                   # the request's own breakdown, as the poller sees it
            sample = self.poller.first_sample_with_tag("reload", seq0)
            if sample or self.clock() >= wait_until:
                break
            self._wait(min(0.1, t.sample_wait))
        with self.lock:
            self.rec["reload"] = {"seconds": seconds, "attempts": attempts, "sample": sample}
        self.rec["_cap"]["settings_after"] = self.poller.get_settings(force=True)
        self._done("reload", f"{seconds:.1f} s")

    def _fail_reload(self, why: str):
        self._skip_rest("reload")
        self._fail("reload", f"The model did not load again: {why} Look at Strata's log.")

    # --- stage 6
    def _stage_compare(self):
        self._begin("compare")
        before, after = self.rec["before_report"], self.rec["after_report"]
        with self.lock:
            self.rec["settings"] = self._build_settings()
            self.rec["compare"] = {"a": before, "b": after}
            if self.rec["state"] == "running" and self.rec["tune_lines"]:
                self.rec["lines"] = self.rec["tune_lines"]
        self._done("compare")
        self._end("succeeded", None, None)

    # --------------------------------------------------------------------------- settings before/after
    def _cap_config(self, name: str):
        """The six calibrated flags from the run config (read only), if --config was given."""
        cfg, _ = read_config(self.config_path)
        if cfg is None:
            return
        args = cfg.get("args")
        self.rec["_cap"][name] = {f: arg_value(args, f) for f in SETTING_FLAGS}

    @staticmethod
    def _rows_from_profile_settings(raw) -> dict:
        out = {}
        for r in _d(raw).get("settings") or []:
            if isinstance(r, dict) and r.get("name") in SETTING_FLAGS:
                v = r.get("run_config") if r.get("run_config") is not None else r.get("loaded_now")
                out[r["name"]] = None if v is None else str(v)
        return out

    def _build_settings(self) -> dict | None:
        cap = self.rec["_cap"]

        def rows_of(before: dict, after: dict, flags=SETTING_FLAGS):
            return [{"flag": f, "before": before.get(f), "after": after.get(f),
                     "changed": not same_value(before.get(f), after.get(f))} for f in flags]
        changes = cap.get("tune_changes")
        if isinstance(changes, list) and changes:
            rows = []
            for c in changes:
                if not isinstance(c, dict) or not c.get("flag"):
                    continue
                b = c.get("before") if c.get("before") is not None else c.get("before_loaded")
                rows.append({"flag": c["flag"], "before": None if b is None else str(b),
                             "after": None if c.get("after") is None else str(c["after"]),
                             "changed": bool(c.get("changed"))})
            if rows:
                return {"source": "tune.report", "rows": rows}
        sb, sa = self._rows_from_profile_settings(cap.get("settings_before")), \
            self._rows_from_profile_settings(cap.get("settings_after"))
        if sb and sa:
            return {"source": "profile/settings", "rows": rows_of(sb, sa)}
        cb, ca = cap.get("config_before"), cap.get("config_after")
        if cb is not None and ca is not None:
            return {"source": "config", "rows": rows_of(cb, ca)}
        try:
            ra = self.reports.get(self.rec["before_report"])
            rb = self.reports.get(self.rec["after_report"])
        except Exception:                            # noqa: BLE001
            return None

        def shown(rep):
            out = {}
            for r in rep["settings"]["rows"]:
                if r["flag"] in SETTING_FLAGS:
                    out[r["flag"]] = r.get("loaded") if r.get("loaded") is not None else r.get("config")
            return out
        if ra["settings"]["rows"] and rb["settings"]["rows"]:
            return {"source": "reports", "rows": rows_of(shown(ra), shown(rb))}
        return None

    # -------------------------------------------------------------------------------------- the end
    def _end(self, state: str, failed_stage: str | None, message: str | None):
        if self._abort.is_set():
            raise _Abandoned()
        with self.lock:
            rec = self.rec
            rec["state"] = state
            rec["ended_at"] = self.clock()
            rec["stage"] = failed_stage if state != "succeeded" else "compare"
            rec["error"] = message
            rec["failed_stage"] = failed_stage if state == "failed" else None
            rec["outcome"] = state
            if state != "succeeded":
                for st in rec["stages"]:
                    if st["state"] == "pending":
                        st["state"] = "skipped"
                    elif st["state"] == "running":
                        st["state"] = "failed"
                        st["message"] = message
            if rec["tune_lines"] and not rec["lines"]:
                rec["lines"] = rec["tune_lines"]
        self.poller.set_tag(None)
        self.poller.tune_by_studio = False
        self.state.save_tune(self._tune_record())
        self.state.set_active_flow(None)
        self.poller.scan_reports(force=True)
        self.state.write(force=True)

    def _tune_record(self) -> dict:
        with self.lock:
            r = self.rec
            old = next((t for t in self.state.tunes() if t.get("id") == r["id"]), {})
            return {"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"], "outcome": r["outcome"],
                    "failed_stage": r["failed_stage"], "error": r["error"], "long": r["long"],
                    "before_report": r["before_report"], "after_report": r["after_report"],
                    "summary": r["summary"], "tok_s": r["tok_s"], "tune_s": r["tune_s"],
                    "candidates": copy.deepcopy(r["candidates"]), "settings": copy.deepcopy(r["settings"]),
                    "reload": copy.deepcopy(r["reload"]),
                    "stages": [{"key": s["key"], "state": s["state"], "elapsed_s": s["elapsed_s"],
                                "message": s["message"]} for s in r["stages"]],
                    "lines_tail": [str(x)[:300] for x in r["tune_lines"][-LINES_TAIL:]],
                    "note": old.get("note", "")}

    # ------------------------------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        with self.lock:
            r = self.rec
            if r is None:
                return {"id": None, "state": "idle", "stage": None, "started_at": None, "elapsed_s": None,
                        "stages": [], "lines": [], "candidates": None, "summary": None, "tok_s": None,
                        "before_report": None, "after_report": None, "settings": None, "reload": None,
                        "error": None, "compare": None, "can_cancel": False, "can_retry_after": False}
            now = self.clock()
            stages = []
            for s in r["stages"]:
                elapsed = s["elapsed_s"]
                if s["state"] == "running" and s["started_at"]:
                    elapsed = round(now - s["started_at"], 1)
                stages.append({"key": s["key"], "label": s["label"], "state": s["state"],
                               "started_at": s["started_at"], "elapsed_s": elapsed, "timeout_s": s["timeout_s"],
                               "message": s["message"]})
            end = r["ended_at"] or now
            running = r["state"] == "running"
            return {"id": r["id"], "state": r["state"], "stage": r["stage"], "started_at": r["started_at"],
                    "elapsed_s": round(end - r["started_at"], 1), "stages": stages,
                    "lines": [str(x) for x in r["lines"][-LINES_TAIL:]], "candidates": copy.deepcopy(r["candidates"]),
                    "summary": r["summary"], "tok_s": r["tok_s"], "before_report": r["before_report"],
                    "after_report": r["after_report"], "settings": copy.deepcopy(r["settings"]),
                    "reload": copy.deepcopy(r["reload"]), "error": r["error"], "compare": copy.deepcopy(r["compare"]),
                    "can_cancel": running and r["stage"] in ("check", "before") and not self._cancel,
                    "can_retry_after": (r["state"] == "failed" and r["failed_stage"] == "after"
                                        and not (self._thread and self._thread.is_alive()))}
