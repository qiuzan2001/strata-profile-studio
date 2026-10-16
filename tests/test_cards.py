"""Result cards: the id, the validator, the exclusion list on real reports, `studio.py --card`, the Share route, and
the cards site (build.py, submit.py).

The exclusion and --card tests read the nine real reports in ~/Strata/bench/results-archive-2026-10-15-round1 when
they are on this PC (read only), and skip otherwise.  The other tests use tests/fixtures/demo.
"""
import copy
import datetime as dt
import getpass
import json
import re
import socket
import tempfile
import unittest
from pathlib import Path

import support
from support import ROOT, fast_timing, request, tmpdir, wait_for

import studio
from cards import render, validate
from studiolib import cardmake

ARCHIVE = Path.home() / "Strata" / "bench" / "results-archive-2026-10-15-round1"
DEMO = ROOT / "tests" / "fixtures" / "demo"
SITE = ROOT / "cards" / "site"
DEMO_RUN = DEMO / "reports" / "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s-6"
FACTS = {"expert_read_gbs": 25.4, "link_gbs": 28.0, "pcie_gen": 4, "pcie_width": 16, "power_limit_w": 300}


def archived():
    return sorted(d for d in ARCHIVE.iterdir() if (d / "results.json").is_file()) if ARCHIVE.is_dir() else []


def base_card():
    return cardmake.card_of_path(DEMO_RUN, facts=FACTS)


class CardId(unittest.TestCase):
    def test_the_id_is_stable(self):
        a, b = base_card(), base_card()
        self.assertEqual(a["id"], b["id"])
        self.assertRegex(a["id"], r"^[a-z2-7]{10}$")
        self.assertEqual(validate.card_id(a), a["id"])

    def test_the_name_does_not_change_the_id_but_the_data_does(self):
        a = base_card()
        named = cardmake.card_of_path(DEMO_RUN, facts=FACTS, name="my pc")
        self.assertEqual((named["id"], named["name"]), (a["id"], "my pc"))
        c = copy.deepcopy(a)
        c["decode"][0]["tps"] += 0.1
        self.assertNotEqual(validate.card_id(c), a["id"])

    def test_the_name_rule(self):
        for bad in ("thirteen chars", "a/b", "x@y", "<b>"):
            with self.assertRaises(cardmake.CardError):
                cardmake.card_of_path(DEMO_RUN, facts=FACTS, name=bad)


