"""Portability: the host readers of each OS and GPU tool, the --host-info off mode, the engine log as other Strata
builds print it, and the Windows layout of a Strata checkout.

Fixture origin:
  real (the development PC: Linux, RTX 5070 Ti, Ryzen 7 5800X3D): tests/fixtures/host/proc_stat_a, proc_stat_b,
      nvidia_smi.csv, engine_pool.log, expert_profile_header.bin
  documentation (unverified on hardware): tests/fixtures/host/doc/* (rocm-smi, amd-smi, vm_stat, a CRLF /proc/stat)
      and the Windows structures built below from their documented layouts
"""
import json
import os
import struct
import sys
import unittest
from pathlib import Path

import support
import hashlib

from studiolib import configwrite, defaultsrun, enginelog, hostinfo
from support import ROOT, tmpdir

HOST = ROOT / "tests" / "fixtures" / "host"
DOC = HOST / "doc"


class Nvidia(unittest.TestCase):
    def test_real_line(self):                                            # real
        d = hostinfo.parse_gpu_line((HOST / "nvidia_smi.csv").read_text().strip())
        self.assertEqual((d["sm_max_mhz"], d["mem_max_mhz"], d["power_limit_w"], d["pstate"]), (3120.0, 14001.0, 300.0, "P8"))
        self.assertEqual((d["vendor"], d["reason_words"]), ("nvidia", []))

    def test_missing_fields_and_reasons(self):
        d = hostinfo.parse_gpu_line("210, 405, 3120, 14001, [N/A], 55, 26.27, 300.00, 37, [Not Supported], P8, 1, 4, 16, 15548, 16303, 0x24\r")
        self.assertIsNone(d["util"])
        self.assertIsNone(d["fan_pct"])
        self.assertEqual(d["reason_words"], ["power cap", "thermal (software)"])
        self.assertIsNone(hostinfo.parse_gpu_line("1, 2, 3"))

    def test_windows_locations(self):
        c = hostinfo.nvidia_smi_candidates("win32", {"ProgramFiles": r"D:\Programs", "SystemRoot": r"C:\Windows"}, lambda n: None)
        self.assertEqual(c, [r"D:\Programs\NVIDIA Corporation\NVSMI\nvidia-smi.exe", r"C:\Windows\System32\nvidia-smi.exe"])
        self.assertEqual(hostinfo.nvidia_smi_candidates("linux", {}, lambda n: None), [])

    def test_a_tool_that_is_not_there(self):
        g = hostinfo.GpuReader("linux", tools=[("nvidia-smi", ["/nonexistent/nvidia-smi"], hostinfo.parse_gpu_line)])
        self.assertIsNone(g.sample())
        self.assertIsNone(g.tool)
        self.assertIn("Needs nvidia-smi", g.note)


class Amd(unittest.TestCase):
    def test_rocm_smi_documented_shape(self):                            # documentation, unverified
        d = hostinfo.parse_rocm_smi((DOC / "rocm_smi.json").read_text())
        self.assertEqual((d["vendor"], d["temp_c"], d["power_w"], d["util"], d["mem_util"]), ("amd", 45.0, 35.0, 12.0, 63.0))
        self.assertEqual((d["sm_mhz"], d["mem_mhz"]), (2100.0, 1250.0))
        self.assertIsNone(d["reason_words"])                              # the throttle mask is NVIDIA's only
        self.assertIsNone(hostinfo.parse_rocm_smi("not json"))
        self.assertIsNone(hostinfo.parse_rocm_smi('{"system": {}}'))

    def test_amd_smi_documented_shape(self):                             # documentation, unverified
        d = hostinfo.parse_amd_smi((DOC / "amd_smi.json").read_text())
        self.assertEqual((d["util"], d["mem_util"], d["power_w"], d["temp_c"]), (7.0, 3.0, 42.0, 44.0))
        self.assertEqual((d["sm_mhz"], d["mem_mhz"], d["mem_used_mib"], d["mem_total_mib"], d["fan_pct"]), (1980.0, 1249.0, 9120.0, 16368.0, 31.0))
        self.assertIsNone(d["pstate"])
        self.assertIsNone(hostinfo.parse_amd_smi("[]"))

    def test_no_tool_on_windows_amd_and_macos(self):
        self.assertIn("Windows", hostinfo.GpuReader("win32", which=lambda n: None, env={}).note)
        self.assertIn("macOS", hostinfo.GpuReader("darwin", which=lambda n: None).note)
        self.assertIsNone(hostinfo.GpuReader("darwin", which=lambda n: None).sample())


