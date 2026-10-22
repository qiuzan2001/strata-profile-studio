"""The tune with the standard test: the closed loop.

  quiet      nobody else uses Strata (the engine log shows no request for a while)
  analyse    the standard test: one conversation of 6 turns (replay.STANDARD_TURNS), the same on every PC and in every run
  measure    interleaved rounds (studiolib/abtest.py): the current settings twice (the noise of this PC) and each
             candidate once, per round, in a random order. The replay (studiolib/replay.py) is the work.
  apply      the winner goes into the run config, after a copy of the file
  restart    the restart command (an argument list, no shell) and a wait for /health
  verify     the same rounds again: the new config against the old values
  rollback   the copy goes back, and Strata restarts, when the new config is worse than the old one

Only knobs that do not change the answer are tuned: the per-request keys pcie_frac and spec_min_p. The engine verifies
every draft, and where an expert is computed does not change the choice of the token. The replay still compares the
text of each arm with the text of the current settings. An arm with another text is never applied.
The knobs that change the answers (thinking budget, route tail skip) are in Settings, for the user to set.
"""
from __future__ import annotations

import copy
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import abtest, configwrite, replay, workload
from .settings import arg_value, read_config

PCIE_GRID = (0.2, 0.35, 0.55, 0.75)
SPEC_GRID = (0.3, 0.5, 0.7)
MIN_GAIN = 0.02                       # a change smaller than 2% is not worth a restart
DEFAULT_ROUNDS = 5
MAX_EXTRA_ROUNDS = 3                  # rounds that replace the rounds that another user's request spoiled
STAGES = ("quiet", "analyse", "calibrate", "measure", "apply", "restart", "verify")


class TuneError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _Stop(Exception):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state, self.message = state, message


def candidates(current: dict, quick: bool = False) -> dict[str, dict]:
    """The arms to try, besides the current settings: {name: per-request keys}. The names tell the value."""
    arms = {}
    cur = current.get("pcie_frac")
    values = [v for v in PCIE_GRID if cur is None or abs(v - cur) >= 0.05]
    if quick and cur is not None:                       # one lower and one higher
        values = [max([v for v in values if v < cur], default=None), min([v for v in values if v > cur], default=None)]
        values = [v for v in values if v is not None]
    for v in values:
        arms[f"pcie_frac {v:g}"] = {"pcie_frac": v}
    if not quick:
        cur = current.get("spec_min_p")
        for v in SPEC_GRID:
            if cur is None or abs(v - cur) >= 0.05:
                arms[f"spec_min_p {v:g}"] = {"spec_min_p": v}
    return arms


def estimate(shapes: list[dict], arms: int, rounds: int, prompt_tok_s, decode_tok_s) -> float | None:
    """The seconds of the measure stage: each of arms + 2 (the current settings twice) runs once per round."""
    one = replay.shapes_seconds(shapes, prompt_tok_s, decode_tok_s)
    return None if one is None else one * (arms + 2) * rounds


