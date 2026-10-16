"""The PC itself: CPU, GPU, memory, disk, power and temperature, read by studio.py once per second while a page asks.

Strata's /metrics stays the source for everything the page needs.  These readings add detail where this OS and this
device give it without root and without a new dependency.  A reading that is not available is None, and `notes`
says why in one sentence ("not available on Windows", "needs rocm-smi").  The Studio never estimates a reading.

One interface, one backend per OS (chosen by sys.platform), one GPU backend per vendor tool:

  Linux    /proc/stat, cpufreq, hwmon (a preference list of sensor names), RAPL (powercap, globbed), /proc/meminfo,
           /proc/diskstats
  Windows  ctypes: NtQuerySystemInformation(SystemProcessorPerformanceInformation), CallNtPowerInformation
           (ProcessorInformation), GlobalMemoryStatusEx.  No temperature or package power: Windows gives none
           without third-party tools.
  macOS    ctypes host_processor_info (per-core ticks), sysctl hw.memsize, vm_stat.  Frequency, temperature and
           power need powermetrics (root): not read.
  GPU      nvidia-smi (Linux, Windows: PATH, then the driver's folders), rocm-smi or amd-smi (AMD on Linux); other
           devices and AMD on Windows: none (the page uses Strata's /metrics).

At most one subprocess runs per second.  Every parser takes raw text or bytes, every reader takes its paths and
commands as arguments, so the tests feed fixtures for each OS and tool.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from glob import glob
from pathlib import Path

HISTORY = 60
PLATFORM_NAME = {"linux": "Linux", "win32": "Windows", "darwin": "macOS"}

# ------------------------------------------------------------------------------------------------ NVIDIA
GPU_FIELDS = ("clocks.sm", "clocks.mem", "clocks.max.sm", "clocks.max.mem", "utilization.gpu", "utilization.memory",
              "power.draw", "power.limit", "temperature.gpu", "fan.speed", "pstate", "pcie.link.gen.current",
              "pcie.link.gen.max", "pcie.link.width.current", "memory.used", "memory.total",
              "clocks_event_reasons.active")
GPU_KEYS = ("sm_mhz", "mem_mhz", "sm_max_mhz", "mem_max_mhz", "util", "mem_util", "power_w", "power_limit_w",
            "temp_c", "fan_pct", "pstate", "pcie_gen", "pcie_gen_max", "pcie_width", "mem_used_mib", "mem_total_mib",
            "reasons")
# NVML's clocks event reasons (nvmlClocksEventReason*): the bit and the word the page shows
REASONS = ((0x1, "idle"), (0x2, "application clocks"), (0x4, "power cap"), (0x8, "hardware slowdown"),
           (0x10, "sync boost"), (0x20, "thermal (software)"), (0x40, "thermal (hardware)"),
           (0x80, "power brake"), (0x100, "display clock"))


def _num(s):
    """A number from a tool's text: "26.27", "26,27" (a decimal comma), "[N/A]" -> None, "2100Mhz" -> 2100."""
    if s is None:
        return None
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    s = str(s).strip()
    if s in ("", "[N/A]", "N/A", "[Not Supported]", "Not Supported", "None"):
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def empty_gpu(vendor=None, source=None) -> dict:
    out = {k: None for k in GPU_KEYS}
    out.update(reason_words=None, vendor=vendor, source=source)
    return out


def decode_reasons(mask) -> list[str]:
    if mask is None:
        return []
    return [word for bit, word in REASONS if mask & bit]


def parse_gpu_line(line: str) -> dict | None:
    """One `nvidia-smi --format=csv,noheader,nounits` line in GPU_FIELDS order -> the GPU dict."""
    parts = [p.strip() for p in (line or "").replace("\r", "").split(",")]
    if len(parts) != len(GPU_FIELDS):
        # a decimal comma would split numbers: the fields then are too many; nvidia-smi prints a point in every
        # locale, so this is a line that is not ours
        return None
    out = empty_gpu("nvidia", "nvidia-smi")
    for key, raw in zip(GPU_KEYS, parts):
        if key == "pstate":
            out[key] = raw or None
        elif key == "reasons":
            try:
                out[key] = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            except ValueError:
                out[key] = None
        else:
            out[key] = _num(raw)
    out["reason_words"] = decode_reasons(out["reasons"])
    return out


