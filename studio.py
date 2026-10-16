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
from studiolib.defaultsrun import DefaultsRun                              # noqa: E402
from studiolib.enginelog import EngineLog                                  # noqa: E402
from studiolib.experts import Profiles                                     # noqa: E402
from studiolib.hostinfo import HostMonitor                                 # noqa: E402
from studiolib.flow import Flow                                            # noqa: E402
from studiolib.poller import Poller                                        # noqa: E402
from studiolib.reports import ReportStore                                  # noqa: E402
from studiolib.server import StudioApp, StudioServer, is_loopback, make_handler  # noqa: E402
from studiolib.state import State, StateError                              # noqa: E402
from studiolib.strata import StrataClient                                  # noqa: E402
from studiolib.timing import Timing                                        # noqa: E402

DEFAULT_RESULTS = "~/Strata/bench/results"
DEFAULT_STRATA = "http://127.0.0.1:8080"


def from_config(config: str | None) -> dict:
    """What the run config tells: Strata's address (its "port", and "host" unless it listens on every address) and
    the results folder (bench/results in the Strata checkout).  Empty for each value it does not tell."""
    out = {}
    if not config:
        return out
    from studiolib.defaultsrun import strata_root
    from studiolib.settings import read_config
    cfg, _ = read_config(config)
    if not cfg:
        return out
    port = cfg.get("port")
    if isinstance(port, int) and 0 < port < 65536:
        host = cfg.get("host") if isinstance(cfg.get("host"), str) else "127.0.0.1"
        host = "127.0.0.1" if host in ("", "0.0.0.0", "::", "localhost") else host
        out["strata"] = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
    root = strata_root(cfg, config) or Path(config).resolve().parent
    if (root / "bench" / "results").is_dir():
        out["results"] = str(root / "bench" / "results")
    return out


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
                 allow_hosts=(), engine_log: str | None = None, read_only: bool = False, host_monitor=None,
                 defaults_tool=None, host_info: bool = True, strata_root: str | None = None, stock_popen=None):
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
        from studiolib.stock import StockBackend
        stock_kw = {"popen": stock_popen} if stock_popen else {}
        self.stock = StockBackend(self.client, self.results, config_path=config,
                                  engine_log=engine_log or default_engine_log(config), explicit_root=strata_root,
                                  key=key, **stock_kw)
        self.poller.stock_backend = self.stock
        self.app = StudioApp(strata_url=public_url(strata), results_dir=self.results, config_path=config, key=key,
                             studio_key=studio_key, host=host, port=port, client=self.client, poller=self.poller,
                             flow=self.flow, reports=self.reports, state=self.state, web_dir=ROOT / "web",
                             started_at=time.time(), allow_hosts=allow_hosts,
                             engine_log=EngineLog(engine_log or default_engine_log(config)), read_only=read_only,
                             host_monitor=host_monitor or HostMonitor(enabled=host_info), profiles=Profiles(config) if config else None,
                             defaults_run=DefaultsRun(self.client, self.poller, self.results, config, key,
                                                      tool=defaults_tool) if config else None,
                             stock=self.stock)
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
        self.stock.stop()
        if self.app.host_monitor:
            self.app.host_monitor.stop()
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
    ap.add_argument("--strata", default=None,
                    help=f"Strata's address (default: the config's \"port\", else {DEFAULT_STRATA})")
    ap.add_argument("--results", default=None,
                    help="Strata's bench/results folder, where profile reports are read from (default: bench/results "
                         f"of the Strata folder of --config, else {DEFAULT_RESULTS})")
    ap.add_argument("--key", default=None, help="Strata's API key (default: $STRATA_KEY)")
    ap.add_argument("--port", type=int, default=None, help="the Studio's port (default 8090; with --demo the first free "
                                                          "port from 8090)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="the address to listen on (default %(default)s); anything else needs --key")
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST[:PORT]",
                    help="also answer to this Host name (repeatable), for a reverse proxy such as tailscale serve; "
                         "the Studio still listens on --host only")
    ap.add_argument("--config", default=None,
                    help="Strata's strata-<model>.json in the Strata folder. The Studio finds Strata's address, the "
                         "results folder and the engine log from it. Written only from the Settings page, with a .bak")
    ap.add_argument("--read-only", action="store_true",
                    help="look only: no profile run, no tune, no config write (every POST answers 403)")
    ap.add_argument("--host-info", choices=("on", "off"), default="on",
                    help="read this PC's CPU, GPU, memory and disk (default on); off: no reader and no subprocess, "
                         "the page uses Strata's readings only")
    ap.add_argument("--host-probe", action="store_true",
                    help="print each host reader of this PC (source, answer, raw first line) and exit")
    ap.add_argument("--engine-log", default=None,
                    help="Strata's engine log, read only (default: the config's \"log\" key, or strata-<model>.log "
                         "next to it): the PC diagram's PCIe link ceiling")
    ap.add_argument("--state", default=None, help="the state file (default studio-state.json next to studio.py)")
    ap.add_argument("--card", metavar="REPORT",
                    help="write a result card of one profile report (its folder or its results.json): card.html, "
                         "card.svg, card.json and card.png (the PNG needs a headless Chrome, Chromium or Edge, or "
                         "Pillow), then stop. Nothing is sent")
    ap.add_argument("--out", metavar="DIR", help="with --card: the folder for the files (default strata-card-<id>)")
    ap.add_argument("--name", default="", help="with --card: an optional name on the card (at most 12 characters: "
                                                "letters, digits, space, - and _)")
    ap.add_argument("--strata-root", metavar="DIR",
                    help="Strata's folder (with setup.py and tools/), for a Strata build without the /profile routes "
                         "(0.1.44.1 as released): the Studio runs the profile and the tune itself from there "
                         "(default: the folder of --config)")
    ap.add_argument("--demo", action="store_true",
                    help="try the Studio without Strata: a demo Strata with the recorded data of one PC "
                         "(tests/fixtures/demo) and the Studio against it, in this process; Ctrl-C stops both")
    ap.add_argument("--version", action="version", version=f"Strata Profile Studio {__version__}")
    return ap.parse_args(argv)


