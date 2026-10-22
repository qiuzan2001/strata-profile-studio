"""Workload: the counts of the requests in the engine log, the presets and the closest scenario."""
import unittest

import support
from fakestrata import FakeStrata
from studiolib import workload
from support import request, wait_for

LINE = ("strata serve: prompt {p} tokens = {r} reused + {x} read in {pm} ms ({pt} tok/s), {g} generated in {dm} ms "
        "({dt} tok/s), drafts accepted {a} of {o}, 1 checkpoints")


def line(p, r, g, pm, dm, a=0, o=0):
    x = p - r
    return LINE.format(p=p, r=r, x=x, pm=pm, pt=round(x / pm * 1000, 1) if pm else 0, g=g, dm=dm,
                       dt=round(g / dm * 1000, 1) if dm else 0, a=a, o=o)


# two agent-like requests: 20,000-token prompts, 40% reused, 800 generated tokens
AGENT = [line(20000, 8000, 800, 4000, 8000, 80, 100), line(24000, 10000, 1000, 5000, 11000, 90, 100)]


class Counts(unittest.TestCase):
    def test_requests_of_skips_other_lines(self):
        reqs = workload.requests_of(["strata serve: KV streaming: 99% of 10 block reads hit VRAM, 1.0 MiB read from RAM", *AGENT])
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0]["prompt_tokens"], 20000)

    def test_summarize_adds_up_tokens_and_times(self):
        m = workload.summarize(workload.requests_of(AGENT))
        self.assertEqual(m["requests"], 2)
        self.assertEqual(m["prompt_tokens"]["median"], 22000)
        self.assertAlmostEqual(m["reused"], 18000 / 44000)
        self.assertEqual(m["generated"]["median"], 900)
        self.assertAlmostEqual(m["prompt_tok_s"], 26000 / 9000 * 1000, places=3)      # read tokens over prompt time
        self.assertAlmostEqual(m["decode_tok_s"], 1800 / 19000 * 1000, places=3)
        self.assertAlmostEqual(m["prompt_share"], 9000 / 28000)
        self.assertAlmostEqual(m["drafts_accepted"], 170 / 200)

    def test_summarize_with_no_request_is_none(self):
        self.assertIsNone(workload.summarize([]))

    def test_a_prompt_only_request_counts_in_the_prompt_time_only(self):
        m = workload.summarize(workload.requests_of([line(1000, 0, 0, 500, 0)]))
        self.assertIsNone(m["decode_tok_s"])
        self.assertEqual(m["prompt_share"], 1.0)
        self.assertIsNone(m["drafts_accepted"])

    def test_quantile(self):
        self.assertEqual(workload._quantile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(workload._quantile([1, 2, 3, 4, 5], 0.9), 4.6)
        self.assertIsNone(workload._quantile([], 0.5))


class Scenarios(unittest.TestCase):
    def test_five_presets_with_unique_ids(self):
        ids = [p["id"] for p in workload.PRESETS]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)

    def test_estimate_uses_the_speeds_of_this_pc(self):
        shape = {"prompt_tokens": 10000, "reused": 0.5, "generated": 100}
        # 5,000 new tokens at 1,000 tok/s = 5 s; 100 tokens at 50 tok/s = 2 s
        self.assertAlmostEqual(workload.estimate_share(shape, 1000.0, 50.0), 5 / 7)
        self.assertIsNone(workload.estimate_share(shape, None, 50.0))
        self.assertIsNone(workload.estimate_share(shape, 1000.0, None))

    def test_the_closest_preset_of_agent_traffic_is_the_browser_agent(self):
        m = workload.summarize(workload.requests_of(AGENT))
        best = min(workload.PRESETS, key=lambda p: workload.distance(m, p))
        self.assertEqual(best["id"], "browser")

    def test_the_closest_preset_of_short_traffic_is_chat(self):
        m = workload.summarize(workload.requests_of([line(500, 300, 250, 300, 3000), line(700, 400, 320, 300, 3500)]))
        best = min(workload.PRESETS, key=lambda p: workload.distance(m, p))
        self.assertEqual(best["id"], "chat")

    def test_levers_name_their_basis_and_follow_the_traffic(self):
        m = workload.summarize(workload.requests_of(AGENT))               # the reply takes most of the time
        kinds = {x["kind"] for x in workload.levers(m)}
        self.assertEqual(kinds, {"changes output", "same output", "client"})
        for x in workload.levers(m):
            self.assertTrue(x["basis"].startswith("your log"))
        prompt_bound = workload.summarize(workload.requests_of([line(30000, 0, 20, 12000, 300)]))
        texts = " ".join(x["text"] for x in workload.levers(prompt_bound))
        self.assertIn("--prefill", texts)
        self.assertNotIn("Shorten the thinking", texts)
        self.assertEqual(workload.levers(None), [])


class Document(unittest.TestCase):
    def test_no_log_known(self):
        d = workload.document(None)
        self.assertFalse(d["readable"])
        self.assertIn("engine log", d["note"])
        self.assertIsNone(d["mine"])

    def test_log_that_cannot_be_read(self):
        d = workload.document("/nonexistent/engine.log")
        self.assertFalse(d["readable"])
        self.assertIn("cannot be read", d["note"])

    def test_empty_log_says_so_and_keeps_the_presets(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write("nothing here\n")
            f.flush()
            d = workload.document(f.name)
        self.assertTrue(d["readable"])
        self.assertIsNone(d["mine"])
        self.assertEqual(len(d["presets"]), 5)
        self.assertIsNone(d["presets"][0]["estimated_prompt_share"])
        self.assertIn("assumed", d["preset_note"])

    def test_last_and_min_prompt(self):
        import tempfile
        lines = [line(100, 0, 10, 100, 100)] + AGENT * 3
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            allr = workload.document(f.name, 100, 0)
            big = workload.document(f.name, 100, 2000)
            two = workload.document(f.name, 2, 2000)
        self.assertEqual(allr["found"], 7)
        self.assertEqual(allr["mine"]["requests"], 7)
        self.assertEqual(big["mine"]["requests"], 6)
        self.assertEqual(two["mine"]["requests"], 2)
        self.assertEqual(big["closest"], "browser")


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)
        self.results = support.copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)

    def studio(self, log):
        import studio
        s = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "st.json"),
                          timing=support.fast_timing(), engine_log=str(log))
        s.start()
        self.addCleanup(s.stop)
        wait_for(lambda: s.poller.status == "ok", what="a connected poller")
        return s

    def test_api_workload(self):
        log = self.tmp / "e.log"
        log.write_text("\n".join(AGENT) + "\n")
        s = self.studio(log)
        status, _, doc = request(s, "GET", "/api/workload?last=50&min_prompt=0")
        self.assertEqual(status, 200)
        self.assertEqual(set(doc), {"path", "readable", "found", "last", "min_prompt", "mine", "presets", "levers",
                                    "closest", "note", "preset_note"})
        self.assertEqual(doc["mine"]["requests"], 2)
        self.assertEqual(doc["closest"], "browser")
        self.assertEqual(doc["last"], 50)

    def test_api_workload_rejects_a_bad_number(self):
        log = self.tmp / "e.log"
        log.write_text("")
        s = self.studio(log)
        status, _, doc = request(s, "GET", "/api/workload?last=many")
        self.assertEqual(status, 400)

    def test_the_page_script_is_served(self):
        log = self.tmp / "e.log"
        log.write_text("")
        s = self.studio(log)
        status, hdrs, body = request(s, "GET", "/static/workload.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", hdrs["content-type"])
        self.assertNotIn(b"innerHTML", body)


if __name__ == "__main__":
    unittest.main()
