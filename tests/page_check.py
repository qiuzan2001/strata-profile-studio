"""Checks the page against redesign section 8 with headless Chromium (Playwright) and tests/fakestrata.py.

Not a unit test (it needs Playwright and a Chromium build); run it by hand:

    python3 tests/page_check.py --python-with-playwright ...      # or simply: <venv>/bin/python tests/page_check.py

It starts a fake Strata and a Studio on ports above 8190, runs a full tune flow through the Studio's API, then
measures every view at 1400x900 and 390x844 in light and dark, saves screenshots to docs/screenshots/ and prints one
PASS/FAIL line per checklist item.  No GPU, no engine, no port 8080.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fakestrata import FakeStrata, request_doc    # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FAKE_PORT, STUDIO_PORT = 8195, 8196
SHOTS = ROOT / "docs" / "screenshots"
RESULTS = []


def record(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def get(path):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{STUDIO_PORT}{path}"))


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{STUDIO_PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req))


JS_VISIBLE_STATS = """
() => {
  const visible = (n) => { const r = n.getBoundingClientRect(); const cs = getComputedStyle(n);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const main = document.querySelector('main');
  const tables = [...document.querySelectorAll('main table')].filter(visible).length;
  const details = [...document.querySelectorAll('main details')].filter(visible).length;
  const longP = [...document.querySelectorAll('main p')].filter(visible)
    .filter((p) => p.textContent.trim().split(/\\s+/).length > 12).length;
  const p8 = [...document.querySelectorAll('main p')].filter(visible)
    .filter((p) => p.textContent.trim().split(/\\s+/).length > 8).length;
  let longText = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.textContent.trim();
    if (!t || !n.parentElement) continue;
    if (n.parentElement.closest('script,style,dialog:not([open]),[hidden],noscript')) continue;
    if (!visible(n.parentElement)) continue;
    if (t.split(/\\s+/).length > 8) longText.push(t.slice(0, 60));
  }
  let maxFont = 0, maxEl = '';
  for (const e of document.querySelectorAll('body *')) {
    if (!visible(e) || !e.childNodes.length) continue;
    const hasText = [...e.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
    if (!hasText) continue;
    const f = parseFloat(getComputedStyle(e).fontSize);
    if (f > maxFont) { maxFont = f; maxEl = (e.className || e.tagName) + ':' + e.textContent.trim().slice(0, 20); }
  }
  const hero = document.querySelector('.hero-value, .headline-value');
  const heroFont = hero ? parseFloat(getComputedStyle(hero).fontSize) : 0;
  const keyRows = [...document.querySelectorAll('.keyrow')].filter(visible).length;
  const bottom = Math.max(...[...document.querySelectorAll('main > section:not([hidden]) *')].filter(visible)
    .map((e) => e.getBoundingClientRect().bottom), 0);
  const body = document.body.innerText + ' ' + [...document.querySelectorAll('[aria-label],[data-segments]')]
    .map((e) => (e.getAttribute('aria-label') || '') + ' ' + (e.dataset.segments || '')).join(' ');
  return { tables, details, longP, p8, longText, maxFont, maxEl, heroFont, keyRows, bottom,
           scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight,
           hasGPU: /\\bGPU\\b/.test(body) };
}
"""

JS_DECODE = """
() => {
  const c = document.querySelector('canvas.bar-decode');
  const chart = Chart.getChart(c);
  const rect = c.getBoundingClientRect();
  const metas = chart.data.datasets.map((d, i) => chart.getDatasetMeta(i).data[0]);
  const widths = metas.map((m) => Math.abs(m.x - m.base));
  const area = chart.chartArea;
  return { canvasW: rect.width, areaW: area.right - area.left, segments: c.dataset.segments, dominant: c.dataset.dominant,
           opacity: c.dataset.opacity, widths, names: chart.data.datasets.map((d) => d.label) };
}
"""


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="page-check-"))
    results = tmp / "results"
    results.mkdir()
    fx = ROOT / "tests" / "fixtures" / "reports"
    for n in ("", "-2", "-3", "-4", "-5", "-6"):
        name = "2026-10-17-profile-rtx-5070-ti-swift-1-5-iq3-s" + n
        shutil.copytree(fx / name, results / name)
    fake = FakeStrata(results, settings=True, history=True, tune_outcome="real", tune_secs=6, port=FAKE_PORT, reload_503s=1).start()
    for r in fake.settings_doc["settings"]:
        r["loaded_now"] = {"pcie_frac": "0.35", "spec_min_p": "0.70"}.get(r["id"], r["loaded_now"])
        if r["id"] == "pcie_frac":
            r["next_load"] = "0.55"
    stop = threading.Event()

    def traffic():
        n = 0
        while not stop.is_set():
            n += 1
            with fake.lock:
                fake.last = request_doc(prompt_tokens=300 + 400 * (n % 7), generated=24 + (n % 5) * 20,
                                        decode_ms=380.0 + 25 * (n % 5), hit=0.62 + 0.03 * (n % 7), adapt=1.0 + 0.5 * (n % 3))
            stop.wait(0.6)
    threading.Thread(target=traffic, daemon=True).start()
    studio = subprocess.Popen([sys.executable, str(ROOT / "studio.py"), "--strata", fake.url, "--results", str(results),
                               "--port", str(STUDIO_PORT), "--state", str(tmp / "state.json")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if get("/api/state")["strata"]["status"] == "ok":
                    break
            except Exception:
                pass
            time.sleep(0.1)
        time.sleep(8)                                              # a few samples for the small multiples
        base = f"http://127.0.0.1:{STUDIO_PORT}/"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--disable-gpu", "--no-sandbox"])
            run_checks(browser, base, fake, tmp)
            browser.close()
    finally:
        stop.set()
        studio.terminate()
        studio.wait(5)
        fake.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)} passed, {len(bad)} failed")
    return 1 if bad else 0


def run_checks(browser, base, fake, tmp):
    errors = []
    for scheme in ("light", "dark"):
        for (w, h, tag) in ((1400, 900, "desktop"), (390, 844, "phone")):
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(base + "#live")
            page.wait_for_selector("canvas.bar-decode")
            page.wait_for_timeout(2500)
            tagname = f"{tag}-{scheme}"
            st = page.evaluate(JS_VISIBLE_STATS)
            dec = page.evaluate(JS_DECODE)
            record(f"live {tagname}: 0 tables, 0 details, 0 <p> over 12 words", st["tables"] == 0 and st["details"] == 0 and st["longP"] == 0,
                   f"tables={st['tables']} details={st['details']} p>12={st['longP']} p>8={st['p8']}")
            record(f"live {tagname}: no visible text node over 8 words", not st["longText"], "; ".join(st["longText"])[:120])
            record(f"live {tagname}: hero has the largest font", st["heroFont"] >= st["maxFont"] and st["heroFont"] == (56 if w > 600 else 44),
                   f"hero={st['heroFont']} max={st['maxFont']} {st['maxEl']}")
            record(f"live {tagname}: no GPU label, one key row", not st["hasGPU"] and st["keyRows"] == 1, f"keyrows={st['keyRows']}")
            names = dec["names"]
            record(f"live {tagname}: decode bar has 5 segments in order", names == ["card", "CPU experts", "draft", "adapt wait", "other"], str(names))
            op = dec["opacity"].split(",")
            dom_i = names.index(dec["dominant"]) if dec["dominant"] in names else -1
            record(f"live {tagname}: dominant segment opacity 1, others .55", dom_i >= 0 and op[dom_i] == "1" and op.count("0.55") == 4, dec["opacity"])
            if w > 600:
                record(f"live {tagname}: decode bar >= 1060 px", dec["areaW"] >= 1060, f"{dec['areaW']:.0f}")
                total = sum(dec["widths"])
                share = dec["widths"][dom_i] / total if total else 0
                record(f"live {tagname}: dominant width = share x bar", dom_i >= 0 and abs(dec["widths"][dom_i] - share * dec["areaW"]) <= 2, f"{dec['widths'][dom_i]:.0f}px")
                record(f"live {tagname}: fits 1400x900, no scrollbar", st["scrollH"] <= 900 and st["bottom"] <= 900, f"scrollH={st['scrollH']} bottom={st['bottom']:.0f}")
            else:
                record(f"live {tagname}: scrollWidth <= 390 and decode bar >= 340", st["scrollW"] <= 390 and dec["canvasW"] >= 340, f"scrollW={st['scrollW']} bar={dec['canvasW']:.0f}")
            page.screenshot(path=str(SHOTS / f"live-{tag}-{scheme}.png"), full_page=(w < 600))

            # compare
            page.goto(base + "#compare")
            page.wait_for_selector("canvas.bullets")
            page.wait_for_timeout(1500)
            st = page.evaluate(JS_VISIBLE_STATS)
            rows = page.evaluate("() => [...document.querySelectorAll('#view-compare canvas.bullets')].flatMap((c) => JSON.parse(c.dataset.rows || '[]'))")
            record(f"compare {tagname}: 0 tables, 0 details, 0 <p> over 12 words", st["tables"] == 0 and st["details"] == 0 and st["longP"] == 0,
                   f"tables={st['tables']} p>12={st['longP']} p>8={st['p8']}")
            record(f"compare {tagname}: no visible text node over 8 words", not st["longText"], "; ".join(st["longText"])[:120])
            record(f"compare {tagname}: every row has a noise band", rows and all(r["band"] is not None and r["band"] > 0 for r in rows if r["b"] is not None and r["a"] is not None), f"{len(rows)} rows")
            record(f"compare {tagname}: hero largest, one key row", st["heroFont"] >= st["maxFont"] and st["keyRows"] == 1, f"hero={st['heroFont']} max={st['maxFont']} keyrows={st['keyRows']}")
            if w > 600:
                record(f"compare {tagname}: fits 1400x900", st["scrollH"] <= 900, f"scrollH={st['scrollH']}")
            else:
                record(f"compare {tagname}: scrollWidth <= 390", st["scrollW"] <= 390, f"{st['scrollW']}")
            page.screenshot(path=str(SHOTS / f"compare-{tag}-{scheme}.png"), full_page=(w < 600))

            # tune (idle, last tune record)
            page.goto(base + "#tune")
            page.wait_for_timeout(1500)
            st = page.evaluate(JS_VISIBLE_STATS)
            record(f"tune {tagname}: 0 tables, 0 details, 0 <p> over 12 words", st["tables"] == 0 and st["details"] == 0 and st["longP"] == 0, f"tables={st['tables']} p>12={st['longP']}")
            record(f"tune {tagname}: no visible text node over 8 words", not st["longText"], "; ".join(st["longText"])[:120])
            if w <= 600:
                record(f"tune {tagname}: scrollWidth <= 390", st["scrollW"] <= 390, f"{st['scrollW']}")
            page.screenshot(path=str(SHOTS / f"tune-{tag}-{scheme}.png"), full_page=True)
            ctx.close()

    # behaviour checks (desktop, light)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "#live")
    page.wait_for_selector("canvas.bar-decode")
    page.evaluate("() => { window.__marker = 42; }")
    nav = page.evaluate("() => [...document.querySelectorAll('#tabs a')].map((a) => a.textContent.trim())")
    record("nav has exactly Live, Compare, Tune", nav == ["Live", "Compare", "Tune"], str(nav))
    page.click("#data-btn")
    page.wait_for_selector("#data-live table")
    n_tables = page.evaluate("() => document.querySelectorAll('#data-live table').length")
    with page.expect_download() as dl:
        page.click("#data-live button:has-text('CSV')")
    csv_ok = dl.value.suggested_filename.endswith(".csv")
    page.click("#data-btn")
    page.wait_for_selector("canvas.bar-decode", state="visible")
    record("Data toggle shows tables, CSV downloads, charts return without reload",
           n_tables >= 1 and csv_ok and page.evaluate("() => window.__marker") == 42, f"tables={n_tables}")
    chips = page.evaluate("() => document.querySelectorAll('.chip-warn').length")
    record("settings: at most one chip on the main view, table only in drawer", chips <= 1 and page.evaluate("() => document.querySelectorAll('main table').length") == 0, f"chips={chips}")
    page.click("#settings-btn")
    page.wait_for_selector("#drawer[open] table")
    page.screenshot(path=str(SHOTS / "settings-drawer.png"))
    page.keyboard.press("Escape")

    # the tune flow through the Studio, then the compare in place on the Tune view
    page.goto(base + "#tune")
    page.wait_for_timeout(500)
    page.click("button:has-text('Tune this PC')")
    page.wait_for_selector("#confirm-dialog[open]")
    page.click("#confirm-ok")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(SHOTS / "tune-running.png"))
    page.wait_for_function("() => document.querySelectorAll('.step-done').length === 4", timeout=60000)
    page.wait_for_selector("#view-tune canvas.bullets", state="visible", timeout=15000)
    page.wait_for_timeout(1500)
    h = page.evaluate("() => location.hash")
    record("after step 4 the compare is visible on Tune, hash unchanged", h == "#tune", h)
    cands = page.evaluate("() => [...document.querySelectorAll('#view-tune canvas.cand')].map((c) => [c.dataset.kept, c.dataset.best])")
    record("4 candidate charts drawn, kept comes from the result", len(cands) == 4 and cands[0][0] == "0.55" and cands[0][1] == "0.35", str(cands))
    page.screenshot(path=str(SHOTS / "tune-done.png"), full_page=True)
    ctx.close()

    # reduced motion
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(base + "#tune")
    page.wait_for_timeout(1200)
    anim = page.evaluate("() => [...document.querySelectorAll('.pulse, .step-dot')].map((e) => getComputedStyle(e).animationName)")
    record("reduced motion: no CSS animation runs", all(a == "none" for a in anim), str(anim))
    ctx.close()
    record("no page errors or console errors", not errors, "; ".join(errors)[:200])


if __name__ == "__main__":
    sys.exit(main())