def nvidia_smi_candidates(platform=None, env=None, which=shutil.which) -> list[str]:
    """Where nvidia-smi can be: PATH, then (Windows) the driver's folders."""
    platform = platform or sys.platform
    env = env if env is not None else os.environ
    out = []
    w = which("nvidia-smi")
    if w:
        out.append(w)
    if platform == "win32":
        pf = env.get("ProgramFiles", r"C:\Program Files")
        windir = env.get("SystemRoot", r"C:\Windows")
        out += [pf + r"\NVIDIA Corporation\NVSMI\nvidia-smi.exe", windir + r"\System32\nvidia-smi.exe"]
    return out


# ------------------------------------------------------------------------------------------------ AMD
def _pick(d: dict, *needles, avoid=()) -> object:
    """The value of the first key that contains every needle (case-insensitive) and no avoided word."""
    for k, v in d.items():
        kl = k.lower()
        if all(n in kl for n in needles) and not any(a in kl for a in avoid):
            return v
    return None


def parse_rocm_smi(text: str) -> dict | None:
    """`rocm-smi --showclocks --showuse --showpower --showtemp --showmemuse --json`: {"card0": {"<name> (unit)":
    "value", ...}}.  The key names differ between ROCm releases, so each value is found by the words in its key."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return None
    cards = [v for k, v in doc.items() if isinstance(v, dict) and k.lower().startswith("card")] if isinstance(doc, dict) else []
    if not cards:
        return None
    c = cards[0]
    out = empty_gpu("amd", "rocm-smi")
    out["temp_c"] = _num(_pick(c, "temperature", "edge")) or _num(_pick(c, "temperature", "junction"))
    out["power_w"] = _num(_pick(c, "power", "(w)", avoid=("cap", "max")))
    out["util"] = _num(_pick(c, "gpu use", "%"))
    out["mem_util"] = _num(_pick(c, "memory", "%"))
    out["sm_mhz"] = _num(_pick(c, "sclk", avoid=("level",))) or _num(_pick(c, "sclk"))
    out["mem_mhz"] = _num(_pick(c, "mclk", avoid=("level",))) or _num(_pick(c, "mclk"))
    return out


def parse_amd_smi(text: str) -> dict | None:
    """`amd-smi metric --json`: a list (or a dict) of GPUs with nested {"value", "unit"} entries."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(doc, dict):
        doc = doc.get("gpu_data") or [doc]
    if not isinstance(doc, list) or not doc or not isinstance(doc[0], dict):
        return None
    g = doc[0]

    def val(*path):
        x = g
        for p in path:
            if not isinstance(x, dict):
                return None
            x = x.get(p)
        if isinstance(x, dict):
            x = x.get("value")
        return _num(x)
    out = empty_gpu("amd", "amd-smi")
    out["util"] = val("usage", "gfx_activity")
    out["mem_util"] = val("usage", "umc_activity")
    out["power_w"] = val("power", "socket_power")
    out["temp_c"] = val("temperature", "edge") or val("temperature", "hotspot")
    out["sm_mhz"] = val("clock", "gfx_0", "clk")
    out["mem_mhz"] = val("clock", "mem_0", "clk")
    out["mem_used_mib"] = val("mem_usage", "used_vram")
    out["mem_total_mib"] = val("mem_usage", "total_vram")
    out["fan_pct"] = val("fan", "usage")
    return out


