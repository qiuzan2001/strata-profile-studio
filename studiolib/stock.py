"""Stock mode: the Studio against a Strata build without the /profile routes (Strata 0.1.44.1 as released).

Strata PRs #2660 and #2661 add tools/strata_profile.py and the /profile/* routes (the last request's breakdown, a
profile run, a tune).  A release build has none of them.  Then the Studio does that work itself, and answers the
same calls as those routes, so the poller, the tune flow and the pages need no second code path:

  last     the newest request's breakdown from the engine log (studiolib/timinglog.py), when the engine writes its
           timing lines (STRATA_PREFILL_TIMING=1 and STRATA_DECODE_TIMING=1 in the run config's "env");
  run      studiolib/vendor/strata_profile.py against the running server (--url), with Strata's tools/ on its path;
  tune     POST /unload to Strata, a check that /health says loaded: false, then setup.calibrate_config(config)
           (studiolib/vendor/calibrate_one.py).  A stock server reads its config at start, so the tuned settings
           apply after a restart of Strata, which the Studio cannot do;
  status   the run's and the tune's state, as GET /profile/status answers it.

The mode is chosen from what Strata answers, without a flag: /profile/status 404 is stock mode, except on a build
that has the routes (serve/profile_page.py in its folder), where 404 means that profiling is off.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import timinglog
from .defaultsrun import strata_python
from .settings import read_config
from .strata import Response, StrataError

VENDOR = Path(__file__).resolve().parent / "vendor"
TIMING_ENV = {"STRATA_PREFILL_TIMING": "1", "STRATA_DECODE_TIMING": "1"}
MSG_STOCK = ("This Strata build has no profile routes (Strata 0.1.44.1 as released; they are in Strata PR #2661). "
             "The Studio does that work itself: see Settings.")
MSG_RESTART_TUNE = "Restart Strata to load the tuned settings."
MSG_RESTART_TIMING = "Restart Strata so the engine starts with them."
LOG_SCAN = 4 * 1024 * 1024                              # how much of the log's tail is searched for a timing line
STOCK_CALLS = ("last", "status", "run", "tune")


def strata_root(config_path, explicit=None) -> tuple[Path | None, list[str]]:
    """Strata's checkout: --strata-root, else the run config's folder, else the engine's grandparent.  It is the
    folder with tools/strata_bench.py.  (the folder or None, the folders looked at)."""
    cands = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    cfg = None
    if config_path:
        cands.append(Path(config_path).expanduser().resolve().parent)
        cfg, _ = read_config(config_path)
    exe = (cfg or {}).get("exe")
    if isinstance(exe, str) and exe and "\\" not in exe:
        cands.append(Path(os.path.expanduser(exe)).resolve().parent.parent)
    looked = []
    for c in cands:
        looked.append(str(c))
        if (c / "tools" / "strata_bench.py").is_file():
            return c.resolve(), looked
    return None, looked


def has_routes(root: Path | None) -> bool:
    """A Strata build with the /profile routes (PR #2661): its 404 means profiling is off, not stock mode."""
    return bool(root) and (root / "serve" / "profile_page.py").is_file()


def env_state(config_path) -> dict:
    """{name: its value in the run config's "env", or None} for the two timing switches; None when no config."""
    cfg, _ = read_config(config_path) if config_path else (None, None)
    if cfg is None:
        return {}
    env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
    return {k: (str(env[k]) if k in env else None) for k in TIMING_ENV}


def log_state(path) -> dict:
    """Whether the Studio can read the engine log, and whether it has a `strata decode timing:` line."""
    out = {"path": str(path) if path else None, "exists": False, "readable": False, "timing_lines": False}
    if not path:
        return out
    out["exists"] = os.path.exists(path)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - LOG_SCAN))
            tail = f.read()
    except OSError:
        return out
    out["readable"] = True
    out["timing_lines"] = b"strata decode timing:" in tail
    return out