class Validator(unittest.TestCase):
    """Each out-of-range example of section 5 of the design is refused."""

    def setUp(self):
        self.card = base_card()

    def refused(self, change, word=None, today=None):
        c = copy.deepcopy(self.card)
        change(c)
        c["id"] = validate.card_id(c)
        errs = validate.validate(c, today=today)
        self.assertTrue(errs, f"not refused: {change}")
        if word:
            self.assertTrue(any(word in e for e in errs), errs)

    def test_the_base_card_is_valid(self):
        self.assertEqual(validate.validate(self.card), [])

    def test_decode_and_prompt_read_ranges(self):
        self.refused(lambda c: c["decode"][0].update(tps=0.05, min=0.05), "decode[0].tps")
        self.refused(lambda c: c["decode"][0].update(tps=1001.0, max=1001.0), "decode[0].tps")
        self.refused(lambda c: c["prefill"][0].update(tps=0.5), "prefill[0].tps")
        self.refused(lambda c: c["prefill"][0].update(tps=200001.0), "prefill[0].tps")
        self.refused(lambda c: c["decode"][0].update(tps=c["decode"][0]["max"] + 5), "not between min and max")

    def test_window_parts(self):
        self.refused(lambda c: c["decode"][0]["window_ms"].update(GPU=10001.0), "window_ms.GPU")
        self.refused(lambda c: c["decode"][0]["window_ms"].update(GPU=c["decode"][0]["window_ms"]["GPU"] + 5), "add up")

    def test_pc_ranges(self):
        self.refused(lambda c: c["pc"]["gpu"].update(vram_mib=512), "vram_mib")             # below 1 GiB
        self.refused(lambda c: c["pc"]["gpu"].update(vram_mib=2 * 1048576), "vram_mib")     # above 1,024 GiB
        self.refused(lambda c: c["pc"]["ram"].update(gib=9000.0), "ram.gib")                 # above 8,192 GiB
        self.refused(lambda c: c["pc"]["pcie"].update(gen=8), "pcie.gen")
        self.refused(lambda c: c["pc"]["pcie"].update(width=3), "pcie.width")
        self.refused(lambda c: c["pc"]["pcie"].update(measured_gbs=40.0), "nominal")         # Gen 4 x16: 31.5 GB/s

    def test_floors_hit_rate_date_strings_size(self):
        self.refused(lambda c: c["decode"][0].update(floor=1.5), "floor")
        self.refused(lambda c: c["decode"][0].update(hit_rate=1.2), "hit_rate")
        self.refused(lambda c: c.update(date="2026-10-18"), "future", today=dt.date(2026, 10, 15))
        self.refused(lambda c: c["pc"]["cpu"].update(name="x" * 81), "longer than 80")
        self.refused(lambda c: c["settings"].extend([{"flag": "--spec", "value": "4" * 40, "default": "d" * 60, "calibrated": False}] * 40))
        big = copy.deepcopy(self.card)
        big["noise"]["labels"] = ["x" * 80] * 300
        self.assertTrue(any("bytes" in e for e in validate.validate(big)))

    def test_no_paths_urls_or_extra_fields(self):
        self.refused(lambda c: c["pc"]["cpu"].update(name="/home/anna/cpu"), "cpu.name")
        self.refused(lambda c: c["pc"]["cpu"].update(name="C:\\Users\\anna"), "cpu.name")
        self.refused(lambda c: c.update(name="http://x.y"), "name")
        self.refused(lambda c: c.update(hostname="anna-pc"), "not a field")
        self.refused(lambda c: c.pop("decode"), "missing")

    def test_a_wrong_id(self):
        c = copy.deepcopy(self.card)
        c["id"] = "aaaaaaaaaa"
        self.assertTrue(any("card.id" in e for e in validate.validate(c)))


@unittest.skipUnless(archived(), "the nine archived reports are not on this PC")
class Exclusions(unittest.TestCase):
    """No hostname, Tailscale name, IP address, user name, path, API key, start command or prompt text in card.json
    or card.html, for each of the nine archived reports."""

    def test_nine_reports(self):
        host, user = socket.gethostname().lower(), getpass.getuser().lower()
        ip = re.compile(r"\b(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|127\.0\.0\.1)\b")
        self.assertEqual(len(archived()), 9)
        for d in archived():
            raw = json.loads((d / "results.json").read_text(encoding="utf-8"))
            texts = []
            def walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k == "text" and isinstance(v, str):
                            texts.append(v.strip()[:40])
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(raw.get("results") or {})
            self.assertTrue(texts, d.name)
            launch = (raw.get("machine") or {}).get("launch") or ""
            card = cardmake.card_of_path(d, facts=FACTS)
            for name, text in (("card.json", json.dumps(card, ensure_ascii=False)), ("card.html", render.card_html(card)),
                               ("card.svg", render.card_svg(card))):
                low = text.lower()
                where = f"{d.name} {name}"
                self.assertNotIn(host, low, where)
                self.assertNotIn(user, low, where)
                self.assertNotIn("/home/", low, where)
                self.assertNotIn("\\users\\", low, where)
                self.assertNotIn(".ts.net", low, where)
                self.assertNotIn("api_key", low, where)
                self.assertNotIn("strata_key", low, where)
                self.assertIsNone(ip.search(text), where)
                self.assertNotIn("--native", text, where)
                self.assertNotIn(".gguf", low, where)
                self.assertNotIn(launch[:40], text, where)
                for t in texts:
                    if len(t) >= 20:
                        self.assertNotIn(t, text, where)