class GpuReader:
    """The one GPU tool this PC has, found once: nvidia-smi, else (Linux) rocm-smi or amd-smi, else none."""

    def __init__(self, platform=None, run=None, which=shutil.which, env=None, timeout=2.0, tools=None):
        self.platform = platform or sys.platform
        self.run = run or _run
        self.timeout = timeout
        self.tool = None                       # (name, cmd, parser)
        self.note = None
        self.error = None
        cands = tools if tools is not None else self._find(which, env)
        for name, cmd, parser in cands:
            self.tool = (name, cmd, parser)
            break
        if self.tool is None:
            self.note = {"win32": "Needs nvidia-smi (NVIDIA). AMD and Intel GPUs on Windows: only Strata's readings.",
                         "darwin": "Not available on macOS."}.get(self.platform,
                                                                    "Needs nvidia-smi, rocm-smi or amd-smi.")

    def _find(self, which, env):
        out = []
        if self.platform in ("linux", "win32"):
            for p in nvidia_smi_candidates(self.platform, env, which):
                if p and (Path(p).exists() or p == which("nvidia-smi")):
                    out.append(("nvidia-smi", [p, f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"],
                                lambda t: parse_gpu_line(next((ln for ln in t.splitlines() if ln.strip()), ""))))
                    break
        if self.platform == "linux":
            r = which("rocm-smi")
            if r:
                out.append(("rocm-smi", [r, "--showclocks", "--showuse", "--showpower", "--showtemp", "--showmemuse", "--json"], parse_rocm_smi))
            a = which("amd-smi")
            if a:
                out.append(("amd-smi", [a, "metric", "--json"], parse_amd_smi))
        return out

    def source(self) -> str:
        return self.tool[0] if self.tool else "none"

    def sample(self) -> dict | None:
        if self.tool is None:
            return None
        name, cmd, parser = self.tool
        text, err = self.run(cmd, self.timeout)
        if err:
            self.error = f"{name}: {err}"
            if err == "not found":
                self.tool, self.note = None, f"Needs {name}."
            return None
        self.error = None
        return parser(text)

    def probe(self) -> dict:
        if self.tool is None:
            return {"reader": "gpu", "source": "none", "ok": False, "raw": self.note}
        name, cmd, _ = self.tool
        text, err = self.run(cmd, self.timeout)
        return {"reader": "gpu", "source": f"{cmd[0]} ({name})", "ok": not err and bool(text.strip()),
                "raw": err or _first_line(text)}


def _run(cmd, timeout) -> tuple[str, str | None]:
    """(stdout, None) or ("", the reason).  No shell, no window on Windows."""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)
    except FileNotFoundError:
        return "", "not found"
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", f"no answer ({type(e).__name__})"
    if r.returncode != 0:
        return "", ((r.stderr or r.stdout or "").strip().splitlines() or [f"exit {r.returncode}"])[0][:200]
    return r.stdout, None


def _first_line(text) -> str:
    return next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")[:200]


# ------------------------------------------------------------------------------------------------ Linux
HWMON_PREFERENCE = ("k10temp", "zenpower", "coretemp", "cpu_thermal", "thinkpad", "acpitz")


