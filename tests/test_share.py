"""The Share dialog's destinations 1 (gh) and 2 (the filled issue form): the issue body that the cards site's check
accepts, the form link's fields and length rule, gh not logged in, the 3-minute wait for the check's answer, read-only
mode and the demo.  No test reaches GitHub: gh is a fake `run`."""
import importlib.util
import json
import types
import unittest
import urllib.parse
from pathlib import Path

import support
from fakestrata import FakeStrata
from support import ROOT, request, tmpdir, wait_for

from cards import render
from studiolib import cardmake, share

DEMO_RUN = ROOT / "tests" / "fixtures" / "demo" / "reports" / "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s-6"
SITE = ROOT / "cards" / "site"
FACTS = {"expert_read_gbs": 25.4, "link_gbs": 28.0, "pcie_gen": 4, "pcie_width": 16, "power_limit_w": 300}
ISSUE = "https://github.com/qiuzan2001/strata-cards/issues/12"


def card():
    return cardmake.card_of_path(DEMO_RUN, facts=FACTS)


def done(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeGh:
    """gh, as seen by share.GhShare: auth status, issue create, and the issue's comments (a list per call)."""

    def __init__(self, logged_in=True, comments=None):
        self.logged_in, self.comments, self.calls, self.body = logged_in, list(comments or []), [], None

    def __call__(self, cmd, timeout=30, stdin=None):
        self.calls.append(list(cmd))
        if cmd[1:3] == ["auth", "status"]:
            return done(0 if self.logged_in else 1, err="" if self.logged_in else "You are not logged into any GitHub hosts.")
        if cmd[1:3] == ["issue", "create"]:
            self.body = Path(cmd[cmd.index("--body-file") + 1]).read_text(encoding="utf-8")
            return done(0, out=ISSUE + "\n")
        if cmd[1] == "api":
            batch = self.comments.pop(0) if self.comments else []
            return done(0, out=json.dumps(batch))
        return done(1, err="unknown")


def load_submit():
    spec = importlib.util.spec_from_file_location("site_submit", SITE / "submit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class IssueBody(unittest.TestCase):
    def test_the_check_of_the_cards_site_accepts_the_body(self):
        c = card()
        body = share.issue_body(c)
        sub = load_submit()
        data = tmpdir(self) / "data"
        changed, msg = sub.handle({"issue": {"body": body, "labels": [{"name": "card"}], "user": {"login": "anna"}}},
                                  data, "https://qiuzan2001.github.io/strata-cards", now=1000.0)
        self.assertTrue(changed, msg)
        self.assertEqual(json.loads((data / f"{c['id']}.json").read_text()), c)
        self.assertIn(f"/c/{c['id']}/", msg)

    def test_the_title(self):
        self.assertEqual(share.title_of(card()), "RTX 5070 Ti · swift-1.5-iq3_s")


class FormLink(unittest.TestCase):
    def test_the_fields_of_the_form(self):
        c = card()
        r = share.form_link(c)
        u = urllib.parse.urlsplit(r["url"])
        self.assertEqual((u.netloc, u.path), ("github.com", "/qiuzan2001/strata-cards/issues/new"))
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(q["template"], ["card.yml"])
        self.assertEqual(q["title"], [share.title_of(c)])
        self.assertEqual(json.loads(q["card"][0]), c)
        self.assertLessEqual(r["length"], share.LINK_MAX)
        tpl = (SITE / ".github" / "ISSUE_TEMPLATE" / "card.yml").read_text()
        self.assertIn(f"id: {share.FORM_FIELD}\n", tpl)                    # the field id of the form

    def test_a_link_longer_than_8000_characters_is_refused(self):
        big = card()
        big["noise"]["labels"] = ["x" * 80] * 120                          # not a valid card: only for the length
        r = share.form_link(big)
        self.assertIsNone(r["url"])
        self.assertGreater(r["length"], share.LINK_MAX)
        self.assertIn("more than 8,000", r["reason"])
        self.assertIn("files on this PC", r["reason"])

    def test_another_repository(self):
        r = share.form_link(card(), "anna/my-cards")
        self.assertTrue(r["url"].startswith("https://github.com/anna/my-cards/issues/new?"))
        self.assertEqual(render.site_url("anna/my-cards"), "https://anna.github.io/my-cards/")


class Gh(unittest.TestCase):
    def test_not_logged_in(self):
        g = share.GhShare(run=FakeGh(logged_in=False))
        st = g.status()
        self.assertFalse(st["ok"])
        self.assertEqual(st["reason"], "gh is not logged in on this PC. Run gh auth login in a terminal, then open "
                                       "this dialog again.")

    def test_the_status_is_cached_for_60_s(self):
        fake, now = FakeGh(), [1000.0]
        g = share.GhShare(run=fake, clock=lambda: now[0])
        g.status(), g.status()
        now[0] += 61
        g.status()
        self.assertEqual(sum(1 for c in fake.calls if c[1:3] == ["auth", "status"]), 2)

    def run_share(self, comments, poll_s=180.0):
        fake, now = FakeGh(comments=comments), [0.0]

        def sleep(s):
            now[0] += s
        g = share.GhShare(run=fake, clock=lambda: now[0], sleep=sleep, poll_s=poll_s)
        c = card()
        g.start(c)
        wait_for(lambda: g.snapshot()["state"] not in ("sending", "waiting"), what="the share")
        return g.snapshot(), fake, c

    def test_the_check_answers_with_the_card_url(self):
        bot = {"user": {"login": "github-actions[bot]"},
               "body": "Thank you. The card: https://qiuzan2001.github.io/strata-cards/c/abcdefghij/ (the site updates in "
                       "about one minute). Check: schema and ranges."}
        snap, fake, c = self.run_share([[], [{"user": {"login": "anna"}, "body": "hello"}], [bot]])
        self.assertEqual(snap["state"], "done")
        self.assertEqual(snap["card_url"], "https://qiuzan2001.github.io/strata-cards/c/abcdefghij/")
        self.assertEqual(snap["issue_url"], ISSUE)
        create = next(x for x in fake.calls if x[1:3] == ["issue", "create"])
        self.assertEqual(create[2:9], ["create", "--repo", "qiuzan2001/strata-cards", "--label", "card", "--title",
                                       share.title_of(c)])
        self.assertEqual(fake.body, share.issue_body(c))

    def test_no_answer_in_3_minutes(self):
        snap, fake, _ = self.run_share([], poll_s=180.0)
        self.assertEqual(snap["state"], "timeout")
        self.assertIn("The card address comes in the issue", snap["message"])
        self.assertEqual(snap["issue_url"], ISSUE)
        self.assertEqual(sum(1 for x in fake.calls if x[1] == "api"), 36)  # every 5 s for 180 s

    def test_the_check_refuses(self):
        bot = {"user": {"login": "github-actions[bot]"}, "body": "This card is on the site already: https://x/c/abcdefghij/"}
        snap, _, _ = self.run_share([[bot]])
        self.assertEqual(snap["state"], "done")
        bot["body"] = "The card is not valid. Nothing was saved."
        snap, _, _ = self.run_share([[bot]])
        self.assertEqual(snap["state"], "refused")


class Routes(unittest.TestCase):
    def studio(self, read_only=False, logged_in=True, cards_repo=None):
        import shutil
        import studio
        tmp = tmpdir(self)
        results = tmp / "results"
        shutil.copytree(ROOT / "tests" / "fixtures" / "demo" / "reports", results)
        fake = FakeStrata(results).start()
        self.addCleanup(fake.stop)
        s = studio.Studio(fake.url, str(results), port=0, state_path=str(tmp / "st.json"), timing=support.fast_timing(),
                          read_only=read_only, host_info=False, cards_repo=cards_repo)
        s.app.gh_share.run = FakeGh(logged_in=logged_in)
        s.start()
        self.addCleanup(s.stop)
        return s

    def test_targets_with_gh_not_logged_in(self):
        s = self.studio(logged_in=False)
        st, _, t = request(s, "GET", f"/api/share/targets?report={DEMO_RUN.name}")
        self.assertEqual(st, 200, t)
        self.assertFalse(t["gh"]["ok"])
        self.assertIn("not logged in", t["gh"]["reason"])
        self.assertTrue(t["link"]["url"])
        st, _, err = request(s, "POST", "/api/share/gh", {"report": DEMO_RUN.name})
        self.assertEqual(st, 412)
        self.assertIn("not logged in", err["error"]["message"])

    def test_the_repository_flag(self):
        s = self.studio(cards_repo="anna/my-cards")
        t = request(s, "GET", f"/api/share/targets?report={DEMO_RUN.name}")[2]
        self.assertEqual((t["repo"], t["site"]), ("anna/my-cards", "https://anna.github.io/my-cards/"))
        st, _, res = request(s, "POST", "/api/card/files", {"report": DEMO_RUN.name})
        self.assertIn("https://anna.github.io/my-cards/", (Path(res["dir"]) / "card.html").read_text())

    def test_a_card_that_changed_is_not_sent(self):
        s = self.studio()
        st, _, err = request(s, "POST", "/api/share/gh", {"report": DEMO_RUN.name, "id": "aaaaaaaaaa"})
        self.assertEqual(st, 409)
        self.assertIn("Nothing was sent", err["error"]["message"])
        self.assertEqual([c for c in s.app.gh_share.run.calls if c[1:3] == ["issue", "create"]], [])

    def test_read_only_sends_nothing(self):
        s = self.studio(read_only=True)
        st, _, _ = request(s, "POST", "/api/share/gh", {"report": DEMO_RUN.name})
        self.assertEqual(st, 403)
        self.assertEqual([c for c in s.app.gh_share.run.calls if c[1:3] == ["issue", "create"]], [])

    def test_the_demo_shares_nothing(self):
        import studio
        s, close = studio.demo_studio(0, timing=support.fast_timing())
        s.start()
        try:
            rid = DEMO_RUN.name
            wait_for(lambda: any(r["id"] == rid for r in request(s, "GET", "/api/reports")[2]["reports"]), what="reports")
            t = request(s, "GET", f"/api/share/targets?report={rid}")[2]
            self.assertEqual((t["gh"]["ok"], t["link"]["url"]), (False, None))
            self.assertEqual(t["gh"]["reason"], share.DEMO_OFF)
            self.assertEqual(request(s, "POST", "/api/share/gh", {"report": rid})[0], 412)
        finally:
            s.stop()
            close()


if __name__ == "__main__":
    unittest.main()
