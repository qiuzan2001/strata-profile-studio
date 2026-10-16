"""The engine log, read only: the few lines the PC diagram needs that no route of Strata answers.

  strata serve: PCIe share: the CPU pool takes 96 us per missed expert (21.3 GB/s of expert bytes), the link 28.0 GB/s
      -> pcie_frac 0.40 (was 0.55; --pcie-frac N fixes it, ...)
  strata serve: WARNING: only 312 MiB of VRAM free at the start of this request (below 512 MiB): ...

Only the tail is read (at most TAIL_BYTES), again only when the file grew or was replaced.  Nothing is written.
"""
from __future__ import annotations

import os
import re
import threading

TAIL_BYTES = 1024 * 1024
RE_PROBE = re.compile(r"PCIe share: the CPU pool takes ([\d.]+) us per missed expert \(([\d.]+) GB/s of expert bytes\), "
                      r"the link ([\d.]+) GB/s -> pcie_frac ([\d.]+)(?: \(was ([\d.]+))?")
RE_POOL = re.compile(r"(\d+) pool workers on logical processors ([\d,]+), the host thread on (\d+)")
RE_VRAM = re.compile(r"WARNING: only (\d+) MiB of VRAM free at the start of this request \(below (\d+) MiB\)")


def parse(lines) -> dict:
    """{"probe": {...} | None, "vram_warning": {...} | None, "probes": n} from log lines (oldest first)."""
    probe = warn = pool = None
    n = 0
    for line in lines:
        m = RE_POOL.search(line)
        if m:
            pool = {"workers": int(m.group(1)), "cpus": [int(x) for x in m.group(2).split(",") if x],
                    "host_cpu": int(m.group(3))}
            continue
        m = RE_PROBE.search(line)
        if m:
            n += 1
            probe = {"us_per_expert": float(m.group(1)), "cpu_gbs": float(m.group(2)), "link_gbs": float(m.group(3)),
                     "pcie_frac": m.group(4), "was": m.group(5)}
            continue
        m = RE_VRAM.search(line)
        if m:
            warn = {"free_mib": int(m.group(1)), "below_mib": int(m.group(2))}
    return {"probe": probe, "vram_warning": warn, "probes": n, "pool": pool}


# which builds print each line (src/program/generate.cpp; the SYCL build has its own fork of that file)
PRINTED_BY = {
    "probe": "CUDA builds with --serve and no --pcie-frac (AMD HIP: gfx1200/gfx1201 native packs only. Not SYCL)",
    "pool": "all builds, once at the engine start",
    "vram_warning": "CUDA, HIP and Metal builds, when less than 512 MiB of VRAM is free (1024 MiB on Windows)",
}


class EngineLog:
    def __init__(self, path):
        self.path = str(path) if path else None
        self._lock = threading.Lock()
        self._key = None
        self._val = {"probe": None, "vram_warning": None, "probes": 0, "pool": None}

    def facts(self) -> dict:
        """The newest facts; {"path": None, ...} when no log is known or it cannot be read.  `missing`: the lines this
        log has not printed (yet), so the page can say "this build does not report ..."."""
        out = self._facts()
        out["missing"] = [k for k in ("probe", "pool", "vram_warning") if not out.get(k)]
        return out

    def _facts(self) -> dict:
        out = {"path": self.path, "readable": False, **self._val}
        if not self.path:
            return out
        try:
            st = os.stat(self.path)
        except OSError:
            return out
        key = (st.st_ino, st.st_size, st.st_mtime_ns)
        with self._lock:
            if key != self._key:
                try:
                    with open(self.path, "rb") as f:
                        pos = max(0, st.st_size - TAIL_BYTES)
                        f.seek(pos)
                        data = f.read(TAIL_BYTES)
                except OSError:
                    return out
                lines = data.decode("utf-8", "replace").splitlines()
                if pos > 0 and lines:
                    lines = lines[1:]                          # the tail starts inside a line
                new = parse(lines)
                for k in ("probe", "pool"):                    # a line that left the tail: keep the last one seen
                    if new[k] is None:
                        new[k] = self._val[k]
                self._val = new
                self._key = key
            return {"path": self.path, "readable": True, **self._val}
