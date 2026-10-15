""""Use the settings of run X": the one write to a Strata file the Studio ever does.

It writes the run config (`--config`, strata-<model>.json) with the speed flags a report recorded in its own
`machine.config` (the file's arguments when that run was made).  Only the flags the person ticked change; every other
key and argument stays.  The file as it was goes to `<file>.bak` first, then the new text is written to a temporary
file next to it and renamed over it.  Strata reads the file at its next start; the running engine keeps its settings.

The preview carries the file's sha256; the write is refused when the file changed since (someone, or Strata's tune,
wrote it in between).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .calibparse import same_value
from .reports import ENV_FLAGS, SETTING_FLAGS, arg_value
from .settings import CONFIG_MAX, CONTROLS, SPEED_SETTINGS, check_value

CALIBRATED = ("--pcie-frac", "--spec-min-p", "--pool-workers", "--adapt-every", "--adapt-swaps", "--adapt-decay")


class ConfigWriteError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _read(path) -> tuple[bytes, dict]:
    if not path:
        raise ConfigWriteError(412, "The Studio was started without --config, so it does not know Strata's run config "
                                    "file. Restart it with --config <Strata folder>/strata-<model>.json.")
    p = Path(path)
    try:
        if p.stat().st_size > CONFIG_MAX:
            raise ConfigWriteError(412, "The config file is larger than 1 MiB; the Studio does not write it.")
        raw = p.read_bytes()
    except OSError as e:
        raise ConfigWriteError(412, f"The config file cannot be read ({e.strerror or type(e).__name__}).")
    try:
        cfg = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        raise ConfigWriteError(412, "The config file is not valid JSON; the Studio does not write it.")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("args"), list) or \
            not all(isinstance(a, str) for a in cfg["args"]):
        raise ConfigWriteError(412, "The config file has no list of arguments (\"args\"); the Studio does not write it.")
    return raw, cfg


def _run_values(rep: dict) -> dict | None:
    """{flag: value or None (absent: the engine's default)} as the run's config had them; None when the report does
    not record its config's arguments."""
    st = rep.get("settings") or {}
    if st.get("source") != "report":
        return None
    rows = {r.get("flag"): r for r in st.get("rows") or [] if isinstance(r, dict)}
    if not any(r.get("config") is not None for r in rows.values()):
        return None
    flags = [f for f, _ in SETTING_FLAGS] + [f for f in ENV_FLAGS if f in rows]
    return {f: (rows.get(f) or {}).get("config") for f in flags}


def _file_value(cfg: dict, flag: str):
    if flag.startswith("--"):
        v = arg_value(cfg.get("args"), flag)
    else:
        env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
        v = env.get(flag)
    return None if v is None else str(v)


def preview(config_path, rep: dict) -> dict:
    raw, cfg = _read(config_path)
    values = _run_values(rep)
    if values is None:
        raise ConfigWriteError(409, "This report does not record its run config's arguments, so there is nothing to go "
                                    "back to. Pick a report made by the profile tool with its config.")
    rows = []
    for flag, run in values.items():
        now = _file_value(cfg, flag)
        changed = not same_value(now, run) and not (now is None and run is None)
        rows.append({"flag": flag, "kind": "arg" if flag.startswith("--") else "env", "file": now, "run": run,
                     "changed": changed, "calibrated": flag in CALIBRATED})
    return {"path": str(config_path), "file": Path(config_path).name, "sha": hashlib.sha256(raw).hexdigest(),
            "report": rep.get("id"), "rows": rows, "backup": Path(config_path).name + ".bak"}


def _set_arg(args: list[str], flag: str, value):
    out = list(args)
    while flag in out:                              # drop every occurrence with its value
        i = out.index(flag)
        del out[i:i + 2]
    if value is not None:
        out += [flag, str(value)]
    return out


def _indent(text: str):
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            return len(line) - len(stripped)
        if line.startswith("\t"):
            return "\t"
    return 2


def apply(config_path, rep: dict, flags: list, sha: str, busy_reason: str | None = None) -> dict:
    if busy_reason:
        raise ConfigWriteError(409, busy_reason)
    pv = preview(config_path, rep)
    if not isinstance(sha, str) or sha != pv["sha"]:
        raise ConfigWriteError(409, "The config file changed since the preview. Look at it again before writing.")
    by = {r["flag"]: r for r in pv["rows"]}
    if not isinstance(flags, list) or not flags or not all(isinstance(f, str) for f in flags):
        raise ConfigWriteError(400, "Pick at least one setting to write.")
    unknown = [f for f in flags if f not in by]
    if unknown:
        raise ConfigWriteError(400, f"{unknown[0]} is not a setting this report records.")
    raw, cfg = _read(config_path)
    changes = []
    for f in flags:
        r = by[f]
        if not r["changed"]:
            continue
        if r["kind"] == "arg":
            cfg["args"] = _set_arg(cfg["args"], f, r["run"])
        else:
            env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
            if r["run"] is None:
                env.pop(f, None)
            else:
                env[f] = str(r["run"])
            cfg["env"] = env
        changes.append({"flag": f, "from": r["file"], "to": r["run"]})
    if not changes:
        raise ConfigWriteError(400, "The file already has these values; nothing was written.")
    return _write(config_path, raw, cfg, changes)


def _write(config_path, raw: bytes, cfg: dict, changes: list) -> dict:
    text = raw.decode("utf-8-sig")
    new = json.dumps(cfg, indent=_indent(text), ensure_ascii=False) + ("\n" if text.endswith("\n") else "")
    p = Path(config_path)
    bak = p.with_name(p.name + ".bak")
    tmp = p.with_name(p.name + ".studio-tmp")
    try:
        bak.write_bytes(raw)
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ConfigWriteError(500, f"The config file could not be written ({e.strerror or type(e).__name__}); "
                                    "it is unchanged.")
    return {"ok": True, "path": str(p), "backup": str(bak), "changes": changes,
            "sha": hashlib.sha256(new.encode("utf-8")).hexdigest()}


def file_doc(config_path) -> dict:
    """The run config's identity for a hand edit: its path and sha256."""
    raw, _ = _read(config_path)
    return {"path": str(config_path), "file": Path(config_path).name, "sha": hashlib.sha256(raw).hexdigest(),
            "backup": Path(config_path).name + ".bak"}


def apply_edits(config_path, edits: list, sha: str, busy_reason: str | None = None) -> dict:
    """Hand edits from the Settings page: [{"id": <setting id>, "value": <text> | None for the engine default}].
    Validated against settings.CONTROLS; written with a .bak like `apply`."""
    if busy_reason:
        raise ConfigWriteError(409, busy_reason)
    if not isinstance(edits, list) or not edits:
        raise ConfigWriteError(400, "There is no edit to write.")
    raw, cfg = _read(config_path)
    if not isinstance(sha, str) or sha != hashlib.sha256(raw).hexdigest():
        raise ConfigWriteError(409, "The config file changed since the page read it. Read it again before writing.")
    by_id = {r[0]: r for r in SPEED_SETTINGS}
    changes = []
    for e in edits:
        if not isinstance(e, dict) or e.get("id") not in by_id or e.get("id") not in CONTROLS:
            raise ConfigWriteError(400, f"{(e or {}).get('id')} is not a setting the Studio can edit.")
        rid, value = e["id"], e.get("value")
        value = None if value is None or str(value).strip() in ("", "default") else str(value).strip()
        why = check_value(rid, value)
        if why:
            raise ConfigWriteError(400, f"{by_id[rid][1]}: {why}")
        _, name, kind, _, _, _ = by_id[rid]
        if kind == "arg":
            before = arg_value(cfg["args"], name)
            cfg["args"] = _set_arg(cfg["args"], name, value)
        elif kind == "env":
            env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
            before = env.get(name)
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
            cfg["env"] = env
        else:
            before = cfg.get(name)
            if value is None:
                cfg.pop(name, None)
            else:
                cfg[name] = value
        if not same_value(before, value) and not (before is None and value is None):
            changes.append({"flag": name, "from": None if before is None else str(before), "to": value})
    if not changes:
        raise ConfigWriteError(400, "The file already has these values. Nothing was written.")
    return _write(config_path, raw, cfg, changes)
