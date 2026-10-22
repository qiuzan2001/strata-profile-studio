"""abtest: the order of the arms, the paired change, the noise floor and the verdict."""
import unittest

import support  # noqa: F401  (puts the repository on the path)
from studiolib import abtest


class Schedule(unittest.TestCase):
    def test_every_arm_once_per_round_and_the_seed_fixes_the_order(self):
        s = abtest.schedule(["a", "b", "c"], 6, seed=3)
        self.assertEqual(len(s), 6)
        for order in s:
            self.assertEqual(sorted(order), ["a", "b", "c"])
        self.assertEqual(s, abtest.schedule(["a", "b", "c"], 6, seed=3))
        self.assertNotEqual(s, abtest.schedule(["a", "b", "c"], 6, seed=4))
        self.assertGreater(len({tuple(o) for o in s}), 1)              # the order changes between rounds


class Changes(unittest.TestCase):
    def test_paired_changes_are_fractions_of_the_base(self):
        self.assertEqual(abtest.paired_changes([10.0, 20.0], [11.0, 18.0]), [0.1, -0.1])

    def test_a_zero_base_is_skipped(self):
        self.assertEqual(abtest.paired_changes([0.0, 10.0], [1.0, 12.0]), [0.2])

    def test_noise_floor_is_the_largest_a_a_difference(self):
        self.assertAlmostEqual(abtest.noise_floor([10, 10, 10], [10.2, 9.7, 10.1]), 0.03)
        self.assertEqual(abtest.noise_floor([], []), 0.0)


class Verdict(unittest.TestCase):
    BASE = [10.0, 10.2, 9.9, 10.1, 10.0, 10.3]

    def test_too_few_rounds(self):
        v = abtest.verdict([10.0] * 4, [9.0] * 4, 0.01)
        self.assertEqual(v["verdict"], "not enough rounds")
        self.assertIsNone(v["delta"])

    def test_a_clear_improvement_in_seconds(self):
        cand = [x * 0.9 for x in self.BASE]                              # 10% less time
        v = abtest.verdict(self.BASE, cand, floor=0.02)
        self.assertEqual(v["verdict"], "better")
        self.assertAlmostEqual(v["gain"], 0.1, places=6)
        self.assertLess(v["ci"][1], 0)

    def test_a_clear_loss(self):
        cand = [x * 1.1 for x in self.BASE]
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.02)["verdict"], "worse")

    def test_a_change_inside_the_noise_is_no_change(self):
        cand = [x * 0.99 for x in self.BASE]
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.03)["verdict"], "no change")

    def test_a_change_with_an_interval_across_zero_is_no_change(self):
        cand = [9.0, 11.0, 9.1, 10.9, 9.0, 11.0]
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.0)["verdict"], "no change")

    def test_min_gain_raises_the_bar(self):
        cand = [x * 0.97 for x in self.BASE]
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.0)["verdict"], "better")
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.0, min_gain=0.05)["verdict"], "no change")

    def test_higher_is_better(self):
        cand = [x * 1.1 for x in self.BASE]
        self.assertEqual(abtest.verdict(self.BASE, cand, floor=0.02, lower_is_better=False)["verdict"], "better")
        self.assertEqual(abtest.verdict(self.BASE, [x * 0.9 for x in self.BASE], floor=0.02, lower_is_better=False)["verdict"], "worse")

    def test_the_same_seed_gives_the_same_interval(self):
        cand = [x * 0.95 for x in self.BASE]
        self.assertEqual(abtest.verdict(self.BASE, cand, 0.0, seed=7)["ci"], abtest.verdict(self.BASE, cand, 0.0, seed=7)["ci"])


if __name__ == "__main__":
    unittest.main()
