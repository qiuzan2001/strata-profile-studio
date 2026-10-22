"""mytune: the closed loop against a fake Strata. The fake's speed follows the per-request keys."""
import json
import sys
import time
import unittest
from pathlib import Path

import support
from fakestrata import FakeStrata
from studiolib import mytune, replay
from test_workload import line

CONFIG = {"exe": "engine/strata", "args": ["--native", "x.gguf", "--pcie-frac", "0.55", "--spec-min-p", "0.5",
                                              "--spec", "3", "--prefill", "2048"]}
OPTS = {"rounds": 5}
SMALL = [line(3000, 1000, 60, 1000, 800), line(3500, 1000, 80, 1200, 900), line(4000, 1500, 100, 1300, 1000)]   # small shapes keep the tests fast
OK_CMD = [sys.executable, "-c", "pass"]


def wait_done(mt, secs=60):
    mt.thread.join(secs)
    assert not mt.running(), "the tune is still running"
    return mt.snapshot()


class Candidates(unittest.TestCase):
    def test_the_current_value_is_not_tried_again(self):
        arms = mytune.candidates({"pcie_frac": 0.55, "spec_min_p": 0.5})
        self.assertNotIn("pcie_frac 0.55", arms)
        self.assertNotIn("spec_min_p 0.5", arms)
        self.assertIn("pcie_frac 0.35", arms)
        self.assertEqual(arms["spec_min_p 0.7"], {"spec_min_p": 0.7})

    def test_quick_tries_one_lower_and_one_higher_and_no_spec_value(self):
        arms = mytune.candidates({"pcie_frac": 0.55, "spec_min_p": 0.5}, quick=True)
        self.assertEqual(sorted(arms), ["pcie_frac 0.35", "pcie_frac 0.75"])

    def test_unknown_current_tries_the_whole_grid(self):
        self.assertEqual(len(mytune.candidates({})), len(mytune.PCIE_GRID) + len(mytune.SPEC_GRID))

    def test_estimate(self):
        shapes = [{"read": 2000, "generated": 100}]
        self.assertAlmostEqual(mytune.estimate(shapes, 3, 5, 1000.0, 50.0), (2 + 2) * 5 * 5)
        self.assertIsNone(mytune.estimate(shapes, 3, 5, None, 50.0))


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)
        self.results = support.copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)
        self.config = self.tmp / "run.json"
        self.config.write_text(json.dumps(CONFIG, indent=2))
        self.log = self.tmp / "e.log"
        self.log.write_text("\n".join(SMALL) + "\n")                  # the shapes of the tune come from the log
        self.client = replay.Client(self.fake.url, timeout=10)

    def tune(self, **kw):
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("quiet_s", 0.0)
        return mytune.MyTune(self.client, kw.pop("engine_log", str(self.log)), str(self.config), **kw)

    def run_tune(self, opts, **kw):
        mt = self.tune(**kw)
        mt.start({**OPTS, **opts})
        return mt, wait_done(mt)

    # ------------------------------------------------------------------------------------ the verdicts
    def test_a_faster_setting_wins_and_the_config_is_not_written_without_the_tick(self):
        self.fake.chat_speeds = lambda t: (2000.0, 100.0 if t.get("pcie_frac") == 0.35 else 80.0)
        before = self.config.read_bytes()
        mt, st = self.run_tune({})
        self.assertEqual(st["state"], "done", st["message"])
        w = st["result"]["winner"]
        self.assertEqual(w["tune"], {"pcie_frac": 0.35})
        self.assertGreater(w["gain"], 0.05)
        self.assertEqual(st["result"]["arms"]["pcie_frac 0.35"]["verdict"], "better")
        self.assertEqual(st["result"]["arms"]["pcie_frac 0.2"]["verdict"], "no change")
        self.assertIsNone(st["result"]["applied"])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(st["stages"]["apply"], "skipped")

    def test_nothing_better_leaves_the_config(self):
        mt, st = self.run_tune({})
        self.assertEqual(st["state"], "done")
        self.assertIsNone(st["result"]["winner"])
        self.assertIn("No setting is better", st["message"])

    def test_a_slower_setting_is_worse(self):
        self.fake.chat_speeds = lambda t: (2000.0, 40.0 if t.get("spec_min_p") == 0.3 else 80.0)
        mt, st = self.run_tune({})
        self.assertEqual(st["result"]["arms"]["spec_min_p 0.3"]["verdict"], "worse")

    def test_a_faster_setting_with_another_text_is_never_the_winner(self):
        self.fake.chat_speeds = lambda t: (2000.0, 120.0 if t.get("spec_min_p") == 0.7 else 80.0)
        self.fake.chat_differs = lambda t: t.get("spec_min_p") == 0.7
        mt, st = self.run_tune({})
        self.assertEqual(st["result"]["excluded_for_output"], ["spec_min_p 0.7"])
        self.assertIsNone(st["result"]["winner"])
        self.assertIs(st["result"]["arms"]["spec_min_p 0.7"]["same_output"], False)

    def test_two_winning_knobs_are_checked_together(self):
        def speeds(t):
            d = 80.0
            if t.get("pcie_frac") == 0.35:
                d += 20
            if t.get("spec_min_p") == 0.7:
                d += 20
            return 2000.0, d
        self.fake.chat_speeds = speeds
        mt, st = self.run_tune({})
        self.assertEqual(st["result"]["winner"]["tune"], {"pcie_frac": 0.35, "spec_min_p": 0.7})
        self.assertEqual(st["result"]["combined"]["verdict"], "better")

    def test_rounds_never_fall_below_five(self):
        mt, st = self.run_tune({"rounds": 2})
        self.assertEqual(st["result"]["rounds"], 5)

    # -------------------------------------------------------------------------------------- the loop
    def test_apply_restart_and_verify(self):
        self.fake.chat_speeds = lambda t: (2000.0, 100.0 if t.get("pcie_frac") == 0.35 else 80.0)
        mt, st = self.run_tune({"apply": True}, restart_cmd=OK_CMD, health=lambda: {"loaded": True})
        self.assertEqual(st["state"], "done", st["message"])
        cfg = json.loads(self.config.read_text())
        self.assertEqual(cfg["args"][cfg["args"].index("--pcie-frac") + 1], "0.35")
        backup = Path(st["result"]["applied"]["backup"])
        self.assertEqual(json.loads(backup.read_text()), CONFIG)
        self.assertEqual(st["result"]["verify"]["verdict"], "no change")
        self.assertFalse(st["result"]["rolled_back"])
        self.assertEqual(st["stages"]["verify"], "done")

    def test_a_worse_config_after_the_restart_is_rolled_back(self):
        flags = {"restarted": False}

        def speeds(t):
            if flags["restarted"] and not t:                  # the new config is slower than the old values
                return 2000.0, 60.0
            return 2000.0, 100.0 if t.get("pcie_frac") == 0.35 else 80.0

        def health():
            flags["restarted"] = True
            return {"loaded": True}
        self.fake.chat_speeds = speeds
        mt, st = self.run_tune({"apply": True}, restart_cmd=OK_CMD, health=health)
        # the second restart (the rollback) also flips the flag; the old config is back in the file
        self.assertEqual(st["state"], "rolled_back", st["message"])
        self.assertTrue(st["result"]["rolled_back"])
        self.assertEqual(json.loads(self.config.read_text()), CONFIG)

    def test_without_a_restart_command_the_state_says_restart(self):
        self.fake.chat_speeds = lambda t: (2000.0, 100.0 if t.get("pcie_frac") == 0.35 else 80.0)
        mt, st = self.run_tune({"apply": True, "apply_without_restart": True})
        self.assertEqual(st["state"], "needs_restart")
        self.assertIn("Restart Strata", st["message"])
        self.assertIn("0.35", self.config.read_text())

    def test_apply_needs_a_restart_command_or_the_tick(self):
        mt = self.tune()
        with self.assertRaises(mytune.TuneError) as cm:
            mt.start({**OPTS, "apply": True})
        self.assertEqual(cm.exception.status, 409)

    def test_a_failing_restart_command_fails_the_run(self):
        self.fake.chat_speeds = lambda t: (2000.0, 100.0 if t.get("pcie_frac") == 0.35 else 80.0)
        mt, st = self.run_tune({"apply": True}, restart_cmd=[sys.executable, "-c", "raise SystemExit(3)"],
                               health=lambda: {"loaded": True})
        self.assertEqual(st["state"], "failed")
        self.assertIn("exited with 3", st["message"])

    # ---------------------------------------------------------------------------------- the guards
    def test_a_busy_strata_stops_the_run(self):
        log = self.tmp / "e.log"
        log.write_text("x\n")                                  # a log that was just written
        mt, st = self.run_tune({}, engine_log=str(log), quiet_s=3600.0, wait_s=4.0)
        self.assertEqual(st["state"], "failed")
        self.assertIn("Run the tune when nobody uses Strata", st["message"])
        self.assertEqual(self.fake.requests_to("/v1/chat/completions"), [])

    def test_a_round_with_a_foreign_request_is_dropped_and_replaced(self):
        extra = {"n": 0, "seen": 0}

        def speeds(t):
            extra["seen"] += 1
            if extra["seen"] == 4:                              # a request of somebody else, in the first round
                extra["n"] += 3
            return 2000.0, 80.0
        self.fake.chat_speeds = speeds
        mt = self.tune()
        mt._log_count = lambda: len(self.fake.requests_to("/v1/chat/completions")) + extra["n"]
        mt.start(OPTS)
        st = wait_done(mt)
        self.assertEqual(st["state"], "done", st["message"])
        self.assertEqual(st["result"]["rounds"], 5)
        self.assertEqual(st["result"]["spoiled"], 1)

    def test_too_many_foreign_requests_fail_the_run(self):
        n = {"n": 0}
        mt = self.tune()
        mt._log_count = lambda: n.__setitem__("n", n["n"] + 10) or n["n"]      # every pass looks spoiled
        mt.start(OPTS)
        st = wait_done(mt)
        self.assertEqual(st["state"], "failed")
        self.assertIn("clean rounds", st["message"])

    def test_cancel(self):
        mt = self.tune()
        calls = {"n": 0}

        def speeds(t):
            calls["n"] += 1
            if calls["n"] == 5:                                 # in the middle of the measure stage
                mt.cancel()
            return 2000.0, 80.0
        self.fake.chat_speeds = speeds
        mt.start(OPTS)
        st = wait_done(mt)
        self.assertEqual(st["state"], "cancelled", st["message"])
        self.assertEqual(json.loads(self.config.read_text()), CONFIG)

    def test_an_unreadable_config_is_refused(self):
        mt = mytune.MyTune(self.client, None, str(self.tmp / "nope.json"))
        with self.assertRaises(mytune.TuneError):
            mt.start(OPTS)

    def test_a_second_start_is_refused(self):
        mt = self.tune(sleep=lambda s: time.sleep(0.01))
        mt._log_count = lambda: None
        self.fake.chat_speeds = lambda t: (2000.0, 80.0)
        mt.start(OPTS)
        try:
            with self.assertRaises(mytune.TuneError):
                mt.start(OPTS)
        finally:
            mt.cancel_event.set()
            wait_done(mt)

    def test_without_requests_in_the_log_there_is_no_shape_and_the_run_fails(self):
        self.log.write_text("nothing here\n")
        mt = self.tune()
        p = mt.plan({})
        self.assertEqual(p["shapes"], [])
        self.assertIn("no request of 2,000 prompt tokens", p["note"])
        mt.start(OPTS)
        st = wait_done(mt)
        self.assertEqual(st["state"], "failed")
        self.assertEqual(self.fake.requests_to("/v1/chat/completions"), [])

    def test_without_an_engine_log_the_plan_says_so(self):
        p = self.tune(engine_log=None).plan({})
        self.assertEqual(p["shapes"], [])
        self.assertIn("--engine-log", p["note"])

    def test_the_plan_names_arms_rounds_and_current_values(self):
        p = self.tune().plan({})
        self.assertEqual(p["current"], {"pcie_frac": 0.55, "spec_min_p": 0.5})
        self.assertEqual(p["rounds"], 5)
        self.assertEqual(len(p["arms"]), 5)
        self.assertTrue(p["config_readable"])
        self.assertFalse(p["restart_command"])

    def test_the_plan_from_the_log_uses_its_requests(self):
        from test_workload import line
        log = self.tmp / "e.log"
        log.write_text("\n".join(SMALL) + "\n")
        p = self.tune(engine_log=str(log)).plan({})
        self.assertGreaterEqual(len(p["shapes"]), 1)
        self.assertIn("requests of your log", p["note"])
        self.assertGreater(p["seconds"], 0)


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)
        self.results = support.copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)
        self.config = self.tmp / "run.json"
        self.config.write_text(json.dumps(CONFIG, indent=2))
        (self.tmp / "e.log").write_text("\n".join(SMALL) + "\n")

    def studio(self, **kw):
        import studio
        s = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "st.json"),
                          timing=support.fast_timing(), config=str(self.config), engine_log=str(self.tmp / "e.log"), **kw)
        s.mytune.quiet_s = 0.0                                  # the log was just written: do not wait for it to go quiet
        s.start()
        self.addCleanup(s.stop)
        support.wait_for(lambda: s.poller.status == "ok", what="a connected poller")
        return s

    def test_idle_and_plan(self):
        s = self.studio()
        st, _, doc = support.request(s, "GET", "/api/mytune")
        self.assertEqual((st, doc["state"]), (200, "idle"))
        st, _, plan = support.request(s, "GET", "/api/mytune/plan?quick=1")
        self.assertEqual(st, 200)
        self.assertEqual(plan["arms"], ["pcie_frac 0.35", "pcie_frac 0.75"])

    def test_start_needs_confirm_then_runs_and_cancel_is_refused_when_idle(self):
        s = self.studio()
        st, _, _ = support.request(s, "POST", "/api/mytune/cancel", {})
        self.assertEqual(st, 409)
        st, _, _ = support.request(s, "POST", "/api/mytune", {})
        self.assertEqual(st, 400)
        st, _, doc = support.request(s, "POST", "/api/mytune", {"quick": True, "confirm": True})
        self.assertEqual((st, doc), (200, {"ok": True}))
        support.wait_for(lambda: support.request(s, "GET", "/api/mytune")[2]["state"] not in ("running",),
                         timeout=60, what="the tune to end")
        _, _, doc = support.request(s, "GET", "/api/mytune")
        self.assertEqual(doc["state"], "done", doc.get("message"))
        self.assertEqual(len(doc["result"]["arms"]), 2)

    def test_apply_without_restart_command_is_refused(self):
        s = self.studio()
        st, _, doc = support.request(s, "POST", "/api/mytune", {"apply": True, "confirm": True})
        self.assertEqual(st, 409)
        self.assertIn("restart command", doc["error"]["message"])

    def test_a_profile_run_is_refused_while_the_tune_runs(self):
        s = self.studio()
        gate = __import__("threading").Event()
        self.fake.chat_speeds = lambda t: (gate.wait(5) and 2000.0, 80.0)
        support.request(s, "POST", "/api/mytune", {"quick": True, "confirm": True})
        try:
            st, _, doc = support.request(s, "POST", "/api/run-profile", {})
            self.assertEqual(st, 409)
            self.assertIn("workload", doc["error"]["message"])
        finally:
            gate.set()
            s.app.mytune.cancel()
            s.app.mytune.thread.join(30)

    def test_read_only_refuses(self):
        s = self.studio(read_only=True)
        st, _, _ = support.request(s, "POST", "/api/mytune", {"confirm": True})
        self.assertEqual(st, 403)

    def test_the_page_script_is_served(self):
        s = self.studio()
        st, hdrs, body = support.request(s, "GET", "/static/mytune.js")
        self.assertEqual(st, 200)
        self.assertNotIn(b"innerHTML", body)


if __name__ == "__main__":
    unittest.main()