DEMO_DATA = ROOT / "tests" / "fixtures" / "demo"
DEMO_NOTE = "Demo: recorded readings of one PC (RTX 5070 Ti, Ryzen 7 5800X3D), not of this PC."


def free_port(start: int) -> int:
    import socket
    for p in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit(f"No free port from {start} to {start + 199}.")


def demo_studio(port: int, timing: Timing | None = None):
    """The demo: a temporary folder with copies of tests/fixtures/demo, the demo Strata (tests/demo_strata.py) on a
    free port, and a Studio against it.  Returns (studio, close); close() stops the demo Strata and removes the
    folder.  Nothing outside the temporary folder is read or written, and no host reader runs."""
    import json
    import shutil
    import tempfile
    import types
    sys.path.insert(0, str(ROOT / "tests"))
    import demo_strata
    tmp = Path(tempfile.mkdtemp(prefix="studio-demo-"))
    cfg = json.loads((DEMO_DATA / "strata-swift-iq3_s.json").read_text(encoding="utf-8"))
    args = cfg["args"]
    if "--expert-profile" in args:
        args[args.index("--expert-profile") + 1] = str(DEMO_DATA / "expert-profile.bin")
    shutil.copy(DEMO_DATA / "strata-swift-iq3_s.log", tmp / "strata-swift-iq3_s.log")
    shutil.copy(DEMO_DATA / "studio-state.json", tmp / "studio-state.json")
    strata_port = free_port(8195)
    cfg.update(log=str(tmp / "strata-swift-iq3_s.log"), port=strata_port)
    config = tmp / "strata-swift-iq3_s.json"
    config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    ns = types.SimpleNamespace(port=strata_port, results=str(tmp / "results"), seed=str(DEMO_DATA / "reports"),
                               live=str(DEMO_DATA / "live.json"), hardware=str(DEMO_DATA / "hardware.json"),
                               settings=str(DEMO_DATA / "settings.json"), state="decoding", tune_secs=40.0,
                               run_secs=6.0)
    fake, stop = demo_strata.start(ns)
    import os                                             # the reports' recorded times (git keeps no file times)
    for name, t in json.loads((DEMO_DATA / "report-times.json").read_text(encoding="utf-8")).items():
        d = tmp / "results" / name
        if d.is_dir():
            for f in [*d.iterdir(), d]:
                os.utime(f, (t, t))
    try:
        studio = Studio(fake.url, str(tmp / "results"), port=port, config=str(config),
                        state_path=str(tmp / "studio-state.json"), timing=timing,
                        host_monitor=HostMonitor(enabled=False, off_reason=DEMO_NOTE))
    except BaseException:
        stop.set()
        fake.stop()
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    def close():
        stop.set()
        fake.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    return studio, close


