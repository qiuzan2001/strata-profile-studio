# Changelog

One PC measured the numbers below: RTX 5070 Ti 16 GB, Ryzen 7 5800X3D, Swift 1.5 IQ3_S, 2026-10-15.

## Unreleased

- **Workload and Tune are one view.** The Workload tab is gone. Tune has two panes, "Standard test" (`#tune`) and "This PC"
  (calibration, `#tune/pc`). `#workload` still opens the first pane. Compare stays a view of its own. The views are five:
  Now, Runs, Compare, Tune, Settings.
- **Stock banner removed:** the top banner "This Strata build has no profile routes (Strata 0.1.44.1 as released) ..." was
  out of date. Strata PRs #2660 and #2661 were closed unmerged, and every release (0.2.2 too) has no profile routes. Stock
  mode is now the normal case. The header still shows "stock Strata". A banner appears only for a version mismatch.
- **Fewer repeated cards:** with two runs, Compare shows the two-run answer first and no longer repeats the change chart,
  the settings table and the window composition (they stay for three or more runs). Tune drops the stage-duration bars
  (the time axis shows them), the second change chart (the full comparison is one click away) and the rate card (the
  history table has a "Best rate" column).
- **Tune with the standard test** (Tune view, pane "Standard test"): replays one conversation of 6 turns like a coding
  agent's session (a 24,000-token first prompt, then turns that add 800 to 3,000 new tokens on the reused conversation,
  replies of 128 to 512 tokens, some copied text and some free text; the same on every PC and in every run) with each
  candidate of `pcie_frac` and `spec_min_p`, in interleaved rounds with an A/A noise floor and a paired bootstrap (at
  least 5 rounds). The score is the generation time per generated token, for the copy replies and the free replies apart. It checks that the reply text stays the same and that Strata reuses
  the conversation, waits for a quiet Strata, drops spoiled rounds, and can write the winner (after a dated copy),
  restart Strata (`--restart-cmd`), verify, and roll back. Only settings that do not change the answers are tuned. There
  are no scenario presets and the turns are not taken from the log: both would be guesses about the workload.
- **Your requests** (same pane, read only): the counts of the requests in the engine log (prompt length, reused share,
  reply length, speeds, the split of the time) and ideas for them. `GET /api/workload?last=N&min_prompt=N` gives them.
- **Settings:** new rows for `--suffix-draft`, `--lookup-chain`, `reasoning_budget_tokens` and `STRATA_ROUTE_TAIL_SKIP`,
  each marked "changes the answers" or "same answers". The two that change answers are never tuned by the Studio.

## 0.1.2 (2026-10-16)

- **Result cards:** `studio.py --card <report>` writes the card of one profile run as `card.html`, `card.svg`,
  `card.json` and `card.png` (the PNG with a headless browser, else Pillow). Schema `strata-card/1` and a
  standard-library validator are in `cards/`.
- **The Share dialog** on Runs and in the run detail shows the card and its JSON first. It has three destinations.
  - A GitHub issue in `qiuzan2001/strata-cards`, with `gh` on this PC. The dialog then shows the card's address
    from the answer of the site's check (it waits up to 3 minutes).
  - A link that opens the filled issue form in the browser (at most 8,000 characters).
  - The files on this PC.
  - `--read-only` hides the dialog, and the demo sends no card. `--cards-repo` names another repository.
- **The cards site** is live: https://qiuzan2001.github.io/strata-cards/.
- **The cards site** (`cards/site/`): the site builder, the issue forms, the workflow for GitHub Pages and the nine
  seed cards. They are ready to copy into the repository `qiuzan2001/strata-cards`.
- **Wording:** the comparison verdicts and the server's messages are sentences, without semicolons.
- A test checks the nine real reports of 2026-10-15: no host name, user name, path, key, start command or prompt
  text reaches a card.

- Fixed: `--demo` on a fresh clone. The demo's state file was hidden by `.gitignore` since 0.1.0, so the demo stopped with "No such file". It is in the repository now.

