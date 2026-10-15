"""What v3 of the page asked of the backend: requests in one shape, the engine log's probe, the hardware route,
the run annotation of live samples, the sign test, the tunes parsed again, and the one config write."""
import hashlib
import json
import os
import unittest

import support
from fakestrata import FakeStrata, request_doc
from studiolib import compare, configwrite, enginelog, requests
from studiolib.reports import ReportStore
from support import ROOT, copy_fixtures, request, start_studio, tmpdir, wait_for

A = support.A
REAL_PROBE = ("strata serve: PCIe share: the CPU pool takes 96 us per missed expert (21.3 GB/s of expert bytes), the "
              "link 28.0 GB/s -> pcie_frac 0.40 (was 0.55; --pcie-frac N fixes it, STRATA_PCIE_FRAC_DEFAULT=old keeps "
              "the start-up rule)")
REAL_VRAM = ("strata serve: WARNING: only 312 MiB of VRAM free at the start of this request (below 512 MiB): when the "
             "card is this full the driver can page GPU memory to system RAM")


class Requests(unittest.TestCase):
    def setUp(self):
        self.rep = ReportStore(support.FIXTURES).get(A)

    def test_a_report_has_its_twelve_requests_in_the_tool_s_order(self):
        ids = [q["id"] for q in self.rep["requests"]]
        self.assertEqual(ids, ["chat-1", "chat-2", "chat-3", "read-2k", "read-16k", "read-32k-long",
                               "agent-0", "agent-1", "agent-2", "agent-3", "agent-4", "agent-5"])
        for q in self.rep["requests"]:
            self.assertTrue(set(requests.REQUEST_KEYS) <= set(q), q["id"])

    def test_an_agent_turn_carries_every_engine_fact(self):
        q = next(x for x in self.rep["requests"] if x["id"] == "agent-1")
        self.assertEqual((q["prompt_tokens"], q["reused"], q["generated"], q["decode_tok_s"]), (3744, 623, 64, 65.4))
        self.assertEqual(q["kind"], "follow-up")
        self.assertEqual(len(q["phases"]), 20)
        g = q["prompt_groups_ms"]                           # the tool's grouping of the phases
        self.assertEqual(g["experts over PCIe"], 174.0 + 9.0 + 12.0)
        self.assertEqual(g["expert GEMM"], 225.0 + 133.0 + 207.0)
        self.assertEqual(q["prompt_top"][0], ["dequant", 323.0])
        self.assertEqual(q["windows"], 26)
        self.assertEqual(q["kv_block_reads"], 585396)       # the request's own KV reads, not the engine's total
        self.assertAlmostEqual(sum(q["window_ms"].values()), q["ms_per_window"], places=3)

    def test_kinds(self):
        self.assertEqual(requests.kind_of(44, 0, 128), "short")
        self.assertEqual(requests.kind_of(3755, 633, 64), "follow-up")
        self.assertEqual(requests.kind_of(16058, 0, 24), "long prompt")
        self.assertEqual(requests.kind_of(906, 0, 1), "prompt only")

    def test_profile_last_and_history_give_the_same_shape(self):
        a = requests.from_last(request_doc())
        self.assertEqual(set(a), set(requests.REQUEST_KEYS))
        self.assertEqual(a["prompt_top"], [["wait copy", 680.0]])
        self.assertEqual(a["window_ms"]["adapt wait"], 1.44)
        b = requests.from_history({"prompt_tokens": 10, "ms": {"GPU": 1.0}, "prompt_top": "GDN"})
        self.assertEqual(set(b), set(requests.REQUEST_KEYS))
        self.assertEqual(b["prompt_top"], [["GDN", None]])


