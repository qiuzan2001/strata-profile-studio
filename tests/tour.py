"""The README's tour GIF (docs/screenshots/tour.gif), recorded from `studio.py --demo`.

Two steps, because Playwright and Pillow can be in two different Pythons:

    <venv with Playwright>/bin/python tests/tour.py record --frames /tmp/tour
    python3 tests/tour.py gif --frames /tmp/tour --out docs/screenshots/tour.gif

`record` starts the demo, opens it at 1200 px and takes about 10 screenshots per second: Now with the live PC
module, then Compare, then the tune result.  `gif` makes the GIF with Pillow, else with ffmpeg, else it says that
it cannot and leaves the README's PNG as it is.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZE = {"width": 1200, "height": 750}
FPS = 10


def free_port(start: int) -> int:
    for p in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("no free port")


def record(frames: Path):
    from playwright.sync_api import sync_playwright
    frames.mkdir(parents=True, exist_ok=True)
    for f in frames.glob("*.png"):
        f.unlink()
    port = free_port(8190)
    proc = subprocess.Popen([sys.executable, str(ROOT / "studio.py"), "--demo", "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    times = []
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/api/state", timeout=2).read()
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport=SIZE, color_scheme="light")

            def shoot(seconds, step=None):
                end = time.time() + seconds
                while time.time() < end:
                    t = time.time()
                    if step:
                        step()
                    page.screenshot(path=str(frames / f"{len(times):04d}.png"))
                    times.append(t)
                    time.sleep(max(0.0, 1 / FPS - (time.time() - t)))

            def scroll_to(text, seconds):
                y = page.evaluate("(t) => { const h = [...document.querySelectorAll('h3')].find((n) => n.textContent.includes(t));"
                                  " return h ? h.closest('.card').getBoundingClientRect().top + window.scrollY - 70 : 0; }", text)
                y0 = page.evaluate("window.scrollY")
                n = max(1, int(seconds * FPS))
                k = [0]

                def step():
                    k[0] = min(n, k[0] + 1)
                    page.evaluate(f"window.scrollTo(0, {y0 + (y - y0) * k[0] / n})")
                shoot(seconds, step)

            page.goto(f"{base}/#now")
            time.sleep(6)                                  # the first requests and readings
            shoot(8)                                       # Now: the live PC module
            page.goto(f"{base}/#compare")
            time.sleep(2.5)
            shoot(1.5)
            scroll_to("Change against the reference", 1.5)
            shoot(2)
            page.goto(f"{base}/#tune")
            time.sleep(2.5)
            shoot(1)
            scroll_to("Tune result per workload", 1.5)
            shoot(3)
            browser.close()
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    (frames / "times.json").write_text(json.dumps(times))
    print(f"{len(times)} frames in {times[-1] - times[0]:.1f} s into {frames}")


def gif(frames: Path, out: Path) -> int:
    files = sorted(frames.glob("*.png"))
    times = json.loads((frames / "times.json").read_text())
    durations = [min(300, max(20, int(round((b - a) * 1000)))) for a, b in zip(times, times[1:])] + [2000]   # the page loads between views: not shown
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        imgs = [Image.open(f).convert("RGB") for f in files]
        sample = Image.new("RGB", (imgs[0].width, imgs[0].height * 4))
        for i, im in enumerate(imgs[:: max(1, len(imgs) // 4)][:4]):  # one palette for all frames: small deltas
            sample.paste(im, (0, i * im.height))
        pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
        q[0].save(out, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=False, disposal=1)
    elif shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "%04d.png"),
                        "-vf", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none", str(out)],
                       check=True)
    else:
        print("No Pillow and no ffmpeg: the GIF cannot be made. The README keeps the PNG.")
        return 1
    print(f"{out}: {out.stat().st_size / 1e6:.2f} MB, {len(files)} frames, {sum(durations) / 1000:.1f} s")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Record the README's tour GIF from studio.py --demo.")
    ap.add_argument("step", choices=("record", "gif"))
    ap.add_argument("--frames", required=True)
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots" / "tour.gif"))
    a = ap.parse_args()
    if a.step == "record":
        record(Path(a.frames))
        return 0
    return gif(Path(a.frames), Path(a.out))


if __name__ == "__main__":
    sys.exit(main())
