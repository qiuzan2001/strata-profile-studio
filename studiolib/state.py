"""studio-state.json: the Studio's one file.  Tune records, report notes, the settings captured for reports that
carry none, and the running flow (so a restarted Studio can resume it).

Atomic writes (temp file, then os.replace), at most one per second, and bounded in every part; the whole file stays
at or under 1 MiB.  The Studio writes nothing else, and never anything under Strata's folders.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path

SCHEMA = 1
MAX_TUNES = 50
MAX_LINES = 400
MAX_LINE_CHARS = 300
MAX_NOTES = 500
NOTE_CHARS = 2000
MAX_META = 500
MAX_FILE = 1024 * 1024
READ_LIMIT = 16 * 1024 * 1024
MIN_WRITE_GAP = 1.0


class StateError(Exception):
    """The state file cannot be used and the Studio must not start (an unknown schema)."""


def empty() -> dict:
    return {"schema": SCHEMA, "tunes": [], "active_flow": None, "notes": {}, "report_meta": {}}


def _clip_lines(lines) -> list[str]:
    return [str(x)[:MAX_LINE_CHARS] for x in list(lines or [])[-MAX_LINES:]]


def bound(data: dict) -> dict:
    """`data` with every part inside its bound (a new dict; the argument is not changed)."""
    out = {"schema": SCHEMA, "active_flow": data.get("active_flow") if isinstance(data.get("active_flow"), dict) else None}
    tunes = [t for t in data.get("tunes") or [] if isinstance(t, dict)]
    tunes = tunes[:MAX_TUNES]                           # newest first: the oldest fall off the end
    for t in tunes:
        if "lines_tail" in t:
            t["lines_tail"] = _clip_lines(t.get("lines_tail"))
        if isinstance(t.get("note"), str):
            t["note"] = t["note"][:NOTE_CHARS]
    out["tunes"] = tunes
    notes = {str(k): str(v)[:NOTE_CHARS] for k, v in (data.get("notes") or {}).items() if isinstance(v, str) and v}
    if len(notes) > MAX_NOTES:                          # dict order is insertion order: the oldest go
        notes = dict(list(notes.items())[-MAX_NOTES:])
    out["notes"] = notes
    meta = {str(k): v for k, v in (data.get("report_meta") or {}).items() if isinstance(v, dict)}
    if len(meta) > MAX_META:
        keep = sorted(meta.items(), key=lambda kv: kv[1].get("captured_at") or 0.0, reverse=True)[:MAX_META]
        meta = dict(keep)
    out["report_meta"] = meta
    return out


def _size(data: dict) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def fit(data: dict) -> dict:
    """`bound`, then smaller until the file is at most 1 MiB: the oldest tunes lose their lines and candidates
    first, then the oldest tunes go."""
    out = bound(data)
    if _size(out) <= MAX_FILE:
        return out
    for t in reversed(out["tunes"]):                    # the last item is the oldest
        t.pop("lines_tail", None)
        t.pop("candidates", None)
        if _size(out) <= MAX_FILE:
            return out
    while out["tunes"] and _size(out) > MAX_FILE:
        out["tunes"].pop()
    while out["report_meta"] and _size(out) > MAX_FILE:
        oldest = min(out["report_meta"], key=lambda k: out["report_meta"][k].get("captured_at") or 0.0)
        del out["report_meta"][oldest]
    while out["notes"] and _size(out) > MAX_FILE:
        out["notes"].pop(next(iter(out["notes"])))
    return out


class State:
    """The in-memory copy, with the file behind it.  `ctx` methods (tune_role, note, meta) let the report store
    read it."""

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.RLock()
        self.data = empty()
        self.dirty = False
        self.last_write = 0.0
        self.warning: str | None = None

    # --- file
    def load(self):
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > READ_LIMIT:
                raise ValueError("too large")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            self._set_aside()
            return
        if raw.get("schema") != SCHEMA:
            raise StateError(f"The state file {self.path} has schema {raw.get('schema')!r}, but this Studio knows "
                             f"schema {SCHEMA}. Move the file away (or pass --state with another path) and start again.")
        with self.lock:
            self.data = bound({**empty(), **raw})
            self.dirty = _size(raw) > MAX_FILE

    def _set_aside(self):
        bad = self.path.with_name(self.path.name + ".bad")
        try:
            os.replace(self.path, bad)
            self.warning = f"The state file could not be read. It was renamed to {bad.name} and the Studio started empty."
        except OSError:
            self.warning = "The state file could not be read. The Studio started empty."

    def write(self, force: bool = False) -> bool:
        """Write now when something changed and the last write is at least a second ago (or `force`)."""
        with self.lock:
            if not self.dirty:
                return False
            now = self.clock()
            if not force and now - self.last_write < MIN_WRITE_GAP:
                return False
            data = fit(copy.deepcopy(self.data))
            self.data = data if _size(data) <= MAX_FILE else self.data
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as e:
                self.warning = f"The state file could not be written: {e.strerror or e}"
                return False
            self.dirty = False
            self.last_write = now
            return True

    def touch(self):
        with self.lock:
            self.dirty = True
        self.write()

    def flush(self):
        """Called on a timer and at exit: a change held back by the one-second gap goes out."""
        self.write(force=False)

    # --- tunes
    def tunes(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.data["tunes"])

    def save_tune(self, record: dict):
        """Insert or replace by id; newest first; the oldest beyond 50 are dropped."""
        with self.lock:
            tunes = [t for t in self.data["tunes"] if t.get("id") != record.get("id")]
            tunes.insert(0, copy.deepcopy(record))
            tunes.sort(key=lambda t: t.get("started_at") or 0.0, reverse=True)
            self.data["tunes"] = tunes[:MAX_TUNES]
        self.touch()

    def set_active_flow(self, record: dict | None):
        with self.lock:
            self.data["active_flow"] = copy.deepcopy(record)
        self.touch()

    def active_flow(self) -> dict | None:
        with self.lock:
            return copy.deepcopy(self.data["active_flow"])

    # --- notes and meta
    def set_note(self, kind: str, ident: str, text: str) -> bool:
        text = str(text)[:NOTE_CHARS]
        with self.lock:
            if kind == "report":
                if text:
                    self.data["notes"].pop(ident, None)       # a re-saved note moves to the newest end
                    self.data["notes"][ident] = text
                else:
                    self.data["notes"].pop(ident, None)
            elif kind == "tune":
                tune = next((t for t in self.data["tunes"] if t.get("id") == ident), None)
                if tune is None:
                    return False
                tune["note"] = text
            else:
                return False
        self.touch()
        return True

    def note(self, report_id: str) -> str:
        with self.lock:
            return self.data["notes"].get(report_id, "")

    def set_meta(self, report_id: str, meta: dict):
        with self.lock:
            self.data["report_meta"][report_id] = copy.deepcopy(meta)
        self.touch()

    def meta(self, report_id: str) -> dict | None:
        with self.lock:
            m = self.data["report_meta"].get(report_id)
            return copy.deepcopy(m) if m else None

    def tune_role(self, report_id: str) -> dict | None:
        """{"id": tune id, "role": "before"|"after"} for a report a tune produced."""
        with self.lock:
            for t in self.data["tunes"]:
                if t.get("before_report") == report_id:
                    return {"id": t.get("id"), "role": "before"}
                if t.get("after_report") == report_id:
                    return {"id": t.get("id"), "role": "after"}
        return None
