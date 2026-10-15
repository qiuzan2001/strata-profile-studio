"""The page check: every view and every state of the Studio in headless Chromium, against tests/demo_strata.py.

Not a unit test (it needs Playwright and Chromium); run it by hand with a Python that has Playwright:

    <venv>/bin/python tests/page_check.py [--out DIR] [--seed RESULTS] [--live live.json] [--hardware hw.json]
                                          [--settings settings.json] [--tunes state.json] [--quick]

With no arguments it uses the fixture reports (tests/fixtures/reports) and synthetic readings.  --seed and the
recorded files make the screenshots show real data (copies; the originals are only read).  For each state it starts
a demo Strata and a Studio on ports above 8190, takes screenshots at 1400x900 light and dark and at 390x844, and
prints one PASS/FAIL line per check: no script error, no horizontal scroll at 390 px, the PC diagram adds no node
in a minute of updates, the tune flow runs from the button to the comparison, a profile run starts from the button,
"use the settings of run X" writes the config copy and its .bak.  Never touches port 8080 or a real Strata file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures" / "reports"
RESULTS = []
SIZES = {"light": ({"width": 1400, "height": 900}, "light"), "dark": ({"width": 1400, "height": 900}, "dark"),
         "phone": ({"width": 390, "height": 844}, "light")}
CONFIG = {"exe": "strata", "args": ["--pack", "demo", "--expert-cache", "auto", "--spec", "4", "--max-context", "262144",
                                   "--kv", "int8", "--kv-resident", "32768", "--spec-min-p", "0.5", "--adapt-every", "1",
                                   "--adapt-swaps", "80", "--adapt-decay", "0.97"],
          "model_name": "swift-1.5-iq3_s", "port": 8080, "env": {"STRATA_PREFILL_CPU_SHARE_MAX": "3072"}, "profile": True}


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def free_port(start):
    p = start
    while True:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
        p += 1


def wait_http(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception:                                   # noqa: BLE001
            time.sleep(0.2)
    return False


def get(url):
    return json.load(urllib.request.urlopen(url, timeout=10))


class Scenario:
    """A demo Strata (unless `unreachable`) and a Studio, in their own temp folder."""

    def __init__(self, a, name, state="ok", reports=True, unreachable=False, read_only=False):
        self.name = name
        self.dir = Path(tempfile.mkdtemp(prefix=f"pagecheck-{name}-"))
        self.results = self.dir / "results"
        self.results.mkdir()
        self.procs = []
        seed = a.seed or str(FIXTURES)
        if reports:
            for p in sorted(Path(seed).iterdir()):
                if "-profile-" in p.name and (p / "results.json").is_file():
                    shutil.copytree(p, self.results / p.name)
        self.config = self.dir / "strata-demo.json"
        if a.config:
            shutil.copy(a.config, self.config)
        else:
            self.config.write_text(json.dumps(CONFIG, indent=2) + "\n")
        self.state_file = self.dir / "studio-state.json"
        if a.tunes and reports:
            shutil.copy(a.tunes, self.state_file)
        self.fake_port = free_port(8195)
        if not unreachable:
            cmd = [sys.executable, str(TESTS / "demo_strata.py"), "--port", str(self.fake_port), "--results",
                   str(self.results), "--state", state, "--tune-secs", str(a.tune_secs), "--run-secs", "5"]
            for flag, val in (("--live", a.live), ("--hardware", a.hardware), ("--settings", a.settings)):
                if val:
                    cmd += [flag, val]
            self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_http(f"http://127.0.0.1:{self.fake_port}/health")
        self.port = free_port(self.fake_port + 1)
        cmd = [sys.executable, str(ROOT / "studio.py"), "--port", str(self.port), "--strata",
               f"http://127.0.0.1:{self.fake_port if not unreachable else free_port(8290)}", "--results", str(self.results),
               "--config", str(self.config), "--state", str(self.state_file), "--engine-log", a.engine_log or str(self.dir / "none.log")]
        if read_only:
            cmd.append("--read-only")
        self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{self.port}/api/state")
        self.base = f"http://127.0.0.1:{self.port}"
        time.sleep(2.5)                                       # the poller's first rounds

    def close(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


def shoot(browser, sc, out, routes, sizes, prefix=None, settle=2.5, errors=None):
    for size in sizes:
        vp, scheme = SIZES[size]
        ctx = browser.new_context(viewport=vp, color_scheme=scheme)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        for name, route in routes:
            page.goto(f"{sc.base}/#{route}")
            time.sleep(settle)
            file = out / f"{prefix or sc.name}-{name}-{size}.png"
            page.screenshot(path=str(file), full_page=True)
            if size == "phone":
                w = page.evaluate("document.documentElement.scrollWidth")
                record(f"{sc.name} {name}: no horizontal scroll at 390 px", w <= 390, f"{w} px")
        record(f"{sc.name} {size}: no script error", not errs, "; ".join(errs[:3]))
        if errors is not None:
            errors += errs
        ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--seed"); ap.add_argument("--live"); ap.add_argument("--hardware"); ap.add_argument("--settings")
    ap.add_argument("--tunes", help="a studio-state.json with tune records to start from")
    ap.add_argument("--config", help="a run config to copy (default: a small demo one)")
    ap.add_argument("--engine-log", help="an engine log to read the PCIe probe from (read only)")
    ap.add_argument("--tune-secs", type=float, default=30.0)
    ap.add_argument("--quick", action="store_true", help="the main views only, light desktop")
    ap.add_argument("--only", default="main,flow,states", help="which parts: main, flow, states")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        all_sizes = ["light"] if a.quick else ["light", "dark", "phone"]

        # ---- the main scenario: every view
        parts = set(a.only.split(","))
        sc = Scenario(a, "main", state="decoding") if "main" in parts else None
        try:
            if sc is None:
                raise StopIteration
            reports = [r for r in get(f"{sc.base}/api/reports")["reports"] if r["ok"]]
            tunes = get(f"{sc.base}/api/tunes")["tunes"]
            newest = reports[0]["id"]
            pair = (tunes[0]["before_report"], tunes[0]["after_report"]) if tunes and tunes[0].get("after_report") else (reports[1]["id"], reports[0]["id"])
            live = get(f"{sc.base}/api/live?since=0")["samples"]
            seq = live[-1]["seq"] if live else 1
            req = "agent-1" if any(q["id"] == "agent-1" for q in get(f"{sc.base}/api/reports/{pair[1]}")["requests"]) else "chat-1"
            routes = [("now", "now"), ("request-live", f"now/r/{seq}"), ("runs", "runs"), ("run", f"runs/{pair[1]}"),
                      ("request-report", f"runs/{pair[1]}/r/{req}"), ("compare", f"compare/{pair[0]}/{pair[1]}"),
                      ("tune", "tune"), ("settings", "settings")]
            shoot(browser, sc, out, routes, all_sizes)
            # the diagram updates in place: no node is added in a minute of 1 s updates
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"{sc.base}/#now")
            time.sleep(4)
            n0 = page.evaluate("document.querySelectorAll('*').length")
            seen = set()
            for _ in range(10 if a.quick else 30):
                seen.add(page.evaluate("document.querySelector('.blk-cpu .blk-big').textContent"))
                time.sleep(2)
            n1 = page.evaluate("document.querySelectorAll('*').length")
            t0, t1 = min(seen), max(seen)
            record("diagram: updates in place (node count stable over the wait)", n1 <= n0 + 2, f"{n0} -> {n1}")
            record("diagram: the readings move", len(seen) > 1, f"CPU readings seen: {sorted(seen)}")
            limit = page.evaluate("[...document.querySelectorAll('.blk.limit')].map(n => n.dataset.block).join(',')")
            record("diagram: a bottleneck block is highlighted", bool(limit), limit)
            # settings: write the config copy with the settings of the tune's before run (or the oldest run)
            before_cfg = sc.config.read_text()
            target = next((r["id"] for r in reports if any(x["changed"] for x in get(
                f"{sc.base}/api/settings/apply-preview?report={r['id']}")["rows"])), pair[0])
            page.goto(f"{sc.base}/#settings")
            page.evaluate(f"localStorage.setItem('studio.settings.report', JSON.stringify('{target}'))")
            page.reload()
            time.sleep(3)
            btn = page.locator("button.danger", has_text="Write")
            if btn.count() and btn.first.is_enabled():
                btn.first.click()
                page.screenshot(path=str(out / "main-settings-confirm-light.png"))
                page.locator("dialog button.danger").click()
                time.sleep(2)
                page.screenshot(path=str(out / "main-settings-written-light.png"), full_page=True)
                bak = sc.config.with_name(sc.config.name + ".bak")
                record("settings: the write leaves a .bak equal to the old file", bak.exists() and bak.read_text() == before_cfg)
                record("settings: the config copy changed", sc.config.read_text() != before_cfg)
            else:
                record("settings: a run with different settings to write", False, "no enabled Write button")
            page.close()
        except StopIteration:
            pass
        finally:
            if sc:
                sc.close()

        # ---- the tune flow, from the button to the comparison, and a profile run from its button
        if not a.quick and "flow" in parts:
            sc = Scenario(a, "flow")
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(f"{sc.base}/#runs")
                time.sleep(2.5)
                page.locator("button.primary", has_text="Run a profile").first.click()
                page.locator("dialog button.primary").click()
                time.sleep(2.5)
                page.screenshot(path=str(out / "flow-profile-running-light.png"), full_page=True)
                running = get(f"{sc.base}/api/state")["strata"]["run"]["state"] == "running"
                record("runs: Run a profile starts a run from the page", running)
                time.sleep(8)
                page.goto(f"{sc.base}/#tune")
                time.sleep(2.5)
                page.locator("button.primary", has_text="Tune this PC").click()
                page.locator("#dlg-check").check()
                page.locator("dialog button.danger").click()
                wait_end = time.time() + 15
                while time.time() < wait_end and get(f"{sc.base}/api/tune-flow")["state"] != "running":
                    time.sleep(0.3)
                deadline = time.time() + 90 + a.tune_secs
                shot_mid = False
                while time.time() < deadline:
                    snap = get(f"{sc.base}/api/tune-flow")
                    if snap["stage"] == "tune" and not shot_mid and len(snap.get("lines") or []) > 25:
                        time.sleep(2.5)
                        page.screenshot(path=str(out / "flow-tune-running-light.png"), full_page=True)
                        shot_mid = True
                    if snap["state"] != "running":
                        break
                    time.sleep(1)
                record("tune: the flow ends succeeded", snap["state"] == "succeeded", snap["state"])
                time.sleep(3)
                page.screenshot(path=str(out / "flow-tune-done-light.png"), full_page=True)
                page.close()
            finally:
                sc.close()

        # ---- the states
        states = [("first-run", dict(state="first-run")), ("no-reports", dict(state="first-run", reports=False)),
                  ("profile-off", dict(state="profile-off")), ("key", dict(state="key")),
                  ("unloaded", dict(state="unloaded")), ("unreachable", dict(unreachable=True)),
                  ("tuning-elsewhere", dict(state="tuning-elsewhere")), ("read-only", dict(read_only=True))]
        if a.quick:
            states = states[:2]
        if "states" not in parts:
            states = []
        for name, kw in states:
            sc = Scenario(a, name, **kw)
            try:
                routes = [("now", "now"), ("runs", "runs"), ("tune", "tune")]
                if name in ("unreachable", "key"):
                    routes.append(("settings", "settings"))
                shoot(browser, sc, out, routes, ["light", "phone"] if not a.quick else ["light"], settle=3.5)
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(f"{sc.base}/#now")
                time.sleep(3)
                text = page.evaluate("document.getElementById('banner').innerText + ' ' + document.querySelector('main').innerText")
                expect = {"first-run": "No requests since Strata started", "no-reports": "No requests since Strata started",
                          "profile-off": "Profiling is off", "key": "Strata needs an API key", "unloaded": "The model is not loaded",
                          "unreachable": "Strata does not answer", "tuning-elsewhere": "Strata is tuning",
                          "read-only": "read-only"}[name]
                if name == "read-only":
                    text += page.evaluate("document.querySelector('header').innerText")
                record(f"state {name}: says so on the page", expect.lower() in text.lower(), expect)
                page.close()
            finally:
                sc.close()
        browser.close()
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed; screenshots in {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
