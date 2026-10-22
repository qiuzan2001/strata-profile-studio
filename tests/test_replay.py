"""replay: the shapes picked from the log, the prompts of the right size, one pass against a fake Strata."""
import threading
import unittest

import support
from fakestrata import FakeStrata
from studiolib import replay


def req(read, gen, reused=0):
    return {"prompt_tokens": read + reused, "reused": reused, "read": read, "generated": gen,
            "read_tokens": read, "prompt_ms": 1.0, "decode_ms": 1.0}


class Shapes(unittest.TestCase):
    def test_no_request_no_shape(self):
        self.assertEqual(replay.pick_shapes([]), [])

    def test_shapes_spread_over_the_reads_and_are_capped(self):
        reqs = [req(r, 100) for r in (1000, 2000, 4000, 8000, 80000)]
        s = replay.pick_shapes(reqs, k=3)                                  # groups: [1000] [2000, 4000] [8000, 80000]
        self.assertEqual([x["read"] for x in s], [1000, 3000, replay.MAX_READ])
        self.assertEqual([x["capped"] for x in s], [False, False, True])

    def test_a_shape_takes_the_median_reply_of_its_group_not_one_request_s(self):
        reqs = [req(1000, g) for g in (10, 20, 900)] + [req(9000, g) for g in (100, 200, 300)]
        s = replay.pick_shapes(reqs, k=2)
        self.assertEqual([(x["read"], x["generated"]) for x in s], [(1000, 20), (9000, 200)])

    def test_generated_is_capped(self):
        s = replay.pick_shapes([req(5000, 5000)], k=1)
        self.assertEqual(s[0]["generated"], replay.MAX_GENERATED)
        self.assertTrue(s[0]["capped"])

    def test_k_is_not_more_than_the_requests(self):
        self.assertEqual(len(replay.pick_shapes([req(1000, 10), req(2000, 10)], k=5)), 2)

    def test_seconds_of_the_shapes(self):
        shapes = [{"read": 2000, "generated": 100}, {"read": 1000, "generated": 50}]
        self.assertAlmostEqual(replay.shapes_seconds(shapes, 1000.0, 50.0), 3.0 + 3.0)
        self.assertIsNone(replay.shapes_seconds(shapes, None, 50.0))


class FromLog(unittest.TestCase):
    def test_the_filter_comes_before_the_last_n(self):
        import tempfile
        from test_workload import line
        big = [line(20000, 8000, 800, 4000, 8000)] * 3
        small = [line(300, 0, 20, 100, 100)] * 50               # newer, short requests (probes) must not push the big ones out
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write("\n".join(big + small) + "\n")
            f.flush()
            out = replay.from_log(f.name, last=5, min_prompt=2000)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["read_tokens"], 12000)


class Filler(unittest.TestCase):
    def test_same_salt_same_text_and_length(self):
        self.assertEqual(replay.filler(50, "a"), replay.filler(50, "a"))
        self.assertEqual(len(replay.filler(50, "a").split()), 50)
        self.assertEqual(replay.filler(0, "a"), "")


class AgainstFake(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)
        self.results = support.copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)
        self.client = replay.Client(self.fake.url, timeout=10)
        self.sizer = replay.Sizer(self.client)
        self.sizer.calibrate()

    def test_calibration_finds_the_tokens_per_word(self):
        self.assertAlmostEqual(self.sizer.per_word, 1.3, delta=0.05)
        self.assertEqual(self.sizer.base_tokens, 15 + round(1.3 * 1))

    def test_prompt_has_the_size_of_the_shape(self):
        shape = {"read": 6000, "generated": 200}
        prompt, max_tokens = replay.prompt_for(shape, self.sizer, "s")
        self.assertEqual(max_tokens, 200)
        r = self.client.chat(prompt, 1)
        self.assertAlmostEqual(r["usage"]["prompt_tokens"], 6000, delta=300)

    def test_one_pass_counts_and_the_tune_is_sent(self):
        shapes = [{"read": 3000, "generated": 100}, {"read": 1500, "generated": 50}]
        before = len(self.fake.requests_to("/v1/chat/completions"))
        out = replay.one_pass(self.client, shapes, self.sizer, {"pcie_frac": 0.35}, "t")
        self.assertIsNone(out["error"])
        self.assertAlmostEqual(out["read"], 4500, delta=300)
        self.assertAlmostEqual(out["generated"], 150, delta=15)
        self.assertEqual(out["cached"], 0)
        self.assertAlmostEqual(out["prompt_tok_s"], 2000.0, delta=1)
        self.assertAlmostEqual(out["decode_tok_s"], 80.0, delta=2)
        self.assertAlmostEqual(out["drafts_accepted"], 0.8)
        # the score is the engine's clock: 4500 / 2000 + 150 / 80 seconds
        self.assertAlmostEqual(out["score_s"], out["read"] / 2000 + out["generated"] / 80, delta=0.05)
        sent = self.fake.requests_to("/v1/chat/completions")[before:]
        self.assertEqual(len(sent), 2)
        for r in sent:
            self.assertEqual(r["body"]["strata_tune"], {"pcie_frac": 0.35})
            self.assertEqual(r["body"]["temperature"], 0)
            self.assertIs(r["body"]["strata_checkpoint"], False)

    def test_no_tune_key_when_there_is_no_tune(self):
        replay.one_pass(self.client, [{"read": 1000, "generated": 20}], self.sizer, None, "t")
        self.assertNotIn("strata_tune", self.fake.requests_to("/v1/chat/completions")[-1]["body"])

    def test_the_score_follows_the_speed_of_the_tune(self):
        self.fake.chat_speeds = lambda t: (2000.0, 120.0 if t.get("pcie_frac") == 0.2 else 80.0)
        shapes = [{"read": 1000, "generated": 200}]
        slow = replay.one_pass(self.client, shapes, self.sizer, {"pcie_frac": 0.5}, "t")
        fast = replay.one_pass(self.client, shapes, self.sizer, {"pcie_frac": 0.2}, "t")
        self.assertLess(fast["score_s"], slow["score_s"])

    def test_the_hash_tells_a_changed_reply(self):
        self.fake.chat_differs = lambda t: t.get("spec_min_p") == 0.9
        shapes = [{"read": 1000, "generated": 40}]
        a = replay.one_pass(self.client, shapes, self.sizer, {"spec_min_p": 0.5}, "t")
        b = replay.one_pass(self.client, shapes, self.sizer, {}, "t")
        c = replay.one_pass(self.client, shapes, self.sizer, {"spec_min_p": 0.9}, "t")
        self.assertEqual(a["hash"], b["hash"])
        self.assertNotEqual(a["hash"], c["hash"])

    def test_cancel_stops_the_pass(self):
        ev = threading.Event()
        ev.set()
        out = replay.one_pass(self.client, [{"read": 1000, "generated": 20}], self.sizer, None, "t", cancel=ev)
        self.assertEqual(out["error"], "cancelled")


class Unreachable(unittest.TestCase):
    def test_calibrate_names_the_failure(self):
        c = replay.Client("http://127.0.0.1:9", timeout=2)
        with self.assertRaises(replay.ReplayError):
            replay.Sizer(c).calibrate()


if __name__ == "__main__":
    unittest.main()
