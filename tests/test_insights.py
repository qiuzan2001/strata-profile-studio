"""Runs tests/insights_cases.js (the page's "what to look at first" sentences) under node."""
import shutil
import subprocess
import unittest

import support


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Insights(unittest.TestCase):
    def test_cases_pass(self):
        r = subprocess.run(["node", str(support.ROOT / "tests" / "insights_cases.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"ok \d+ cases")

    def test_insights_js_parses(self):
        r = subprocess.run(["node", "--check", str(support.ROOT / "web" / "insights.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