class Numbers(unittest.TestCase):
    def test_tool_numbers(self):
        self.assertEqual(hostinfo._num("26,27"), 26.27)                    # a decimal comma
        self.assertEqual(hostinfo._num("(2100Mhz)"), 2100.0)
        self.assertIsNone(hostinfo._num("[N/A]"))
        self.assertIsNone(hostinfo._num(None))


def linux_tree(d: Path, rapl=True, freq=True, sensor="k10temp", stat="proc_stat_a"):
    (d / "proc").mkdir()
    (d / "proc" / "stat").write_text((HOST / stat).read_text() if (HOST / stat).exists() else (DOC / stat).read_text())
    (d / "proc" / "meminfo").write_text("MemTotal:       80181068 kB\nMemFree:         1000000 kB\nMemAvailable:   13000000 kB\n")
    (d / "proc" / "diskstats").write_text("   7       0 loop0 14 0 34 0 0 0 0 0 0 0 0\n 259       0 nvme0n1 1000 0 2000 0 0 0 4000 0 0 0 0\n 259       1 nvme0n1p1 1 0 2 0 0 0 4 0 0 0 0\n")
    for i in range(16):
        c = d / "cpu" / f"cpu{i}"
        c.mkdir(parents=True)
        if freq:
            (c / "cpufreq").mkdir()
            (c / "cpufreq" / "scaling_cur_freq").write_text(f"{3400000 + i * 1000}\n")
    (d / "hwmon" / "hwmon0").mkdir(parents=True)
    (d / "hwmon" / "hwmon0" / "name").write_text("acpitz\n")
    (d / "hwmon" / "hwmon0" / "temp1_input").write_text("30000\n")
    if sensor:
        (d / "hwmon" / "hwmon1").mkdir(parents=True)
        (d / "hwmon" / "hwmon1" / "name").write_text(sensor + "\n")
        (d / "hwmon" / "hwmon1" / "temp1_input").write_text("43375\n")
    (d / "powercap").mkdir()
    if rapl:
        for name, sub in (("package-0", "intel-rapl:0"), ("core", "intel-rapl:0:0")):
            (d / "powercap" / sub).mkdir()
            (d / "powercap" / sub / "name").write_text(name + "\n")
            (d / "powercap" / sub / "energy_uj").write_text("1000000\n")
    return d


class Linux(unittest.TestCase):
    def backend(self, d, t):
        return hostinfo.LinuxBackend(proc=d / "proc", cpu_root=d / "cpu", hwmon_root=d / "hwmon", powercap_root=d / "powercap", clock=lambda: t[0])

    def test_real_proc_stat_and_a_full_tree(self):                       # real /proc/stat
        d = linux_tree(tmpdir(self))
        t = [100.0]
        b = self.backend(d, t)
        first = b.sample()
        (d / "proc" / "stat").write_text((HOST / "proc_stat_b").read_text())
        for sub in ("intel-rapl:0", "intel-rapl:0:0"):
            (d / "powercap" / sub / "energy_uj").write_text("46000000\n")
        t[0] = 101.0
        s = b.sample()
        self.assertIsNone(first["cpu"]["busy"])
        self.assertIsNotNone(s["cpu"]["busy"])
        self.assertEqual(len(s["cpu"]["cores"]), 16)
        self.assertEqual((s["cpu"]["temp_c"], s["cpu"]["temp_source"]), (43.4, "k10temp"))   # preferred over acpitz
        self.assertEqual((s["cpu"]["power_w"], s["cpu"]["core_power_w"]), (45.0, 45.0))
        self.assertEqual(s["memory"]["total"], 80181068 * 1024)
        self.assertEqual(s["notes"], {})

    def test_vm_or_arm_without_cpufreq_rapl_or_cpu_sensor(self):
        d = linux_tree(tmpdir(self), rapl=False, freq=False, sensor=None)
        s = self.backend(d, [0.0]).sample()
        self.assertTrue(all(c["mhz"] is None for c in s["cpu"]["cores"]))
        self.assertIn("cpufreq", s["notes"]["freq"])
        self.assertEqual(s["cpu"]["temp_source"], "acpitz")                    # the last fallback of the list
        self.assertIsNone(s["cpu"]["power_w"])
        self.assertIn("RAPL", s["notes"]["power"])

    def test_root_only_rapl_names_the_files(self):
        if os.geteuid() == 0:
            self.skipTest("root reads a 000 file")
        d = linux_tree(tmpdir(self))
        for sub in ("intel-rapl:0", "intel-rapl:0:0"):
            (d / "powercap" / sub / "energy_uj").chmod(0)
        s = self.backend(d, [0.0]).sample()
        self.assertIn("sudo chmod a+r", s["notes"]["power"])
        self.assertIn("intel-rapl:0:0/energy_uj", s["notes"]["power"])
        for sub in ("intel-rapl:0", "intel-rapl:0:0"):
            (d / "powercap" / sub / "energy_uj").chmod(0o600)

    def test_crlf_proc_stat(self):                                         # documentation
        p = hostinfo.parse_proc_stat((DOC / "proc_stat_crlf.txt").read_text())
        self.assertEqual(sorted(p), ["cpu", "cpu0", "cpu1"])

    def test_diskstats_whole_disks_only(self):
        self.assertEqual(hostinfo.parse_diskstats(" 259 0 nvme0n1 1000 0 2000 0 0 0 4000 0 0 0 0\n 259 1 nvme0n1p1 1 0 2 0 0 0 4 0 0 0 0\n 7 0 loop0 1 0 2 0 0 0 4 0 0 0 0\n"), (2000, 4000))