@unittest.skipUnless(archived(), "the nine archived reports are not on this PC")
class CardCommand(unittest.TestCase):
    def test_card_on_all_nine_reports(self):
        out = tmpdir(self)
        ids = set()
        for d in archived():
            o = out / d.name
            code = studio.main(["--card", str(d), "--out", str(o), "--host-info", "off", "--state", str(out / "none.json")])
            self.assertEqual(code, 0, d.name)
            for f in ("card.html", "card.svg", "card.json"):
                self.assertTrue((o / f).is_file(), f"{d.name} {f}")
            card = json.loads((o / "card.json").read_text(encoding="utf-8"))
            self.assertEqual(validate.validate(card), [], d.name)
            self.assertNotIn("disk", card["pc"])
            ids.add(card["id"])
            if render.renderer()["kind"]:
                self.assertTrue((o / "card.png").is_file(), d.name)
        self.assertEqual(len(ids), 9)

    def test_results_json_and_a_name(self):
        d = archived()[-1]
        o = tmpdir(self) / "c"
        self.assertEqual(studio.main(["--card", str(d / "results.json"), "--out", str(o), "--name", "trout", "--host-info", "off"]), 0)
        self.assertEqual(json.loads((o / "card.json").read_text())["name"], "trout")

    def test_not_a_report(self):
        self.assertEqual(studio.main(["--card", str(tmpdir(self)), "--host-info", "off"]), 1)


class ShareRoute(unittest.TestCase):
    """The Share dialog's routes: the card, and destination 3 (the files on this PC).  Nothing leaves the PC."""

    def setUp(self):
        self.tmp = tmpdir(self)
        self.results = self.tmp / "results"
        import shutil
        shutil.copytree(DEMO / "reports", self.results)

    def studio(self, read_only=False):
        from fakestrata import FakeStrata
        fake = FakeStrata(self.results)
        fake.start()
        self.addCleanup(fake.stop)
        s = studio.Studio(fake.url, str(self.results), port=0, state_path=str(self.tmp / "st.json"), timing=fast_timing(),
                          read_only=read_only, host_info=False)
        s.start()
        self.addCleanup(s.stop)
        return s

    def test_card_and_files(self):
        s = self.studio()
        rid = DEMO_RUN.name
        st, _, doc = request(s, "GET", f"/api/card?report={rid}&name=demo")
        self.assertEqual(st, 200, doc)
        self.assertEqual(doc["card"]["name"], "demo")
        self.assertEqual(validate.validate(doc["card"]), [])
        self.assertIn("the prompts and the answers", doc["excluded"])
        self.assertIn(doc["image"]["type"], ("png", "svg"))
        st, _, res = request(s, "POST", "/api/card/files", {"report": rid, "name": "demo"})
        self.assertEqual(st, 200, res)
        d = Path(res["dir"])
        self.assertEqual(d, (self.tmp / "card-files" / doc["card"]["id"]).resolve())
        for f in ("card.html", "card.svg", "card.json"):
            self.assertTrue((d / f).is_file())
        st, _, err = request(s, "POST", "/api/card/files", {"report": rid, "name": "a/b"})
        self.assertEqual(st, 400)
        st, _, err = request(s, "GET", "/api/card?report=nope")
        self.assertEqual(st, 400)

    def test_read_only_writes_no_card(self):
        s = self.studio(read_only=True)
        st, _, _ = request(s, "POST", "/api/card/files", {"report": DEMO_RUN.name})
        self.assertEqual(st, 403)
        self.assertFalse((self.tmp / "card-files").exists())


