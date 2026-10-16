"""Stock mode: the Studio against Strata 0.1.44.1 as released (no /profile routes, /health without "profile",
/unload and /load with the same-origin rule).  The fake is tests/fakestrata.py with profile=False.  The profile run
and the tune use a stub process (nothing reaches a GPU); the tune helper itself runs against a stub setup.py."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import support
from fakestrata import FakeStrata
from support import ROOT, request, start_studio, tmpdir, wait_for

from studiolib import configwrite, stock, timinglog

FIXTURE_LOG = ROOT / "tests" / "fixtures" / "stock" / "engine_two_requests.log"
DEMO_RUN = ROOT / "tests" / "fixtures" / "demo" / "reports" / "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s-6"


def strata_folder(test, *, routes=False, log_lines=None) -> tuple[Path, Path]:
    """A Strata folder of a release build: setup.py, tools/strata_bench.py and a run config.  (folder, config)."""
    d = tmpdir(test) / "Strata"
    (d / "tools").mkdir(parents=True)
    (d / "tools" / "strata_bench.py").write_text("")
    (d / "setup.py").write_text("def calibrate_config(cfg):\n    print('  [ok] tuned for this PC: --pcie-frac 0.35 (90.0 tok/s)', flush=True)\n    return True\n")
    if routes:
        (d / "serve").mkdir()
        (d / "serve" / "profile_page.py").write_text("")
    log = d / "strata-x.log"
    if log_lines is not None:
        log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    cfg = d / "strata-x.json"
    cfg.write_text(json.dumps({"exe": str(d / "engine" / "strata"), "args": ["--pack", "p", "--spec", "4"],
                               "env": {"STRATA_MMQ_KQ": "1"}, "log": str(log), "port": 8080}, indent=2) + "\n")
    return d, cfg


def stub_popen(results: Path, calls: list):
    """The Studio's child processes, replaced: the command line is recorded, and a short stub runs instead."""
    def popen(cmd, **kw):
        calls.append({"cmd": list(cmd), "cwd": kw.get("cwd"), "env": kw.get("env") or {}})
        if Path(cmd[1]).name == "strata_profile.py":
            dst = results / "2026-10-16-profile-stub"
            code = (f"import shutil; shutil.copytree({str(DEMO_RUN)!r}, {str(dst)!r}); print('[1/4] chat', flush=True); "
                    f"print('Report: ' + {str(dst / 'README.md')!r} + ' (home folders replaced)', flush=True)")
        else:
            code = ("print('  Measuring the PCIe share ...', flush=True); "
                    "print('  [ok] tuned for this PC: --pcie-frac 0.35 (90.0 tok/s)', flush=True)")
        kw.pop("creationflags", None)
        return subprocess.Popen([sys.executable, "-c", code], **kw)
    return popen


class StockCase(unittest.TestCase):
    def make(self, log_lines=None, routes=False, unload="unloaded", key=None):
        self.root, self.cfg = strata_folder(self, routes=routes, log_lines=log_lines)
        self.results = tmpdir(self) / "results"
        self.results.mkdir()
        self.fake = FakeStrata(self.results, profile=False, key=key).start()
        self.fake.unload_answer = unload
        self.addCleanup(self.fake.stop)
        self.calls = []
        import studio
        s = studio.Studio(self.fake.url, str(self.results), key=key, port=0, config=str(self.cfg),
                          state_path=str(self.results.parent / "st.json"), timing=support.fast_timing(),
                          host_info=False, stock_popen=stub_popen(self.results, self.calls))
        s.start()
        self.addCleanup(s.stop)
        return s


