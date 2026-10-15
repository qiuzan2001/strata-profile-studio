import copy
import unittest

import support
from studiolib import compare, reports


def load(name):
    return reports.ReportStore(support.FIXTURES).get(name)


def decode(rep, kind):
    return next(d for d in rep["decode"] if d["kind"] == kind)


class Floors(unittest.TestCase):
    def test_fixture_floors(self):
        a, b = load(support.A), load(support.B)
        self.assertEqual(a["noise"]["chat_tps_spread"], 0.0462)
        self.assertEqual(a["noise"]["chat_hit_range"], 0.069)
        fl = compare.compute(a, b)["floors"]
        self.assertEqual(fl, {"tps_median": 0.0462, "tps_single": 0.0785, "hit_median": 0.069, "hit_single": 0.1173,
                              "ms_part_abs": 0.5})

    def test_fewer_than_two_chat_runs_fall_back_and_say_so(self):
        a, b = load(support.A), load(support.B)
        for rep in (a, b):
            rep["chat_runs"] = rep["chat_runs"][:1]
            rep["noise"] = {"chat_tps_spread": None, "chat_hit_range": None}
        out = compare.compute(a, b)
        self.assertEqual(out["floors"]["tps_median"], 0.03)
        self.assertEqual(out["floors"]["tps_single"], 0.05)
        self.assertIn("noise unknown (fewer than two chat runs)", out["verdicts"][0]["text"])


class Verdicts(unittest.TestCase):
    def setUp(self):
        self.a, self.b = load(support.A), load(support.B)

    def verdict(self, out, group, key):
        return next(v for v in out["verdicts"] if v["group"] == group and v["key"] == key)

    def test_better_with_the_documents_wording(self):
        out = compare.compute(self.a, self.b)
        v = self.verdict(out, "decode", "chat")
        self.assertEqual(v["state"], "better")
        self.assertEqual(v["text"], "chat: 8.2% faster, 75.7 → 81.9 tok/s (noise floor 4.6%); "
                                    "largest change: adapt wait +5.6 ms per window")
        row = next(r for r in out["rows"] if r["key"] == "chat" and r["metric"] == "decode tok/s")
        self.assertEqual((row["a"], row["b"], row["delta"], row["pct"], row["state"]), (75.7, 81.9, 6.2, 8.19, "better"))

    def test_plain_better_has_no_part_suffix_when_nothing_else_moved(self):
        b = copy.deepcopy(self.b)
        decode(b, "chat")["window_ms"] = dict(decode(self.a, "chat")["window_ms"])
        v = self.verdict(compare.compute(self.a, b), "decode", "chat")
        self.assertEqual(v["text"], "chat: 8.2% faster, 75.7 → 81.9 tok/s (noise floor 4.6%)")

    def test_worse_for_a_single_run(self):
        b = copy.deepcopy(self.b)
        decode(b, "long context")["tps"]["median"] = 74.6
        v = self.verdict(compare.compute(self.a, b), "decode", "long context")
        self.assertEqual(v["state"], "worse")
        self.assertTrue(v["text"].startswith("long context: 9.0% slower, 82.0 → 74.6 tok/s (noise floor 7.8%)"))

    def test_small_change_is_same(self):
        b = copy.deepcopy(self.b)
        decode(b, "agent turns")["tps"]["median"] = 73.06                       # +1.9 %
        v = self.verdict(compare.compute(self.a, b), "decode", "agent turns")
        self.assertEqual(v["state"], "same")
        self.assertEqual(v["text"], "agent turns: no measurable change (+1.9%, inside the 4.6% noise floor)")

    def test_a_change_inside_the_floor_is_same_even_when_it_is_the_chat_median(self):
        b = copy.deepcopy(self.b)
        decode(b, "chat")["tps"]["median"] = round(75.7 * 1.045, 1)
        decode(b, "chat")["window_ms"] = dict(decode(self.a, "chat")["window_ms"])
        v = self.verdict(compare.compute(self.a, b), "decode", "chat")
        self.assertEqual(v["state"], "same")

    def test_missing_prompt_is_not_in_both(self):
        b = copy.deepcopy(self.b)
        b["prompts"] = [p for p in b["prompts"] if p["label"] != "16K"]
        out = compare.compute(self.a, b)
        v = self.verdict(out, "prompt", "16K")
        self.assertEqual(v["state"], "missing")
        self.assertEqual(v["text"], "16K read: not in both reports")
        self.assertTrue(any(r["key"] == "16K" and r["state"] == "missing" for r in out["rows"]))

    def test_prompt_wording(self):
        b = copy.deepcopy(self.b)
        next(p for p in b["prompts"] if p["label"] == "2K")["prompt_tps"] = 1300.0
        v = self.verdict(compare.compute(self.a, b), "prompt", "2K")
        self.assertEqual(v["text"], "2K read: 16.3% faster, 1,118 → 1,300 tok/s (noise floor 7.8%)")

    def test_hit_rate_beyond_the_floor_is_appended(self):
        b = copy.deepcopy(self.b)
        decode(b, "chat")["hit_rate"] = 0.781
        decode(b, "chat")["window_ms"] = dict(decode(self.a, "chat")["window_ms"])
        v = self.verdict(compare.compute(self.a, b), "decode", "chat")
        self.assertTrue(v["text"].endswith("; hit rate 70.8% → 78.1%"), v["text"])

    def test_warnings(self):
        b = copy.deepcopy(self.b)
        b["machine"]["gpu"] = "NVIDIA GeForce RTX 4060 Ti"
        b["machine"]["context"] = 65536
        b["machine"]["strata_version"] = "0.1.45"
        self.assertEqual(compare.compute(self.a, b)["warnings"],
                         ["different GPU", "different context", "different Strata version"])