class Site(unittest.TestCase):
    def test_build_on_the_nine_seed_cards(self):
        sys_path = str(SITE)
        import importlib.util
        spec = importlib.util.spec_from_file_location("site_build", SITE / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        out = tmpdir(self) / "_site"
        r = build.build(SITE / "data", out, "https://example.github.io/strata-cards", png=False)
        self.assertEqual((r["cards"], r["invalid"]), (9, []))
        self.assertTrue((out / "index.html").is_file() and (out / "compare.html").is_file() and (out / "site.js").is_file())
        idx = json.loads((out / "data" / "index.json").read_text())
        self.assertEqual(len(idx["entries"]), r["entries"])
        self.assertEqual(sum(len(e["runs"]) for e in idx["entries"]), 9)
        pages = sorted(p.parent.name for p in (out / "c").glob("*/index.html"))
        self.assertEqual(len(pages), 9)                                  # entries and their aliases
        e = idx["entries"][0]["id"]
        page = (out / "c" / e / "index.html").read_text()
        self.assertIn(f"https://example.github.io/strata-cards/c/{e}/card.svg", page)  # og:image (no PNG here)
        self.assertIn("checked: schema and ranges", page)
        for f in (SITE / "data").glob("*.json"):
            c = json.loads(f.read_text())
            self.assertNotIn("disk", c["pc"])
            self.assertEqual(c["name"], "")
        html = (out / "index.html").read_text() + (out / "site.js").read_text()
        self.assertFalse("innerHTML" in html, "innerHTML in the site")
        self.assertFalse("?</h" in html, "a question heading")

    def test_submit_add_limit_duplicate_remove(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("site_submit", SITE / "submit.py")
        sub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sub)
        data = tmpdir(self) / "data"
        card = base_card()
        body = f"### Card JSON\n\n```json\n{json.dumps(card, indent=1)}\n```\n"
        ev = lambda body, label="card", login="anna": {"issue": {"body": body, "labels": [{"name": label}], "user": {"login": login}}}
        changed, msg = sub.handle(ev(body), data, "https://x.github.io/strata-cards", now=1000.0)
        self.assertTrue(changed, msg)
        self.assertTrue((data / f"{card['id']}.json").is_file())
        self.assertIn(f"/c/{card['id']}/", msg)
        self.assertNotIn("anna", (data / "submitters.json").read_text())
        changed, msg = sub.handle(ev(body), data, "https://x.github.io/strata-cards", now=1001.0)
        self.assertFalse(changed)
        self.assertIn("already", msg)
        bad = copy.deepcopy(card)
        bad["decode"][0]["tps"] = 5000
        changed, msg = sub.handle(ev(f"```json\n{json.dumps(bad)}\n```"), data, "u", now=1002.0)
        self.assertFalse(changed)
        self.assertIn("not valid", msg)
        changed, msg = sub.handle(ev("no json here"), data, "u", now=1003.0)
        self.assertFalse(changed)
        for i in range(9):                                               # 1 + 9 = 10 in 24 hours
            c = copy.deepcopy(card)
            c["decode"][0]["min"] = round(c["decode"][0]["min"] - 0.1 * (i + 1), 1)
            c["id"] = validate.card_id(c)
            self.assertTrue(sub.handle(ev(f"```json\n{json.dumps(c)}\n```"), data, "u", now=1010.0 + i)[0])
        c = copy.deepcopy(card)
        c["decode"][0]["min"] = round(c["decode"][0]["min"] - 2, 1)
        c["id"] = validate.card_id(c)
        changed, msg = sub.handle(ev(f"```json\n{json.dumps(c)}\n```"), data, "u", now=1100.0)
        self.assertFalse(changed)
        self.assertIn("limit", msg)
        self.assertTrue(sub.handle(ev(f"```json\n{json.dumps(c)}\n```", login="bob"), data, "u", now=1100.0)[0])
        changed, msg = sub.handle(ev(f"Remove {card['id']}", "remove", "bob"), data, "u")
        self.assertFalse(changed)                                       # bob did not share it
        changed, msg = sub.handle(ev(f"Remove {card['id']}", "remove", "anna"), data, "u")
        self.assertTrue(changed, msg)
        self.assertFalse((data / f"{card['id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
