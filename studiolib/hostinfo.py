"""The PC itself, read by studio.py once per second (Strata's /metrics stays the source for everything else).

CPU: per-logical-CPU busy % from /proc/stat deltas, per-CPU frequency from cpufreq, the package temperature from the
k10temp (AMD) or coretemp (Intel) hwmon, package and core power from the RAPL energy counters.  The RAPL files are
root-only on many systems: then the power fields are None and `power_hint` says how to make them readable.

GPU: `nvidia-smi --query-gpu=...` (one subprocess per second, 2 s timeout).  Without nvidia-smi (or on an AMD card)
`gpu` is None and the page shows /metrics' GPU fields only.

Every reader takes its paths and its command as arguments, so the tests feed it fixture files and lines.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path

HISTORY = 60
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
RAPL_HINT = ("not readable: run `sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj "
             "/sys/class/powercap/intel-rapl:0:0/energy_uj`")


def _num(s):
    s = (s or "").strip()
    if s in ("", "[N/A]", "N/A", "[Not Supported]"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def decode_reasons(mask) -> list[str]:
    if mask is None:
        return []
    return [word for bit, word in REASONS if mask & bit]


def parse_gpu_line(line: str) -> dict | None:
    """One `nvidia-smi --format=csv,noheader,nounits` line in GPU_FIELDS order -> {GPU_KEYS...}."""
    parts = [p.strip() for p in (line or "").split(",")]
    if len(parts) != len(GPU_FIELDS):
        return None
    out = {}
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


def busy_pct(prev: dict, now: dict) -> dict[str, float | None]:
    out = {}
    for k, (b, t) in now.items():
        if k in prev and t > prev[k][1]:
            out[k] = round(100.0 * (b - prev[k][0]) / (t - prev[k][1]), 1)
        else:
            out[k] = None
    return out


def find_hwmon_temp(root="/sys/class/hwmon") -> Path | None:
    """The package temperature input of the k10temp (AMD) or coretemp (Intel) hwmon."""
    try:
        for d in sorted(Path(root).iterdir()):
            try:
                name = (d / "name").read_text().strip()
            except OSError:
                continue
            if name in ("k10temp", "coretemp", "zenpower") and (d / "temp1_input").exists():
                return d / "temp1_input"
    except OSError:
        pass
    return None


def _read_int(path) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


class CpuSampler:
    def __init__(self, proc_stat="/proc/stat", cpu_root="/sys/devices/system/cpu", hwmon_root="/sys/class/hwmon",
                 rapl_pkg="/sys/class/powercap/intel-rapl:0/energy_uj",
                 rapl_core="/sys/class/powercap/intel-rapl:0:0/energy_uj", clock=time.monotonic):
        self.proc_stat, self.cpu_root = proc_stat, Path(cpu_root)
        self.temp_path = find_hwmon_temp(hwmon_root)
        self.rapl = {"package": Path(rapl_pkg), "cores": Path(rapl_core)}
        self.clock = clock
        self._prev_stat = None
        self._prev_energy: dict = {}
        self.power_hint = None

    def _freqs(self, names) -> dict:
        out = {}
        for n in names:
            khz = _read_int(self.cpu_root / n / "cpufreq" / "scaling_cur_freq")
            out[n] = round(khz / 1000) if khz is not None else None
        return out

    def _power(self) -> dict:
        now = self.clock()
        out = {}
        for name, path in self.rapl.items():
            try:
                uj = int(path.read_text().strip())
            except PermissionError:
                self.power_hint = RAPL_HINT
                out[name] = None
                continue
            except (OSError, ValueError):
                out[name] = None
                continue
            prev = self._prev_energy.get(name)
            self._prev_energy[name] = (now, uj)
            if prev and now > prev[0] and uj >= prev[1]:
                out[name] = round((uj - prev[1]) / 1e6 / (now - prev[0]), 1)
            else:
                out[name] = None
        return out

    def sample(self) -> dict:
        try:
            stat = parse_proc_stat(Path(self.proc_stat).read_text())
        except OSError:
            stat = {}
        busy = busy_pct(self._prev_stat or {}, stat)
        self._prev_stat = stat
        names = sorted((k for k in stat if k != "cpu"), key=lambda k: int(k[3:]))
        freqs = self._freqs(names)
        temp = _read_int(self.temp_path) if self.temp_path else None
        power = self._power()
        return {"busy": busy.get("cpu"), "cores": [{"id": int(n[3:]), "busy": busy.get(n), "mhz": freqs.get(n)}
                                                   for n in names],
                "temp_c": round(temp / 1000, 1) if temp is not None else None,
                "power_w": power.get("package"), "core_power_w": power.get("cores"), "power_hint": self.power_hint}


class GpuSampler:
    def __init__(self, cmd=None, timeout=2.0):
        self.cmd = cmd or ["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"]
        self.timeout = timeout
        self.missing = False
        self.error = None

    def sample(self) -> dict | None:
        if self.missing:
            return None
        try:
            r = subprocess.run(self.cmd, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError:
            self.missing, self.error = True, "nvidia-smi is not installed: only Strata's GPU readings are shown"
            return None
        except (OSError, subprocess.TimeoutExpired):
            self.error = "nvidia-smi did not answer"
            return None
        if r.returncode != 0:
            self.error = (r.stderr or r.stdout or "").strip()[:200] or f"nvidia-smi exit {r.returncode}"
            return None
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.error = None
        return parse_gpu_line(lines[0]) if lines else None


class HostMonitor:
    """Samples the CPU and the GPU once per `every` seconds in its own thread; keeps 60 samples of history.
    Sampling starts on the first read of `doc()` and stops 30 s after the last one (no page, no subprocesses)."""

    SERIES = {"cpu": ("busy", "temp_c", "power_w"), "gpu": ("sm_mhz", "mem_mhz", "util", "mem_util", "power_w",
                                                            "temp_c", "fan_pct", "mem_used_mib")}

    def __init__(self, cpu=None, gpu=None, every=1.0, idle_stop=30.0):
        self.cpu, self.gpu = cpu or CpuSampler(), gpu or GpuSampler()
        self.every, self.idle_stop = every, idle_stop
        self.lock = threading.Lock()
        self.now = {"cpu": None, "gpu": None, "at": None}
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
        cpu, gpu = self.cpu.sample(), self.gpu.sample()
        with self.lock:
            self.now = {"cpu": cpu, "gpu": gpu, "at": time.time()}
            for g, src in (("cpu", cpu), ("gpu", gpu)):
                for k in self.SERIES[g]:
                    self.hist[g][k].append((src or {}).get(k))
            for c in (cpu or {}).get("cores") or []:
                self.core_hist.setdefault(c["id"], deque(maxlen=HISTORY)).append(c["busy"])

    def doc(self, pool_cpus=None, host_cpu=None) -> dict:
        with self.lock:
            self._last_read = time.monotonic()
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._loop, name="host-monitor", daemon=True)
                self._thread.start()
            now = {k: v for k, v in self.now.items()}
            hist = {g: {k: list(v) for k, v in d.items()} for g, d in self.hist.items()}
            cores = {str(k): list(v) for k, v in self.core_hist.items()}
        return {"at": now["at"], "cpu": now["cpu"], "gpu": now["gpu"], "history": hist, "core_history": cores,
                "gpu_error": self.gpu.error, "pool_cpus": pool_cpus, "host_cpu": host_cpu}

    def stop(self):
        self._stop.set()