class Windows(unittest.TestCase):                                         # documented struct layouts, unverified
    def calls(self, idle, kernel, user, mhz=3800):
        def perf():
            return b"".join(hostinfo.SPPI.pack(i, k, u, 0, 0, 0) for i, k, u in zip(idle, kernel, user))

        def power(n):
            return b"".join(hostinfo.PPI.pack(i, 4500, mhz, 4500, 0, 0) for i in range(n))
        return {"perf": perf, "power": power, "memory": lambda: (32 * 2 ** 30, 20 * 2 ** 30), "ncpu": len(idle)}

    def test_two_samples(self):
        b = hostinfo.WindowsBackend(calls=self.calls([100, 100], [200, 200], [100, 100]))
        b.sample()
        b.calls = self.calls([150, 190], [300, 300], [200, 200])           # cpu0: 200 total, 150 busy; cpu1: 110 busy
        s = b.sample()
        self.assertEqual([c["busy"] for c in s["cpu"]["cores"]], [75.0, 55.0])
        self.assertEqual(s["cpu"]["busy"], 65.0)
        self.assertEqual(s["cpu"]["cores"][0]["mhz"], 3800)
        self.assertEqual(s["memory"]["used"], 12 * 2 ** 30)
        self.assertIsNone(s["cpu"]["temp_c"])
        self.assertIn("Windows", s["notes"]["temp"])
        self.assertIn("Windows", s["notes"]["power"])
        self.assertEqual(hostinfo.SPPI.size, 48)
        self.assertEqual(hostinfo.PPI.size, 24)


class Mac(unittest.TestCase):                                             # documentation, unverified
    def test_ticks_and_vm_stat(self):
        seq = [[10, 5, 85, 0, 20, 10, 70, 0], [20, 10, 100, 0, 40, 20, 90, 0]]
        b = hostinfo.MacBackend(ticks=lambda: seq.pop(0), memsize=lambda: 16 * 2 ** 30, vm_stat=lambda: (DOC / "vm_stat.txt").read_text())
        b.sample()
        s = b.sample()
        self.assertEqual([c["busy"] for c in s["cpu"]["cores"]], [50.0, 60.0])   # 15 of 30 ticks, 30 of 50
        self.assertEqual(s["memory"]["used"], (200000 + 80000 + 20000) * 16384)
        self.assertIn("powermetrics", s["notes"]["temp"])


class Off(unittest.TestCase):
    def test_host_info_off(self):
        m = hostinfo.HostMonitor(enabled=False, every=0.01)
        m.step()
        doc = m.doc()
        m.stop()
        self.assertFalse(doc["enabled"])
        self.assertIsNone(doc["cpu"])
        self.assertIsNone(doc["gpu"])
        self.assertIn("--host-info off", doc["notes"]["cpu"])
        self.assertIn("--host-info off", doc["notes"]["gpu"])
        self.assertEqual(doc["gpu_source"], "none")

    def test_an_os_without_a_reader(self):
        b = hostinfo.backend_for("freebsd13")
        self.assertIsInstance(b, hostinfo.NullBackend)
        self.assertIn("freebsd13", b.sample()["notes"]["cpu"])

    def test_a_failing_reader_does_not_stop_the_monitor(self):
        class Bad:
            name = "bad"

            def sample(self):
                raise OSError("boom")
        m = hostinfo.HostMonitor(host=Bad(), gpu=hostinfo.GpuReader("linux", tools=[]))
        m.step()
        self.assertIn("failed", m.now["notes"]["cpu"])

    def test_the_probe_text(self):
        m = hostinfo.HostMonitor(host=hostinfo.NullBackend("off for the test"), gpu=hostinfo.GpuReader("darwin", which=lambda n: None))
        text = hostinfo.probe_text(m)
        self.assertIn("off for the test", text)
        self.assertIn("Not available on macOS", text)


