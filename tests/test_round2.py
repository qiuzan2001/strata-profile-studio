"""Round 2 of v3: the PC's own readings (CPU per core, nvidia-smi), the expert profiles, the profile run with the
engine defaults, hand edits of the run config and the routes that serve them.  Fixtures in tests/fixtures/host are
real lines and files from the development PC (RTX 5070 Ti, Ryzen 7 5800X3D)."""
import json
import os
import struct
import sys
import time
import unittest
from pathlib import Path

import support
from fakestrata import FakeStrata
from studiolib import configwrite, defaultsrun, enginelog, experts, hostinfo
from studiolib.reports import ReportStore
from studiolib.settings import CONTROLS, check_value
from support import ROOT, copy_fixtures, request, start_studio, tmpdir, wait_for

HOST = ROOT / "tests" / "fixtures" / "host"


class Gpu(unittest.TestCase):
    def test_a_real_nvidia_smi_line(self):
        d = hostinfo.parse_gpu_line((HOST / "nvidia_smi.csv").read_text().strip())
        self.assertEqual((d["sm_max_mhz"], d["mem_max_mhz"], d["power_limit_w"], d["pstate"]), (3120.0, 14001.0, 300.0, "P8"))
        self.assertEqual((d["pcie_gen"], d["pcie_gen_max"], d["pcie_width"], d["mem_total_mib"]), (1.0, 4.0, 16.0, 16303.0))
        self.assertEqual((d["reasons"], d["reason_words"]), (0, []))

    def test_throttle_reasons_and_bad_fields(self):
        self.assertEqual(hostinfo.decode_reasons(0x1 | 0x4 | 0x40), ["idle", "power cap", "thermal (hardware)"])
        line = "210, 405, 3120, 14001, [N/A], 55, 26.27, 300.00, 37, [Not Supported], P8, 1, 4, 16, 15548, 16303, 0x0000000000000024"
        d = hostinfo.parse_gpu_line(line)
        self.assertIsNone(d["util"])
        self.assertIsNone(d["fan_pct"])
        self.assertEqual(d["reason_words"], ["power cap", "thermal (software)"])
        self.assertIsNone(hostinfo.parse_gpu_line("1, 2, 3"))

    def test_no_nvidia_smi(self):
        g = hostinfo.GpuSampler(cmd=["/nonexistent/nvidia-smi"])
        self.assertIsNone(g.sample())
        self.assertTrue(g.missing)
        self.assertIn("not installed", g.error)

    def test_a_command_that_prints_the_line(self):
        g = hostinfo.GpuSampler(cmd=[sys.executable, "-c", f"print(open({str(HOST / 'nvidia_smi.csv')!r}).read())"])
        self.assertEqual(g.sample()["sm_max_mhz"], 3120.0)