def parse_proc_stat(text: str) -> dict[str, tuple[int, int]]:
    """{"cpu": (busy, total), "cpu0": ...} from /proc/stat (busy = total - idle - iowait)."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        try:
            vals = [int(x) for x in parts[1:9]]
        except ValueError:
            continue
        total = sum(vals)
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        out[parts[0]] = (total - idle, total)
    return out


def busy_pct(prev: dict, now: dict) -> dict:
    out = {}
    for k, (b, t) in now.items():
        if k in prev and t > prev[k][1]:
            out[k] = round(100.0 * (b - prev[k][0]) / (t - prev[k][1]), 1)
        else:
            out[k] = None
    return out


def parse_meminfo(text: str) -> dict:
    """/proc/meminfo -> {"total", "available"} in bytes."""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            vals[m.group(1)] = int(m.group(2)) * 1024
    total, avail = vals.get("MemTotal"), vals.get("MemAvailable")
    return {"total": total, "used": total - avail if total is not None and avail is not None else None}


def parse_diskstats(text: str) -> tuple[int, int] | None:
    """(sectors read, sectors written) of the whole disks in /proc/diskstats (partitions and virtual devices left out)."""
    rd = wr = 0
    seen = False
    for line in text.splitlines():
        p = line.split()
        if len(p) < 10:
            continue
        name = p[2]
        if re.match(r"^(loop|ram|zram|dm-|md|sr)", name) or re.match(r"^(sd[a-z]+|vd[a-z]+|hd[a-z]+)\d+$", name) \
                or re.match(r"^(nvme\d+n\d+|mmcblk\d+)p\d+$", name):
            continue
        rd += int(p[5])
        wr += int(p[9])
        seen = True
    return (rd, wr) if seen else None


def find_hwmon(root="/sys/class/hwmon", preference=HWMON_PREFERENCE) -> tuple[Path | None, str | None]:
    """The CPU temperature input: the first sensor of the preference list that this PC has."""
    found = {}
    try:
        for d in sorted(Path(root).iterdir()):
            try:
                name = (d / "name").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if name in preference and (d / "temp1_input").exists() and name not in found:
                found[name] = d / "temp1_input"
    except OSError:
        pass
    for name in preference:
        if name in found:
            return found[name], name
    return None, None


def _read_int(path) -> int | None:
    try:
        return int(Path(path).read_text(encoding="utf-8", errors="replace").strip())
    except (OSError, ValueError):
        return None


class LinuxBackend:
    name = "Linux"

    def __init__(self, proc="/proc", cpu_root="/sys/devices/system/cpu", hwmon_root="/sys/class/hwmon",
                 powercap_root="/sys/class/powercap", clock=time.monotonic):
        self.proc, self.cpu_root, self.powercap = Path(proc), Path(cpu_root), Path(powercap_root)
        self.temp_path, self.temp_name = find_hwmon(hwmon_root)
        self.clock = clock
        self._prev_stat = None
        self._prev_energy: dict = {}
        self._prev_disk = None
        # RAPL domains (Intel and AMD both use the intel-rapl name): the packages and their "core" sub-domains
        self.rapl = []
        for f in sorted(glob(str(self.powercap / "intel-rapl*" / "energy_uj"))):
            d = Path(f).parent
            try:
                dname = (d / "name").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                dname = d.name
            kind = "core" if dname.startswith("core") else "package" if dname.startswith("package") else None
            if kind:
                self.rapl.append((kind, Path(f)))

    def _freqs(self, names) -> dict:
        out = {}
        for n in names:
            khz = _read_int(self.cpu_root / n / "cpufreq" / "scaling_cur_freq")
            out[n] = round(khz / 1000) if khz is not None else None
        return out

    def _power(self) -> tuple[dict, str | None]:
        if not self.rapl:
            return {"package": None, "cores": None}, "Not available: this PC exposes no RAPL energy counter."
        now = self.clock()
        sums = {"package": None, "cores": None}
        denied = []
        for kind, path in self.rapl:
            try:
                uj = int(path.read_text(encoding="utf-8", errors="replace").strip())
            except PermissionError:
                denied.append(str(path))
                continue
            except (OSError, ValueError):
                continue
            prev = self._prev_energy.get(str(path))
            self._prev_energy[str(path)] = (now, uj)
            if prev and now > prev[0] and uj >= prev[1]:
                w = (uj - prev[1]) / 1e6 / (now - prev[0])
                key = "cores" if kind == "core" else "package"
                sums[key] = round((sums[key] or 0.0) + w, 1)
        note = f"Not readable without access: run `sudo chmod a+r {' '.join(denied)}`." if denied else None
        return sums, note

    def sample(self) -> dict:
        try:
            stat = parse_proc_stat((self.proc / "stat").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            stat = {}
        busy = busy_pct(self._prev_stat or {}, stat)
        self._prev_stat = stat
        names = sorted((k for k in stat if k != "cpu" and k[3:].isdigit()), key=lambda k: int(k[3:]))
        freqs = self._freqs(names)
        temp = _read_int(self.temp_path) if self.temp_path else None
        power, pnote = self._power()
        try:
            mem = parse_meminfo((self.proc / "meminfo").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            mem = {"total": None, "used": None}
        disk = {"read_mb": None, "write_mb": None}
        try:
            d = parse_diskstats((self.proc / "diskstats").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            d = None
        now = self.clock()
        if d and self._prev_disk and now > self._prev_disk[0]:
            dt = now - self._prev_disk[0]
            disk = {"read_mb": round((d[0] - self._prev_disk[1][0]) * 512 / 2 ** 20 / dt, 2),
                    "write_mb": round((d[1] - self._prev_disk[1][1]) * 512 / 2 ** 20 / dt, 2)}
        if d:
            self._prev_disk = (now, d)
        notes = {}
        if names and all(v is None for v in freqs.values()):
            notes["freq"] = "Not available: this system has no cpufreq."
        if self.temp_path is None:
            notes["temp"] = f"Not available: no CPU sensor ({', '.join(HWMON_PREFERENCE)}) in hwmon."
        if pnote:
            notes["power"] = pnote
        return {"cpu": {"busy": busy.get("cpu"), "cores": [{"id": int(n[3:]), "busy": busy.get(n), "mhz": freqs.get(n)} for n in names],
                        "temp_c": round(temp / 1000, 1) if temp is not None else None, "temp_source": self.temp_name,
                        "power_w": power["package"], "core_power_w": power["cores"]},
                "memory": mem, "disk": disk, "notes": notes}

    def probe(self) -> list[dict]:
        def first(path):
            try:
                return _first_line(Path(path).read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                return f"not readable ({type(e).__name__})"
        cpu0 = self.cpu_root / "cpu0" / "cpufreq" / "scaling_cur_freq"
        out = [{"reader": "cpu busy", "source": str(self.proc / "stat"), "ok": (self.proc / "stat").exists(), "raw": first(self.proc / "stat")},
               {"reader": "cpu frequency", "source": str(cpu0), "ok": cpu0.exists(), "raw": first(cpu0) if cpu0.exists() else "absent"},
               {"reader": "cpu temperature", "source": f"{self.temp_path} ({self.temp_name})" if self.temp_path else f"hwmon: none of {', '.join(HWMON_PREFERENCE)}",
                "ok": self.temp_path is not None, "raw": first(self.temp_path) if self.temp_path else "absent"}]
        if self.rapl:
            for kind, p in self.rapl:
                raw = first(p)
                out.append({"reader": f"power ({kind})", "source": str(p), "ok": raw.isdigit(), "raw": raw})
        else:
            out.append({"reader": "power", "source": str(self.powercap / "intel-rapl*"), "ok": False, "raw": "absent"})
        out.append({"reader": "memory", "source": str(self.proc / "meminfo"), "ok": (self.proc / "meminfo").exists(), "raw": first(self.proc / "meminfo")})
        out.append({"reader": "disk", "source": str(self.proc / "diskstats"), "ok": (self.proc / "diskstats").exists(), "raw": first(self.proc / "diskstats")})
        return out


# ------------------------------------------------------------------------------------------------ Windows
SPPI = struct.Struct("<qqqqqI4x")              # SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION: Idle, Kernel, User, Dpc,
#                                                Interrupt (LARGE_INTEGER), InterruptCount (ULONG), padding: 48 bytes
PPI = struct.Struct("<6I")                     # PROCESSOR_POWER_INFORMATION: Number, MaxMhz, CurrentMhz, MhzLimit,
#                                                MaxIdleState, CurrentIdleState


def parse_sppi(blob: bytes) -> list[tuple[int, int]]:
    """Per logical CPU (busy, total) in 100 ns units: kernel time includes idle time."""
    out = []
    for i in range(len(blob) // SPPI.size):
        idle, kernel, user, _, _, _ = SPPI.unpack_from(blob, i * SPPI.size)
        total = kernel + user
        out.append((total - idle, total))
    return out


def parse_ppi(blob: bytes) -> list[dict]:
    out = []
    for i in range(len(blob) // PPI.size):
        n, mx, cur, lim, _, _ = PPI.unpack_from(blob, i * PPI.size)
        out.append({"id": n, "max_mhz": mx, "mhz": cur, "limit_mhz": lim})
    return out


class WindowsBackend:
    name = "Windows"
    NOTES = {"temp": "Not available on Windows without third-party tools.",
             "power": "Not available on Windows without third-party tools.",
             "disk": "Not read on Windows: the Disk block uses Strata's readings."}

    def __init__(self, calls=None):
        """`calls`: {"perf": () -> bytes, "power": (n) -> bytes, "memory": () -> (total, avail), "ncpu": int}; the
        default uses ctypes (Windows only)."""
        self.calls = calls or self._ctypes_calls()
        self._prev = None

    @staticmethod
    def _ctypes_calls():
        import ctypes
        from ctypes import wintypes
        ntdll, powrprof, k32 = ctypes.WinDLL("ntdll"), ctypes.WinDLL("powrprof"), ctypes.WinDLL("kernel32")
        ncpu = os.cpu_count() or 1

        def perf():
            buf = ctypes.create_string_buffer(SPPI.size * ncpu)
            ret = wintypes.ULONG(0)
            if ntdll.NtQuerySystemInformation(8, buf, len(buf), ctypes.byref(ret)) != 0:
                return b""
            return buf.raw[:ret.value]

        def power(n):
            buf = ctypes.create_string_buffer(PPI.size * n)
            if powrprof.CallNtPowerInformation(11, None, 0, buf, len(buf)) != 0:
                return b""
            return buf.raw

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        def memory():
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(m)
            if not k32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None, None
            return m.ullTotalPhys, m.ullAvailPhys
        return {"perf": perf, "power": power, "memory": memory, "ncpu": ncpu}

    def sample(self) -> dict:
        now = parse_sppi(self.calls["perf"]())
        prev, self._prev = self._prev, now
        cores = []
        tb = tt = 0
        for i, (b, t) in enumerate(now):
            busy = None
            if prev and i < len(prev) and t > prev[i][1]:
                busy = round(100.0 * (b - prev[i][0]) / (t - prev[i][1]), 1)
                tb += b - prev[i][0]
                tt += t - prev[i][1]
            cores.append({"id": i, "busy": busy, "mhz": None})
        for p in parse_ppi(self.calls["power"](len(now) or self.calls["ncpu"])):
            if p["id"] < len(cores):
                cores[p["id"]]["mhz"] = p["mhz"] or None
        total, avail = self.calls["memory"]()
        return {"cpu": {"busy": round(100.0 * tb / tt, 1) if tt else None, "cores": cores, "temp_c": None, "temp_source": None,
                        "power_w": None, "core_power_w": None},
                "memory": {"total": total, "used": total - avail if total and avail is not None else None},
                "disk": {"read_mb": None, "write_mb": None}, "notes": dict(self.NOTES)}

    def probe(self) -> list[dict]:
        perf = self.calls["perf"]()
        pw = parse_ppi(self.calls["power"](len(perf) // SPPI.size or 1))
        total, avail = self.calls["memory"]()
        return [{"reader": "cpu busy", "source": "NtQuerySystemInformation(8)", "ok": bool(perf), "raw": f"{len(perf) // SPPI.size} logical CPUs"},
                {"reader": "cpu frequency", "source": "CallNtPowerInformation(ProcessorInformation)", "ok": bool(pw), "raw": str(pw[0]) if pw else "no answer"},
                {"reader": "memory", "source": "GlobalMemoryStatusEx", "ok": total is not None, "raw": f"total {total}, available {avail}"},
                {"reader": "cpu temperature", "source": "none", "ok": False, "raw": self.NOTES["temp"]},
                {"reader": "power", "source": "none", "ok": False, "raw": self.NOTES["power"]}]


# ------------------------------------------------------------------------------------------------ macOS
def parse_vm_stat(text: str) -> dict:
    """`vm_stat`: the page size and the page counts -> {"used": bytes in active, wired and compressed pages}."""
    m = re.search(r"page size of (\d+) bytes", text)
    page = int(m.group(1)) if m else 4096
    pages = {}
    for line in text.splitlines():
        mm = re.match(r'"?([^":]+)"?:\s+(\d+)\.?', line.strip())
        if mm:
            pages[mm.group(1).strip()] = int(mm.group(2))
    used = sum(pages.get(k, 0) for k in ("Pages active", "Pages wired down", "Pages occupied by compressor"))
    return {"used": used * page if pages else None}


def parse_cpu_ticks(ticks: list[int]) -> list[tuple[int, int]]:
    """host_processor_info(PROCESSOR_CPU_LOAD_INFO): 4 counters per CPU (user, system, idle, nice) -> (busy, total)."""
    out = []
    for i in range(0, len(ticks) - 3, 4):
        user, system, idle, nice = ticks[i:i + 4]
        total = user + system + idle + nice
        out.append((total - idle, total))
    return out


class MacBackend:
    name = "macOS"
    NOTES = {"freq": "Needs powermetrics (root): not read.", "temp": "Needs powermetrics (root): not read.",
             "power": "Needs powermetrics (root): not read.", "disk": "Not read on macOS: the Disk block uses Strata's readings."}

    def __init__(self, ticks=None, memsize=None, vm_stat=None):
        self.ticks = ticks or self._mach_ticks
        self.memsize = memsize or self._memsize
        self.vm_stat = vm_stat or (lambda: _run(["vm_stat"], 2.0)[0])
        self._prev = None

    @staticmethod
    def _mach_ticks() -> list[int]:
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        host = lib.mach_host_self()
        count, info, n = ctypes.c_uint(0), ctypes.POINTER(ctypes.c_int)(), ctypes.c_uint(0)
        if lib.host_processor_info(host, 2, ctypes.byref(count), ctypes.byref(info), ctypes.byref(n)) != 0:
            return []
        return [info[i] & 0xFFFFFFFF for i in range(n.value)]

    @staticmethod
    def _memsize():
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        v, size = ctypes.c_uint64(0), ctypes.c_size_t(8)
        if lib.sysctlbyname(b"hw.memsize", ctypes.byref(v), ctypes.byref(size), None, 0) != 0:
            return None
        return v.value

    def sample(self) -> dict:
        now = parse_cpu_ticks(self.ticks())
        prev, self._prev = self._prev, now
        cores, tb, tt = [], 0, 0
        for i, (b, t) in enumerate(now):
            busy = None
            if prev and i < len(prev) and t > prev[i][1]:
                busy = round(100.0 * (b - prev[i][0]) / (t - prev[i][1]), 1)
                tb += b - prev[i][0]
                tt += t - prev[i][1]
            cores.append({"id": i, "busy": busy, "mhz": None})
        mem = parse_vm_stat(self.vm_stat() or "")
        return {"cpu": {"busy": round(100.0 * tb / tt, 1) if tt else None, "cores": cores, "temp_c": None, "temp_source": None,
                        "power_w": None, "core_power_w": None},
                "memory": {"total": self.memsize(), "used": mem["used"]}, "disk": {"read_mb": None, "write_mb": None},
                "notes": dict(self.NOTES)}

    def probe(self) -> list[dict]:
        t = self.ticks()
        return [{"reader": "cpu busy", "source": "host_processor_info", "ok": bool(t), "raw": f"{len(t) // 4} CPUs"},
                {"reader": "memory", "source": "sysctl hw.memsize, vm_stat", "ok": self.memsize() is not None, "raw": _first_line(self.vm_stat())},
                {"reader": "cpu frequency, temperature, power", "source": "none", "ok": False, "raw": self.NOTES["temp"]}]


class NullBackend:
    """--host-info off, or an OS the Studio has no reader for: every reading None."""

    def __init__(self, reason="Host readings are off (--host-info off)."):
        self.name, self.reason = "none", reason

    def sample(self) -> dict:
        n = {k: self.reason for k in ("cpu", "freq", "temp", "power", "memory", "disk")}
        return {"cpu": None, "memory": None, "disk": None, "notes": n}

    def probe(self) -> list[dict]:
        return [{"reader": "all", "source": "none", "ok": False, "raw": self.reason}]


def backend_for(platform=None, **kw):
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return LinuxBackend(**kw)
    if platform == "win32":
        return WindowsBackend(**kw)
    if platform == "darwin":
        return MacBackend(**kw)
    return NullBackend(f"Not available on {platform}: the Studio has no reader for this OS.")


# ------------------------------------------------------------------------------------------------ the monitor
class HostMonitor:
    """Samples the host backend and the GPU reader once per `every` seconds in its own thread; keeps 60 samples.
    Sampling starts on the first read of `doc()` and stops 30 s after the last one (no page, no subprocesses)."""

    SERIES = {"cpu": ("busy", "temp_c", "power_w"), "gpu": ("sm_mhz", "mem_mhz", "util", "mem_util", "power_w",
                                                            "temp_c", "fan_pct", "mem_used_mib")}

    def __init__(self, host=None, gpu=None, every=1.0, idle_stop=30.0, enabled=True, platform=None):
        self.platform = platform or sys.platform
        self.enabled = enabled
        if not enabled:
            host, gpu = NullBackend(), None
        self.host = host if host is not None else _safe(lambda: backend_for(self.platform))
        self.gpu = gpu if gpu is not None else (_safe(lambda: GpuReader(self.platform)) if enabled else None)
        self.every, self.idle_stop = every, idle_stop
        self.lock = threading.Lock()
        self.now = {"cpu": None, "gpu": None, "memory": None, "disk": None, "notes": {}, "at": None}
        self.hist = {g: {k: deque(maxlen=HISTORY) for k in keys} for g, keys in self.SERIES.items()}
        self.core_hist: dict[int, deque] = {}
        self._last_read = 0.0
        self._thread = None
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            self.step()
            if time.monotonic() - self._last_read > self.idle_stop:
                break
            self._stop.wait(self.every)
        with self.lock:
            self._thread = None

    def step(self):
        try:
            h = self.host.sample() if self.host else NullBackend("Not available.").sample()
        except Exception as e:                       # noqa: BLE001 - a reader must never stop the Studio
            h = NullBackend(f"The host reader failed ({type(e).__name__}).").sample()
        try:
            g = self.gpu.sample() if self.gpu else None
        except Exception:                            # noqa: BLE001
            g = None
        notes = dict(h.get("notes") or {})
        if self.gpu is None:
            notes["gpu"] = "Host readings are off (--host-info off)." if not self.enabled else "Not available."
        elif g is None:
            notes["gpu"] = self.gpu.note or self.gpu.error or "No answer from the GPU tool."
        with self.lock:
            self.now = {"cpu": h.get("cpu"), "gpu": g, "memory": h.get("memory"), "disk": h.get("disk"), "notes": notes,
                        "at": time.time()}
            for grp, src in (("cpu", h.get("cpu")), ("gpu", g)):
                for k in self.SERIES[grp]:
                    self.hist[grp][k].append((src or {}).get(k))
            for c in (h.get("cpu") or {}).get("cores") or []:
                self.core_hist.setdefault(c["id"], deque(maxlen=HISTORY)).append(c["busy"])

    def doc(self, pool_cpus=None, host_cpu=None) -> dict:
        with self.lock:
            self._last_read = time.monotonic()
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._loop, name="host-monitor", daemon=True)
                self._thread.start()
            now = dict(self.now)
            hist = {g: {k: list(v) for k, v in d.items()} for g, d in self.hist.items()}
            cores = {str(k): list(v) for k, v in self.core_hist.items()}
        return {"at": now["at"], "cpu": now["cpu"], "gpu": now["gpu"], "memory": now["memory"], "disk": now["disk"],
                "notes": now["notes"], "history": hist, "core_history": cores, "enabled": self.enabled,
                "os": PLATFORM_NAME.get(self.platform, self.platform), "backend": getattr(self.host, "name", "none"),
                "gpu_source": self.gpu.source() if self.gpu else "none",
                "gpu_error": self.gpu.error if self.gpu else None, "pool_cpus": pool_cpus, "host_cpu": host_cpu}

    def probe(self) -> list[dict]:
        out = list(self.host.probe()) if self.host else []
        out.append(self.gpu.probe() if self.gpu else {"reader": "gpu", "source": "none", "ok": False, "raw": "off"})
        return out

    def stop(self):
        self._stop.set()


def _safe(make):
    try:
        return make()
    except Exception as e:                           # noqa: BLE001 - no reader for this PC: say so, do not fail
        return NullBackend(f"Not available: the reader did not start ({type(e).__name__}).")


def probe_text(monitor: HostMonitor, engine_log=None) -> str:
    """`studio.py --host-probe`: each reader, its source, whether it answered, the raw first line."""
    lines = [f"Strata Profile Studio host probe: {PLATFORM_NAME.get(sys.platform, sys.platform)} ({sys.platform}), "
             f"Python {sys.version.split()[0]}, backend {getattr(monitor.host, 'name', 'none')}, GPU tool "
             f"{monitor.gpu.source() if monitor.gpu else 'none'}"]
    if isinstance(monitor.host, LinuxBackend):           # the busy readings need two samples
        monitor.host.sample()
    for p in monitor.probe():
        lines.append(f"{'ok ' if p['ok'] else 'no '} {p['reader']:<34} {p['source']}")
        lines.append(f"     {p['raw']}")
    if engine_log is not None:
        f = engine_log.facts()
        lines.append(f"{'ok ' if f['readable'] else 'no '} {'engine log':<34} {f['path']}")
        lines.append(f"     probe line: {'yes' if f['probe'] else 'no'}, pool line: {'yes' if f['pool'] else 'no'}, "
                     f"VRAM warning: {'yes' if f['vram_warning'] else 'no'}")
    return "\n".join(lines)
