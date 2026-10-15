import copy
import unittest

import support
from studiolib import compare, reports

R = "2026-10-17-profile-rtx-5070-ti-swift-1-5-iq3-s"          # the real reports of 2026-10-15, runs 1..6
REAL = {n: R + (f"-{n}" if n > 1 else "") for n in range(1, 7)}


def store_of(testcase, names):
    d = support.copy_fixtures(support.tmpdir(testcase) / "r", names)
    return reports.ReportStore(d)


def decode(rep, kind):
    return next(d for d in rep["decode"] if d["kind"] == kind)


def verdict(out, group, key):
    return next(v for v in out["verdicts"] if v["group"] == group and v["key"] == key)


def row(out, key, metric):
    return next(r for r in out["rows"] if r["key"] == key and r["metric"] == metric)


class NoPair(unittest.TestCase):
    """The fixtures A and B were made with different settings: no same-settings pair, so the floors are assumed."""

    def setUp(self):
        self.s = store_of(self, (support.A, support.B))
        self.a, self.b = self.s.get(support.A), self.s.get(support.B)
        self.out = compare.compute(self.a, self.b, self.s.pool())

    def test_floors_are_assumed_and_the_verdict_says_so(self):
        self.assertEqual(self.out["floors"], {"minimum": 0.03, "assumed": {"tps": 0.05, "ms_part": 1.0,
                                                                           "hit_points": 0.02}, "pairs": []})
        v = verdict(self.out, "decode", "chat")
        self.assertEqual(v["state"], "better")
        self.assertEqual(v["text"], "chat: 8.2% faster, 75.7 → 81.9 tok/s; largest change: adapt wait +5.6 ms per "
                                    "window (assumed floor, no same-settings pair measured yet)")
        self.assertIsNone(v["floor_note"])
        r = row(self.out, "chat", "decode tok/s")
        self.assertEqual((r["a"], r["b"], r["delta"], r["pct"], r["state"], r["floor"], r["floor_kind"]),
                         (75.7, 81.9, 6.2, 8.19, "better", 0.05, "rel"))

    def test_below_the_assumed_floor_is_same(self):
        b = copy.deepcopy(self.b)
        decode(b, "agent turns")["tps"]["median"] = 73.06                         # +1.9 %
        v = verdict(compare.compute(self.a, b), "decode", "agent turns")           # no pool given at all
        self.assertEqual(v["state"], "same")
        self.assertIn("agent turns: no measurable change (+1.9%, inside the 5.0% floor)", v["text"])
        self.assertTrue(v["text"].endswith("(assumed floor, no same-settings pair measured yet)"))

    def test_assumed_part_floor_is_one_ms(self):
        b = copy.deepcopy(self.b)
        wa = decode(self.a, "chat")["window_ms"]
        b_win = dict(wa)
        b_win["draft"] = wa["draft"] + 0.9
        b_win["other"] = wa["other"] + 1.2
        decode(b, "chat")["window_ms"] = b_win
        out = compare.compute(self.a, b, self.s.pool())
        self.assertEqual(row(out, "chat", "draft (ms per window)")["state"], "same")
        self.assertEqual(row(out, "chat", "other (ms per window)")["state"], "worse")

    def test_missing_prompt_is_not_in_both(self):
        b = copy.deepcopy(self.b)
        b["prompts"] = [p for p in b["prompts"] if p["label"] != "16K"]
        out = compare.compute(self.a, b)
        v = verdict(out, "prompt", "16K")
        self.assertEqual((v["state"], v["text"]), ("missing", "16K read: not in both reports"))
        self.assertTrue(any(r["key"] == "16K" and r["state"] == "missing" for r in out["rows"]))

    def test_zero_a_gives_null_pct(self):
        a = copy.deepcopy(self.a)
        decode(a, "chat")["window_ms"]["adapt wait"] = 0
        r = row(compare.compute(a, self.b), "chat", "adapt wait (ms per window)")
        self.assertIsNone(r["pct"])
        self.assertEqual((r["delta"], r["state"]), (7.0, "worse"))

    def test_hit_rate_rows_are_in_points(self):
        b = copy.deepcopy(self.b)
        decode(b, "chat")["hit_rate"] = 0.781
        out = compare.compute(self.a, b)
        r = row(out, "chat", "expert cache hit rate (points)")
        self.assertEqual((r["floor_kind"], r["floor"], r["state"], r["delta"]), ("pts", 2.0, "better", 7.3))
        self.assertTrue(verdict(out, "decode", "chat")["text"].count("; hit rate 70.8% → 78.1%"))

    def test_settings_diff_and_warnings(self):
        diff = {x["flag"]: x for x in self.out["settings"]}
        self.assertEqual((diff["--pcie-frac"]["a"], diff["--pcie-frac"]["b"], diff["--pcie-frac"]["changed"]),
                         ("0.55", "0.35", True))
        self.assertFalse(diff["--spec-min-p"]["changed"])
        b = copy.deepcopy(self.b)
        b["machine"]["gpu"] = "NVIDIA GeForce RTX 4060 Ti"
        b["machine"]["strata_version"] = "0.1.45"
        self.assertEqual(compare.compute(self.a, b)["warnings"], ["different GPU", "different Strata version"])

    def test_the_chat_prompts_spread_is_never_a_floor(self):
        a, b = copy.deepcopy(self.a), copy.deepcopy(self.b)
        for rep in (a, b):                                  # three chats that differ wildly, as the real ones do
            rep["chat_runs"] = [{"decode_tps": 40.0, "hit_rate": 0.5}, {"decode_tps": 76.0, "hit_rate": 0.7},
                                {"decode_tps": 100.0, "hit_rate": 0.9}]
        out = compare.compute(a, b, self.s.pool())
        self.assertEqual(out["floors"]["assumed"]["tps"], 0.05)
        self.assertEqual(row(out, "chat", "decode tok/s")["floor"], 0.05)
        self.assertNotIn("noise", self.a)


