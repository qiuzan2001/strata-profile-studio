# Installing Strata Profile Studio for a user (instructions for AI agents)

This page is for an AI coding assistant (Claude Code, Codex, Cursor, GitHub Copilot, ...) that a user asked to
install Strata Profile Studio. The Studio is a web page about [Strata](https://github.com/Niko1221/Strata) on this
PC. It shows the limiting component, the time per request, the change between runs, and the tune. It runs next to
Strata on the same PC. It needs Python 3.11 or newer and nothing else (standard library only). The version for
persons is the [README](../README.md).

Do the steps in order. Each step has a check, and what to do when the check fails. Tell the user what you do in
plain words. Ask the user only where a step says so.

## 0. Rules

- **Do not change the user's PC more than these steps say.** The Studio goes into its own folder. The only file of
  Strata that you may change is the run config, in step 4 only, and only with the consent of the user.
- **Do not start a tune or a profile run.** They unload the model for minutes. The user starts them from the page.
- **Never make the Studio reachable from other PCs without a key.** Keep the default address `127.0.0.1`. Do not
  use `--host` or `--allow-host` unless the user asks for it. Then also give `--key` (see the README, "On another PC
  or over Tailscale").
- **Never edit the run config while a tune runs.** The calibration writes the file at its end. Step 4 says how to
  check.
- **Never print the API key.** Strata's run config can have an `"api_key"`. Read it into an environment variable
  only.
- **Keep notes of each problem.** When a check fails, or a command gives an error, write down the step and the
  exact error text. Also write down what you did to get past it. Step 12 uses these notes.

## 1. Find the Strata install

Strata's setup writes a run config `strata-<model>.json` (for example `strata-swift-iq3_s.json`) into the Strata
folder, next to `setup.py`. Look for it in the usual places first:

```
Linux, macOS:  ls ~/Strata/strata-*.json
               find ~ -maxdepth 3 -name 'strata-*.json' -not -name '*.bak*' 2>/dev/null
Windows:       Get-ChildItem -Path $HOME, C:\ -Filter 'strata-*.json' -Recurse -Depth 2 -ErrorAction SilentlyContinue
```

A Strata folder has `setup.py` and at least one `strata-*.json`. Ignore files that end in `.bak`.

**Check:** one Strata folder with one `strata-*.json`. Use that file. This page calls it `<config>`.

**When the check fails:**
- One folder with several `strata-*.json` (several models): use the one that runs now. Its `"port"` answers on
  `/health` (step 3) with its `"model_name"`. When none runs, ask the user which model to use.
- Several Strata folders: ask the user which one.
- No Strata folder: Strata is not installed. Stop, and tell the user. Strata's own installer is in its repository:
  `docs/AI_SETUP.md` of [Strata](https://github.com/Niko1221/Strata).

## 2. Read the run config

Read three values. Do not print the key:

```
python3 -c "import json,sys; c=json.load(open(sys.argv[1], encoding='utf-8-sig')); print(c.get('port', 8080), 'key' if c.get('api_key') else 'no key', c.get('profile'))" "<config>"
```

(On Windows, use `py -3` instead of `python3`.) The values are Strata's port (8080 when there is no `"port"`),
whether Strata needs a key, and the `"profile"` key. That key does something only on a Strata build with the
profile routes (step 4).

**Check:** the command prints a port.

**When the check fails:** the file is not valid JSON. Do not change it. Tell the user and stop.

## 3. Check that Strata answers

```
curl -s http://127.0.0.1:<port>/health
```

**Check:** the answer has `"status": "ok"`. `"loaded": false` is also a pass: Strata loads the model at its next
request, and the Studio works without the model.

**When the check fails:**
- No answer: Strata does not run. Start it in the user's usual way. That is the start script in the Strata folder (`run-<model>.sh` on Linux and macOS, `run-<model>.bat` on Windows), or `./setup.sh` /
  `START-HERE.bat` without flags. Start it in the background. Poll `/health` every 10 s for up to 5 minutes (the
  first load can take minutes). Tell the user that you started Strata.
- `401`: Strata has an API key. Step 7 gives it to the Studio.

## 4. Turn on the time per component (with the user's consent)

The Studio shows the time of each part of a request (the GPU, the CPU experts, the draft, PCIe) when the engine
writes it. First find out which Strata this is:

```
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/profile/status
ls "<Strata folder>/serve/profile_page.py"
```

- **A build with the profile routes** (Strata with PRs #2660 and #2661: the status answers 200, or
  `serve/profile_page.py` exists): go to step 4a.
- **A release build** (Strata 0.1.44.1 as released: the status answers 404 and there is no
  `serve/profile_page.py`): go to step 4b. Do not add `"profile": true` there. That key does nothing on this build.

Before you change the run config, make sure that no tune runs. A tune writes the file at its end. On a build with
the routes, `/profile/status` must not show a tune with `"state": "running"`. On a release build, ask the user
whether a tune runs in the Studio.

### 4a. A build with the profile routes

**Check:** step 2 printed `True`, or `/health` has `"profile": true`. Then go to step 5.

**When the check fails:** ask the user: "Strata's profiling is off. With it on, Strata records the time of each part
of a request, and the Studio can show it and tune Strata. It needs a restart of Strata (the model loads again).
Turn it on?"
- **Yes:** copy `<config>` to `<config>.bak`. Add the top-level key `"profile": true` and keep all other keys as
  they are. Stop Strata (close its window, or stop its process), and start it again as in step 3.
- **No:** continue. The Studio then shows speeds only on Now. Runs, Compare and Settings work.

### 4b. A release build (stock mode)

The Studio does the work of the profile routes itself. It reads the engine's timing lines from the engine log
(`"log"` in the run config). The engine writes them only when the run config's `"env"` has
`"STRATA_PREFILL_TIMING": "1"` and `"STRATA_DECODE_TIMING": "1"`.

**Check:** the run config's `"env"` has both keys with `"1"`, and the engine log has a line that starts with
`strata decode timing:`. Then go to step 5.

**When the check fails:** ask the user: "The engine writes the time of each part of a request only with two
switches. They go into the run config. They need a restart of Strata, and the model loads again. Add them?"
- **Yes:** copy `<config>` to `<config>.bak`. Add the two keys to `"env"`. When `"env"` exists, keep its other
  keys (setup writes some of them). Then restart Strata as in step 3, and send one short chat request. The engine
  log then has a `strata decode timing:` line. The Studio's Settings page also has a button for this write
  (**Timing lines**).
- **No:** continue. The Studio then shows speeds only on Now. Runs, Compare and Settings work.
- **The log is in another container or on another PC:** the Studio cannot read it. Tell the user. The Studio then
  shows the speeds from `/metrics` and the API, and its profile runs have no breakdown columns.

On a release build, the Studio runs **Run a profile** itself with Strata's `tools/` (from the Strata folder).
**Tune this PC** unloads the model and runs setup's calibration. Then it asks the user to restart Strata.

## 5. Check Python

```
python3 --version          (Windows: py -3 --version)
```

**Check:** Python 3.11 or newer.

**When the check fails:** look for a newer Python on this PC, in this order:
1. By its version number: `python3.14`, `python3.13`, `python3.12`, `python3.11` (Windows: `py -3.13`, `py -3.12`,
   `py -3.11`).
2. The Python of Strata's own environment, which Strata's setup installs: `<Strata folder>/.venv/bin/python`
   (Linux, macOS) or `<Strata folder>\.venv\Scripts\python.exe` (Windows).

Run `--version` on each. Use the first one with 3.11 or newer as `python3` in the steps below, and write a note
(step 12). When none is 3.11 or newer, ask the user before you install a newer Python.

## 6. Get the Studio

Put it next to the Strata folder (in the same parent folder):

```
cd "<parent of the Strata folder>"
git clone https://github.com/qiuzan2001/strata-profile-studio
```

**Check:** `strata-profile-studio/studio.py` exists.

**When the check fails:**
- No `git`: download <https://github.com/qiuzan2001/strata-profile-studio/archive/refs/heads/main.zip> and extract
  it there. The folder is then `strata-profile-studio-main`. Use that name below.
- The folder exists already: the Studio is there from an earlier install. Run `git pull` in it (see "Update"), and continue.

## 7. Start the Studio

The Studio's port is 8090. First check that the port is free:

```
curl -s http://127.0.0.1:8090/api/state
```

- No answer: the port is free. Use 8090.
- A JSON answer with `"studio"` in it: a Studio runs already. When its `"config_path"` is `<config>`, it is this
  Studio: go to step 8. Otherwise use the next free port (8091, 8092, ...) with `--port`.

Start the Studio in the background, with a log file:

```
Linux, macOS:
  cd strata-profile-studio
  nohup python3 studio.py --config "<config>" --port 8090 > studio.log 2>&1 &

Windows (PowerShell):
  cd strata-profile-studio
  Start-Process -WindowStyle Hidden -FilePath py -ArgumentList '-3','studio.py','--config','"<config>"','--port','8090' -RedirectStandardOutput studio.log -RedirectStandardError studio.err
```

With an API key (step 2 printed `key`), set `STRATA_KEY` in the environment of that command to the config's
`"api_key"`, for example on Linux: `STRATA_KEY="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['api_key'])" "<config>")" nohup python3 studio.py ...`.

The Studio finds Strata's address, the reports (`bench/results` in the Strata folder) and the engine log from
`<config>`.

**Check:** after 2 s, the first line of `studio.log` is
`Strata Profile Studio on http://127.0.0.1:8090/ (Strata: http://127.0.0.1:<port>, reports: <Strata folder>/bench/results)`.
The Strata address and the reports folder must be the ones of step 1.

**When the check fails:**
- `Cannot listen on 127.0.0.1:8090`: the port is in use. Start again with the next port.
- `Refusing to listen on a non-loopback address`: remove `--host`.
- A wrong reports folder: add `--results "<Strata folder>/bench/results"`.

## 8. Check the page

```
curl -s http://127.0.0.1:8090/api/state
```

**Check:** the answer has `"studio"` and `"strata"`. In `"strata"`, `"status"` is `"ok"`, or `"stock"` on a release
build (step 4b), or `"profile_off"` when the user said no in step 4a.

**When the check fails:**
- `"status": "unreachable"`: the Studio does not reach Strata. Compare `"strata_url"` in `"studio"` with the port of
  step 2. Restart the Studio with `--strata http://127.0.0.1:<port>`.
- `"status": "key_needed"`: restart the Studio with `STRATA_KEY` (step 7).

## 9. Record what the Studio can read on this PC

```
python3 studio.py --host-probe --config "<config>" > host-probe.txt
```

It prints one line per reader (CPU, temperature, power, memory, disk, GPU tool, engine log) with `ok` or `no`. Keep
the file for the user. A `no` is not an error: the page then says "not available" for that reading. On Linux,
package power often needs read access to `/sys/class/powercap`. The probe output says which files.

## 10. Open the page

```
Linux:    xdg-open http://127.0.0.1:8090
macOS:    open http://127.0.0.1:8090
Windows:  start http://127.0.0.1:8090
```

## 11. Tell the user

Tell the user, in plain words:
- The address: `http://127.0.0.1:8090` (or the port that you used). It works on this PC only.
- The five views:
  - **Now**: what limits Strata at this moment (disk, RAM, CPU, PCIe, VRAM, GPU), and the time of each request.
  - **Runs**: each profile run as one experiment, with its numbers.
  - **Compare**: any runs against the baseline, with the noise floor of this PC.
  - **Tune**: **Tune this PC** runs a profile, Strata's calibration, and a second profile, then compares them.
  - **Settings**: Strata's settings, loaded now and in the run config, editable with a backup.
- The readings that the probe could not get (the `no` lines of `host-probe.txt`), if any.
- How to stop the Studio: stop the `python3 studio.py` process (Linux, macOS: `kill <pid>`). Closing the browser
  does not stop it.

## 12. Report what you found

Do this step at the end of each install. It has two cases. When neither case applies, this step ends the install.

**Case A: something failed, and you fixed it or found a way around it.** For example, a tool was missing, a path
was wrong, or a check failed. It can also be a difference on Windows, macOS or an AMD card, or a wrong sentence in
this guide.

**Case B: all steps worked, and this PC is not in the list of PCs tested on hardware.** Today that list has Linux
with an NVIDIA card only (README, "Works on"). Thus each install on Windows, on macOS, or with an AMD or Intel card
is case B. Get the GPU vendor from the `gpu` line of `host-probe.txt` (`nvidia-smi`, `rocm-smi` or `amd-smi`). When
that line says `no`, use the engine's build file: `cuda_dirs` in `<Strata folder>/engine/BUILD.json` means NVIDIA.

### Collect the report

Write the report into `install-report.md` next to `studio.py`, with these parts:
- **Kind:** `install-report` (case A) or `works-on` (case B).
- **OS and version:** for example `uname -sr` and the distribution (Linux), `sw_vers` (macOS), or `ver` (Windows).
- **Python version:** the Python that runs the Studio.
- **Strata version:** `"version"` in `<Strata folder>/engine/BUILD.json`, or `git -C "<Strata folder>" describe
  --tags --always`.
- **GPU:** the vendor and the model. Case B always has it. Case A has it when the problem is about the GPU.
- **Run config:** the name of the file only, for example `strata-swift-iq3_s.json`. Never its content.
- **Agent:** your name and version, for example `Claude Code 2.1.301`.
- **What failed** (case A): for each note, the step and the exact error text.
- **What fixed it** (case A): for each note, the command or change that got past it.
- **Host probe:** the content of `host-probe.txt`.

### Remove secrets before you show it

- No key: not the `"api_key"` of the run config, not `STRATA_KEY`, not a `--key` value.
- No names of this PC or the network. Remove the host name (`hostname`), a Tailscale name (`*.ts.net`), and each IP
  address of the local network.
- No user name: replace the home folder in each path with `~` (for example `/home/anna/Strata` becomes `~/Strata`,
  and `C:\Users\anna\Strata` becomes `~\Strata`).

Then check the file. Use the user name, the host name and the key, if any, as search words:
`grep -n -i -e "<user name>" -e "<host name>" -e "ts.net" -e "api_key" install-report.md` must find nothing.

### Ask one time

Show the report to the user. Then ask one question, one time:

> May I open an issue on GitHub with this report? It helps the next person who installs the Studio.

- **No:** keep `install-report.md` and tell the user where it is. Do not ask again.
- **Yes:** open at most one issue for this install, as follows.

### Open the issue (only after a yes)

1. **Look for an open issue about the same problem first.** With `gh`:
   `gh issue list --repo qiuzan2001/strata-profile-studio --state open --search "<two or three words of the error>"`.
   Without `gh`, give the user the search page:
   `https://github.com/qiuzan2001/strata-profile-studio/issues?q=is%3Aissue+is%3Aopen+<words>`.
   When an open issue is about the same problem, do not open a new issue. Add the report as a comment to it:
   `gh issue comment <number> --repo qiuzan2001/strata-profile-studio --body-file install-report.md`.
2. **With `gh` installed and logged in** (`gh auth status` says "Logged in"):
   `gh issue create --repo qiuzan2001/strata-profile-studio --label <kind> --title "<title>" --body-file install-report.md`.
   The title has the OS, the GPU vendor and the problem in a few words, for example
   `Install: Windows 11, AMD: no rocm-smi`, or `Works on: macOS 15, Apple M4`.
3. **Without `gh`:** give the user a link that opens a filled form. Make the link from the form name and the parts
   of the report. URL-encode each value. Keep the whole link under 6 kB. When it is longer, shorten the host probe.
   - case A: `https://github.com/qiuzan2001/strata-profile-studio/issues/new?template=install-report.yml&title=<title>&os=<OS>&python=<Python>&strata=<Strata version>&config=<run config name>&failed=<what failed>&fix=<what fixed it>&probe=<host probe>&agent=<agent>`
   - case B: `https://github.com/qiuzan2001/strata-profile-studio/issues/new?template=works-on.yml&title=<title>&os=<OS>&gpu=<GPU vendor and model>&strata=<Strata version>&probe=<host probe>&agent=<agent>`

   GitHub fills each field of the form from the parameter with its name. The user clicks the link, reads the form
   and clicks **Create**.
4. Tell the user the address of the new issue or comment, or that the link is ready to click.

## Update

```
cd strata-profile-studio
git pull
```

Then stop the Studio and start it again as in step 7. (Without git: download the zip again and replace the folder,
but keep `studio-state.json`.)

## Uninstall

Stop the Studio and remove the `strata-profile-studio` folder. Outside that folder, the Studio left only:
- `<config>.bak`, when the user wrote settings from the Settings page (or when you turned on profiling in step 4).
  It is the run config before the last write. Ask the user before you remove it.

Inside the folder it keeps `studio-state.json` (its tune records and notes) and `studio.log`.

## What never to do

- Do not open an issue or add a comment without a yes from the user. Do not open more than one issue for one
  install.
- Do not start the Studio with `--host` or `--allow-host` without `--key`.
- Do not edit the run config by hand while a tune runs, and do not stop Strata during a tune.
- Do not start **Tune this PC** or **Run a profile** for the user. Each takes the model offline for minutes.
- Do not change other files of Strata.

## Tried with

| Agent | Result |
| --- | --- |
| Claude Code 2.1.301 (`claude -p`), 2026-10-15 | Installed and started the Studio without a question: 7 tool calls, 26 s. |
| Claude Code 2.1.301 (`claude -p`), 2026-10-15, with a fault | `python3` was 3.9. The agent found a newer Python, started the Studio, wrote the report without secrets and asked one time. After "No" it kept `install-report.md` and opened no issue. 12 turns, 47 s. |
| Codex, Cursor, GitHub Copilot | Not tried. |

The trial with Claude Code ran on the development PC (Linux, RTX 5070 Ti, Ryzen 7 5800X3D) in a scratch folder:
- **The test setup.** A test copy of a Strata folder: the run config with its own port, `setup.py`, the reports and
  the engine log. A demo Strata (`tests/demo_strata.py`) replayed recorded data on that port. The sentence of the
  README had a local copy of the repository in place of the GitHub address. A test note told the agent to stay in
  the scratch folder and to leave ports 8080 and 8090 alone. It also told the agent to print the browser command.
- **What the agent did.** It cloned the repository next to the Strata folder and read this page. It found the run
  config, read its port, checked `/health`, and found a free port (8091). It then started
  `studio.py --config ... --port 8091` with a log. It checked `/api/state` (`"status": "ok"`) and ran the host probe.
- **What it told the user.** The address, the five views, and how to stop the Studio. It also said that it changed
  nothing in Strata (profiling was on). Two readings were not available: package and core power (no read access to
  RAPL).

The trial with a fault used the same test setup, with two changes:
- **The fault.** A `python3` first in the agent's `PATH` said `Python 3.9.18` and failed on `studio.py`.
- **A test `gh`.** It was not logged in and wrote each call to a log. The only call was `gh auth status`.

The agent noted the fault in step 5 and used `/usr/bin/python3.14`. In step 12 it wrote `install-report.md`. The
report had the OS, Python, Strata version, GPU and the name of the run config. It also had the host probe and two
problems with their fixes. The problems were the old Python, and port 8090, which the test kept for other programs. The report had `~` for the home folder and no user name, host name or key. The agent asked "May I open an
issue on GitHub with this report?" one time. The answer "No." ended the install, and the report stayed unchanged.