def run_demo(a) -> int:
    import signal
    studio, close = demo_studio(a.port if a.port is not None else free_port(8090))

    def on_term(*_):
        raise KeyboardInterrupt                           # `kill`: stop as for Ctrl-C, and remove the folder
    signal.signal(signal.SIGTERM, on_term)
    print(f"Strata Profile Studio demo on http://127.0.0.1:{studio.port}/", flush=True)
    print("The numbers are recorded data of one PC (RTX 5070 Ti, Ryzen 7 5800X3D, Swift 1.5 IQ3_S), not of this PC. "
          "Press Ctrl-C to stop.", flush=True)
    try:
        studio.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        close()
    return 0


def card_command(a) -> int:
    """`studio.py --card <report>`: the card files on this PC.  Reads the report folder, the engine log (the probe
    line) and, with host readings on, the GPU tool once.  Writes only into --out."""
    from cards import render
    from studiolib.cardmake import CardError, card_of_path, facts_from
    state = State(a.state or ROOT / "studio-state.json")
    try:
        state.load()
    except StateError:
        state = None
    log = EngineLog(a.engine_log or default_engine_log(a.config)).facts() if (a.engine_log or a.config) else None
    gpu = None
    if a.host_info == "on":
        try:
            from studiolib.hostinfo import GpuReader
            gpu = GpuReader().sample()
        except Exception:                                 # noqa: BLE001 - no GPU tool: the card says "not reported"
            gpu = None
    try:
        card = card_of_path(a.card, state=state, facts=facts_from(log, gpu), name=a.name)
    except CardError as e:
        print(f"No card: {e}", flush=True)
        return 1
    res = render.write_files(card, Path(a.out or f"strata-card-{card['id']}"))
    print(f"Card {card['id']} in {res['dir']}: {', '.join(res['files'])}", flush=True)
    print(f"PNG: {'made with ' + res['renderer'] if res['png'] else res['renderer']}", flush=True)
    print("Not in the card: " + " · ".join(render.EXCLUDED) + ". Nothing was sent.", flush=True)
    return 0


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.card:
        return card_command(a)
    if a.demo:
        return run_demo(a)
    a.port = 8090 if a.port is None else a.port
    if a.host_probe:
        from studiolib.hostinfo import probe_text
        mon = HostMonitor(enabled=True)
        print(probe_text(mon, EngineLog(a.engine_log or default_engine_log(a.config))), flush=True)
        return 0
    found = from_config(a.config)
    a.strata = a.strata or found.get("strata") or DEFAULT_STRATA
    a.results = a.results or found.get("results") or DEFAULT_RESULTS
    key = a.key or os.environ.get("STRATA_KEY") or None
    studio = Studio(a.strata, a.results, key=key, host=a.host, port=a.port, config=a.config, state_path=a.state,
                    allow_hosts=a.allow_host, engine_log=a.engine_log, read_only=a.read_only,
                    host_info=a.host_info == "on", strata_root=a.strata_root)
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