## 0.1.1 (2026-10-16)

- **Stock mode: Strata 0.1.44.1 as released.** That build has no `/profile` routes and no `tools/strata_profile.py`.
  Two users reported that the Studio then said "Profiling is off" and that `"profile": true` did nothing. Now a 404
  of `/profile/status` is stock mode (except on a build with the routes), and the Studio does the work itself.
  - The last request's breakdown comes from the engine log's timing lines.
  - **Timing lines** on Settings adds the two switches to the run config's `"env"`, with a `.bak` copy.
  - **Run a profile** runs a copy of Strata's profile tool (MIT) against the running server.
  - **Tune this PC** unloads the model and checks that `/health` says it is not loaded. Then it runs setup's
    calibration for the run config and asks for a restart of Strata.
- The comparison of a release build's runs and the Compare page work as before.
- `--strata-root DIR`: Strata's folder, when the run config is not in it.
- Works on: a community report of Windows 11 with an RTX 5060 Ti (Strata #2453).

## 0.1.0 (2026-10-15)

The first release. It has the five rounds below. It also has three items for people without Strata.
These are `python3 studio.py --demo` (a demo Strata with recorded data, in one process), a 20-second tour GIF, and
this file.

### Round 1: the Studio, designed from the real data

- Four views (Now, Runs, Tune, Settings) and a request panel. All charts are hand-written SVG and HTML, with no library.
- Now: a live diagram of the PC (Disk, RAM, CPU, PCIe, VRAM, GPU) from Strata's `/metrics`, with the limit marked.
- An icicle of where the time went: over 66 requests, prompt reads took 130 s and the decode 48 s.
- Compare against a noise floor measured from runs with the same settings, never less than 3 %.
- Tune this PC: a profile, Strata's calibration, a model load, a second profile and the comparison.

### Round 2: the user's ten requests

- No flicker: in 30 s of live updates, the page added and removed no node (759 layout boxes, none moved).
- A Compare view against the baseline (the newest run with the engine defaults). Tuned runs were 5 to 27 % faster than
  the baseline (agent turns +26.6 %).
- The sign test: from run 5 to run 6, 6 of 6 agent turns were slower (p = 1/32).
- A PC module with per-core CPU, GPU clocks and power, the expert flow, and an expert map.
- Editable settings with a `.bak` copy, a Tune detail page, and plain wording (ASD-STE100).

### Round 3: portability, and the Settings reset fix

- Host readers for Linux, Windows and macOS, and GPU readers for NVIDIA and AMD. A missing reading says why.
- `--host-info off`: each view works from Strata's data alone. `--host-probe` prints what each reader gets on a PC.
- Flicker with new requests every 6 s: two causes found and fixed. Then 0 nodes changed in 30 s (766 boxes).
- Fix: "Reset all" removed flags that setup wrote, and Strata did not start. Reset now never removes them. Before
  each write, the Studio checks the engine's rule for native packs.
- The page check passed 94 of 94 with host readings on, and 94 of 94 with them off.

### Round 4: the README for other people

- The README is a front page: install first, a section per view with its screenshot, a tune step by step.
- One sentence to give an AI agent, and `docs/AI_SETUP.md` with numbered steps for the agent.
- Tried with `claude -p` against a test copy of Strata: the Studio ran after 26 s, with no question.
- `studio.py --config <file>` alone finds Strata's address and the reports.
- `docs/REFERENCE.md`, `CONTRIBUTING.md`, a `.gitignore`.

### Round 5: the agent reports back

- At the end of an install, the agent writes a report: what failed, what fixed it, and the host probe.
- It removes keys, host names and user names, shows the report, and asks one time to open an issue.
- Issue forms: install report, works on my PC, bug.
- Tried with a planted fault (an old `python3`): the agent used a newer Python and wrote a report with no secret.
  After "No", it opened no issue (47 s).
- Step 5 of the guide now looks for a newer Python by its version number.