class HipLog(unittest.TestCase):
    """An engine log with only the lines a HIP build prints (see the report: the pool line, the timing and summary
    lines; no PCIe-share probe line, which the CUDA DMA path prints)."""

    LINES = [
        "strata generate: 7 pool workers on logical processors 1,2,3,4,5,6,7, the host thread on 0 (draining too) (--host-core first)\r",
        "strata serve: prompt 44 tokens = 0 reused + 44 read in 412 ms (106.8 tok/s), 128 generated in 1700 ms (75.3 tok/s), drafts accepted 80 of 110, 0 checkpoints\r",
        "strata serve: decode expert cache hit rate: 71.2% (1000 hits / 1404 lookups)\r",
    ]

    def test_missing_lines_are_none_not_errors(self):
        d = enginelog.parse(self.LINES)
        self.assertIsNone(d["probe"])
        self.assertIsNone(d["vram_warning"])
        self.assertEqual(d["pool"]["cpus"], [1, 2, 3, 4, 5, 6, 7])

    def test_the_hardware_doc_says_what_this_build_does_not_report(self):
        log = tmpdir(self) / "hip.log"
        log.write_bytes("\n".join(self.LINES).encode())
        f = enginelog.EngineLog(log).facts()
        self.assertTrue(f["readable"])
        self.assertIsNone(f["probe"])
        self.assertIn("probe", f["missing"])


class StrataLayout(unittest.TestCase):
    def test_root_and_python_on_windows_and_linux_layouts(self):
        d = tmpdir(self)
        (d / "tools").mkdir()
        (d / "tools" / "strata_profile.py").write_text("")
        cfg = d / "strata-swift-iq3_s.json"
        cfg.write_text("{}")
        self.assertEqual(defaultsrun.strata_root({"exe": str(d / "engine" / "strata.exe")}, cfg), d.resolve())
        self.assertEqual(defaultsrun.strata_python(d, "win32"), None)       # no venv yet: the Studio's own Python
        (d / ".venv" / "Scripts").mkdir(parents=True)
        (d / ".venv" / "Scripts" / "python.exe").write_text("")
        self.assertEqual(defaultsrun.strata_python(d, "win32"), d / ".venv" / "Scripts" / "python.exe")
        (d / ".venv" / "bin").mkdir(parents=True)
        (d / ".venv" / "bin" / "python").write_text("")
        self.assertEqual(defaultsrun.strata_python(d, "linux"), d / ".venv" / "bin" / "python")


class FromConfig(unittest.TestCase):
    def test_address_and_results_from_the_run_config(self):        # `studio.py --config X` alone
        import studio
        d = tmpdir(self)
        (d / "tools").mkdir()
        (d / "tools" / "strata_profile.py").write_text("")
        (d / "bench" / "results").mkdir(parents=True)
        cfg = d / "strata-swift-iq3_s.json"
        cfg.write_text(json.dumps({"exe": str(d / "engine" / "strata"), "port": 8085, "host": "0.0.0.0"}))
        self.assertEqual(studio.from_config(str(cfg)), {"strata": "http://127.0.0.1:8085", "results": str(d / "bench" / "results")})
        cfg.write_text(json.dumps({"args": []}))                      # no port: the defaults apply
        self.assertEqual(studio.from_config(str(cfg)), {"results": str(d / "bench" / "results")})
        self.assertEqual(studio.from_config(None), {})
        self.assertEqual(studio.from_config(str(d / "missing.json")), {})

class WindowsConfig(unittest.TestCase):
    def test_a_crlf_config_with_a_bom_keeps_both(self):                 # setup.py on Windows; Notepad adds the BOM
        d = tmpdir(self)
        p = d / "strata.json"
        raw = b'\xef\xbb\xbf{\r\n  "exe": "C:\\\\Strata\\\\engine\\\\strata.exe",\r\n  "args": []\r\n}\r\n'
        p.write_bytes(raw)
        _, cfg = configwrite._read(p)
        cfg["args"] = ["--spec", "4"]
        out = configwrite._write(p, raw, cfg, [])
        new = p.read_bytes()
        self.assertTrue(new.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", new.replace(b"\r\n", b""))                # every line end is CRLF
        self.assertEqual(out["sha"], hashlib.sha256(new).hexdigest())   # the sha is the file's own
        self.assertEqual(json.loads(new.decode("utf-8-sig"))["exe"], "C:\\Strata\\engine\\strata.exe")
        self.assertEqual((d / "strata.json.bak").read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