class Cpu(unittest.TestCase):
    def test_busy_from_two_real_proc_stat_reads(self):
        a = hostinfo.parse_proc_stat((HOST / "proc_stat_a").read_text())
        b = hostinfo.parse_proc_stat((HOST / "proc_stat_b").read_text())
        self.assertEqual(len([k for k in a if k != "cpu"]), 16)
        busy = hostinfo.busy_pct(a, b)
        self.assertTrue(all(v is None or 0 <= v <= 100 for v in busy.values()))
        self.assertIsNotNone(busy["cpu"])
        self.assertTrue(all(v is None for v in hostinfo.busy_pct({}, b).values()))

    def test_sampler_on_a_fake_sys_tree(self):
        d = tmpdir(self)
        stat = d / "stat"
        stat.write_text((HOST / "proc_stat_a").read_text())
        for i in range(16):
            (d / "cpu" / f"cpu{i}" / "cpufreq").mkdir(parents=True)
            (d / "cpu" / f"cpu{i}" / "cpufreq" / "scaling_cur_freq").write_text(f"{3400000 + i * 1000}\n")
        (d / "hwmon" / "hwmon0").mkdir(parents=True)
        (d / "hwmon" / "hwmon0" / "name").write_text("nvme\n")
        (d / "hwmon" / "hwmon1").mkdir(parents=True)
        (d / "hwmon" / "hwmon1" / "name").write_text("k10temp\n")
        (d / "hwmon" / "hwmon1" / "temp1_input").write_text("43375\n")
        rapl = d / "energy_uj"
        rapl.write_text("1000000\n")
        rapl.chmod(0)
        t = [100.0]
        s = hostinfo.CpuSampler(proc_stat=stat, cpu_root=d / "cpu", hwmon_root=d / "hwmon", rapl_pkg=rapl,
                                rapl_core=d / "missing", clock=lambda: t[0])
        first = s.sample()
        stat.write_text((HOST / "proc_stat_b").read_text())
        t[0] = 101.0
        second = s.sample()
        self.assertIsNone(first["busy"])
        self.assertIsNotNone(second["busy"])
        self.assertEqual(second["temp_c"], 43.4)
        self.assertEqual(second["cores"][3]["mhz"], 3403)
        if os.geteuid() != 0:                                        # root reads a 000 file: the hint is moot
            self.assertIsNone(second["power_w"])
            self.assertIn("sudo chmod a+r", second["power_hint"])
        rapl.chmod(0o600)

    def test_rapl_power_from_two_reads(self):
        d = tmpdir(self)
        rapl = d / "energy_uj"
        rapl.write_text("1000000\n")
        t = [0.0]
        s = hostinfo.CpuSampler(proc_stat=d / "none", cpu_root=d, hwmon_root=d, rapl_pkg=rapl, rapl_core=d / "x", clock=lambda: t[0])
        s.sample()
        rapl.write_text("46000000\n")                               # 45 J in 1 s
        t[0] = 1.0
        self.assertEqual(s.sample()["power_w"], 45.0)

    def test_the_monitor_keeps_history(self):
        class C:
            def sample(self):
                return {"busy": 10.0, "cores": [{"id": 0, "busy": 5.0, "mhz": 3400}], "temp_c": 40.0, "power_w": None, "power_hint": None}

        class G:
            error = None

            def sample(self):
                return {"util": 3.0, "sm_mhz": 210.0}
        m = hostinfo.HostMonitor(cpu=C(), gpu=G(), every=0.01)
        m.step()
        m.step()
        doc = m.doc(pool_cpus=[1, 2], host_cpu=0)
        m.stop()
        self.assertEqual(doc["history"]["cpu"]["busy"][-2:], [10.0, 10.0])
        self.assertEqual(doc["core_history"]["0"][-1], 5.0)
        self.assertEqual((doc["pool_cpus"], doc["host_cpu"]), ([1, 2], 0))


class Pool(unittest.TestCase):
    def test_the_engine_s_pinning_line(self):
        d = enginelog.parse((HOST / "engine_pool.log").read_text().splitlines())
        self.assertEqual(d["pool"], {"workers": 7, "cpus": [1, 2, 3, 4, 5, 6, 7], "host_cpu": 0})


def write_profile(path, layers, n_exp, ranked, table=True):
    """The format of Strata's tools/make_profile.py and expert_cache.cpp write_expert_profile."""
    blob = b"STRP" + struct.pack("<5I", 1, layers, n_exp, len(ranked), len(ranked))
    for layer, e in ranked:
        blob += struct.pack("<HH", layer, e)
    if table:
        t = [-1] * (layers * n_exp)
        for r, (layer, e) in enumerate(ranked):
            t[layer * n_exp + e] = r
        blob += struct.pack(f"<{layers * n_exp}i", *t)
    Path(path).write_bytes(blob)


