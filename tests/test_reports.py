import json
import os
import unittest

import support
from studiolib import reports
from studiolib.state import State


def store(d, **kw):
    return reports.ReportStore(d, **kw)


class Listing(unittest.TestCase):
    def test_order_is_date_then_suffix_then_mtime(self):
        d = support.tmpdir(self)
        support.copy_fixtures(d, (support.A, support.B, support.NOCFG, support.BROKEN))
        ids = [r["id"] for r in store(d).scan()["reports"]]
        self.assertEqual(ids, [support.B, support.A, support.BROKEN, support.NOCFG])

    def test_the_mtime_breaks_ties_between_other_slugs_on_one_day(self):
        d = support.tmpdir(self)
        for name, mtime in (("2026-10-15-profile-gpu-a", 1000), ("2026-10-15-profile-gpu-b", 2000)):
            (d / name).mkdir()
            f = d / name / "results.json"
            f.write_text("{}")
            os.utime(f, (mtime, mtime))
        ids = [r["id"] for r in store(d).scan()["reports"]]
        self.assertEqual(ids, ["2026-10-15-profile-gpu-b", "2026-10-15-profile-gpu-a"])

    def test_a_slug_that_ends_in_a_number_is_not_a_suffix_without_its_base(self):
        d = support.tmpdir(self)
        for name in ("2026-10-15-profile-gpu-qwen-3", "2026-10-15-profile-gpu-qwen-3-2"):
            (d / name).mkdir()
            (d / name / "results.json").write_text("{}")
        orders = {r["id"]: r["order"][3] for r in store(d).scan()["reports"]}
        self.assertEqual(orders, {"2026-10-15-profile-gpu-qwen-3": 1, "2026-10-15-profile-gpu-qwen-3-2": 2})

    def test_folders_that_do_not_match_or_lack_results_are_not_listed(self):
        d = support.tmpdir(self)
        support.copy_fixtures(d, (support.A,))
        (d / "2026-10-15-bench-gpu").mkdir()
        (d / "2026-10-15-bench-gpu" / "results.json").write_text("{}")
        (d / "2026-10-16-profile-empty").mkdir()
        (d / "notes.md").write_text("x")
        self.assertEqual([r["id"] for r in store(d).scan()["reports"]], [support.A])

    def test_more_than_500_keeps_the_newest(self):
        d = support.tmpdir(self)
        for i in range(510):
            name = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}-profile-gpu"
            (d / name).mkdir()
            (d / name / "results.json").write_text("{}")
        out = store(d).scan()["reports"]
        self.assertEqual(len(out), 500)
        newest = max((r["id"] for r in out))
        self.assertEqual(out[0]["id"], newest)
        dates = [r["date"] for r in out]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_missing_folder(self):
        out = store(support.tmpdir(self) / "nope").scan()
        self.assertEqual((out["exists"], out["reports"]), (False, []))