class WithPairs(unittest.TestCase):
    """The real reports: runs 2-5 share their settings, run 6 is the tune's result, run 1 the first defaults."""

    def setUp(self):
        self.s = store_of(self, [REAL[n] for n in range(1, 7)])

    def cmp(self, a, b, only=None):
        s = self.s if only is None else store_of(self, [REAL[n] for n in only])
        return compare.compute(s.get(REAL[a]), s.get(REAL[b]), s.pool()), s

    def test_runs_3_and_4_alone_are_a_pair_with_the_documented_spreads(self):
        out, _ = self.cmp(3, 4, only=(1, 3, 4))      # run 1 has other settings and takes no part
        v = verdict(out, "decode", "chat")
        self.assertEqual(v["floor_note"], "noise floor from runs 3 and 4, same settings")
        # the pair's own spreads: chat 0.9% (below the 3% minimum), long context 2.7% (same), agent turns 7.6%
        self.assertEqual(row(out, "chat", "decode tok/s")["floor"], 0.03)
        self.assertEqual(row(out, "long context", "decode tok/s")["floor"], 0.03)
        self.assertAlmostEqual(row(out, "agent turns", "decode tok/s")["floor"], 0.0758, places=3)
        self.assertEqual(v["text"], "chat: no measurable change (+0.9%, inside the 3.0% noise floor)")
        self.assertEqual(out["floors"]["pairs"][0]["label"], "runs 3 and 4, same settings")
        self.assertEqual(verdict(out, "decode", "agent turns")["state"], "same")

    def test_the_floor_is_the_widest_spread_among_all_same_settings_runs(self):
        out, _ = self.cmp(3, 4)                              # runs 2, 3, 4 and 5 share their settings
        r = row(out, "chat", "decode tok/s")
        self.assertAlmostEqual(r["floor"], 0.0335, places=3)       # 77.6 (run 2) against 80.2 (run 4)
        v = verdict(out, "decode", "chat")
        self.assertEqual(v["floor_note"], "noise floor from runs 2 and 4, same settings; "
                                          "the widest spread of 4 runs with the same settings")
        self.assertIn("inside the 3.3% noise floor", v["text"])

    def test_the_tune_result_is_inside_the_floor_with_the_sign_test_said(self):
        out, _ = self.cmp(5, 6)
        v = verdict(out, "decode", "chat")
        self.assertEqual(v["state"], "same")
        self.assertIn("chat: no measurable change (+0.4%, inside the 3.3% noise floor)", v["text"])
        agent = verdict(out, "decode", "agent turns")
        self.assertEqual(agent["state"], "same")
        self.assertIn("; 6 of 6 agent turns slower", agent["text"])
        self.assertIn("largest change: CPU experts +2.7 ms per window", agent["text"])

    def test_a_change_above_a_measured_floor_counts_and_below_it_does_not(self):
        s = self.s
        a, b = copy.deepcopy(s.get(REAL[3])), copy.deepcopy(s.get(REAL[4]))
        decode(b, "chat")["tps"]["median"] = round(decode(a, "chat")["tps"]["median"] * 1.06, 1)
        pool = s.pool()
        out = compare.compute(a, b, pool)
        self.assertEqual(verdict(out, "decode", "chat")["state"], "better")
        decode(b, "chat")["tps"]["median"] = round(decode(a, "chat")["tps"]["median"] * 1.025, 1)
        self.assertEqual(verdict(compare.compute(a, b, pool), "decode", "chat")["state"], "same")

    def test_a_wide_pair_raises_the_floor_so_a_big_looking_change_is_same(self):
        d = support.tmpdir(self) / "r"
        support.copy_fixtures(d, (REAL[3], REAL[4], REAL[6]))
        import json
        f = d / REAL[4] / "results.json"
        raw = json.loads(f.read_text())
        raw["results"]["decode"]["chat"]["decode_tps"]["median"] = 85.0           # 3 and 4 differ by 6.9%
        f.write_text(json.dumps(raw))
        s = reports.ReportStore(d)
        a, b = s.get(REAL[3]), s.get(REAL[6])
        decode(b, "chat")["tps"]["median"] = 84.0                                  # +5.7% over run 3's 79.5
        out = compare.compute(a, b, s.pool())
        self.assertEqual(verdict(out, "decode", "chat")["state"], "same")
        self.assertEqual(verdict(compare.compute(a, b), "decode", "chat")["state"], "better")   # no pool: 5% assumed

    def test_sign_test_needs_five_turns_and_one_direction(self):
        a, b = copy.deepcopy(self.s.get(REAL[5])), copy.deepcopy(self.s.get(REAL[6]))
        out = compare.compute(a, b, self.s.pool())
        self.assertIn("6 of 6 agent turns slower", verdict(out, "decode", "agent turns")["text"])
        b["agent"][2]["decode_tps"] = a["agent"][2]["decode_tps"] + 5.0          # one turn goes the other way
        out = compare.compute(a, b, self.s.pool())
        self.assertNotIn("agent turns slower", verdict(out, "decode", "agent turns")["text"])
        for rep in (a, b):
            rep["agent"] = rep["agent"][:4]
        for t_a, t_b in zip(a["agent"], b["agent"]):
            t_b["decode_tps"] = t_a["decode_tps"] - 3
        self.assertNotIn("agent turns slower", verdict(compare.compute(a, b, self.s.pool()), "decode",
                                                       "agent turns")["text"])

    def test_pool_ignores_other_machines(self):
        other = copy.deepcopy(self.s.get(REAL[4]))
        other["machine"]["gpu"] = "NVIDIA GeForce RTX 4060 Ti"
        decode(other, "chat")["tps"]["median"] = 40.0
        pool = self.s.pool()
        pool.append({"id": "x", "rep": other, "short": compare.short_of(other), "order": [2026, 10, 17, 9, 0.0]})
        out = compare.compute(self.s.get(REAL[3]), self.s.get(REAL[4]), pool)
        self.assertAlmostEqual(row(out, "chat", "decode tok/s")["floor"], 0.0335, places=3)

    def test_compare_with_itself_is_all_same(self):
        rep = self.s.get(REAL[4])
        out = compare.compute(rep, rep, self.s.pool())
        self.assertTrue(all(v["state"] == "same" for v in out["verdicts"]))


if __name__ == "__main__":
    unittest.main()