class Experts(unittest.TestCase):
    def test_the_real_header(self):
        blob = (HOST / "expert_profile_header.bin").read_bytes()
        self.assertEqual(struct.unpack("<5I", blob[4:24]), (1, 48, 512, 24576, 24576))
        with self.assertRaises(experts.ProfileError):                # the header alone: the pairs are missing
            experts.parse_profile(blob)

    def test_parse_with_and_without_the_table(self):
        d = tmpdir(self)
        ranked = [(1, 2), (0, 0), (1, 0), (0, 3)]
        for table in (True, False):
            write_profile(d / "p.bin", 2, 4, ranked, table)
            p = experts.parse_profile((d / "p.bin").read_bytes())
            self.assertEqual((p["layers"], p["experts"], p["n"]), (2, 4, 4))
            self.assertEqual(p["ranks"], [1, -1, -1, 3, 2, -1, 0, -1])
        with self.assertRaises(experts.ProfileError):
            experts.parse_profile(b"XXXX" + bytes(20))

    def test_diff_and_encode(self):
        a = {"layers": 1, "experts": 4, "ranks": [0, 1, 2, 3]}
        b = {"layers": 1, "experts": 4, "ranks": [2, 0, 1, 3]}
        self.assertEqual(experts.diff(a, b, 2), {"comparable": True, "entered": 1, "left": 1, "moved": 3})
        import base64
        raw = base64.b64decode(experts.encode_ranks([0, -1, 5]))
        self.assertEqual(struct.unpack("<3H", raw), (0, 65535, 5))

    def test_profiles_from_the_config_and_the_learned_saves(self):
        d = tmpdir(self)
        write_profile(d / "base.bin", 2, 4, [(0, 0), (0, 1), (1, 0), (1, 1)])
        cfg = d / "strata-x.json"
        cfg.write_text(json.dumps({"args": ["--expert-profile", "base.bin"], "expert_profile_save": "learned.bin", "cwd": str(d)}))
        prof = experts.Profiles(str(cfg))
        doc = prof.doc(2)
        self.assertEqual(doc["base"]["layers"], 2)
        self.assertTrue(doc["learned_configured"])
        self.assertEqual(doc["learned"]["error"], "the file does not exist")
        write_profile(d / "learned.bin", 2, 4, [(0, 0), (0, 1), (1, 0), (1, 1)])
        prof.doc(2)
        time.sleep(0.02)
        write_profile(d / "learned.bin", 2, 4, [(1, 1), (0, 1), (0, 0), (1, 0)])
        os.utime(d / "learned.bin", (time.time() + 5, time.time() + 5))
        doc = prof.doc(2)
        self.assertEqual(doc["learned"]["previous"]["entered"], 1)
        self.assertEqual(doc["learned"]["previous"]["left"], 1)
        self.assertNotIn("ranks_b64", prof.doc(2, include_ranks=False)["base"])


