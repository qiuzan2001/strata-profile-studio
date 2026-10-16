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
whether Strata needs a key, and whether profiling is on (`True`).

**Check:** the command prints a port.

**When the check fails:** the file is not valid JSON. Do not repair it. Tell the user and stop.

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

## 4. Turn on profiling (with the user's consent)

The Studio shows the time per request, **Run a profile** and **Tune** only when Strata's profiling is on. It is on
when the run config has `"profile": true`, and then `/health` has `"profile": true`.

**Check:** step 2 printed `True`, or `/health` has `"profile": true`. Then go to step 5.

**When the check fails:** ask the user: "Strata's profiling is off. With it on, Strata records the time of each part
of a request, and the Studio can show it and tune Strata. It needs a restart of Strata (the model loads again).
Turn it on?"
- **Yes:** first make sure that no tune runs. Run `curl -s http://127.0.0.1:<port>/profile/status`. Its answer must
  not show a tune with `"state": "running"`. A 404 is also a pass: no profiling, so no tune. Copy `<config>` to `<config>.bak`. Add
  the top-level key `"profile": true` and keep all other keys as they are. Stop Strata (close its window, or stop
  its process), and start it again as in step 3.
- **No:** continue. The Studio then shows speeds only on Now. Runs, Compare and Settings work.

## 5. Check Python

```
python3 --version          (Windows: py -3 --version)
```

**Check:** Python 3.11 or newer.

**When the check fails:** use the Python of Strata's own environment. Strata's setup installs it:
`<Strata folder>/.venv/bin/python` (Linux, macOS) or `<Strata folder>\.venv\Scripts\python.exe` (Windows). Run
`--version` on it. Use it as `python3` in the steps below. When it is also older than 3.11, ask the user before you
install a newer Python.

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

**Check:** the answer has `"studio"` and `"strata"`. In `"strata"`, `"status"` is `"ok"` (or `"profile_off"` when
the user said no in step 4).

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

- Do not start the Studio with `--host` or `--allow-host` without `--key`.
- Do not edit the run config by hand while a tune runs, and do not stop Strata during a tune.
- Do not start **Tune this PC** or **Run a profile** for the user. Each takes the model offline for minutes.
- Do not change other files of Strata.

## Tried with

| Agent | Result |
| --- | --- |
| Claude Code 2.1.301 (`claude -p`), 2026-10-15 | Installed and started the Studio without a question: 7 tool calls, 26 s. |
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
