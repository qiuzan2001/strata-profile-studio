"""The page check: every view and every state of the Studio in headless Chromium, against tests/demo_strata.py.

Not a unit test (it needs Playwright and Chromium); run it by hand with a Python that has Playwright:

    <venv>/bin/python tests/page_check.py [--out DIR] [--seed RESULTS] [--live live.json] [--hardware hw.json]
                                          [--settings settings.json] [--tunes state.json] [--quick]
                                          [--host-info on|off]

With no arguments it uses the fixture reports (tests/fixtures/reports) and synthetic readings.  --seed and the
recorded files make the screenshots show real data (copies; the originals are only read).  For each state it starts
a demo Strata and a Studio on ports above 8190, takes screenshots at 1400x900 light and dark and at 390x844, and
prints one PASS/FAIL line per check: no script error, no horizontal scroll at 390 px, the PC diagram adds no node
in a minute of updates, the tune flow runs from the button to the comparison, a profile run starts from the button,
"use the settings of run X" writes the config copy and its .bak.  --host-info off starts every Studio without host
readings (every view then renders from /metrics, the engine log and the reports alone); the state "host-off" checks
this in every run.  Never touches port 8080 or a real Strata file.
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
LIVE = "#view-now .tiles, #view-now .pc-card, #view-now .grid > .card:not(.map-card), #status, #banner"
OBSERVE = """() => { window.__m = {added: 0, removed: 0, attr: 0, text: 0, where: {}};
  const live = [...document.querySelectorAll('%s')];
  const inLive = (n) => live.some((r) => r.contains(n));
  new MutationObserver((ms) => { for (const m of ms) {
    if (!inLive(m.target)) continue;
    if (m.type === 'childList') {
      window.__m.added += m.addedNodes.length; window.__m.removed += m.removedNodes.length;
      if (m.addedNodes.length || m.removedNodes.length) { const k = (m.target.className && m.target.className.baseVal !== undefined ? m.target.className.baseVal : m.target.className) || m.target.tagName; window.__m.where[k] = (window.__m.where[k] || 0) + 1; }
    } else if (m.type === 'attributes') window.__m.attr++; else window.__m.text++; } })
    .observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true}); }""" % LIVE
BOXES = """() => [...document.querySelectorAll('%s')].flatMap((r) => [r, ...r.querySelectorAll('*')])
  .filter((n) => { const cs = getComputedStyle(n); return cs.display !== 'inline' && cs.display !== 'none' && !(n instanceof SVGElement && n.tagName !== 'svg'); })
  .map((n) => { const b = n.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)].join(','); })""" % LIVE
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

    def __init__(self, a, name, state="ok", reports=True, unreachable=False, read_only=False, host_info=None,
                 routes_root=False, stock_root=False):
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
        cmd += ["--host-info", host_info or a.host_info]
        if routes_root:                                   # a Strata build with the /profile routes: 404 = profiling off
            r = self.dir / "strata-with-routes"
            (r / "tools").mkdir(parents=True)
            (r / "serve").mkdir()
            (r / "tools" / "strata_bench.py").write_text("")
            (r / "serve" / "profile_page.py").write_text("")
            cmd += ["--strata-root", str(r)]
        if stock_root:                                    # a release build (0.1.44.1): tools/, no profile_page.py
            r = self.dir / "strata-stock"
            (r / "tools").mkdir(parents=True)
            (r / "tools" / "strata_bench.py").write_text("")
            (r / "setup.py").write_text("")
            cmd += ["--strata-root", str(r)]
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


def demo_check(browser, out):
    """`studio.py --demo` as a visitor starts it: each view has data, no script error, Ctrl-C stops both servers."""
    import signal
    port = free_port(8190)
    proc = subprocess.Popen([sys.executable, str(ROOT / "studio.py"), "--demo", "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        record("demo: the Studio answers", wait_http(f"http://127.0.0.1:{port}/api/state"))
        base = f"http://127.0.0.1:{port}"
        time.sleep(4)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme="light")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        expect = {"now": "Limiting component", "runs": "run 7", "compare": "baseline", "tune": "Tune history",
                  "settings": "--pcie-frac"}
        for view, words in expect.items():
            page.goto(f"{base}/#{view}")
            time.sleep(3)
            page.screenshot(path=str(out / f"demo-{view}-light.png"), full_page=True)
            text = page.evaluate("document.querySelector('main').innerText")
            record(f"demo {view}: has data", words.lower() in text.lower(), words)
        page.goto(f"{base}/#now")
        time.sleep(2)
        seen = set()
        for _ in range(6):
            seen.add(page.evaluate("document.querySelector('.blk-cpu .blk-big').textContent"))
            time.sleep(1)
        record("demo now: the PC module moves", len(seen) > 1, f"CPU readings seen: {sorted(seen)}")
        # the Share dialog of a run: the card and its JSON first, then only the files on this PC
        rid = [r for r in get(f"{base}/api/reports")["reports"] if r["ok"]][0]["id"]
        page.goto(f"{base}/#runs/{rid}")
        time.sleep(2.5)
        page.locator(".view-head button.btn", has_text="Share").click()
        def until(js, secs):                              # page.evaluate: the page's CSP forbids wait_for_function
            end = time.time() + secs
            while time.time() < end:
                if page.evaluate(js):
                    return True
                time.sleep(0.25)
            return False
        shown = until("(document.querySelector('dialog .share-json') || {}).textContent.includes('strata-card/1') && "
                      "document.querySelector('dialog .share-img').naturalWidth === 1200", 40)
        record("share: the dialog shows the card image and the JSON", shown)
        dis = page.evaluate("[...document.querySelectorAll('dialog input[name=share-dest]')].map(r => r.value + ':' + r.disabled).join(',')")
        record("share: only the files on this PC can be chosen", dis == "gh:true,form:true,files:false", dis)
        page.locator("dialog button.primary", has_text="Write the files").click()
        until("document.querySelector('.share-result').textContent.includes('Nothing left the PC')", 60)
        page.screenshot(path=str(out / "demo-share-light.png"))
        record("share: the card files are written on this PC", "card.json" in page.locator(".share-result").inner_text())
        page.keyboard.press("Escape")
        record("demo: no script error", not errs, "; ".join(errs[:3]))
        ctx.close()
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        outp = proc.stdout.read() if proc.stdout else ""
        record("demo: Ctrl-C stops both servers", "Stopped." in outp and proc.returncode == 0, f"exit {proc.returncode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--seed"); ap.add_argument("--live"); ap.add_argument("--hardware"); ap.add_argument("--settings")
    ap.add_argument("--tunes", help="a studio-state.json with tune records to start from")
    ap.add_argument("--config", help="a run config to copy (default: a small demo one)")
    ap.add_argument("--engine-log", help="an engine log to read the PCIe probe from (read only)")
    ap.add_argument("--tune-secs", type=float, default=30.0)
    ap.add_argument("--quick", action="store_true", help="the main views only, light desktop")
    ap.add_argument("--only", default="main,flow,states,demo", help="which parts: main, flow, states, demo")
    ap.add_argument("--host-info", choices=("on", "off"), default="on", help="the Studio's host readings (default on)")
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
                      ("request-report", f"runs/{pair[1]}/r/{req}"), ("compare-pair", f"compare/{pair[0]}/{pair[1]}"),
                      ("compare", "compare"), ("tune", "tune"), ("workload", "workload"), ("settings", "settings")]
            shoot(browser, sc, out, routes, all_sizes)
            # workload: with requests in the engine log the page shows the counts and names the closest preset
            wl_log = sc.dir / "none.log"
            wl_log.write_text("\n".join(
                f"strata serve: prompt {p} tokens = {r} reused + {p - r} read in {pm} ms ({(p - r) / pm * 1000:.1f} tok/s), "
                f"{g} generated in {dm} ms ({g / dm * 1000:.1f} tok/s), drafts accepted 80 of 100, 1 checkpoints"
                for p, r, g, pm, dm in ((20000, 8000, 800, 4000, 8000), (24000, 10000, 1000, 5000, 11000))) + "\n")
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                page.goto(f"{sc.base}/#workload")
                page.wait_for_selector("section#view-workload .tiles", timeout=8000)
                chip = page.locator("section#view-workload tr", has=page.locator(".chip", has_text="Closest")).first.inner_text()
                record("workload: the closest preset is the browser agent", "Browser agent" in chip, chip.replace("\n", " ")[:80])
                record("workload: the page says the preset shapes are assumed",
                       "assumed" in page.locator("section#view-workload").inner_text())
                page.wait_for_selector("section#view-workload .mytune button.btn", timeout=8000)
                mt = page.locator("section#view-workload .mytune").inner_text()
                record("workload: the tune card shows the plan, the start button and the Settings link",
                       "Start the tune" in mt and "Settings" in mt and "Quick" in mt, mt.replace("\n", " ")[:80])
                page.screenshot(path=str(out / "workload-light.png"), full_page=True)
                page.close()
            finally:
                wl_log.unlink(missing_ok=True)
            # no flicker: 30 s of live updates (hardware every 1 s, a new request every 6 s) change attributes and text
            # only; no node is added or removed in the live regions, and the layout boxes stay where they are
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"{sc.base}/#now")
            time.sleep(5)
            page.evaluate(OBSERVE)
            time.sleep(30)
            m = page.evaluate("window.__m")
            record("flicker: no node added or removed in the live regions in 30 s", m["added"] == 0 and m["removed"] == 0,
                   f"added {m['added']}, removed {m['removed']}, attribute changes {m['attr']}, text changes {m['text']}, where {m['where']}")
            record("flicker: the live regions changed (attributes and text)", m["attr"] + m["text"] > 0, f"{m['attr'] + m['text']} changes")
            page.evaluate("window.scrollTo(0, 0)")
            b0 = page.evaluate(BOXES)
            page.screenshot(path=str(out / "flicker-t0.png"))
            time.sleep(1)
            b1 = page.evaluate(BOXES)
            page.screenshot(path=str(out / "flicker-t1.png"))
            moved = sum(1 for x, y in zip(b0, b1) if x != y) + abs(len(b0) - len(b1))
            record("flicker: the layout boxes 1 s apart are identical", moved == 0, f"{len(b0)} boxes, {moved} moved")
            (out / "flicker-proof.json").write_text(json.dumps({"mutations_30s": m, "boxes": len(b0), "boxes_moved_1s": moved}, indent=1))
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
            win = get(f"{sc.base}/api/hardware").get("window") or {}
            if win.get("window_ms"):
                record("diagram: a bottleneck block is highlighted", bool(limit), limit)
            else:                                             # the engine log has no time split: no limit to show
                record("diagram: no bottleneck without a time split in the engine log", not limit, limit or "none")
            # settings: a hand edit is staged, shown in the edit list, and written with a .bak
            page.goto(f"{sc.base}/#settings")
            time.sleep(3)
            num = page.locator("input.ctl-num").first
            num.fill("0.45")
            num.dispatch_event("change")
            time.sleep(0.5)
            staged = page.evaluate("document.querySelector('.edits-card h4').textContent")
            record("settings: an edit is staged in the edit list", "1 setting changed" in staged, staged)
            page.screenshot(path=str(out / "main-settings-edit-light.png"), full_page=True)
            page.locator("button.btn", has_text="Discard the edits").click()
            # settings: "Reset the calibrated settings" stages the calibrated knobs only, never a flag setup wrote
            page.locator("button.btn", has_text="Reset the calibrated settings").click()
            time.sleep(0.5)
            names = page.evaluate("[...document.querySelectorAll('.edits-card .diff b')].map(b => b.textContent)")
            setup = [n for n in names if n in ("--spec", "--prefill", "--expert-cache", "--max-context", "--kv-resident", "--kv")]
            record("settings: the calibrated reset keeps each flag setup wrote", not setup, f"staged {names}")
            page.screenshot(path=str(out / "main-settings-reset-light.png"), full_page=True)
            discard = page.locator("button.btn", has_text="Discard the edits")
            if discard.is_enabled():                       # the reset stages nothing when no calibrated value is set
                discard.click()
            # settings: write the config copy with the settings of the tune's before run (or the oldest run)
            before_cfg = sc.config.read_text()
            target = next((r["id"] for r in reports if any(x["changed"] for x in get(
                f"{sc.base}/api/settings/apply-preview?report={r['id']}")["rows"])), pair[0])
            page.goto(f"{sc.base}/#settings")
            page.evaluate(f"localStorage.setItem('studio.settings.report', JSON.stringify('{target}'))")
            page.reload()
            time.sleep(3)
            btn = page.locator(".card:has(h3:text('Settings of a run')) button.danger", has_text="Write")
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
            # the Share dialog's remote destinations: looked at, never selected (nothing is sent from the check)
            page.goto(f"{sc.base}/#runs/{pair[1]}")
            time.sleep(2.5)
            page.locator(".view-head button.btn", has_text="Share").click()
            ready = False
            for _ in range(80):
                ready = page.evaluate("!!document.querySelector('dialog input[value=form]') && "
                                      "document.querySelector('dialog .share-json').textContent.includes('strata-card/1') && "
                                      "!document.querySelector('dialog .share-why') .textContent.includes('...')")
                if ready:
                    time.sleep(1.5)
                    break
                time.sleep(0.25)
            st = page.evaluate("""() => { const q = (v) => document.querySelector(`dialog input[value=${v}]`);
              const why = (v) => q(v).closest('label').querySelector('.share-why').textContent.trim();
              return {gh: q('gh').disabled, ghWhy: why('gh'), form: q('form').disabled, files: q('files').checked,
                      button: document.querySelector('dialog .dlg-buttons button.primary').textContent}; }""")
            record("share: the form link destination is ready", not st["form"], str(st))
            record("share: the gh destination is ready, or the dialog says why", (not st["gh"]) or len(st["ghWhy"]) > 10, st["ghWhy"] or "ready")
            record("share: the files on this PC are the first choice", st["files"] and st["button"] == "Write the files", st["button"])
            page.screenshot(path=str(out / "main-share-destinations-light.png"))
            page.keyboard.press("Escape")
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

        if "demo" in parts:
            demo_check(browser, out)

        # ---- the states
        states = [("first-run", dict(state="first-run")), ("no-reports", dict(state="first-run", reports=False)),
                  ("profile-off", dict(state="profile-off", routes_root=True)), ("stock", dict(state="profile-off", stock_root=True)),
                  ("key", dict(state="key")),
                  ("unloaded", dict(state="unloaded")), ("unreachable", dict(unreachable=True)),
                  ("tuning-elsewhere", dict(state="tuning-elsewhere")), ("read-only", dict(read_only=True)),
                  ("host-off", dict(state="decoding", host_info="off"))]
        if a.quick:
            states = states[:2] + states[-1:]
        if "states" not in parts:
            states = []
        for name, kw in states:
            sc = Scenario(a, name, **kw)
            try:
                routes = [("now", "now"), ("runs", "runs"), ("compare", "compare"), ("tune", "tune")]
                if name in ("unreachable", "key"):
                    routes.append(("settings", "settings"))
                shoot(browser, sc, out, routes, ["light", "phone"] if not a.quick else ["light"], settle=3.5)
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(f"{sc.base}/#now")
                time.sleep(3)
                text = page.evaluate("document.getElementById('banner').innerText + ' ' + document.querySelector('main').innerText")
                expect = {"first-run": "No requests since Strata started", "no-reports": "No requests since Strata started",
                          "profile-off": "Profiling is off", "key": "Strata needs an API key", "unloaded": "The model is not loaded",
                          "unreachable": "Strata does not answer", "tuning-elsewhere": "Strata runs a tune",
                          "read-only": "read-only", "host-off": "Host readings are off",
                          "stock": "stock Strata"}[name]
                if name in ("read-only", "stock"):
                    text += page.evaluate("document.querySelector('header').innerText")
                    page.goto(f"{sc.base}/#runs")
                    time.sleep(2.5)
                    record("read-only: no Share button", page.locator("button.btn", has_text="Share").count() == 0)
                    page.goto(f"{sc.base}/#now")
                    time.sleep(1)
                record(f"state {name}: says so on the page", expect.lower() in text.lower(), expect)
                if name == "stock":
                    record("stock: no \"profile\": true advice on Now", "\"profile\": true" not in text)
                    page.goto(f"{sc.base}/#settings")
                    time.sleep(3)
                    st_text = page.evaluate("document.querySelector('main').innerText")
                    record("stock: Settings shows the Timing lines block", "Timing lines" in st_text and "STRATA_DECODE_TIMING" in st_text)
                    page.screenshot(path=str(out / "stock-settings-light.png"), full_page=True)
                    page.goto(f"{sc.base}/#now")
                    time.sleep(1)
                if name == "host-off":
                    host = get(f"{sc.base}/api/host")
                    record("host-off: /api/host has no reading", host["cpu"] is None and host["gpu"] is None and not host["enabled"])
                    big = page.evaluate("document.querySelector('.blk-cpu .blk-big').textContent")
                    record("host-off: the CPU block shows Strata's own /metrics reading", any(c.isdigit() for c in big), big)
                    vram = page.evaluate("document.querySelector('.blk-vram .blk-big').textContent")
                    has = isinstance(get(f"{sc.base}/api/hardware").get("now", {}).get("gpu_mem_used"), (int, float))
                    record("host-off: the VRAM block shows /metrics' reading, or '–' when /metrics has none",
                           any(c.isdigit() for c in vram) == has, f"{vram} (/metrics has a reading: {has})")
                    record("host-off: no 'not readable' text", "not readable" not in text.lower())
                    cells = page.evaluate("[...document.querySelectorAll('.core')].filter(c => !c.hidden && c.offsetParent).length")
                    record("host-off: no core grid without per-core readings", cells == 0, f"{cells} cells")
                page.close()
            finally:
                sc.close()
        browser.close()
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed; screenshots in {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