class Detection(StockCase):
    def test_stock_status_and_the_exact_message(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        st = request(s, "GET", "/api/state")[2]["strata"]
        self.assertEqual(st["status"], "stock")
        self.assertEqual(st["message"], "This Strata build has no profile routes (Strata 0.1.44.1 as released). They "
                                        "are in Strata PR #2661. The Studio does that work itself: see Settings.")
        self.assertTrue(st["stock"])
        self.assertNotIn("\"profile\": true", st["message"])           # that key does nothing on a stock build
        self.assertIsNot(st["profile"], False)                           # not "profiling off"

    def test_health_without_profile_is_normal(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        self.assertNotIn("profile", self.fake_health())
        self.assertIsNone(s.poller.health.get("profile"))

    def fake_health(self):
        import urllib.request
        return json.load(urllib.request.urlopen(self.fake.url + "/health"))

    def test_a_build_with_the_routes_and_profiling_off_is_still_profile_off(self):
        s = self.make(routes=True)
        wait_for(lambda: s.poller.status == "profile_off", what="profile_off")
        self.assertFalse(request(s, "GET", "/api/state")[2]["strata"]["stock"])


class TimingSwitches(StockCase):
    def test_the_env_write_merges_and_keeps_the_other_keys(self):
        _, cfg = strata_folder(self)
        before = cfg.read_bytes()
        res = configwrite.merge_env(cfg, stock.TIMING_ENV)
        env = json.loads(cfg.read_text())["env"]
        self.assertEqual(env, {"STRATA_MMQ_KQ": "1", "STRATA_PREFILL_TIMING": "1", "STRATA_DECODE_TIMING": "1"})
        self.assertEqual(Path(res["backup"]).read_bytes(), before)
        with self.assertRaises(configwrite.ConfigWriteError):
            configwrite.merge_env(cfg, stock.TIMING_ENV)                   # nothing to change: nothing written

    def test_a_config_without_env_gets_one(self):
        _, cfg = strata_folder(self)
        c = json.loads(cfg.read_text())
        del c["env"]
        cfg.write_text(json.dumps(c))
        configwrite.merge_env(cfg, stock.TIMING_ENV)
        self.assertEqual(json.loads(cfg.read_text())["env"], stock.TIMING_ENV)

    def test_the_settings_block_and_its_button(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        doc = request(s, "GET", "/api/stock")[2]
        self.assertTrue(doc["active"])
        self.assertEqual(doc["env"], {"STRATA_PREFILL_TIMING": None, "STRATA_DECODE_TIMING": None})
        self.assertFalse(doc["log"]["readable"])                           # the log does not exist yet
        st, _, res = request(s, "POST", "/api/stock/timing", {})
        self.assertEqual(st, 200, res)
        self.assertEqual(res["message"], "Restart Strata so the engine starts with them.")
        doc = request(s, "GET", "/api/stock")[2]
        self.assertTrue(doc["env_complete"])
        self.assertEqual(json.loads(self.cfg.read_text())["env"]["STRATA_MMQ_KQ"], "1")

    def test_an_unreadable_log(self):
        d = stock.log_state("/nonexistent/other-container/strata-x.log")
        self.assertEqual((d["readable"], d["timing_lines"]), (False, False))


class LastRequest(StockCase):
    def test_two_real_lines(self):
        lines = FIXTURE_LOG.read_text().splitlines()
        self.assertTrue(any(x.startswith("strata decode timing:") for x in lines))
        r = timinglog.last_request(lines)
        self.assertEqual((r["summary"]["prompt_tokens"], r["decode"]["tok_s"], r["prompt"]["tok_s"]), (55830, 90.2, 1825.6))
        self.assertEqual({k: round(v, 2) for k, v in r["decode"]["groups"]["ms"].items()},
                         {"GPU": 11.7, "CPU experts": 7.77, "draft": 1.98, "adapt wait": 1.32, "other": 0.85})
        self.assertEqual(r["hit"]["hit_rate"], 0.882)
        self.assertEqual(r["kv"]["block_reads"], 129716172 - 113534748)  # this request's share of the counters
        self.assertIsNone(timinglog.last_request(lines[:3]))              # no summary line yet: no request

    def test_now_reads_the_last_request_from_the_log(self):
        s = self.make(log_lines=FIXTURE_LOG.read_text().splitlines())
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        wait_for(lambda: any(x.get("decode_tok_s") == 90.2 for x in s.poller.ring), what="the log's request")
        x = next(x for x in s.poller.ring if x.get("decode_tok_s") == 90.2)
        self.assertNotEqual(x["source"], "metrics")
        self.assertIsNotNone(x["window_ms"])

    def test_without_timing_lines_now_reads_metrics(self):
        s = self.make(log_lines=["strata serve: ready"])
        self.fake.metrics_requests = [{"time": 1000.0, "duration_s": 3.0, "prompt_tokens": 70, "reused": 0,
                                       "prompt_read": 70, "prompt_ms": 140.0, "decode_ms": 500.0,
                                       "decode_tok_s": 61.2, "output_tokens": 30, "hit_rate": 0.7, "pcie_share": 0.1}]
        wait_for(lambda: s.poller.ring, what="a metrics sample")
        self.assertEqual(s.poller.ring[0]["source"], "metrics")


class Commands(StockCase):
    def test_the_run_command(self):
        s = self.make(log_lines=["x"], key="k1")
        cmd, cwd, env = s.stock.run_command(4096)
        self.assertEqual(cmd[1:], [str(stock.VENDOR / "strata_profile.py"), "--url", self.fake.url, "--out",
                                   str(self.results), "--engine-log", str(self.root / "strata-x.log"), "--long", "4096"])
        self.assertEqual((Path(cwd), Path(env["STRATA_ROOT"]), env["STRATA_KEY"]), (self.root.resolve(), self.root.resolve(), "k1"))
        self.assertNotIn("k1", " ".join(cmd))                              # the key stays out of ps

    def test_a_run_from_the_studio(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": None})
        self.assertEqual(st, 200, doc)
        wait_for(lambda: s.stock.run.status()["state"] == "done", what="the run")
        self.assertEqual(Path(s.stock.run.status()["report"]["path"]).name, "2026-10-16-profile-stub")
        self.assertNotIn("--engine-log", self.calls[0]["cmd"])            # no readable log: no breakdown columns
        wait_for(lambda: any(r["id"] == "2026-10-16-profile-stub" for r in request(s, "GET", "/api/reports")[2]["reports"]),
                 what="the report on the Runs page")

    def test_tools_not_found_says_where_it_looked(self):
        s = self.make()
        shutil.rmtree(self.root / "tools")
        r = s.stock.call("run", None, {})
        self.assertEqual(r.status, 412)
        self.assertIn(str(self.root), r.message)
        self.assertIn("--strata-root", r.message)

    def test_the_tune_command(self):
        s = self.make()
        cmd, cwd, _ = s.stock.tune_command()
        self.assertEqual(cmd[1:], [str(stock.VENDOR / "calibrate_one.py"), str(self.root.resolve()), str(self.cfg.resolve())])
        self.assertEqual(Path(cwd), self.root.resolve())

    def test_calibrate_one_calls_setup_calibrate_config(self):
        root, cfg = strata_folder(self)
        out = subprocess.run([sys.executable, str(stock.VENDOR / "calibrate_one.py"), str(root), str(cfg)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("tuned for this PC: --pcie-frac 0.35", out.stdout)


class Tune(StockCase):
    def test_the_tune_is_refused_while_strata_holds_the_model(self):
        s = self.make(unload="unsupported")                               # the server answers, but stays loaded
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        r = s.stock.call("tune", None, {})
        self.assertEqual(r.status, 409)
        self.assertIn("still holds the model", r.message)
        self.assertEqual(self.calls, [])                                   # no calibration started

    def test_a_busy_model(self):
        s = self.make(unload="busy")
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        r = s.stock.call("tune", None, {})
        self.assertEqual(r.status, 409)
        self.assertEqual(self.calls, [])

    def test_the_unload_is_json_without_origin(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        self.assertEqual(s.stock.call("tune", None, {}).status, 200)       # the Studio's unload: JSON, no Origin
        self.assertFalse(self.fake.loaded)
        import urllib.request
        bad = urllib.request.Request(self.fake.url + "/unload", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Origin": "http://other.example"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        self.assertEqual(e.exception.code, 403)                            # the fake keeps Strata's own rule
        e.exception.close()

    def test_the_flow_stops_after_the_tune_and_asks_for_a_restart(self):
        s = self.make()
        wait_for(lambda: s.poller.status == "stock", what="stock mode")
        st, _, doc = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(st, 200, doc)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", timeout=30, what="the flow to end")
        snap = s.flow.snapshot()
        stages = {x["key"]: x for x in snap["stages"]}
        self.assertEqual(stages["tune"]["state"], "done", snap.get("error"))
        self.assertEqual(stages["reload"]["state"], "skipped")
        self.assertEqual(stages["reload"]["message"], "Restart Strata to load the tuned settings.")
        self.assertEqual((snap["state"], snap["stage"], snap["can_retry_after"]), ("failed", "after", True))
        self.assertTrue(snap["error"].startswith("Restart Strata to load the tuned settings."))
        tools = [Path(c["cmd"][1]).name for c in self.calls]
        self.assertEqual(tools, ["strata_profile.py", "calibrate_one.py"])
        self.assertFalse(self.fake.loaded)                                 # unloaded before the calibration


class Docs(unittest.TestCase):
    def test_the_ai_setup_steps_for_stock_mode(self):
        t = (ROOT / "docs" / "AI_SETUP.md").read_text(encoding="utf-8")
        self.assertIn("### 4b. A release build (stock mode)", t)
        self.assertIn("Do not add `\"profile\": true` there", t)
        for k, v in stock.TIMING_ENV.items():
            self.assertIn(f"\"{k}\": \"{v}\"", t)
        self.assertIn("strata decode timing:", t)
        self.assertIn("keep its other\n  keys", t)                         # merge, never replace
        self.assertIn("`\"stock\"` on a release", t)
        r = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Which Strata", r)
        self.assertIn("Strata #2453", r)


if __name__ == "__main__":
    unittest.main()
