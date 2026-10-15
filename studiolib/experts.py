"""The expert profiles, read only, for the expert map.

Format (Strata's tools/make_profile.py and src/core/expert_cache.cpp, version 1): b"STRP", five little-endian u32
(version, layers, experts per layer, slots, n ranked), n pairs of (layer u16, expert u16) in cache order, then per
layer a table of `experts` int32: the expert's rank in that order (-1: not ranked).  The engine fills its VRAM slots
with the ranked pairs in order (`--expert-cache auto`), so the first `expert_slots` ranks are the experts resident at
load.  The adaptive tier then swaps experts while it runs; the engine reports no per-expert ids per window, so the
live residency is known only from a learned profile (`expert_profile_save`), which the engine writes with the
resident experts first, then by heat.

The page gets each profile's ranks as base64 of little-endian u16 (rank, 65535 for "not ranked"), layer by layer.
"""
from __future__ import annotations

import base64
import os
import struct
import threading
from pathlib import Path

from .reports import arg_value
from .settings import read_config

MAGIC = b"STRP"
NOT_RANKED = 65535
MAX_BYTES = 64 * 1024 * 1024


class ProfileError(Exception):
    pass


def parse_profile(blob: bytes) -> dict:
    """{"version", "layers", "experts", "slots", "n", "ranks": [layers*experts] (-1 not ranked)}."""
    if len(blob) < 24 or blob[:4] != MAGIC:
        raise ProfileError("not a Strata expert profile (no STRP header)")
    version, nl, ne, slots, n = struct.unpack_from("<5I", blob, 4)
    if version != 1:
        raise ProfileError(f"a version {version} profile; this reader knows version 1")
    if nl == 0 or ne == 0 or nl * ne > 1 << 22 or n > nl * ne:
        raise ProfileError("the header's layout is not plausible")
    if len(blob) < 24 + 4 * n:
        raise ProfileError("the ranked list is truncated")
    ranks = [-1] * (nl * ne)
    table_at = 24 + 4 * n
    if len(blob) >= table_at + 4 * nl * ne:           # the per-layer rank table
        table = struct.unpack_from(f"<{nl * ne}i", blob, table_at)
        ranks = list(table)
    else:                                             # a writer without the table: the pairs give the ranks
        for r in range(n):
            layer, e = struct.unpack_from("<HH", blob, 24 + 4 * r)
            if layer < nl and e < ne and ranks[layer * ne + e] < 0:
                ranks[layer * ne + e] = r
    return {"version": version, "layers": nl, "experts": ne, "slots": slots, "n": n, "ranks": ranks}


def encode_ranks(ranks) -> str:
    vals = [NOT_RANKED if r is None or r < 0 or r >= NOT_RANKED else r for r in ranks]
    return base64.b64encode(struct.pack(f"<{len(vals)}H", *vals)).decode("ascii")


def diff(old: dict, new: dict, slots: int) -> dict:
    """What moved between two profiles of the same model: experts that entered and left the first `slots` ranks,
    and how many ranks moved."""
    if (old["layers"], old["experts"]) != (new["layers"], new["experts"]):
        return {"comparable": False}
    entered = left = moved = 0
    for a, b in zip(old["ranks"], new["ranks"]):
        ra, rb = 0 <= a < slots, 0 <= b < slots
        entered += (rb and not ra)
        left += (ra and not rb)
        moved += (a != b)
    return {"comparable": True, "entered": entered, "left": left, "moved": moved}


def _resolve(path: str, cfg: dict, config_path) -> Path:
    p = Path(os.path.expanduser(path))
    if p.is_absolute():
        return p
    here = cfg.get("cwd") or (str(Path(config_path).parent) if config_path else ".")
    return Path(here) / p


class Profiles:
    """The config's --expert-profile and the learned profile ("expert_profile_save"), parsed again only when the
    file changes; the learned profile's previous save is kept so the page can show what moved between saves."""

    def __init__(self, config_path):
        self.config_path = config_path
        self.lock = threading.Lock()
        self.cache: dict = {}                         # path -> (key, parsed)
        self.learned_prev: dict | None = None         # the save before the newest one: {"key", "parsed", "mtime"}
        self.learned_key = None

    def _load(self, path: Path):
        try:
            st = path.stat()
        except OSError:
            return None, None, "the file does not exist"
        if st.st_size > MAX_BYTES:
            return None, st, "the file is larger than 64 MiB"
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.cache.get(str(path))
        if hit and hit[0] == key:
            return hit[1], st, None
        try:
            parsed = parse_profile(path.read_bytes())
        except (OSError, ProfileError) as e:
            return None, st, str(e)
        with self.lock:
            self.cache[str(path)] = (key, parsed)
        return parsed, st, None

    def doc(self, resident_slots=None, include_ranks=True) -> dict:
        cfg, err = read_config(self.config_path)
        out = {"config": bool(cfg), "config_error": err, "base": None, "learned": None, "resident_slots": resident_slots,
               "learned_configured": False, "learned_every_min": None}
        if not cfg:
            return out
        args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
        base = arg_value(args, "--expert-profile")
        if base:
            p = _resolve(base, cfg, self.config_path)
            parsed, st, e = self._load(p)
            out["base"] = self._entry(p, parsed, st, e, resident_slots, include_ranks)
        save = cfg.get("expert_profile_save")
        if isinstance(save, str) and save.strip():
            out["learned_configured"] = True
            every = cfg.get("expert_profile_save_every")
            out["learned_every_min"] = every if isinstance(every, (int, float)) and not isinstance(every, bool) else 10
            p = _resolve(save, cfg, self.config_path)
            parsed, st, e = self._load(p)
            entry = self._entry(p, parsed, st, e, resident_slots, include_ranks)
            if parsed is not None:
                key = (st.st_mtime_ns, st.st_size)
                with self.lock:
                    if self.learned_key is not None and key != self.learned_key[0]:
                        self.learned_prev = {"parsed": self.learned_key[1], "mtime": self.learned_key[2]}
                    if self.learned_key is None or key != self.learned_key[0]:
                        self.learned_key = (key, parsed, st.st_mtime)
                    prev = self.learned_prev
                if prev:
                    entry["previous"] = {"mtime": prev["mtime"],
                                         "ranks_b64": encode_ranks(prev["parsed"]["ranks"]) if include_ranks else None,
                                         **diff(prev["parsed"], parsed, resident_slots or parsed["slots"])}
                if out["base"] and out["base"].get("layers"):
                    base_parsed = self.cache.get(str(_resolve(base, cfg, self.config_path)), (None, None))[1]
                    if base_parsed:
                        entry["against_base"] = diff(base_parsed, parsed, resident_slots or parsed["slots"])
            out["learned"] = entry
        return out

    @staticmethod
    def _entry(path, parsed, st, err, slots, include_ranks):
        e = {"path": str(path), "error": err, "mtime": st.st_mtime if st else None}
        if parsed:
            e.update(version=parsed["version"], layers=parsed["layers"], experts=parsed["experts"],
                     header_slots=parsed["slots"], n=parsed["n"])
            if include_ranks:
                e["ranks_b64"] = encode_ranks(parsed["ranks"])
        return e