class Defaults(unittest.TestCase):
    def test_the_calibrated_flags_go(self):
        cfg = {"args": ["--pack", "p", "--pcie-frac", "0.35", "--spec-min-p", "0.70", "--adapt-swaps", "80", "--kv", "int8"],
               "expert_profile_save": "x.bin"}
        out = defaultsrun.without_calibrated(cfg)
        self.assertEqual(out["args"], ["--pack", "p", "--kv", "int8"])
        self.assertNotIn("expert_profile_save", out)
        self.assertIn("--pcie-frac", cfg["args"])                    # the original is not changed

    def test_which_reports_are_defaults_runs(self):
        st = ReportStore(support.FIXTURES)
        self.assertTrue(st.scan()["reports"])
        by = {r["id"]: r["defaults"] for r in st.scan()["reports"]}
        self.assertTrue(by[support.A])
        self.assertFalse(by[support.NOCFG])                          # no config recorded: unknown, not a defaults run

    def test_a_run_with_a_fake_tool(self):
        d = tmpdir(self)
        results = copy_fixtures(d / "results")
        fake = FakeStrata(results).start()
        self.addCleanup(fake.stop)
        cfg = d / "strata-x.json"
        cfg.write_text(json.dumps({"exe": "strata", "args": ["--pcie-frac", "0.35", "--kv", "int8"]}))
        tool = d / "tool.py"
        tool.write_text("import json, shutil, sys\n"
                        "a = sys.argv\ncfg = json.load(open(a[a.index('--config') + 1]))\n"
                        "assert '--pcie-frac' not in cfg['args'], cfg\n"
                        f"shutil.copytree({str(support.FIXTURES / support.A)!r}, a[a.index('--out') + 1] + '/2026-10-20-profile-fake-defaults')\n"
                        "print('Report: done')\n")
        s = start_studio(self, fake, results, config=str(cfg))
        s.app.defaults_run.tool = [sys.executable, str(tool)]
        wait_for(lambda: s.poller.status == "ok" and s.poller.health.get("loaded"), what="Strata's health")
        st, _, _ = request(s, "POST", "/api/run-defaults", body={})
        self.assertEqual(st, 200)
        wait_for(lambda: request(s, "GET", "/api/run-defaults")[2]["state"] != "running", what="the defaults run")
        doc = request(s, "GET", "/api/run-defaults")[2]
        self.assertEqual(doc["state"], "done", doc)
        self.assertEqual(doc["report"], "2026-10-20-profile-fake-defaults")
        self.assertTrue(fake.requests_to("/unload"))                   # Strata's model was unloaded first
        self.assertFalse(any("strata-defaults" in p.name for p in d.rglob("*.json") if p.parent == d))

    def test_refused_while_a_tune_runs_and_without_config(self):
        d = tmpdir(self)
        results = copy_fixtures(d / "results")
        fake = FakeStrata(results).start()
        self.addCleanup(fake.stop)
        s = start_studio(self, fake, results)
        self.assertEqual(request(s, "POST", "/api/run-defaults", body={})[0], 412)
        cfg = d / "strata-x.json"
        cfg.write_text(json.dumps({"args": []}))
        s2 = start_studio(self, fake, results, config=str(cfg), state_path=d / "s2.json")
        wait_for(lambda: s2.poller.status == "ok", what="Strata's health")
        with fake.lock:
            fake.tune.update(state="running", started_at=time.time())
        s2.poller.refresh_status()
        self.assertEqual(request(s2, "POST", "/api/run-defaults", body={})[0], 409)


