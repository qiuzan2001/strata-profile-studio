import json
import unittest

import support
from fakestrata import FakeStrata
from support import fast_timing, request, start_studio, wait_for

KEY = "sekret-key-4242"


class FlowCase(unittest.TestCase):
    def make(self, **fake_kw):
        self.results = support.tmpdir(self) / "results"
        self.results.mkdir()
        self.fake = FakeStrata(self.results, **fake_kw).start()
        self.addCleanup(self.fake.stop)

    def studio(self, **kw):
        s = start_studio(self, self.fake, self.results, **kw)
        wait_for(lambda: s.poller.status == "ok" and s.poller.profile, what="the poller to reach the fake")
        return s

    def run_flow(self, s, long=None, timeout=20):
        status, _, doc = request(s, "POST", "/api/tune-flow", {"long": long})
        self.assertEqual(status, 200, doc)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", timeout=timeout, what="the flow to end")
        return s.flow.snapshot()


class HappyPath(FlowCase):
    def test_succeeds_with_the_pair_bound_and_the_record_saved(self):
        self.make()
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        self.assertEqual([st["state"] for st in snap["stages"]], ["done"] * 6)
        self.assertEqual([st["key"] for st in snap["stages"]],
                         ["check", "before", "tune", "reload", "after", "compare"])
        self.assertEqual(snap["before_report"], "2026-10-16-profile-fake-gpu-1")
        self.assertEqual(snap["after_report"], "2026-10-16-profile-fake-gpu-2")
        self.assertEqual(snap["compare"], {"a": snap["before_report"], "b": snap["after_report"]})
        self.assertEqual(snap["tok_s"], 96.6)
        self.assertTrue(snap["summary"].startswith("--pcie-frac 0.35"))
        self.assertEqual(snap["settings"]["source"], "tune.report")
        rows = {r["flag"]: r for r in snap["settings"]["rows"]}
        self.assertEqual((rows["--pcie-frac"]["before"], rows["--pcie-frac"]["after"], rows["--pcie-frac"]["changed"]),
                         ("0.55", "0.35", True))
        self.assertFalse(rows["--pool-workers"]["changed"])
        self.assertGreaterEqual(snap["reload"]["attempts"], 2)             # one 503 first
        self.assertIsNotNone(snap["reload"]["sample"])
        self.assertEqual(snap["reload"]["sample"]["tag"], "reload")
        self.assertEqual(snap["reload"]["sample"]["window_ms"]["adapt wait"], 7.0)
        self.assertFalse(snap["can_cancel"] or snap["can_retry_after"])
        # the candidates are filled from the printed lines, with the default taken from the loaded value
        steps = {c["key"]: c for c in snap["candidates"]["steps"]}
        self.assertEqual(steps["pcie_frac"]["kept"], "0.35")
        self.assertEqual(steps["pcie_frac"]["default"], "0.55")
        # saved
        tunes = request(s, "GET", "/api/tunes")[2]["tunes"]
        self.assertEqual(len(tunes), 1)
        t = tunes[0]
        self.assertEqual((t["id"], t["outcome"], t["before_report"], t["after_report"]),
                         (snap["id"], "succeeded", snap["before_report"], snap["after_report"]))
        self.assertEqual(t["tok_s"], 96.6)
        self.assertIsNotNone(t["tune_s"])
        self.assertTrue(any("tuned for this PC" in ln for ln in t["lines_tail"]))
        # the badge: both reports know their tune
        listed = {r["id"]: r for r in request(s, "GET", "/api/reports")[2]["reports"]}
        self.assertEqual(listed[snap["before_report"]]["tune"], {"id": snap["id"], "role": "before"})
        self.assertEqual(listed[snap["after_report"]]["tune"], {"id": snap["id"], "role": "after"})
        # the flow is no longer active in the state file
        self.assertIsNone(s.state.active_flow())

    def test_samples_carry_the_stage_tags(self):
        self.make(reload_503s=0)
        s = self.studio()
        self.run_flow(s)
        tags = {x["tag"] for x in request(s, "GET", "/api/live?since=0")[2]["samples"]}
        self.assertIn("reload", tags)

    def test_requests_to_strata_have_no_origin_and_json_posts_with_the_key(self):
        self.make(key=KEY)
        s = self.studio(key=KEY)
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        with self.fake.lock:
            reqs = list(self.fake.requests)
        self.assertTrue(reqs)
        for r in reqs:
            self.assertNotIn("origin", r["headers"], r["path"])
            self.assertTrue(r["headers"]["user-agent"].startswith("strata-profile-studio/"))
            self.assertEqual(r["headers"].get("accept"), "application/json")
            if r["path"] != "/health":
                self.assertEqual(r["headers"].get("authorization"), f"Bearer {KEY}", r["path"])
            if r["method"] == "POST":
                self.assertEqual(r["headers"]["content-type"], "application/json")
                self.assertIsInstance(r["body"], dict)
        body = next(r["body"] for r in reqs if r["path"] == "/v1/chat/completions")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Reply with OK."}])
        self.assertEqual((body["max_tokens"], body["temperature"], body["stream"], body["model"]),
                         (8, 0, False, "swift-1.5-iq3_s"))

    def test_the_key_appears_in_no_response_of_the_studio(self):
        self.make(key=KEY)
        s = self.studio(key=KEY)
        self.run_flow(s)
        self.assertIsNone(s.state.active_flow())
        s.state.write(force=True)
        blobs = []
        for path in ("/", "/static/app.js", "/static/charts.js", "/static/style.css", "/api/state", "/api/live?since=0",
                     "/api/reports", "/api/settings", "/api/tune-flow", "/api/tunes", "/api/run-profile"):
            st, _, doc = request(s, "GET", path)
            blobs.append(json.dumps(doc) if not isinstance(doc, bytes) else doc.decode("utf-8", "replace"))
        with open(s.state.path, encoding="utf-8") as fh:
            blobs.append(fh.read())
        self.assertFalse(any(KEY in b for b in blobs))
        st, _, doc = request(s, "GET", "/api/state")
        self.assertTrue(doc["studio"]["key_set"])


