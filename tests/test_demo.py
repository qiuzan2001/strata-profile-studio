"""studio.py --demo: the demo Strata with the recorded data of tests/fixtures/demo, and the Studio against it."""
import json
import unittest
from pathlib import Path

import support
from support import fast_timing, request, wait_for

DEMO = support.ROOT / "tests" / "fixtures" / "demo"


class DemoFlag(unittest.TestCase):
    def test_the_flag(self):
        import studio
        a = studio.parse_args(["--demo"])
        self.assertTrue(a.demo)
        self.assertIsNone(a.port)                         # --demo takes the first free port from 8090
        self.assertFalse(studio.parse_args([]).demo)


class DemoData(unittest.TestCase):
    def test_no_private_data_in_the_demo_files(self):
        for p in DEMO.rglob("*"):
            if p.is_file() and p.suffix != ".bin":
                t = p.read_text(encoding="utf-8")
                self.assertNotIn("/home/", t, p.name)
                self.assertNotIn(".ts.net", t, p.name)
                self.assertNotIn("C:\\\\Users", t, p.name)

    def test_the_tune_record_names_reports_of_the_demo(self):
        names = {p.name for p in (DEMO / "reports").iterdir()}
        for t in json.loads((DEMO / "studio-state.json").read_text(encoding="utf-8"))["tunes"]:
            self.assertIn(t["before_report"], names)
            self.assertIn(t["after_report"], names)


class DemoRun(unittest.TestCase):
    def test_each_view_has_data(self):
        import studio
        s, close = studio.demo_studio(0, timing=fast_timing())
        folder = Path(s.results).parent
        s.start()
        try:
            wait_for(lambda: request(s, "GET", "/api/state")[2]["strata"]["status"] == "ok", what="Strata ok")
            st = request(s, "GET", "/api/state")[2]
            self.assertEqual(st["strata"]["model"], "swift-1.5-iq3_s")
            wait_for(lambda: request(s, "GET", "/api/state")[2]["strata"]["tune"]["available"],
                     what="Tune this PC available")                                   # Tune: the button works
            reports = request(s, "GET", "/api/reports")[2]["reports"]
            self.assertEqual(len([r for r in reports if r["ok"]]), 7)                  # Runs
            self.assertEqual(sum(1 for r in reports if r.get("defaults")), 1)           # Compare: a baseline
            self.assertEqual(len(request(s, "GET", "/api/tunes")[2]["tunes"]), 1)       # Tune: a finished tune
            self.assertTrue(request(s, "GET", "/api/live?since=0")[2]["samples"])       # Now: requests
            self.assertTrue(request(s, "GET", "/api/experts")[2]["base"]["ranks_b64"])  # Now: the expert map
            wait_for(lambda: request(s, "GET", "/api/settings")[2]["rows"], what="settings rows")   # Settings
            host = request(s, "GET", "/api/host")[2]
            self.assertIsNone(host["cpu"])                                             # no reader of this PC
        finally:
            s.stop()
            close()
        self.assertFalse(folder.exists())                                              # the temp folder is gone


if __name__ == "__main__":
    unittest.main()