class MyTune:
    def __init__(self, client: replay.Client, engine_log, config_path, restart_cmd=None, health=None,
                 clock=time.time, sleep=time.sleep, quiet_s: float = 90.0, wait_s: float = 300.0,
                 restart_wait_s: float = 600.0):
        self.client = client
        self.engine_log = engine_log
        self.config_path = config_path
        self.restart_cmd = list(restart_cmd) if restart_cmd else None
        self.health = health or self._health
        self.clock, self.sleep = clock, sleep
        self.quiet_s, self.wait_s, self.restart_wait_s = quiet_s, wait_s, restart_wait_s
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.state = {"state": "idle"}

    # ----------------------------------------------------------------------------------------- public
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy({**self.state, "restart_command": bool(self.restart_cmd)})

    def plan(self, opts: dict | None = None) -> dict:
        """What a run would do and how long it takes (an estimate from the log's own speeds), without a request."""
        opts = opts or {}
        shapes = replay.standard_turns()
        cur = self._current()
        arms = candidates(cur, bool(opts.get("quick")))
        rounds = max(abtest.MIN_ROUNDS, int(opts.get("rounds") or DEFAULT_ROUNDS))
        sec = estimate(shapes, len(arms), rounds, *self._speeds())
        return {"shapes": shapes, "arms": list(arms), "rounds": rounds, "seconds": sec, "current": cur,
                "note": replay.STANDARD_NOTE, "restart_command": bool(self.restart_cmd),
                "config_readable": bool(read_config(self.config_path)[0])}

    def start(self, opts: dict | None = None) -> None:
        opts = dict(opts or {})
        if self.running():
            raise TuneError(409, "A tune is already running.")
        if not self.config_path or read_config(self.config_path)[0] is None:
            raise TuneError(409, "The Studio cannot read Strata's run config, so it cannot write the result. Start it with --config.")
        auto_apply = bool(opts.get("apply"))
        if auto_apply and self.restart_cmd is None and not opts.get("apply_without_restart"):
            raise TuneError(409, "The Studio has no restart command, so it cannot check the result after a restart. "
                                 "Start the Studio with --restart-cmd, or tick \"Write the result without a restart\".")
        self.cancel_event.clear()
        with self.lock:
            self.state = {"state": "running", "stage": "quiet", "message": "Waiting for Strata to be quiet.",
                          "started_at": self.clock(), "log": [], "progress": None, "opts": opts, "result": None,
                          "stages": {k: "pending" for k in STAGES}}
        self.thread = threading.Thread(target=self._run, args=(opts,), daemon=True)
        self.thread.start()

    def cancel(self) -> bool:
        if not self.running():
            return False
        self.cancel_event.set()
        return True

    # ------------------------------------------------------------------------------------------ state
    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _stage(self, key: str, status: str, message: str | None = None):
        with self.lock:
            self.state["stages"][key] = status
            if status == "running":
                self.state["stage"] = key
            if message is not None:
                self.state["message"] = message

    def _log(self, line: str):
        with self.lock:
            self.state["log"].append(line)
            del self.state["log"][:-200]

    def _check(self):
        if self.cancel_event.is_set():
            raise _Stop("cancelled", "Cancelled. Nothing was written.")

    # ---------------------------------------------------------------------------------------- helpers
    def _health(self) -> dict | None:
        return self.client.health()

    def _current(self) -> dict:
        cfg, _ = read_config(self.config_path)
        args = (cfg or {}).get("args") if isinstance((cfg or {}).get("args"), list) else []
        out = {}
        for key, flag in (("pcie_frac", "--pcie-frac"), ("spec_min_p", "--spec-min-p")):
            try:
                v = arg_value(args, flag)
                out[key] = None if v is None else float(v)
            except (TypeError, ValueError):
                out[key] = None
        return out

    def _log_count(self) -> int | None:
        if not self.engine_log:
            return None
        try:
            return len(workload.requests_of(workload.tail_lines(self.engine_log)))
        except OSError:
            return None

    def _last_request_age(self) -> float | None:
        if not self.engine_log:
            return None
        try:
            return max(0.0, self.clock() - os.stat(self.engine_log).st_mtime)
        except OSError:
            return None

    def _speeds(self) -> tuple[float | None, float | None]:
        """The prompt and the reply speed (tok/s) that this PC showed in its engine log, for the time estimate only."""
        if not self.engine_log:
            return None, None
        try:
            reqs = [r for r in workload.requests_of(workload.tail_lines(self.engine_log)) if r["prompt_tokens"] >= 2000][-100:]
        except OSError:
            return None, None
        m = workload.summarize(reqs)
        return (m["prompt_tok_s"], m["decode_tok_s"]) if m else (None, None)

    # ---------------------------------------------------------------------------------- measure rounds
    def _measure(self, shapes, sizer, arms: dict[str, dict], rounds: int, seed: int, label: str) -> dict:
        """Interleaved rounds. `arms` has the arm "base" ({} keys) and the others. Returns the per-arm scores."""
        names = ["base", "base_aa", *[n for n in arms if n != "base"]]
        tunes = {"base": arms.get("base", {}), "base_aa": arms.get("base", {}), **{n: t for n, t in arms.items() if n != "base"}}
        scores = {n: [] for n in names}
        hashes = {n: [] for n in names}
        stats = {n: [] for n in names}
        spoiled = 0
        done = 0
        total = rounds
        sched = abtest.schedule(names, rounds + MAX_EXTRA_ROUNDS, seed)
        i = 0
        while done < total and i < len(sched):
            self._check()
            results, ok = {}, True
            for name in sched[i]:
                self._check()
                before = self._log_count()
                out = replay.one_pass(self.client, shapes, sizer, tunes[name], f"{label}-{i}-{name}", self.cancel_event)
                if out["error"] == "cancelled":
                    raise _Stop("cancelled", "Cancelled. Nothing was written.")
                if out["error"]:
                    raise _Stop("failed", f"A replay request failed: {out['error']}")
                if out["cached"] > replay.CLEAN_FIRST:
                    ok = False                           # the first turn found a prefix cached: it was not a clean read
                if out["reuse_min"] is not None and out["reuse_min"] < replay.REUSE_MIN:
                    ok = False                           # a later turn did not reuse the conversation (evicted, or no cache)
                after = self._log_count()
                if before is not None and after is not None and after - before > len(shapes):
                    ok = False                           # somebody else used Strata in this pass
                results[name] = out
            i += 1
            if not ok:
                spoiled += 1
                self._log(f"{label}: round {i} dropped (another request reached Strata, or the conversation was not reused as planned).")
                continue
            for name, out in results.items():
                scores[name].append(out["score_ms"])
                hashes[name].append(out["hash"])
                stats[name].append(out)
            done += 1
            self._set(progress={"label": label, "round": done, "rounds": total, "spoiled": spoiled})
        return {"names": names, "tunes": tunes, "scores": scores, "hashes": hashes, "stats": stats, "rounds": done,
                "spoiled": spoiled}

    @staticmethod
    def _verdicts(m: dict, seed: int = 1) -> dict:
        base, aa = m["scores"]["base"], m["scores"]["base_aa"]
        floor = abtest.noise_floor(base, aa)
        # the text of the two copies of the current settings: if they differ by themselves, a text check says nothing
        aa_same = m["hashes"]["base"] == m["hashes"]["base_aa"]
        out = {"floor": floor, "output_check": "reliable" if aa_same else "unreliable", "arms": {}}
        for name in m["names"]:
            if name in ("base", "base_aa"):
                continue
            v = abtest.verdict(base, m["scores"][name], floor, lower_is_better=True, min_gain=MIN_GAIN, seed=seed)
            same = m["hashes"][name] == m["hashes"]["base"]
            v["same_output"] = same if aa_same else None
            v["tune"] = m["tunes"][name]
            v["median_ms"] = _median(m["scores"][name])
            out["arms"][name] = v
        out["base_median_ms"] = _median(base)
        # every round of every arm, so that a noisy run can be read afterwards (the order is the order of the rounds)
        out["per_round"] = {n: [{"score_ms": o["score_ms"], "generated": o["generated"], "decode_tok_s": o["decode_tok_s"],
                                 "drafts_accepted": o["drafts_accepted"], "wall_s": o["wall_s"], "hash": o["hash"][:8]}
                                for o in m["stats"][n]] for n in m["names"]}
        return out

    # ------------------------------------------------------------------------------------------ the run
    def _run(self, opts: dict):
        try:
            self._flow(opts)
        except _Stop as s:
            self._finish(s.state, s.message)
        except replay.ReplayError as e:
            self._finish("failed", f"The replay could not start: {e}")
        except TuneError as e:
            self._finish("failed", e.message)
        except Exception as e:                           # noqa: BLE001
            self._finish("failed", f"The tune stopped on an error: {type(e).__name__}: {e}")

    def _finish(self, state: str, message: str, result=None):
        with self.lock:
            self.state["state"] = state
            self.state["message"] = message
            self.state["finished_at"] = self.clock()
            if result is not None:
                self.state["result"] = result

    def _flow(self, opts: dict):
        # ------------------------------------------------------------------ quiet
        self._stage("quiet", "running")
        if self.engine_log is None:
            self._log("The Studio does not know the engine log: it cannot tell whether another client uses Strata.")
        else:
            waited = 0.0
            while True:
                self._check()
                age = self._last_request_age()
                if age is None or age >= self.quiet_s:
                    break
                if waited >= self.wait_s:
                    raise _Stop("failed", f"Strata served a request {age:.0f} s ago. Run the tune when nobody uses Strata "
                                          f"(it waited {waited:.0f} s).")
                self._set(message=f"Strata served a request {age:.0f} s ago. Waiting for {self.quiet_s:.0f} quiet seconds.")
                self.sleep(2.0)
                waited += 2.0
        self._stage("quiet", "done")
        # ------------------------------------------------------------------ analyse
        self._stage("analyse", "running", "Reading the shape of the requests.")
        shapes = replay.standard_turns()
        self._log(replay.STANDARD_NOTE)
        cur = self._current()
        arms = candidates(cur, bool(opts.get("quick")))
        if not arms:
            raise _Stop("failed", "There is no candidate to try.")
        rounds = max(abtest.MIN_ROUNDS, int(opts.get("rounds") or DEFAULT_ROUNDS))
        self._stage("analyse", "done")
        self._stage("calibrate", "running", "Measuring how many tokens a word is.")
        sizer = replay.Sizer(self.client, self.cancel_event)
        sizer.calibrate()
        self._stage("calibrate", "done")
        # ------------------------------------------------------------------ measure
        self._stage("measure", "running", f"{len(arms)} candidates, {rounds} rounds.")
        m = self._measure(shapes, sizer, {"base": {}, **arms}, rounds, seed=1, label="measure")
        v = self._verdicts(m)
        if m["rounds"] < abtest.MIN_ROUNDS:
            raise _Stop("failed", f"Only {m['rounds']} clean rounds. Other requests reached Strata during the tune, or Strata did not keep the conversation between turns. Run it again when Strata is quiet.")
        winners = {n: a for n, a in v["arms"].items() if a["verdict"] == "better" and a["same_output"] is not False}
        excluded = [n for n, a in v["arms"].items() if a["same_output"] is False]
        combo = None
        by_knob = {}
        for n, a in sorted(winners.items(), key=lambda kv: -kv[1]["gain"]):
            knob = next(iter(a["tune"]))
            by_knob.setdefault(knob, n)
        if len(by_knob) >= 2:                                 # two knobs won: do they add up?
            tune = {}
            for n in by_knob.values():
                tune.update(winners[n]["tune"])
            self._set(message="Two settings won. Checking them together.")
            m2 = self._measure(shapes, sizer, {"base": {}, "both": tune}, rounds, seed=2, label="together")
            v2 = self._verdicts(m2, seed=2)
            combo = {"tune": tune, **v2["arms"]["both"]}
            if combo["verdict"] == "better" and combo["same_output"] is not False:
                best = ("both", combo)
            else:
                n = max(winners, key=lambda x: winners[x]["gain"])
                best = (n, winners[n])
        elif winners:
            n = max(winners, key=lambda x: winners[x]["gain"])
            best = (n, winners[n])
        else:
            best = None
        self._stage("measure", "done")
        result = {"shapes": shapes, "rounds": m["rounds"], "spoiled": m["spoiled"], "current": cur, "floor": v["floor"],
                  "output_check": v["output_check"], "base_median_ms": v["base_median_ms"], "arms": v["arms"], "per_round": v["per_round"],
                  "excluded_for_output": excluded, "combined": combo, "winner": None, "applied": None,
                  "verify": None, "rolled_back": False}
        if best is None:
            for k in ("apply", "restart", "verify"):
                self._stage(k, "skipped")
            self._finish("done", "No setting is better than the current ones by more than the noise of this PC "
                                 f"({v['floor'] * 100:.1f}%) and 2%. The config is unchanged.", result)
            return
        name, arm = best
        result["winner"] = {"name": name, "tune": arm["tune"], "gain": arm["gain"], "ci": arm["ci"]}
        self._set(result=result)
        # ------------------------------------------------------------------ apply
        if not opts.get("apply"):
            for k in ("apply", "restart", "verify"):
                self._stage(k, "skipped")
            self._finish("done", f"{name} is {arm['gain'] * 100:.1f}% faster on the standard test. Nothing was written: "
                                 "tick \"Write the result\" to apply it.", result)
            return
        self._stage("apply", "running", "Writing the config.")
        applied = self._apply(arm["tune"], cur)
        result["applied"] = applied
        self._set(result=result)
        self._stage("apply", "done")
        if self.restart_cmd is None:
            self._stage("restart", "skipped")
            self._stage("verify", "skipped")
            self._finish("needs_restart", "The config is written. Restart Strata to load it. The Studio has no restart command.", result)
            return
        # ------------------------------------------------------------------ restart + verify
        self._stage("restart", "running", "Restarting Strata.")
        self._restart()
        self._stage("restart", "done")
        self._stage("verify", "running", "Checking the new config against the old values.")
        old = {k: cur[k] for k in arm["tune"] if cur.get(k) is not None}
        if len(old) != len(arm["tune"]):
            self._stage("verify", "skipped")
            self._finish("done", "The config is written and Strata is restarted. The old value of a setting is the engine default, "
                                 "which the Studio cannot send, so it did not check.", result)
            return
        sizer2 = replay.Sizer(self.client, self.cancel_event)
        sizer2.calibrate()
        m3 = self._measure(shapes, sizer2, {"base": {}, "old": old}, rounds, seed=3, label="verify")
        v3 = self._verdicts(m3, seed=3)
        # "old" against "base" (the new config): the new config is better when the old values are worse
        old_v = v3["arms"]["old"]
        result["verify"] = {"verdict": old_v["verdict"], "gain_of_new": -old_v["gain"] if old_v["gain"] is not None else None,
                            "floor": v3["floor"], "rounds": m3["rounds"]}
        if old_v["verdict"] == "better" and opts.get("rollback", True):
            self._stage("verify", "failed")
            self._rollback(applied)
            result["rolled_back"] = True
            self._finish("rolled_back", "After the restart the old values were faster than the new config. The old config is back.", result)
            return
        self._stage("verify", "done")
        self._finish("done", f"Applied {name}. The check after the restart found the new config "
                             f"{'faster' if old_v['verdict'] == 'worse' else 'no slower'} than the old values.", result)

    # -------------------------------------------------------------------------------- write and restart
    def _apply(self, tune: dict, cur: dict) -> dict:
        path = Path(self.config_path)
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(self.clock()))
        backup = path.with_name(f"{path.name}.bak-pre-mytune-{stamp}")
        shutil.copy2(path, backup)
        doc = configwrite.file_doc(path)
        edits = [{"id": k, "value": f"{v:g}"} for k, v in tune.items()]
        try:
            res = configwrite.apply_edits(path, edits, doc["sha"])
        except configwrite.ConfigWriteError as e:
            raise _Stop("failed", f"The config was not written: {e}")
        return {"backup": str(backup), "changes": res.get("changes"), "from": {k: cur.get(k) for k in tune}, "to": dict(tune)}

    def _restart(self):
        try:
            p = subprocess.run(self.restart_cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise _Stop("failed", f"The restart command failed: {e}")
        if p.returncode != 0:
            raise _Stop("failed", f"The restart command exited with {p.returncode}: {(p.stderr or p.stdout)[-200:]}")
        self.sleep(1.0)
        end = self.clock() + self.restart_wait_s
        while self.clock() < end:
            self._check()
            h = self.health()
            if h and h.get("loaded"):
                return
            self.sleep(2.0)
        raise _Stop("failed", f"Strata did not report loaded within {self.restart_wait_s:.0f} s after the restart.")

    def _rollback(self, applied: dict):
        shutil.copy2(applied["backup"], self.config_path)
        self._log("The old config was copied back.")
        self._restart()


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2 if xs else None
