import unittest

import support  # noqa: F401  (puts the repo on sys.path)
from studiolib import calibparse

LINES = [
    "  Loading the model for the measurements ...",
    "    round 1/3 PCIe share: 0 71.2  0.2 80.4  0.35 95.1  0.55 88.0  0.75 86.1  0.9 83.0  1 79.9",
    "    PCIe share, median of 3: 0: 71.0 (70.2-71.2), 0.2: 80.4 (79.9-81.0), 0.35: 95.1 (94.0-96.2), "
    "0.55: 88.0 (87.1-88.4), 0.75: 86.1 (85.0-86.3), 0.9: 83.0 (82.2-83.9), 1: 79.9 (79.0-80.5) -> best 0.35",
    "    round 2/3 draft floor: 0.7 96.0  0.5 94.9  0.3 90.1",
    "  Measuring with 7 CPU workers (restarts the engine) ...",
    "    7 workers: 95.0 tok/s",
    "  Measuring with 5 CPU workers (restarts the engine) ...",
    "    5 workers: 93.1 tok/s",
    "  Measuring the expert tier (the engine default) (restarts the engine) ...",
    "    87.0 tok/s",
    "  Measuring the expert tier every 1, 160 swaps, decay 0.97 (restarts the engine) ...",
    "    96.6 tok/s",
    "    the engine did not start for 4 workers: out of memory - this candidate is dropped, the earlier results are kept",
    "  [ok] tuned for this PC: --pcie-frac 0.35, --spec-min-p 0.70, --adapt-every 1, --adapt-swaps 160, "
    "--adapt-decay 0.97 (96.6 tok/s)",
]


def step(res, key):
    return next(s for s in res["steps"] if s["key"] == key)


def value(st, v):
    return next(x for x in st["values"] if x["value"] == v)


class SweepLines(unittest.TestCase):
    def test_round_line_gives_one_sample_per_value(self):
        res = calibparse.parse([LINES[1]])
        st = step(res, "pcie_frac")
        self.assertEqual([x["value"] for x in st["values"]], ["0", "0.2", "0.35", "0.55", "0.75", "0.9", "1"])
        self.assertEqual(value(st, "0.35")["rounds"], [95.1])
        self.assertEqual(value(st, "0")["rounds"], [71.2])
        self.assertIsNone(st["best"])

    def test_summary_line_gives_median_min_max_and_best(self):
        res = calibparse.parse(LINES[1:3])
        st = step(res, "pcie_frac")
        v = value(st, "0.35")
        self.assertEqual((v["median"], v["min"], v["max"]), (95.1, 94.0, 96.2))
        self.assertEqual(st["best"], "0.35")

    def test_draft_floor_round(self):
        res = calibparse.parse([LINES[3]])
        st = step(res, "spec_min_p")
        self.assertEqual([x["value"] for x in st["values"]], ["0.3", "0.5", "0.7"])
        self.assertEqual(value(st, "0.7")["rounds"], [96.0])

    def test_rounds_accumulate_and_a_live_median_is_filled_in(self):
        lines = ["    round 1/3 PCIe share: 0 71.2  0.35 95.1", "    round 2/3 PCIe share: 0.35 94.0  0 70.2"]
        st = step(calibparse.parse(lines), "pcie_frac")
        self.assertEqual(value(st, "0.35")["rounds"], [95.1, 94.0])
        self.assertAlmostEqual(value(st, "0.35")["median"], 94.55)
        self.assertEqual(value(st, "0.35")["min"], 94.0)

    def test_default_is_the_products_and_was_is_what_the_engine_had(self):
        res = calibparse.parse(LINES[1:3], {"pcie_frac": "0.35", "spec_min_p": "0.70"})
        st = step(res, "pcie_frac")
        self.assertEqual((st["default"], st["was"]), ("0.55", "0.35"))
        res = calibparse.parse([LINES[3]], {"spec_min_p": "0.70"})
        st = step(res, "spec_min_p")
        self.assertEqual((st["default"], st["was"]), ("0.5", "0.7"))      # printed as the sweep printed them
        self.assertIsNone(step(calibparse.parse(LINES[1:3]), "pcie_frac")["was"])


class RestartLines(unittest.TestCase):
    def test_workers(self):
        st = step(calibparse.parse(LINES[4:8]), "pool_workers")
        self.assertEqual([(v["value"], v["median"]) for v in st["values"]], [("7", 95.0), ("5", 93.1)])
        self.assertEqual(st["default"], "7")                        # the engine's own count is measured first
        self.assertEqual(st["best"], "7")

    def test_tier(self):
        st = step(calibparse.parse(LINES[8:12]), "adapt")
        self.assertEqual([(v["value"], v["median"]) for v in st["values"]],
                         [("default", 87.0), ("1/160/0.97", 96.6)])
        self.assertEqual(st["default"], "default")
        self.assertEqual(st["best"], "1/160/0.97")

    def test_a_rate_line_without_a_tier_line_is_ignored(self):
        res = calibparse.parse(["    96.6 tok/s"])
        self.assertEqual(step(res, "adapt")["values"], [])

    def test_failed_start(self):
        res = calibparse.parse(LINES[12:13])
        self.assertEqual(res["failed_starts"], [{"what": "4 workers", "reason": "out of memory"}])