class Failures(FlowCase):
    def test_tune_failed_still_loads_the_model_then_ends_failed(self):
        self.make(tune_outcome="failed")
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "failed")
        states = {st["key"]: st["state"] for st in snap["stages"]}
        self.assertEqual(states, {"check": "done", "before": "done", "tune": "failed", "reload": "done",
                                  "after": "skipped", "compare": "skipped"})
        self.assertIn("the engine did not start", snap["error"])
        self.assertEqual(len(self.fake.requests_to("/v1/chat/completions")) >= 1, True)
        self.assertEqual(len(self.fake.requests_to("/profile/run")), 1)
        self.assertFalse(snap["can_retry_after"])
        rec = request(s, "GET", "/api/tunes")[2]["tunes"][0]
        self.assertEqual((rec["outcome"], rec["failed_stage"]), ("failed", "tune"))

    def test_reload_timeout_skips_after(self):
        self.make(reload_503s=100000)
        s = self.studio(timing=fast_timing(reload_timeout=0.6))
        snap = self.run_flow(s)
        states = {st["key"]: st["state"] for st in snap["stages"]}
        self.assertEqual(states["reload"], "failed")
        self.assertEqual(states["after"], "skipped")
        self.assertIn("The model did not load again", snap["error"])
        self.assertIn("Strata's log", snap["error"])
        self.assertEqual(snap["state"], "failed")

    def test_after_failed_allows_retry_after(self):
        self.make(run_fail_on=(2,))
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "failed")
        wait_for(lambda: s.flow.snapshot()["can_retry_after"], what="retry to be offered")
        self.assertEqual({st["key"]: st["state"] for st in s.flow.snapshot()["stages"]}["after"], "failed")
        self.assertIsNone(snap["after_report"])
        status, _, doc = request(s, "POST", "/api/tune-flow/retry-after", {})
        self.assertEqual(status, 200, doc)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", what="the retry to end")
        snap = s.flow.snapshot()
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        self.assertEqual(snap["after_report"], "2026-10-16-profile-fake-gpu-3")
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 1)         # the tune was not run again
        self.assertEqual(len(request(s, "GET", "/api/tunes")[2]["tunes"]), 1)    # the same record, updated
        self.assertEqual(request(s, "GET", "/api/tunes")[2]["tunes"][0]["outcome"], "succeeded")

    def test_retry_after_without_a_failed_after_is_refused(self):
        self.make()
        s = self.studio()
        status, _, _ = request(s, "POST", "/api/tune-flow/retry-after", {})
        self.assertEqual(status, 409)

    def test_busy_409_is_retried_three_times(self):
        self.make(tune_busy_409s=3)
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 4)

    def test_busy_409_that_does_not_clear_fails_the_tune_stage(self):
        self.make(tune_busy_409s=10)
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "failed")
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 4)
        self.assertEqual(snap["failed_stage"] if "failed_stage" in snap else "tune", "tune")
        self.assertIn("busy", snap["error"])

    def test_report_outside_results_fails_the_stage_with_the_mismatch_sentence(self):
        self.make(report_inside=False)
        s = self.studio()
        snap = self.run_flow(s)
        self.assertEqual(snap["state"], "failed")
        self.assertIn("restart the Studio with --results", snap["error"])
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 0)
        # and the banner says it, while the Studio is otherwise fine
        wait_for(lambda: request(s, "GET", "/api/state")[2]["strata"]["message"], what="the mismatch message")
        self.assertIn("Strata wrote its report to", request(s, "GET", "/api/state")[2]["strata"]["message"])

    def test_tune_unavailable_is_a_412_with_the_reason(self):
        self.make(tune_available=False)
        s = self.studio()
        status, _, doc = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(status, 412)
        self.assertIn("NVIDIA only", doc["error"]["message"])
        st = request(s, "GET", "/api/state")[2]["strata"]["tune"]
        self.assertEqual((st["available"], st["unavailable_reason"]), (False, "setup offers the tuning on NVIDIA only"))

    def test_profile_off_is_a_412_and_the_state_says_so(self):
        self.make(profile=False)
        s = start_studio(self, self.fake, self.results)
        wait_for(lambda: s.poller.status == "profile_off", what="profile_off")
        status, _, doc = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(status, 412)
        strata = request(s, "GET", "/api/state")[2]["strata"]
        self.assertEqual(strata["status"], "profile_off")
        self.assertIn("\"profile\": true", strata["message"])
        status, _, doc = request(s, "POST", "/api/run-profile", {"long": None})
        self.assertEqual(status, 412)

    def test_a_second_flow_while_one_runs_is_409(self):
        self.make(tune_secs=1.0)
        s = self.studio()
        status, _, _ = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(status, 200)
        status, _, doc = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(status, 409)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", what="the flow")