class ToolRun:
    """One child process at a time (the profile tool, or the calibration), its output kept, its report read back
    when it ends.  The state is what Strata's GET /profile/status gives for its own runs (PR #2661)."""

    def __init__(self, name: str, find_report, popen=subprocess.Popen, on_end=None):
        self.name, self.find_report, self.popen, self.on_end = name, find_report, popen, on_end
        self.lock = threading.Lock()
        self.proc = None
        self.lines: collections.deque = collections.deque(maxlen=400)
        self.state = "idle"                             # idle | running | done | failed
        self.started_at = self.ended_at = self.returncode = None
        self.report = None
        self.command = None

    def running(self) -> bool:
        with self.lock:
            return self.state == "running"

    def start(self, cmd: list[str], cwd: str, env: dict) -> str:
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return "running"
            self.lines.clear()
            self.report, self.returncode, self.ended_at = None, None, None
            self.started_at, self.state, self.command = time.time(), "running", list(cmd)
            try:
                self.proc = self.popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=cwd, env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                self.state, self.ended_at = "failed", time.time()
                self.lines.append(f"The {self.name} could not start: {e}")
                return "failed"
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True, name=f"stock-{self.name}").start()
            return "started"

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                self.lines.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass
        rc = proc.wait()
        with self.lock:
            if self.proc is not proc:
                return
            self.returncode, self.ended_at = rc, time.time()
            self.report = self.find_report(list(self.lines))
            self.state = "done" if rc == 0 else "failed"
        if self.on_end:
            self.on_end()

    def stop(self):
        with self.lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass

    def status(self) -> dict:
        with self.lock:
            now = time.time()
            return {"state": self.state, "lines": list(self.lines), "started_at": self.started_at,
                    "elapsed_s": round((self.ended_at or now) - self.started_at, 1) if self.started_at else None,
                    "returncode": self.returncode, "report": self.report, "note": None}