class EngineLog(unittest.TestCase):
    def test_parse_the_probe_and_the_vram_warning(self):
        d = enginelog.parse(["noise", REAL_PROBE, REAL_VRAM])
        self.assertEqual(d["probe"], {"us_per_expert": 96.0, "cpu_gbs": 21.3, "link_gbs": 28.0, "pcie_frac": "0.40",
                                      "was": "0.55"})
        self.assertEqual(d["vram_warning"], {"free_mib": 312, "below_mib": 512})

    def test_the_tail_only_and_the_last_probe_is_kept(self):
        d = tmpdir(self)
        log = d / "strata.log"
        log.write_text(REAL_PROBE + "\n" + "x" * 100 + "\n")
        el = enginelog.EngineLog(log)
        self.assertEqual(el.facts()["probe"]["link_gbs"], 28.0)
        with open(log, "a") as f:                           # the probe line leaves the tail
            f.write(("y" * 200 + "\n") * (enginelog.TAIL_BYTES // 200 + 10))
        facts = el.facts()
        self.assertTrue(facts["readable"])
        self.assertEqual(facts["probe"]["link_gbs"], 28.0)
        self.assertFalse(enginelog.EngineLog(d / "missing.log").facts()["readable"])
        self.assertEqual(enginelog.EngineLog(None).facts()["path"], None)


class SignTest(unittest.TestCase):
    def test_exact_two_sided(self):
        self.assertEqual(compare.sign_test(0, 6), 1 / 32)
        self.assertAlmostEqual(compare.sign_test(1, 5), 14 / 64)
        self.assertEqual(compare.sign_test(3, 3), 1.0)
        self.assertIsNone(compare.sign_test(0, 0))

    def test_the_comparison_carries_it_and_each_floor_says_if_assumed(self):
        store = ReportStore(support.FIXTURES)
        c = compare.compute(store.get(A), store.get(support.B))
        self.assertEqual(set(c["sign"]), {"turns", "faster", "slower", "p", "min_turns"})
        self.assertTrue(all("floor_assumed" in r for r in c["rows"]))


def write_config(path, args, env=None, indent=2):
    cfg = {"exe": "strata", "args": args, "model_name": "m", "port": 8080, "profile": True}
    if env is not None:
        cfg["env"] = env
    path.write_text(json.dumps(cfg, indent=indent) + "\n")
    return path


class ConfigWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        self.rep = ReportStore(support.FIXTURES).get(A)        # its config: --spec-min-p, --adapt-*, ... as recorded
        self.runs = {r["flag"]: r["config"] for r in self.rep["settings"]["rows"]}

    def test_preview_lists_what_differs(self):
        cfg = write_config(self.tmp / "s.json", ["--pack", "p", "--spec-min-p", "0.9", "--pcie-frac", "0.1"])
        pv = configwrite.preview(cfg, self.rep)
        rows = {r["flag"]: r for r in pv["rows"]}
        self.assertEqual(pv["sha"], hashlib.sha256(cfg.read_bytes()).hexdigest())
        self.assertEqual(rows["--spec-min-p"]["file"], "0.9")
        self.assertEqual(rows["--spec-min-p"]["run"], self.runs["--spec-min-p"])
        self.assertTrue(rows["--spec-min-p"]["changed"] and rows["--spec-min-p"]["calibrated"])

    def test_apply_writes_the_ticked_flags_and_a_bak(self):
        cfg = write_config(self.tmp / "s.json", ["--pack", "p", "--spec-min-p", "0.9", "--pcie-frac", "0.1"],
                           env={"KEEP": "1"}, indent=4)
        old = cfg.read_text()
        pv = configwrite.preview(cfg, self.rep)
        flags = [r["flag"] for r in pv["rows"] if r["changed"]]
        res = configwrite.apply(cfg, self.rep, flags, pv["sha"])
        self.assertEqual((self.tmp / "s.json.bak").read_text(), old)
        new = json.loads(cfg.read_text())
        self.assertEqual(new["exe"], "strata")
        self.assertEqual(new["env"]["KEEP"], "1")
        self.assertEqual(new["args"][:2], ["--pack", "p"])         # arguments that are no setting stay first
        for c in res["changes"]:
            got = new["args"][new["args"].index(c["flag"]) + 1] if c["flag"] in new["args"] else None
            self.assertEqual(got, c["to"], c["flag"])
        if self.runs["--pcie-frac"] is None:                      # the run had the engine default: removed
            self.assertNotIn("--pcie-frac", new["args"])
        self.assertIn('\n    "exe"', cfg.read_text())             # the file's indentation is kept
        self.assertFalse((self.tmp / "s.json.studio-tmp").exists())

    def test_apply_refuses_a_changed_file_a_busy_strata_and_unknown_flags(self):
        cfg = write_config(self.tmp / "s.json", ["--spec-min-p", "0.9"])
        pv = configwrite.preview(cfg, self.rep)
        with self.assertRaises(configwrite.ConfigWriteError) as e:
            configwrite.apply(cfg, self.rep, ["--spec-min-p"], "0" * 64)
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(configwrite.ConfigWriteError) as e:
            configwrite.apply(cfg, self.rep, ["--spec-min-p"], pv["sha"], busy_reason="Strata is tuning.")
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(configwrite.ConfigWriteError) as e:
            configwrite.apply(cfg, self.rep, ["--port"], pv["sha"])
        self.assertEqual(e.exception.status, 400)
        self.assertFalse((self.tmp / "s.json.bak").exists())     # nothing was written

    def test_no_config_or_a_bad_one(self):
        with self.assertRaises(configwrite.ConfigWriteError) as e:
            configwrite.preview(None, self.rep)
        self.assertEqual(e.exception.status, 412)
        bad = self.tmp / "bad.json"
        bad.write_text('{"args": "not a list"}')
        with self.assertRaises(configwrite.ConfigWriteError):
            configwrite.preview(bad, self.rep)


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        self.results = copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)

    def test_hardware(self):
        self.fake.hardware = {"gpu_util": 41, "cpu": 38.0, "ram_used": 1, "ram_total": 2}
        self.fake.hardware_static = {"cpu_name": "CPU", "threads": 16}
        self.fake.hw_history = {"cpu": [1, 2, 3]}
        log = self.tmp / "e.log"
        log.write_text(REAL_PROBE + "\n")
        import studio
        s = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "st.json"),
                          timing=support.fast_timing(), engine_log=str(log))
        s.start()
        self.addCleanup(s.stop)
        wait_for(lambda: s.poller.hardware.get("now"), what="a hardware reading")
        _, _, doc = request(s, "GET", "/api/hardware")
        self.assertEqual(set(doc), {"at", "now", "static", "history", "live", "engine", "log", "window"})
        self.assertEqual(doc["now"]["gpu_util"], 41)
        self.assertEqual(doc["history"]["cpu"], [1, 2, 3])
        self.assertEqual(doc["log"]["probe"]["link_gbs"], 28.0)
        self.assertEqual(doc["engine"]["pool_workers"], 7)
        wait_for(lambda: request(s, "GET", "/api/hardware")[2]["window"], what="the last window")

    def test_live_samples_name_their_report_request(self):
        rep = ReportStore(self.results).get(A)
        q = next(x for x in rep["requests"] if x["id"] == "agent-2")
        doc = request_doc(prompt_tokens=q["prompt_tokens"], generated=q["generated"], prompt_ms=q["prompt_ms"],
                          decode_ms=q["decode_ms"])
        doc["summary"].update(reused=q["reused"], decode_tok_s=q["decode_tok_s"])
        self.fake.set_last(doc)
        s = start_studio(self, self.fake, self.results)
        wait_for(lambda: s.poller.ring, what="a sample")
        _, _, live = request(s, "GET", "/api/live?since=0")
        x = live["samples"][-1]
        rr = x["report_request"]                         # fixture B is a copy of A: either is that request
        self.assertIn(rr["report"], (A, support.B))
        self.assertEqual((rr["id"], rr["label"]), ("agent-2", "agent turn 2"))
        self.assertEqual(x["run"]["report"], rr["report"])
        self.assertTrue(x["late"])                       # the first answer is from before the Studio started
        self.assertTrue(all({"from", "to", "kind"} <= set(sp) for sp in live["spans"]))

    def test_settings_routes_and_read_only(self):
        cfg = write_config(self.tmp / "strata-x.json", ["--spec-min-p", "0.9"])
        s = start_studio(self, self.fake, self.results, config=str(cfg))
        st, _, pv = request(s, "GET", f"/api/settings/apply-preview?report={A}")
        self.assertEqual(st, 200)
        self.assertIsNone(pv["busy"])
        st, _, err = request(s, "POST", "/api/settings/apply", body={"report": A, "flags": ["--spec-min-p"], "sha": pv["sha"]})
        self.assertEqual(st, 400)                        # no confirm: nothing written
        self.assertFalse(cfg.with_name(cfg.name + ".bak").exists())
        st, _, res = request(s, "POST", "/api/settings/apply",
                             body={"report": A, "flags": ["--spec-min-p"], "sha": pv["sha"], "confirm": True})
        self.assertEqual(st, 200, res)
        self.assertTrue(cfg.with_name(cfg.name + ".bak").exists())
        self.assertEqual(request(s, "GET", "/api/settings/apply-preview?report=nope")[0], 404)
        import studio
        ro = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "ro.json"),
                           timing=support.fast_timing(), config=str(cfg), read_only=True)
        ro.start()
        self.addCleanup(ro.stop)
        self.assertTrue(request(ro, "GET", "/api/state")[2]["studio"]["read_only"])
        for path in ("/api/settings/apply", "/api/run-profile", "/api/tune-flow", "/api/notes"):
            self.assertEqual(request(ro, "POST", path, body={})[0], 403, path)

    def test_preview_without_config(self):
        s = start_studio(self, self.fake, self.results)
        self.assertEqual(request(s, "GET", f"/api/settings/apply-preview?report={A}")[0], 412)

    def test_tunes_are_parsed_again_with_what_ran_before(self):
        real = json.loads((ROOT / "tests" / "fixtures" / "real_tune.json").read_text())
        from studiolib.state import State
        st = State(self.tmp / "t.json")
        st.save_tune({"id": "tune-1", "started_at": 1.0, "outcome": "succeeded", "lines_tail": real["lines"],
                      "candidates": {"steps": []},
                      "settings": {"source": "tune.report", "rows": [
                          {"flag": "--pcie-frac", "before": "0.35", "after": None, "changed": True},
                          {"flag": "--spec-min-p", "before": "0.70", "after": "0.5", "changed": True}]}})
        st.write(force=True)
        s = start_studio(self, self.fake, self.results, state_path=self.tmp / "t.json")
        _, _, doc = request(s, "GET", "/api/tunes")
        steps = {x["key"]: x for x in doc["tunes"][0]["candidates"]["steps"]}
        self.assertEqual(steps["pcie_frac"]["was"], "0.35")
        self.assertTrue(steps["pcie_frac"]["values"])


class EngineLogDefault(unittest.TestCase):
    def test_the_config_s_log_key_or_the_name_next_to_it(self):
        import studio
        d = tmpdir(self)
        cfg = write_config(d / "strata-a.json", [])
        self.assertEqual(studio.default_engine_log(str(cfg)), str(d / "strata-a.log"))
        data = json.loads(cfg.read_text())
        data["log"] = "logs/engine.log"
        cfg.write_text(json.dumps(data))
        self.assertEqual(studio.default_engine_log(str(cfg)), str(d / "logs" / "engine.log"))
        self.assertIsNone(studio.default_engine_log(None))


if __name__ == "__main__":
    unittest.main()