class CancelAndResume(FlowCase):
    def test_cancel_in_stage_2_ends_cancelled_after_the_run_and_never_tunes(self):
        self.make(run_secs=0.6)
        s = self.studio()
        request(s, "POST", "/api/tune-flow", {"long": None})
        wait_for(lambda: s.flow.snapshot()["stage"] == "before", what="stage 2")
        self.assertTrue(s.flow.snapshot()["can_cancel"])
        status, _, _ = request(s, "POST", "/api/tune-flow/cancel", {})
        self.assertEqual(status, 200)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", what="the end")
        snap = s.flow.snapshot()
        self.assertEqual(snap["state"], "cancelled")
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 0)
        self.assertEqual(self.fake.run["state"], "done")           # the run was not stopped
        self.assertFalse(snap["can_cancel"])

    def test_cancel_is_refused_once_the_tune_runs(self):
        self.make(tune_secs=1.0)
        s = self.studio()
        request(s, "POST", "/api/tune-flow", {"long": None})
        wait_for(lambda: s.flow.snapshot()["stage"] == "tune", what="stage 3")
        status, _, _ = request(s, "POST", "/api/tune-flow/cancel", {})
        self.assertEqual(status, 409)
        wait_for(lambda: s.flow.snapshot()["state"] != "running", what="the end")

    def test_restart_in_stage_3_resumes_waiting(self):
        self.make(tune_secs=1.5)
        state_path = support.tmpdir(self) / "state.json"
        s1 = self.studio(state_path=state_path)
        request(s1, "POST", "/api/tune-flow", {"long": None})
        wait_for(lambda: s1.flow.snapshot()["stage"] == "tune" and s1.flow.snapshot()["lines"], what="stage 3 lines")
        flow_id = s1.flow.snapshot()["id"]
        s1.flow.abandon()
        s1.state.write(force=True)
        s1.stop()
        saved = json.loads(state_path.read_text())
        self.assertEqual(saved["active_flow"]["stage"], "tune")
        self.assertEqual(saved["active_flow"]["id"], flow_id)
        s2 = start_studio(self, self.fake, self.results, state_path=state_path)
        snap = s2.flow.snapshot()
        self.assertEqual((snap["id"], snap["state"]), (flow_id, "running"))
        wait_for(lambda: s2.flow.snapshot()["state"] != "running", what="the resumed flow to end")
        snap = s2.flow.snapshot()
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 1)         # not started a second time
        self.assertEqual(len(self.fake.requests_to("/profile/run")), 2)

    def test_restart_after_the_tune_ended_continues_with_the_reload(self):
        self.make(tune_secs=0.3)
        state_path = support.tmpdir(self) / "state.json"
        s1 = self.studio(state_path=state_path)
        request(s1, "POST", "/api/tune-flow", {"long": None})
        wait_for(lambda: s1.flow.snapshot()["stage"] == "tune", what="stage 3")
        s1.flow.abandon()
        s1.state.write(force=True)
        s1.stop()
        wait_for(lambda: self.fake.tune["state"] == "done", what="the tune to end by itself")
        s2 = start_studio(self, self.fake, self.results, state_path=state_path)
        wait_for(lambda: s2.flow.snapshot()["state"] != "running", what="the resumed flow to end")
        snap = s2.flow.snapshot()
        self.assertEqual(snap["state"], "succeeded", snap["error"])
        self.assertEqual(snap["tok_s"], 96.6)

    def test_restart_in_any_other_stage_ends_failed(self):
        self.make(run_secs=1.0)
        state_path = support.tmpdir(self) / "state.json"
        s1 = self.studio(state_path=state_path)
        request(s1, "POST", "/api/tune-flow", {"long": None})
        wait_for(lambda: s1.flow.snapshot()["stage"] == "before", what="stage 2")
        s1.flow.abandon()
        s1.state.write(force=True)
        s1.stop()
        s2 = start_studio(self, self.fake, self.results, state_path=state_path)
        snap = s2.flow.snapshot()
        self.assertEqual(snap["state"], "failed")
        self.assertEqual(snap["error"], "interrupted: the Studio was restarted")
        self.assertEqual(request(s2, "GET", "/api/tunes")[2]["tunes"][0]["outcome"], "failed")


class ExternalTune(FlowCase):
    def test_a_tune_started_elsewhere_is_shown_read_only(self):
        self.make(tune_secs=2.0)
        s = self.studio()
        # somebody posts to Strata directly (as its own page would)
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.fake.port)
        c.request("POST", "/profile/tune", body=b"{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 200)
        c.close()
        wait_for(lambda: request(s, "GET", "/api/state")[2]["strata"]["tune"]["state"] == "running", what="running")
        st = request(s, "GET", "/api/state")[2]["strata"]["tune"]
        self.assertFalse(st["started_by_studio"])
        doc = wait_for(lambda: (lambda d: d if d["lines"] and d["state"] == "idle" else None)(
            request(s, "GET", "/api/tune-flow")[2]), what="its lines")
        self.assertEqual(len(doc["candidates"]["steps"]), 4)
        status, _, _ = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(status, 412)
        wait_for(lambda: request(s, "GET", "/api/state")[2]["strata"]["tune"]["state"] == "done", what="done")


if __name__ == "__main__":
    unittest.main()
