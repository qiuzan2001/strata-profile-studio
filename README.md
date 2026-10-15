# Strata Profile Studio

A profiling and tuning tool for [Strata](https://github.com/Niko1221/Strata), running next to it on the same PC.
It shows four things:
- which part of the PC is the limit, live;
- where each request's time goes;
- whether a change made it faster, against this PC's own noise;
- what a tune measured, and what it did to the requests you actually send.

It needs only Python 3. The server uses only the standard library, and the page is plain JavaScript with no library.

![Now](docs/screenshots/now.png)

| Runs | Compare | Tune |
| --- | --- | --- |
| ![Runs](docs/screenshots/runs.png) | ![Compare](docs/screenshots/compare.png) | ![Tune](docs/screenshots/tune.png) |
| **A request** | **Settings** | **Dark** |
| ![Request](docs/screenshots/request.png) | ![Settings](docs/screenshots/settings.png) | ![Dark](docs/screenshots/now-dark.png) |

These are screenshots of the real data of one PC (RTX 5070 Ti, Ryzen 7 5800X3D, Swift 1.5 IQ3_S) on 2026-10-15.
`docs/screenshots/` has every view in light, dark and at 390 px, plus the states (Strata unreachable, profiling off,
a tune started elsewhere and others).

## Requirements

- Linux, Python 3.11 or newer, and a browser. (It may work on other systems; only Linux is tested.)
- Strata, running on the same PC.
- For where the time goes (Now), **Run a profile** and **Tune**, Strata needs `"profile": true` in
  `strata-<model>.json` (or `serve/server.py --profile`). Without it, Now shows speeds only.
- **Runs**, **Compare** and the Settings file work without profiling, and without Strata running at all. The page
  says which state Strata is in and what to do about it.

## Start

```
python3 studio.py --strata http://127.0.0.1:8080 --results ~/Strata/bench/results
```

Give it the run config too. This enables the Settings view and "go back to the settings of a run", and the
engine log next to the config gives the PC diagram its measured PCIe ceiling:

```
python3 studio.py --strata http://127.0.0.1:8080 --results ~/Strata/bench/results \
    --config ~/Strata/strata-swift-iq3_s.json
```

Then open <http://127.0.0.1:8090>. When Strata has an API key, give it without putting it on the command line:

```
STRATA_KEY=... python3 studio.py --strata http://127.0.0.1:8080 --results ~/Strata/bench/results
```

The Studio prints `Strata Profile Studio on http://127.0.0.1:8090/ (Strata: ..., reports: ...)` and keeps running until
you press Ctrl-C. Closing the browser tab does not stop anything: the polling and a running tune live in `studio.py`.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--strata URL` | `http://127.0.0.1:8080` | Where Strata answers. |
| `--results DIR` | `~/Strata/bench/results` | Strata's `bench/results` folder: profile reports are read from here. |
| `--key KEY` | `$STRATA_KEY` | Strata's API key. Prefer the environment variable: it stays out of `ps`. |
| `--port N` | `8090` | The Studio's port. |
| `--host ADDR` | `127.0.0.1` | The address to listen on. Anything but `127.0.0.1`, `::1` or `localhost` needs a key. |
| `--allow-host HOST[:PORT]` | none | Also answer to this `Host` name (repeatable) while still listening on `--host` only: for a reverse proxy, see below. A name without a port allows any port. |
| `--config PATH` | none | Strata's `strata-<model>.json`. The Settings view shows its values. It is written only when you confirm "use the settings of run X" (a `.bak` is kept first). |
| `--engine-log PATH` | the config's `"log"`, else `strata-<model>.log` next to it | Strata's engine log, read only, tail only. The PC diagram reads the engine's PCIe probe line from it (the measured link and CPU-pool speeds). |
| `--read-only` | off | Look only: no profile run, no tune, no config write (every POST answers 403). Use it to show the Studio to someone. |
| `--state PATH` | `studio-state.json` next to `studio.py` | The Studio's own file (tune records, notes). |
| `--version` | | Print the version. |

## The views

Every view opens with a row of headline numbers. Each chart is titled by the question it answers and says whether
higher or lower is better. Comparisons are ranked bars with the value at the end. Tables live behind **All numbers**,
or in the detail views.

**Now: how fast is it, where does the time go, what is the limit?**
- **The headline:** the last request's decode and prompt-read speed, the hit rate, the limit, and the engine time of
  the window.
- **The PC diagram:** Disk → RAM → CPU → PCIe → VRAM → GPU.
  - Each block shows its live reading with a 60 s sparkline. Each link is filled by its throughput against its
    ceiling.
  - The block that owns the largest share of the last decode window is marked **the limit**. On this PC that is the CPU
    experts: RAM-bandwidth-bound.
  - It reads `/metrics` every second and updates in place.
- **Where did the time go:** an icicle of the last request or the last N requests. The prompt read is split by phase
  and the decode by window part, so the widest block is the biggest cost. Over a profile run, prompt reads are most
  of the engine time.
- **The trend:** every request's decode, prompt read, window or hit rate.
  - It plots against order, clock, context size or hit rate.
  - Profile runs show as bands and the calibration as a marker. Outliers for their kind are ringed.
  - The calibration's own requests are drawn hollow and are not counted in any number on the page.
- **The rest:** the slowest kind, the slowest requests for their kind, and where the experts came from.

**A request (the panel): why was this one slow?**
- Click any request anywhere to open it.
- It shows the prompt read by phase and the decode by window on one time axis, and one window magnified.
- Then: where the experts came from (hits and lookups), the KV reads from VRAM, the drafts, and every prompt phase.
- Last, the same request in every other run, which is that request's run-to-run noise.

**Runs: every profile report as an experiment.**
- Ranked decode and prompt speeds per run.
- The spread of runs with identical settings, which is the noise a change has to beat.
- A table of every run with only the settings that differ.
- One run: each request's time split, the window per workload, the report's own findings, its settings and
  environment, a note, and **Save as image** (a card with the headline numbers).
- **Compare** two runs (see below).

**Tune: run the tune, see what it did.**
- **Tune this PC** runs Strata's own calibration inside a profile before and a profile after: profile before →
  calibrate → reload → profile after → compare. Progress shows live. **The model is unloaded for the whole
  calibration; requests get 503 meanwhile.** The confirm dialog says so and asks you to tick that nobody needs
  Strata.
- **The calibration's sweeps:** one plot per setting (PCIe share, draft floor, CPU workers, expert tier). Each marks
  the value kept, the best value when it was not kept (the calibration keeps a value only when it beats the default
  by 3%), and the value before the tune.
- **The gap:** the calibration measures 128-token decodes of three short prompts. The before/after profiles measure
  the rest (long context, agent turns), against the noise and with a sign test. If your workloads got slower, **Go
  back to the settings of run X** opens Settings with that run.
- **Every tune**, with its outcome.

**Settings: what runs now, what the next load uses, what the file says.**
- Every speed setting in its three layers, with what it does. A row is marked where the layers disagree. For
  example: after a tune, the tuned values stay in the running Strata process until it restarts, while the file may
  say something else.
- **Use the settings of run X** writes that run's values into the run config file. See below.

## How the comparison decides

A change counts only when it is larger than the noise of this PC, and the noise is **measured**, not assumed, whenever
the results folder allows it. Two reports made with the same settings (the same PCIe share, draft floor, workers and
expert tier, and the same GPU, model, quant and context) differ from each other only by noise; for each metric (per
kind of request) the floor is the largest such difference found on disk, never less than 3% (calibrate.py's own
`MIN_GAIN`). The verdict says where it came from ("noise floor from runs 3 and 4, same settings"). With no
same-settings pair yet, the floors are fixed guesses (5% for speeds, 1 ms for a window part, 2 points for a hit rate)
and the verdict says "(assumed floor, no same-settings pair measured yet)": run the profile twice with one setting to
measure it. The three chat questions of one report are three different prompts, so their spread is never used. When
at least five agent turns all move the same way, the verdict says so ("6 of 6 agent turns slower"), a sign test that
can show a small real change even where each turn is inside its floor. The page gives the exact two-sided p
(6 of 6 turns: 1 in 32).

## What the Studio writes, and what it never touches

**`studio-state.json`** (and its `.tmp` and `.bad` siblings): tune records, notes, and the settings captured for
reports that record none.

**The run config file (`--config`)**, written only by **Use the settings of run X** in Settings, and only when you
confirm it:
- Only the settings you tick change in its `"args"` (or `"env"`). A setting the run left to the engine is removed.
- Every other key and argument stays. The file is written again as JSON with its own indentation.
- The file as it was is copied to `<file>.bak` first. A previous `.bak` is replaced.
- The write is refused in four cases:
  - while a tune runs (Strata writes that file at the end of a tune);
  - when the file changed since you looked at it (a sha check);
  - without `--config`;
  - with `--read-only`.
- **Strata reads the file at its next start.** The running engine keeps its settings until then, so restart Strata
  to use them.

**Everything else is only read:** the results folder, the engine log, and Strata's routes.

**What changes Strata's settings:** only `POST /profile/tune`, Strata's own route, which **Tune this PC** calls.

**What the Studio does not do:** it does not start, stop or reload Strata. The one exception is the short chat
request that loads the model again after a tune.

## Security

- It listens on `127.0.0.1` only. `--host` with any other address refuses to start without `--key`, and then every
  `/api/` request must carry `Authorization: Bearer <key>` (the page asks for it and keeps it in `sessionStorage`).
- The key lives only in the process's memory. It is never in a response, the page, the state file or a log line.
- `Host` must be one of the Studio's own addresses (DNS rebinding), and a POST needs `Content-Type: application/json`,
  at most 64 KiB and no foreign `Origin`, so a web page elsewhere cannot start a tune through it.
- The page's Content-Security-Policy is `script-src 'self'`; it never uses `innerHTML` (a test greps for it) and
  draws text with `textContent`. The page loads nothing from outside the PC.
- The Studio calls a fixed list of Strata routes and never forwards a path the page names.

### Behind a reverse proxy (tailscale serve)

The Studio keeps listening on `127.0.0.1`; the proxy reaches it there, and `--allow-host` lets the proxy's name
through the Host check (which is otherwise strict). With tailscale:

```
python3 studio.py --strata http://127.0.0.1:8080 --results ~/Strata/bench/results \
    --config ~/Strata/strata-swift-iq3_s.json --allow-host ub01.char-logarithm.ts.net:8444
tailscale serve --bg --https=8444 http://127.0.0.1:8090
```

Then open `https://ub01.char-logarithm.ts.net:8444/`. A page served over HTTPS sends `Origin: https://<that name>`,
which the Studio accepts only for names given with `--allow-host`. Everyone who can reach that address can use the
Studio, including starting a tune and writing the run config: with `--key` given, every `/api/` request needs it
(the page asks for it). To let others look without being able to change anything, add `--read-only`.

## Tests

```
python3 -m unittest discover -s tests
```

**The unit tests** need no Strata, no GPU and no network, and take about 20 seconds. `tests/fakestrata.py` is an
in-process stand-in for Strata's routes.

**The page check** drives every view and every state in headless Chromium against `tests/demo_strata.py`. That is a
fake Strata fed with recorded data: it can replay real reports, live samples, `/metrics` hardware and a real
calibration's output. Run it with a Python that has Playwright:

```
<venv>/bin/python tests/page_check.py                 # fixture reports; screenshots into docs/screenshots
<venv>/bin/python tests/page_check.py --seed ~/Strata/bench/results --live live.json --hardware hardware.json \
    --settings settings.json --tunes studio-state.json --config strata-x.json   # copies of real data
```

It prints one PASS/FAIL line per check. The checks:
- no script error;
- no horizontal scroll at 390 px;
- the PC diagram adds no node in a minute of updates, and its readings move;
- Run a profile and Tune this PC work from their buttons, and the tune ends succeeded;
- the settings write leaves a `.bak`;
- each state says what it is.

It never touches port 8080 or a real Strata file.

**To try the Studio with no GPU:**

```
python3 tests/demo_strata.py --port 8195 --results /tmp/demo-results --state decoding
python3 studio.py --strata http://127.0.0.1:8195 --results /tmp/demo-results --port 8196
```

## Third-party code

None is loaded by the page. Every chart is hand-written SVG or HTML, so the dark theme needs no redraw and the live
diagram can update attributes in place.

[Chart.js](https://www.chartjs.org/) **4.4.1** (MIT) is still vendored byte for byte at
`web/vendor/chart-4.4.1.umd.js`, from `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js`:
- SHA-256 `74401d738dd3e03ee5dfb3b6841210fe2c4ead8a960c4011ca4ba0b78a9fd8f3`, recorded in `web/vendor/README.md` and
  checked by a test;
- its licence is in `web/vendor/LICENSE-chart.js.md`.

## Where the design comes from

| Source | What was taken |
| --- | --- |
| Chrome DevTools' Performance panel and speedscope | The icicle of where the time went, and the overview → one request → details order. |
| Nsight Systems | The request on one time axis, with one window magnified. |
| Grafana | Headline numbers with a delta, and run and tune bands on time axes. |
| Optuna's slice plots | The calibration's sweeps. |
| Weights & Biases | The runs table, with only the differing settings and same-settings groups. |
| Benchmark comparisons (forest plots) | The change against a noise band. |
| artificialanalysis.ai and ModelDock | Charts titled by their question, ranked bars with the value at the end, distributions for noise, the headline row, a share card. |

## Licence

MIT, Copyright (c) 2026 qiuzan2001. See `LICENSE`. Chart.js is MIT as well.
