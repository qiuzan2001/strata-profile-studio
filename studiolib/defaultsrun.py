"""A profile run with the engine's defaults: the baseline the Compare page measures every other run against.

Strata's own /profile/run profiles the engine as it runs now, with the run config's calibrated flags.  This run uses
a temporary copy of the run config without them (the PCIe share, the draft floor, the CPU workers, the expert tier),
so every setting the calibration changes is the engine's own default:

  1. the copy goes to the Studio's temporary folder (readable by this user only; deleted when the run ends);
  2. Strata's model is unloaded through Strata's own POST /unload, so the profile's server has the VRAM;
  3. tools/strata_profile.py of the Strata checkout starts its own server from the copy, on a free port, and writes
     its report into the results folder;
  4. Strata loads its model again at its next request.

It never writes Strata's run config.  It is refused while a tune, a profile run or another defaults run is going.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .settings import read_config

CALIBRATED = ("--pcie-frac", "--spec-min-p", "--pool-workers", "--adapt-every", "--adapt-swaps", "--adapt-decay")
MAX_LINES = 400


def without_calibrated(cfg: dict) -> dict:
    out = dict(cfg)
    args = list(cfg.get("args") or [])
    for flag in CALIBRATED:
        while flag in args:
            i = args.index(flag)
            del args[i:i + 2]
    out["args"] = args
    out.pop("expert_profile_save", None)                 # a few prompts must not overwrite a learned profile
    return out


def strata_root(cfg: dict, config_path) -> Path | None:
    """The Strata checkout: the run config's folder when it holds tools/strata_profile.py, else the engine's
    grandparent ("exe": <root>/engine/strata)."""
    cands = []
    if config_path:
        cands.append(Path(config_path).resolve().parent)
    exe = cfg.get("exe") if isinstance(cfg.get("exe"), str) else None
    if exe:
        cands.append(Path(os.path.expanduser(exe)).resolve().parent.parent)
    for c in cands:
        if (c / "tools" / "strata_profile.py").is_file():
            return c
    return None


def free_port(start=8200) -> int:
    p = start
    while p < start + 200:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
        p += 1
    raise OSError("no free port")


class DefaultsRun:
    def __init__(self, client, poller, results_dir, config_path, key=None, tool=None, clock=time.time):
        self.client, self.poller = client, poller
        self.results_dir, self.config_path, self.key = Path(results_dir), config_path, key
        self.tool = tool                                   # tests: a command list instead of strata_profile.py
        self.clock = clock
        self.lock = threading.Lock()
        self.state = "idle"                                # idle | running | done | failed
        self.lines: deque = deque(maxlen=MAX_LINES)
        self.started_at = self.ended_at = None
        self.error = None
        self.report = None

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "lines": list(self.lines), "started_at": self.started_at,
                    "elapsed_s": round((self.ended_at or self.clock()) - self.started_at, 1) if self.started_at else None,
                    "error": self.error, "report": self.report}

    def check(self, busy_reason) -> tuple[dict, Path]:
        """The config and the Strata checkout, or ValueError with the reason it cannot start."""
        if self.state == "running":
            raise ValueError("A defaults run is already going.")
        if busy_reason:
            raise ValueError(busy_reason)
        cfg, err = read_config(self.config_path)
        if cfg is None:
            raise ValueError(err or "The Studio needs --config to know the run config.")
        root = strata_root(cfg, self.config_path) if self.tool is None else Path(".")
        if root is None:
            raise ValueError("The Strata checkout (tools/strata_profile.py) is not next to the run config.")
        if not self.results_dir.is_dir():
            raise ValueError(f"The results folder {self.results_dir} does not exist.")
        return cfg, root

    def start(self, busy_reason=None) -> None:
        cfg, root = self.check(busy_reason)
        with self.lock:
            self.state, self.error, self.report = "running", None, None
            self.lines.clear()
            self.started_at, self.ended_at = self.clock(), None
        threading.Thread(target=self._run, args=(cfg, root), name="defaults-run", daemon=True).start()

    def _say(self, line: str):
        with self.lock:
            self.lines.append(str(line)[:300])

    def _end(self, state, error=None):
        with self.lock:
            self.state, self.error, self.ended_at = state, error, self.clock()
        self.poller.scan_cache = (0.0, None)

    def _run(self, cfg: dict, root: Path):
        tmpdir = Path(tempfile.mkdtemp(prefix="studio-defaults-"))
        tmp = tmpdir / "strata-defaults.json"
        try:
            tmp.touch(mode=0o600)
            tmp.write_text(json.dumps(without_calibrated(cfg), indent=2), encoding="utf-8")
            self._say(f"A copy of the run config without {', '.join(CALIBRATED)} is in {tmp}.")
            if self.poller.health.get("loaded"):
                self._say("Unloading Strata's model so the profile's own server has the VRAM ...")
                try:
                    r = self.client.call("unload", body={})
                    if r.status != 200:
                        self._end("failed", f"Strata did not unload its model: {r.message}")
                        return
                except Exception as e:                     # noqa: BLE001
                    self._end("failed", f"Strata did not answer the unload ({type(e).__name__}).")
                    return
            before = {p.name for p in self.results_dir.iterdir()}
            if self.tool is not None:
                cmd = list(self.tool) + ["--config", str(tmp), "--out", str(self.results_dir)]
            else:
                cmd = [sys.executable, str(root / "tools" / "strata_profile.py"), "--config", str(tmp),
                       "--port", str(free_port()), "--out", str(self.results_dir)]
            env = dict(os.environ)
            if self.key:
                env["STRATA_KEY"] = self.key
            self._say("Starting: " + " ".join(cmd[1:3]) + " ... (loading the model takes 1-3 minutes)")
            proc = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    env=env)
            for line in proc.stdout:
                self._say(line.rstrip("\n"))
            code = proc.wait()
            new = sorted(p.name for p in self.results_dir.iterdir() if p.name not in before and "-profile-" in p.name)
            with self.lock:
                self.report = new[-1] if new else None
            if code != 0:
                self._end("failed", f"The profile tool ended with exit code {code}.")
            elif not new:
                self._end("failed", "The profile tool ended without a report in the results folder.")
            else:
                self._say("Done. Strata loads its model again at its next request.")
                self._end("done")
        except Exception as e:                             # noqa: BLE001 - the run must end, not hang
            self._end("failed", f"The defaults run stopped ({type(e).__name__}: {e}).")
        finally:
            try:
                tmp.unlink()
                tmpdir.rmdir()
            except OSError:
                pass