class Edits(unittest.TestCase):
    def test_ranges_come_with_their_source(self):
        for rid, c in CONTROLS.items():
            self.assertIn("source", c, rid)
            self.assertIn("engine_default", c, rid)
        self.assertIsNone(check_value("pcie_frac", "0.4"))
        self.assertIn("largest", check_value("pcie_frac", "1.2"))
        self.assertIn("20480", check_value("kv_resident", "1000"))
        self.assertIsNone(check_value("kv_resident", "0"))
        self.assertIsNone(check_value("expert_cache", "auto"))
        self.assertIn("whole", check_value("adapt_swaps", "1.5"))
        self.assertIsNone(check_value("prefill_cpu_share", "auto"))

    def test_apply_edits_writes_args_env_and_keys_with_a_bak(self):
        d = tmpdir(self)
        cfg = d / "strata-x.json"
        cfg.write_text(json.dumps({"args": ["--pack", "p", "--pcie-frac", "0.35"], "env": {"A": "1"}}, indent=2) + "\n")
        sha = configwrite.file_doc(cfg)["sha"]
        res = configwrite.apply_edits(cfg, [{"id": "pcie_frac", "value": None}, {"id": "spec_min_p", "value": "0.6"},
                                            {"id": "prefill_cpu_share", "value": "auto"},
                                            {"id": "expert_profile_save", "value": "data/learned.bin"}], sha)
        new = json.loads(cfg.read_text())
        self.assertEqual(new["args"], ["--pack", "p", "--spec-min-p", "0.6"])
        self.assertEqual(new["env"], {"A": "1", "STRATA_PREFILL_CPU_SHARE": "auto"})
        self.assertEqual(new["expert_profile_save"], "data/learned.bin")
        self.assertEqual(len(res["changes"]), 4)
        self.assertTrue((d / "strata-x.json.bak").exists())

    def test_apply_edits_refuses(self):
        d = tmpdir(self)
        cfg = d / "strata-x.json"
        cfg.write_text(json.dumps({"args": []}))
        sha = configwrite.file_doc(cfg)["sha"]
        for edits, status in (([{"id": "pcie_frac", "value": "2"}], 400), ([{"id": "port", "value": "1"}], 400), ([], 400)):
            with self.assertRaises(configwrite.ConfigWriteError) as e:
                configwrite.apply_edits(cfg, edits, sha)
            self.assertEqual(e.exception.status, status)
        with self.assertRaises(configwrite.ConfigWriteError) as e:
            configwrite.apply_edits(cfg, [{"id": "pcie_frac", "value": "0.5"}], "0" * 64)
        self.assertEqual(e.exception.status, 409)
        self.assertFalse((d / "strata-x.json.bak").exists())


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        self.results = copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, settings=True).start()
        self.addCleanup(self.fake.stop)

    def test_host_route(self):
        class C:
            def sample(self):
                return {"busy": 12.0, "cores": [], "temp_c": 41.0, "power_w": None, "power_hint": hostinfo.RAPL_HINT}

        class G:
            error = None

            def sample(self):
                return hostinfo.parse_gpu_line((HOST / "nvidia_smi.csv").read_text().strip())
        log = self.tmp / "e.log"
        log.write_text((HOST / "engine_pool.log").read_text())
        import studio
        s = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "st.json"),
                          timing=support.fast_timing(), engine_log=str(log),
                          host_monitor=hostinfo.HostMonitor(cpu=C(), gpu=G(), every=0.02))
        s.start()
        self.addCleanup(s.stop)
        request(s, "GET", "/api/host")
        doc = wait_for(lambda: (lambda d: d if d["gpu"] else None)(request(s, "GET", "/api/host")[2]), what="a host sample")
        self.assertEqual(doc["gpu"]["pstate"], "P8")
        self.assertEqual(doc["pool_cpus"], [1, 2, 3, 4, 5, 6, 7])
        self.assertIn("sudo chmod", doc["cpu"]["power_hint"])

    def test_experts_route_and_settings_routes(self):
        write_profile(self.tmp / "p.bin", 2, 4, [(0, 0), (1, 1), (0, 1), (1, 0)])
        cfg = self.tmp / "strata-x.json"
        cfg.write_text(json.dumps({"args": ["--expert-profile", str(self.tmp / "p.bin")]}))
        s = start_studio(self, self.fake, self.results, config=str(cfg))
        doc = request(s, "GET", "/api/experts")[2]
        self.assertEqual((doc["base"]["layers"], doc["base"]["experts"]), (2, 4))
        self.assertIn("ranks_b64", doc["base"])
        f = request(s, "GET", "/api/settings/file")[2]
        st, _, err = request(s, "POST", "/api/settings/edit", body={"edits": [{"id": "pcie_frac", "value": "0.5"}], "sha": f["sha"]})
        self.assertEqual(st, 400)                                        # no confirm
        st, _, res = request(s, "POST", "/api/settings/edit", body={"edits": [{"id": "pcie_frac", "value": "0.5"}], "sha": f["sha"], "confirm": True})
        self.assertEqual(st, 200, res)
        self.assertIn("--pcie-frac", json.loads(cfg.read_text())["args"])
        rows = request(s, "GET", "/api/settings")[2]["rows"]
        self.assertTrue(all("control" in r for r in rows))


if __name__ == "__main__":
    unittest.main()