class Rows(unittest.TestCase):
    def setUp(self):
        self.a, self.b = load(support.A), load(support.B)

    def test_a_tiny_window_part_change_stays_same(self):
        b = copy.deepcopy(self.b)
        wa = decode(self.a, "chat")["window_ms"]
        decode(b, "chat")["window_ms"] = dict(wa)
        wa2 = dict(wa)
        wa2["draft"] = wa["draft"] + 0.02
        decode(b, "chat")["window_ms"] = wa2
        rows = compare.compute(self.a, b)["rows"]
        row = next(r for r in rows if r["key"] == "chat" and r["metric"] == "draft (ms per window)")
        self.assertEqual(row["state"], "same")

    def test_a_small_part_that_doubles_but_moves_under_half_a_ms_is_same(self):
        a, b = copy.deepcopy(self.a), copy.deepcopy(self.b)
        decode(a, "chat")["window_ms"]["other"] = 0.1
        decode(b, "chat")["window_ms"]["other"] = 0.4
        row = next(r for r in compare.compute(a, b)["rows"]
                   if r["key"] == "chat" and r["metric"] == "other (ms per window)")
        self.assertEqual(row["state"], "same")

    def test_zero_a_gives_null_pct(self):
        a, b = copy.deepcopy(self.a), copy.deepcopy(self.b)
        decode(a, "chat")["window_ms"]["adapt wait"] = 0
        row = next(r for r in compare.compute(a, b)["rows"]
                   if r["key"] == "chat" and r["metric"] == "adapt wait (ms per window)")
        self.assertIsNone(row["pct"])
        self.assertEqual(row["delta"], 7.0)
        self.assertEqual(row["state"], "worse")

    def test_hit_rate_rows_are_in_points(self):
        row = next(r for r in compare.compute(self.a, self.b)["rows"]
                   if r["key"] == "chat" and r["metric"].startswith("expert cache hit rate"))
        self.assertEqual(row["floor_kind"], "pts")
        self.assertEqual(row["floor"], 6.9)

    def test_settings_diff(self):
        diff = {s["flag"]: s for s in compare.compute(self.a, self.b)["settings"]}
        self.assertEqual((diff["--pcie-frac"]["a"], diff["--pcie-frac"]["b"], diff["--pcie-frac"]["changed"]),
                         ("0.55", "0.35", True))
        self.assertFalse(diff["--spec-min-p"]["changed"])           # "0.50" and "0.50"

    def test_compare_with_itself_is_all_same(self):
        out = compare.compute(self.a, self.a)
        self.assertTrue(all(v["state"] == "same" for v in out["verdicts"]))


if __name__ == "__main__":
    unittest.main()
