"""replay: the shapes picked from the log, the prompts of the right size, one pass against a fake Strata."""
import threading
import unittest

import support
from fakestrata import FakeStrata
from studiolib import replay


class Shapes(unittest.TestCase):
    def test_seconds_of_the_shapes(self):
        shapes = [{"read": 2000, "generated": 100}, {"read": 1000, "generated": 50}]
        self.assertAlmostEqual(replay.shapes_seconds(shapes, 1000.0, 50.0), 3.0 + 3.0)
        self.assertIsNone(replay.shapes_seconds(shapes, None, 50.0))


class Standard(unittest.TestCase):
    def test_six_fixed_turns_and_a_copy_each_time(self):
        a, b = replay.standard_turns(), replay.standard_turns()
        self.assertEqual(a, b)
        self.assertEqual(len(a), 6)
        self.assertEqual(a[0]["read"], 24000)                     # the first turn reads the whole prompt
        self.assertTrue(all(t["read"] <= 3000 for t in a[1:]))     # the later turns add a few thousand tokens
        self.assertEqual(sum(t["generated"] for t in a), 1728)
        self.assertEqual({t["kind"] for t in a}, {"copy", "free"})
        a[0]["read"] = 1                                          # a caller cannot change the standard
        self.assertEqual(replay.standard_turns()[0]["read"], 24000)

    def test_the_note_names_the_numbers_of_the_turns(self):
        for n in ("24,000", "6 turns", "1,728"):
            self.assertIn(n, replay.STANDARD_NOTE)


class Filler(unittest.TestCase):
    def test_same_salt_same_text_and_length(self):
        self.assertEqual(replay.filler(50, "a"), replay.filler(50, "a"))
        self.assertEqual(len(replay.filler(50, "a").split()), 50)
        self.assertEqual(replay.filler(0, "a"), "")


TURNS = [{"read": 3000, "generated": 100, "kind": "copy"}, {"read": 1000, "generated": 60, "kind": "free"},
         {"read": 800, "generated": 50, "kind": "copy"}]


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

    def test_the_first_turn_has_the_size_of_the_turn(self):
        text = replay.user_turn({"read": 6000, "generated": 200, "kind": "copy"}, 0, self.sizer, "s", 0)
        r = self.client.chat("", 1, messages=[{"role": "user", "content": text}])
        self.assertAlmostEqual(r["usage"]["prompt_tokens"], 6000, delta=300)

    def test_a_pass_counts_reuse_and_the_tune_is_sent(self):
        before = len(self.fake.requests_to("/v1/chat/completions"))
        out = replay.one_pass(self.client, TURNS, self.sizer, {"pcie_frac": 0.35}, "t")
        self.assertIsNone(out["error"])
        self.assertEqual(out["cached"], 0)                         # the first turn reads everything
        self.assertGreater(out["reuse_min"], 0.7)                  # the later turns reuse the conversation
        self.assertAlmostEqual(out["read"], 3000 + 1000 + 800, delta=500)
        self.assertAlmostEqual(out["generated"], 210, delta=25)
        self.assertAlmostEqual(out["decode_tok_s"], 80.0, delta=2)
        self.assertAlmostEqual(out["drafts_accepted"], 0.8)
        # the score is the engine's time per generated token
        self.assertAlmostEqual(out["score_ms"], 1000 / 80, delta=0.3)          # 12.5 ms per token at 80 tok/s
        sent = self.fake.requests_to("/v1/chat/completions")[before:]
        self.assertEqual(len(sent), 3)
        for r in sent:
            self.assertEqual(r["body"]["strata_tune"], {"pcie_frac": 0.35})
            self.assertEqual(r["body"]["temperature"], 0)
        # the engine keeps the first turns, not the last one
        self.assertEqual(["strata_checkpoint" in r["body"] for r in sent], [False, False, True])
        self.assertEqual([len(r["body"]["messages"]) for r in sent], [1, 3, 5])

    def test_the_history_is_the_same_whatever_the_reply(self):
        self.fake.chat_differs = lambda t: t.get("spec_min_p") == 0.9
        n0 = len(self.fake.requests_to("/v1/chat/completions"))
        replay.one_pass(self.client, TURNS, self.sizer, {}, "same")
        replay.one_pass(self.client, TURNS, self.sizer, {"spec_min_p": 0.9}, "same")
        sent = self.fake.requests_to("/v1/chat/completions")[n0:]
        self.assertEqual(len(sent), 6)
        for a, b in zip(sent[:3], sent[3:]):
            self.assertEqual(a["body"]["messages"], b["body"]["messages"])

    def test_a_pass_has_no_prefix_in_common_with_another_pass(self):
        replay.one_pass(self.client, TURNS, self.sizer, {}, "one")
        out = replay.one_pass(self.client, TURNS, self.sizer, {}, "two")
        self.assertEqual(out["cached"], 0)

    def test_no_conversation_cache_shows_as_no_reuse(self):
        self.fake.conv_cache_on = False
        out = replay.one_pass(self.client, TURNS, self.sizer, {}, "t")
        self.assertEqual(out["reuse_min"], 0.0)

    def test_the_score_follows_the_speed_of_the_tune(self):
        self.fake.chat_speeds = lambda t: (2000.0, 120.0 if t.get("pcie_frac") == 0.2 else 80.0)
        slow = replay.one_pass(self.client, TURNS, self.sizer, {"pcie_frac": 0.5}, "a")
        fast = replay.one_pass(self.client, TURNS, self.sizer, {"pcie_frac": 0.2}, "b")
        self.assertLess(fast["score_ms"], slow["score_ms"])

    def test_the_hash_tells_a_changed_reply(self):
        self.fake.chat_differs = lambda t: t.get("spec_min_p") == 0.9
        a = replay.one_pass(self.client, TURNS, self.sizer, {"spec_min_p": 0.5}, "t1")
        b = replay.one_pass(self.client, TURNS, self.sizer, {}, "t2")
        c = replay.one_pass(self.client, TURNS, self.sizer, {"spec_min_p": 0.9}, "t3")
        self.assertEqual(a["hash"], b["hash"])
        self.assertNotEqual(a["hash"], c["hash"])

    def test_cancel_stops_the_pass(self):
        ev = threading.Event()
        ev.set()
        out = replay.one_pass(self.client, TURNS, self.sizer, None, "t", cancel=ev)
        self.assertEqual(out["error"], "cancelled")


class Unreachable(unittest.TestCase):
    def test_calibrate_names_the_failure(self):
        c = replay.Client("http://127.0.0.1:9", timeout=2)
        with self.assertRaises(replay.ReplayError):
            replay.Sizer(c).calibrate()


if __name__ == "__main__":
    unittest.main()
