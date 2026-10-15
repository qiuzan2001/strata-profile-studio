#!/usr/bin/env python3
"""Strata Profile Studio: a profiling and tuning dashboard for Strata.

    python3 studio.py --strata http://127.0.0.1:8080 --results ~/Strata/bench/results

Then open http://127.0.0.1:8090/.  Standard library only; the page's one library (Chart.js) is vendored.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from studiolib import __version__                                          # noqa: E402
from studiolib.enginelog import EngineLog                                  # noqa: E402
from studiolib.flow import Flow                                            # noqa: E402
from studiolib.poller import Poller                                        # noqa: E402
from studiolib.reports import ReportStore                                  # noqa: E402
from studiolib.server import StudioApp, StudioServer, is_loopback, make_handler  # noqa: E402
from studiolib.state import State, StateError                              # noqa: E402
from studiolib.strata import StrataClient                                  # noqa: E402
from studiolib.timing import Timing                                        # noqa: E402

DEFAULT_RESULTS = "~/Strata/bench/results"


def public_url(url: str) -> str:
    """The Strata address without any user:password part (it is shown on the page)."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{u.scheme}://{host}{':' + str(u.port) if u.port else ''}{u.path.rstrip('/')}"


def default_engine_log(config: str | None) -> str | None:
    """The engine log Strata writes: the config's "log" key, else strata-<model>.log next to the config."""
    if not config:
        return None
    from studiolib.settings import read_config
    cfg, _ = read_config(config)
    p = Path(config)
    if cfg and isinstance(cfg.get("log"), str) and cfg["log"]:
        log = Path(os.path.expanduser(cfg["log"]))
        return str(log if log.is_absolute() else p.parent / log)
    return str(p.with_suffix(".log"))


class Studio:
    """Everything wired together.  `start()` runs the background threads, `serve_forever()` the HTTP server."""

    def __init__(self, strata: str, results: str, key: str | None = None, host: str = "127.0.0.1", port: int = 8090,
                 config: str | None = None, state_path: str | None = None, timing: Timing | None = None,
                 allow_hosts=(), engine_log: str | None = None, read_only: bool = False):
        if not is_loopback(host) and not key:
            raise SystemExit("Refusing to listen on a non-loopback address without a key: add --key KEY "
                             "(or STRATA_KEY=KEY). The same key protects the Studio and is sent to Strata.")
        self.timing = timing or Timing()
        self.state = State(state_path or ROOT / "studio-state.json")
        try:
            self.state.load()
        except StateError as e:
            raise SystemExit(str(e))
        self.results = Path(os.path.expanduser(results))
        self.client = StrataClient(strata, key, self.timing)
        self.reports = ReportStore(self.results, self.state)
        self.poller = Poller(self.client, self.timing, self.reports, self.results, state=self.state,
                             config_path=config)
        self.flow = Flow(self.client, self.poller, self.reports, self.state, self.timing, self.results,
                         config_path=config)
        # the bearer is required wherever the Studio can be reached from beyond this PC: a non-loopback bind, or
        # names let in with --allow-host (a reverse proxy) when a key was given
        studio_key = key if (not is_loopback(host) or allow_hosts) else None
        self.app = StudioApp(strata_url=public_url(strata), results_dir=self.results, config_path=config, key=key,
                             studio_key=studio_key, host=host, port=port, client=self.client, poller=self.poller,
                             flow=self.flow, reports=self.reports, state=self.state, web_dir=ROOT / "web",
                             started_at=time.time(), allow_hosts=allow_hosts,
                             engine_log=EngineLog(engine_log or default_engine_log(config)), read_only=read_only)
        try:
            self.server = StudioServer(host, port, make_handler(self.app))
        except OSError as e:
            raise SystemExit(f"Cannot listen on {host}:{port}: {e.strerror or e}")
        self.app.port = self.server.server_address[1]
        self._flusher = threading.Event()

    @property
    def port(self) -> int:
        return self.app.port

    def start(self):
        self.poller.start()
        self.flow.resume()
        threading.Thread(target=self._flush_loop, name="state-flush", daemon=True).start()
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, name="http",
                         daemon=True).start()

    def _flush_loop(self):
        while not self._flusher.wait(1.0):
            self.state.flush()

    def serve_forever(self):
        self.poller.start()
        self.flow.resume()
        threading.Thread(target=self._flush_loop, name="state-flush", daemon=True).start()
        try:
            self.server.serve_forever(poll_interval=0.05)
        finally:
            self.stop()

    def stop(self):
        self._flusher.set()
        self.flow.abandon()
        self.poller.stop()
        try:
            self.server.shutdown()
        except Exception:                                 # noqa: BLE001 - already stopped
            pass
        self.server.server_close()
        self.state.write(force=True)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="studio.py", description="Strata Profile Studio: profile, tune and compare Strata on this PC.",
        epilog="The API key can also be given as STRATA_KEY in the environment, which keeps it out of `ps`.")
    ap.add_argument("--strata", default="http://127.0.0.1:8080", help="Strata's address (default %(default)s)")
    ap.add_argument("--results", default=DEFAULT_RESULTS,
                    help="Strata's bench/results folder, where profile reports are read from (default %(default)s)")
    ap.add_argument("--key", default=None, help="Strata's API key (default: $STRATA_KEY)")
    ap.add_argument("--port", type=int, default=8090, help="the Studio's port (default %(default)s)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="the address to listen on (default %(default)s); anything else needs --key")
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST[:PORT]",
                    help="also answer to this Host name (repeatable), for a reverse proxy such as tailscale serve; "
                         "the Studio still listens on --host only")
    ap.add_argument("--config", default=None,
                    help="Strata's strata-<model>.json, read only: the Settings view shows its values")
    ap.add_argument("--read-only", action="store_true",
                    help="look only: no profile run, no tune, no config write (every POST answers 403)")
    ap.add_argument("--engine-log", default=None,
                    help="Strata's engine log, read only (default: the config's \"log\" key, or strata-<model>.log "
                         "next to it): the PC diagram's PCIe link ceiling")
    ap.add_argument("--state", default=None, help="the state file (default studio-state.json next to studio.py)")
    ap.add_argument("--version", action="version", version=f"Strata Profile Studio {__version__}")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    key = a.key or os.environ.get("STRATA_KEY") or None
    studio = Studio(a.strata, a.results, key=key, host=a.host, port=a.port, config=a.config, state_path=a.state,
                    allow_hosts=a.allow_host, engine_log=a.engine_log, read_only=a.read_only)
    shown = "[" + a.host + "]" if ":" in a.host else a.host
    print(f"Strata Profile Studio on http://{shown}:{studio.port}/ (Strata: {studio.app.strata_url}, "
          f"reports: {studio.results})", flush=True)
    if a.allow_host:
        print("Also answering to Host: " + ", ".join(a.allow_host) + (". Anyone who can reach those names can use the "
              "Studio; add --key to require a key." if not key else ". The key is required on every /api/ request."),
              flush=True)
    if studio.state.warning:
        print(f"Note: {studio.state.warning}", flush=True)
    try:
        studio.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
