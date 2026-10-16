# Contributing

Thank you for your help. Keep each change small and measured.

## Before a pull request

1. Run the unit tests. They need no Strata, no GPU and no network:

   ```
   python3 -m unittest discover -s tests
   ```

2. When you change the page (`web/`), run the page check with a Python that has Playwright. It opens each view and
   each state in headless Chromium against a demo Strata:

   ```
   <venv>/bin/python tests/page_check.py
   <venv>/bin/python tests/page_check.py --host-info off
   ```

   Each line must say PASS. The page check never connects to port 8080 and never writes a real Strata file.

3. Run `node --check` on each JavaScript file that you changed.

## Rules

- **One change per pull request.** A fix and a new feature go into two pull requests.
- **Standard library only.** The server uses no package outside the Python standard library. The page loads no
  library and never uses `innerHTML` (a test asserts this).
- **No invented numbers.** When the Studio cannot get a reading, the page says "not available" and the reason. Each
  number in the README or a screenshot names the PC that measured it.
- **Screenshots from real data.** Take them with the page check and copies of real reports (`--seed`, `--live`,
  `--hardware`). Keep each file at the size of the screenshot that it replaces.
- **Wording.** The page and the documentation use ASD-STE100 Simplified Technical English. An instruction has at
  most 20 words, a description at most 25. Use active verbs and one word for one thing (see the
  [glossary](docs/REFERENCE.md#glossary)). Headings are noun phrases.

## Report your PC

When the Studio cannot read something on your PC, run `python3 studio.py --host-probe --config <strata-model.json>` and
open an issue with its output, your OS, CPU, GPU and Strata build. See the [README](README.md#report-your-pc).