class SummaryLines(unittest.TestCase):
    def test_summary_kept_and_outcome(self):
        res = calibparse.parse(LINES)
        self.assertEqual(res["outcome"], "tuned")
        self.assertEqual(res["tok_s"], 96.6)
        self.assertTrue(res["summary"].startswith("--pcie-frac 0.35, --spec-min-p 0.70"))
        self.assertEqual(step(res, "pcie_frac")["kept"], "0.35")
        self.assertEqual(step(res, "spec_min_p")["kept"], "0.7")      # "0.70" equals the sweep's "0.7"
        self.assertEqual(step(res, "adapt")["kept"], "1/160/0.97")
        self.assertEqual(step(res, "pool_workers")["kept"], "7")      # not in the summary: the default stays

    def test_defaults_line(self):
        res = calibparse.parse(LINES[:11] + ["  [ok] tuned for this PC: the default settings are already the fastest "
                                             "here (87.0 tok/s)"])
        self.assertEqual(res["outcome"], "defaults")
        self.assertEqual(res["tok_s"], 87.0)
        self.assertEqual(step(res, "pcie_frac")["kept"], "0.55")
        self.assertEqual(step(res, "adapt")["kept"], "default")

    def test_failure_line(self):
        res = calibparse.parse(["  [!]  the tuning did not finish (the engine did not start): the default settings stay"])
        self.assertEqual(res["outcome"], "failed")
        self.assertEqual(res["error"], "the engine did not start")
        self.assertIsNone(res["summary"])

    def test_kept_differs_from_best_when_the_summary_has_no_pcie_frac(self):
        lines = LINES[1:3] + ["  [ok] tuned for this PC: --pool-workers 5 (93.1 tok/s)"]
        res = calibparse.parse(lines, {"pcie_frac": "0.35"})
        st = step(res, "pcie_frac")
        self.assertEqual(st["best"], "0.35")
        self.assertEqual(st["kept"], "0.55")                          # within 3% of the default: not kept
        self.assertEqual(step(res, "pool_workers")["kept"], "5")

    def test_nothing_is_an_error(self):
        res = calibparse.parse(["what is this", "", "  ...", None, 5])
        self.assertEqual(len(res["steps"]), 4)
        self.assertIsNone(res["outcome"])


class RealRun(unittest.TestCase):
    """The lines of the real one-click tune (817 s) on 2026-10-15: the engine ran with 0.35 / 0.70 / 1/160/0.97
    before it, the sweeps found 0.35 and 0.7 best, and the result line named only the adapt flags."""

    def setUp(self):
        import json
        doc = json.loads((support.ROOT / "tests" / "fixtures" / "real_tune.json").read_text())
        self.lines = doc["lines"]
        self.res = calibparse.parse(self.lines, {"pcie_frac": "0.35", "spec_min_p": "0.70", "pool_workers": "7",
                                                 "adapt": "1/160/0.97"})

    def test_summary(self):
        self.assertEqual(self.res["outcome"], "tuned")
        self.assertEqual(self.res["tok_s"], 93.3)
        self.assertEqual(self.res["summary"], "--adapt-every 1, --adapt-swaps 80, --adapt-decay 0.97")

    def test_the_sweeps_winners_are_best_but_not_kept(self):
        pcie = step(self.res, "pcie_frac")
        self.assertEqual((pcie["best"], pcie["kept"], pcie["default"], pcie["was"]), ("0.35", "0.55", "0.55", "0.35"))
        floor = step(self.res, "spec_min_p")
        self.assertEqual((floor["best"], floor["kept"], floor["default"], floor["was"]), ("0.7", "0.5", "0.5", "0.7"))
        self.assertEqual(value(pcie, "0.35")["rounds"], [75.0, 74.9, 76.5])
        self.assertEqual(value(pcie, "0.35")["median"], 75.0)

    def test_workers_and_tier(self):
        w = step(self.res, "pool_workers")
        self.assertEqual([(v["value"], v["median"]) for v in w["values"]],
                         [("7", 74.9), ("5", 75.2), ("4", 74.4), ("2", 63.4)])
        self.assertEqual((w["default"], w["best"], w["kept"]), ("7", "5", "7"))
        t = step(self.res, "adapt")
        self.assertEqual((t["default"], t["was"], t["best"], t["kept"]), ("default", "1/160/0.97", "1/80/0.97", "1/80/0.97"))

    def test_startup_chatter_of_the_engine_is_ignored(self):
        self.assertTrue(any(ln.startswith("[strata]") for ln in self.lines))
        self.assertEqual(self.res["failed_starts"], [])

    def test_while_running_nothing_is_kept_yet(self):
        res = calibparse.parse(self.lines[:30], {"pcie_frac": "0.35"})
        self.assertIsNone(step(res, "pcie_frac")["kept"])
        self.assertIsNone(res["outcome"])


class Merge(unittest.TestCase):
    def test_overlap_merge_keeps_lines_that_left_the_deque(self):
        stored = [f"line {i}" for i in range(500)]
        new = [f"line {i}" for i in range(100, 520)]            # a 420-line window: the first 100 are gone
        merged = calibparse.merge_lines(stored, new)
        self.assertEqual(merged, [f"line {i}" for i in range(520)])

    def test_the_cap_window_slides(self):
        stored = [f"l{i}" for i in range(400)]
        new = [f"l{i}" for i in range(10, 410)]
        self.assertEqual(calibparse.merge_lines(stored, new)[-1], "l409")
        self.assertEqual(len(calibparse.merge_lines(stored, new)), 410)

    def test_no_overlap_appends_and_empty_new_keeps(self):
        self.assertEqual(calibparse.merge_lines(["a"], ["b"]), ["a", "b"])
        self.assertEqual(calibparse.merge_lines(["a"], []), ["a"])
        self.assertEqual(calibparse.merge_lines([], ["b"]), ["b"])

    def test_identical_lists_do_not_duplicate(self):
        self.assertEqual(calibparse.merge_lines(["a", "b"], ["a", "b"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