class Normalised(unittest.TestCase):
    def setUp(self):
        self.s = store(support.FIXTURES)
        self.rep = self.s.get(support.A)

    def test_numbers_of_the_fixture(self):
        r = self.rep
        chat = next(d for d in r["decode"] if d["kind"] == "chat")
        self.assertEqual(chat["tps"], {"median": 75.7, "min": 73.3, "max": 76.8, "n": 3})
        self.assertEqual(chat["window_ms"]["adapt wait"], 1.4232236842105261)
        self.assertEqual(chat["hit_rate"], 0.708)
        p2k = next(p for p in r["prompts"] if p["label"] == "2K")
        self.assertEqual((p2k["prompt_tps"], p2k["tokens"], p2k["gpu_ms"]), (1118.1, 2023, 1614.0))
        self.assertEqual(list(p2k["groups_ms"])[0], "experts over PCIe")
        self.assertEqual([p["label"] for p in r["prompts"]], ["2K", "16K", "32K (long)"])
        turn4 = next(t for t in r["agent"] if t["turn"] == 4)
        self.assertEqual(turn4["hit_rate"], 0.861)
        self.assertEqual(r["machine"]["gpu"], "NVIDIA GeForce RTX 5070 Ti")
        self.assertEqual(r["params"]["long"], 32768)
        self.assertEqual(len(r["findings"]), 10)
        self.assertNotIn("noise", r)                      # the chats are three different prompts: no noise measure
        self.assertEqual([c["decode_tps"] for c in r["chat_runs"]], [76.8, 75.7, 73.3])

    def test_settings_rows_from_config_args_and_engine_info(self):
        rows = {r["flag"]: r for r in self.rep["settings"]["rows"]}
        self.assertEqual(self.rep["settings"]["source"], "report")
        self.assertEqual(list(rows)[:3], ["--pcie-frac", "--spec-min-p", "--pool-workers"])
        self.assertEqual((rows["--pcie-frac"]["config"], rows["--pcie-frac"]["loaded"]), (None, "0.55"))
        self.assertEqual((rows["--spec-min-p"]["config"], rows["--spec-min-p"]["loaded"]), ("0.5", "0.50"))
        self.assertEqual(rows["--kv-resident"]["config"], "32768")
        self.assertEqual(rows["--expert-cache"]["loaded"], "3880 experts (7586 MiB)")
        self.assertEqual(rows["--spec"]["config"], "4")
        self.assertIsNone(rows["--adapt-every"]["config"])
        self.assertIsNone(rows["--adapt-every"]["loaded"])

    def test_the_second_report_has_the_edits(self):
        rep = self.s.get(support.B)
        self.assertEqual(next(d for d in rep["decode"] if d["kind"] == "chat")["tps"]["median"], 81.9)
        rows = {r["flag"]: r for r in rep["settings"]["rows"]}
        self.assertEqual(rows["--pcie-frac"]["config"], "0.35")

    def test_settings_short(self):
        chips = {r["id"]: r["settings_short"] for r in self.s.scan()["reports"]}
        self.assertEqual(chips[support.A], {"pcie_frac": "0.55", "spec_min_p": "0.50", "pool_workers": "7",
                                            "adapt": "engine default"})
        self.assertEqual(chips[support.B]["pcie_frac"], "0.35")

    def test_adapt_chip_names_the_tier_when_the_config_has_it(self):
        raw = json.loads((support.FIXTURES / support.A / "results.json").read_text())
        raw["machine"]["config"]["args"] += ["--adapt-every", "1", "--adapt-swaps", "160", "--adapt-decay", "0.97"]
        d = support.tmpdir(self)
        (d / support.A).mkdir()
        (d / support.A / "results.json").write_text(json.dumps(raw))
        self.assertEqual(store(d).scan()["reports"][0]["settings_short"]["adapt"], "1/160/0.97")

    def test_broken_json_is_listed_not_ok_and_cannot_be_opened(self):
        listed = {r["id"]: r for r in self.s.scan()["reports"]}
        self.assertFalse(listed[support.BROKEN]["ok"])
        self.assertEqual(listed[support.BROKEN]["error"], "results.json could not be read")
        with self.assertRaises(reports.ReportError) as cm:
            self.s.get(support.BROKEN)
        self.assertTrue(cm.exception.unreadable)

    def test_a_report_with_no_config_falls_back_to_engine_info_then_captured_rows(self):
        rep = self.s.get(support.NOCFG)
        self.assertEqual(rep["settings"]["source"], "report")      # engine_info is still there
        self.assertIsNone(next(r for r in rep["settings"]["rows"] if r["flag"] == "--spec-min-p")["config"])
        raw = json.loads((support.FIXTURES / support.NOCFG / "results.json").read_text())
        del raw["machine"]["engine_info"]
        d = support.tmpdir(self)
        (d / support.NOCFG).mkdir()
        (d / support.NOCFG / "results.json").write_text(json.dumps(raw))
        st = State(d / "state.json")
        s = store(d, ctx=st)
        self.assertEqual(s.get(support.NOCFG)["settings"], {"source": "none", "rows": []})
        st.set_meta(support.NOCFG, {"captured_at": 1.0, "source": "profile/settings",
                                    "rows": [{"flag": "--pcie-frac", "config": "0.35", "loaded": "0.35"}]})
        rep = s.get(support.NOCFG)
        self.assertEqual(rep["settings"]["source"], "captured")
        self.assertEqual(s.scan()["reports"][0]["settings_short"]["pcie_frac"], "0.35")

    def test_missing_keys_are_none_never_an_error(self):
        d = support.tmpdir(self)
        (d / "2026-10-15-profile-empty").mkdir()
        (d / "2026-10-15-profile-empty" / "results.json").write_text("{}")
        rep = store(d).get("2026-10-15-profile-empty")
        self.assertEqual((rep["prompts"], rep["decode"], rep["agent"]), ([], [], []))
        self.assertIsNone(rep["machine"]["gpu"])

    def test_oversize_results_is_not_ok(self):
        d = support.tmpdir(self)
        (d / "2026-10-15-profile-big").mkdir()
        f = d / "2026-10-15-profile-big" / "results.json"
        with open(f, "wb") as fh:
            fh.write(b'{"x": "' + b"a" * (reports.MAX_RESULTS_BYTES + 10) + b'"}')
        self.assertFalse(store(d).scan()["reports"][0]["ok"])

    def test_pool_lists_every_ok_report_with_its_chips_and_order(self):
        d = support.tmpdir(self)
        support.copy_fixtures(d, (support.A, support.B, support.BROKEN))
        pool = store(d).pool()
        self.assertEqual([e["id"] for e in pool], [support.B, support.A])
        self.assertEqual(pool[0]["short"]["pcie_frac"], "0.35")
        self.assertEqual(pool[0]["order"][:4], [2026, 10, 15, 2])
        self.assertEqual(pool[1]["rep"]["id"], support.A)

    def test_cache_follows_the_file(self):
        d = support.tmpdir(self)
        support.copy_fixtures(d, (support.A,))
        s = store(d)
        self.assertEqual(s.scan()["reports"][0]["chat_tps"], 75.7)
        f = d / support.A / "results.json"
        raw = json.loads(f.read_text())
        raw["results"]["decode"]["chat"]["decode_tps"]["median"] = 80.0
        f.write_text(json.dumps(raw))
        self.assertEqual(s.scan()["reports"][0]["chat_tps"], 80.0)


class Paths(unittest.TestCase):
    def test_bad_ids_and_symlinks_are_not_found(self):
        d = support.tmpdir(self)
        support.copy_fixtures(d, (support.A,))
        outside = support.tmpdir(self)
        support.copy_fixtures(outside, (support.B,))
        os.symlink(outside / support.B, d / "2026-10-15-profile-linked")
        os.symlink(outside / support.B / "results.json", d / support.A / "link.json")
        s = store(d)
        for bad in ("../" + support.A, "2026-10-15-profile-linked", "x", "", "2026-10-15-profile-..", support.A + "/",
                    "2026-10-15-profile-nope"):
            with self.assertRaises(reports.ReportError, msg=bad):
                s.get(bad)
        self.assertEqual([r["id"] for r in s.scan()["reports"]], [support.A])

    def test_results_json_that_is_a_symlink_out_of_the_folder_is_refused(self):
        d = support.tmpdir(self)
        (d / "2026-10-15-profile-gpu").mkdir()
        outside = support.tmpdir(self)
        (outside / "r.json").write_text("{}")
        os.symlink(outside / "r.json", d / "2026-10-15-profile-gpu" / "results.json")
        self.assertEqual(store(d).scan()["reports"], [])


if __name__ == "__main__":
    unittest.main()