class StockBackend:
    """The /profile/* calls of a Strata build that has none, answered by the Studio (see the module text)."""

    def __init__(self, client, results_dir, config_path=None, engine_log=None, explicit_root=None, key=None,
                 popen=subprocess.Popen, python=None):
        self.client = client
        self.results_dir = Path(results_dir)
        self.config_path, self.engine_log, self.explicit_root, self.key = config_path, engine_log, explicit_root, key
        self.python = python
        self.run = ToolRun("profile run", self._find_report, popen)
        self.tune = ToolRun("tuning", self._find_tune_report, popen)
        self.last = timinglog.LastRequest()
        self._log_cache = (0.0, None)

    # ------------------------------------------------------------------------------------------- facts
    def root(self):
        return strata_root(self.config_path, self.explicit_root)

    def routes_build(self) -> bool:
        return has_routes(self.root()[0])

    def log(self) -> dict:
        """log_state, cached for 5 s (the poller asks every second)."""
        at, doc = self._log_cache
        if doc is None or time.time() - at > 5:
            doc = log_state(self.engine_log)
            self._log_cache = (time.time(), doc)
        return doc

    def log_has_timing(self) -> bool:
        d = self.log()
        return d["readable"] and d["timing_lines"]

    def _python(self, root: Path | None) -> str:
        if self.python:
            return str(self.python)
        p = strata_python(root) if root else None
        return str(p or sys.executable)

    def tools_reason(self) -> str | None:
        root, looked = self.root()
        if root is not None:
            return None
        return ("Strata's tools/ (with strata_bench.py) is not found. The Studio looked in " + ", ".join(looked or ["no folder"])
                + ". Start the Studio with --strata-root <Strata folder>, or with --config <Strata folder>/strata-<model>.json.")

    def tune_reason(self) -> str | None:
        """Why this Studio cannot tune this Strata, or None."""
        if not self.config_path:
            return "The tune needs the run config: start the Studio with --config <Strata folder>/strata-<model>.json."
        root, _ = self.root()
        if root is None:
            return self.tools_reason()
        if not (root / "setup.py").is_file():
            return f"There is no setup.py in {root}: the tune runs Strata's own setup.calibrate_config."
        return None

    # ------------------------------------------------------------------------------------------- commands
    def run_command(self, long=None) -> tuple[list[str], str, dict]:
        """The profile run: (the command, its folder, its environment).  The key goes in STRATA_KEY, out of ps."""
        root, _ = self.root()
        cmd = [self._python(root), str(VENDOR / "strata_profile.py"), "--url", self.client.base_url,
               "--out", str(self.results_dir)]
        if self.log()["readable"]:
            cmd += ["--engine-log", str(self.engine_log)]
        if long is not None:
            cmd += ["--long", str(int(long))]
        env = dict(os.environ, STRATA_ROOT=str(root), PYTHONIOENCODING="utf-8")
        if self.key:
            env["STRATA_KEY"] = self.key
        return cmd, str(root), env

    def tune_command(self) -> tuple[list[str], str, dict]:
        """setup.calibrate_config(<config>) for this one config, without the engine update and the start of
        `setup.py --calibrate`."""
        root, _ = self.root()
        cmd = [self._python(root), str(VENDOR / "calibrate_one.py"), str(root), str(Path(self.config_path).resolve())]
        return cmd, str(root), dict(os.environ, PYTHONIOENCODING="utf-8")

    def _find_report(self, lines: list[str]):
        """The tool's `Report: <folder>/README.md (home folders ...)` line, inside the results folder only."""
        parent = self.results_dir.resolve()
        for line in reversed(lines):
            if not line.startswith("Report: "):
                continue
            readme = Path(line[len("Report: "):].rsplit(" (home folders", 1)[0].strip())
            if not readme.is_absolute():                   # the tool prints it from its own folder: Strata's
                readme = (self.root()[0] or parent) / readme
            readme = readme.resolve()
            if readme.name != "README.md" or readme.parent.parent != parent:
                return None
            try:
                results = json.loads((readme.parent / "results.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                results = None
            return {"path": str(readme.parent), "readme": None, "results": results}
        return None

    @staticmethod
    def _find_tune_report(lines: list[str]):
        for line in reversed(lines):
            t = line.strip()
            if "tuned for this PC" in t:
                return {"summary": t.split("tuned for this PC: ", 1)[-1].strip()}
        return None

    # ------------------------------------------------------------------------------------------- the calls
    def call(self, name: str, query=None, body=None) -> Response:
        if name == "status":
            tune_reason = self.tune_reason()
            return Response(200, {**self.run.status(), "tune": None if tune_reason else self.tune.status(),
                                  "tune_unavailable": tune_reason, "stock": True}, {})
        if name == "last":
            req = self.last.read(self.engine_log, 0) if self.log()["readable"] else None
            return Response(200, {"request": req, "log": self.log()["readable"], "stock": True}, {})
        if name == "run":
            return self._start_run(query)
        if name == "tune":
            return self._start_tune()
        return Response(404, {"error": {"message": "not found"}}, {})

    @staticmethod
    def _err(code: int, message: str, **extra) -> Response:
        return Response(code, {"error": {"message": message}, **extra}, {})

    def _start_run(self, query) -> Response:
        why = self.tools_reason()
        if why:
            return self._err(412, why)
        if self.tune.running():
            return self._err(409, "A tune is running: the profile waits for it.", status="tuning")
        long = None
        if query and query.get("long") is not None:
            try:
                long = min(max(int(query["long"]), 0), 1048576)
            except (TypeError, ValueError):
                return self._err(400, "long must be a number of tokens.")
        r = self.run.start(*self.run_command(long))
        if r == "running":
            return self._err(409, "A profile run is still going.", status=r)
        if r == "failed":
            lines = self.run.status()["lines"]
            return self._err(500, lines[-1] if lines else "The profile run could not start.", status=r)
        return Response(200, {"status": r}, {})

    def _start_tune(self) -> Response:
        why = self.tune_reason()
        if why:
            return self._err(404, why)
        if self.tune.running() or self.run.running():
            return self._err(409, "A tune or a profile run is still going.", status="running")
        try:
            r = self.client.call("unload", query={"wait": "1"}, body={})
        except StrataError as e:
            return self._err(502, f"Strata did not answer the unload ({e.message}).")
        state = (r.data or {}).get("status") if isinstance(r.data, dict) else None
        if r.status == 409 or state == "busy":
            return self._err(409, "A request is running in Strata. Try the tune again when the model is idle.", status="busy")
        if r.status != 200:
            return self._err(r.status, f"Strata did not unload the model: {r.message}")
        try:
            h = self.client.call("health")
            loaded = bool((h.data or {}).get("loaded")) if isinstance(h.data, dict) else True
        except StrataError:
            loaded = True
        if loaded:
            return self._err(409, f"Strata still holds the model (/health says loaded, the unload answered "
                                  f"\"{state}\"). The Studio starts no calibration while Strata has the GPU.", status="loaded")
        r2 = self.tune.start(*self.tune_command())
        if r2 != "started":
            lines = self.tune.status()["lines"]
            return self._err(409 if r2 == "running" else 500, lines[-1] if lines else "The tune could not start.", status=r2)
        return Response(200, {"status": "started"}, {})

    def stop(self):
        self.run.stop()
        self.tune.stop()

    # ------------------------------------------------------------------------------------------- the Settings block
    def doc(self, active: bool) -> dict:
        """The Settings page's block "Timing lines" (and what else stock mode needs)."""
        root, looked = self.root()
        log = self.log()
        env = env_state(self.config_path)
        return {"active": active, "message": MSG_STOCK if active else None,
                "config": str(self.config_path) if self.config_path else None,
                "env": env, "env_complete": bool(env) and all(env.get(k) == v for k, v in TIMING_ENV.items()),
                "log": log, "strata_root": str(root) if root else None, "looked": looked,
                "tools": self.tools_reason(), "tune": self.tune_reason(),
                "restart": MSG_RESTART_TIMING, "restart_tune": MSG_RESTART_TUNE}
