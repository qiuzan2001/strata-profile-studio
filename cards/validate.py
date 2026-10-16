"""Validate a Strata result card (schema strata-card/1) with the standard library only.

    python3 validate.py card.json [more.json ...]       exit 0 when each file is valid

The checks come from cards/schema/strata-card-1.schema.json: this file reads the schema and applies its keywords
(type, const, enum, pattern, minimum, maximum, maxLength, required, properties, additionalProperties, items,
minItems, maxItems, anyOf), then the checks that one schema keyword cannot state: the window parts against the
window time, the measured PCIe link against the nominal speed, the range of the median, the date, the size, and the
id.  The cards site's Action and the Studio use this same file.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "strata-card-1.schema.json"
MAX_BYTES = 16 * 1024
NOMINAL_GBS = {1: 0.25, 2: 0.5, 3: 0.985, 4: 1.969, 5: 3.938, 6: 7.563, 7: 15.125}   # per lane, one direction
_SCHEMA = None


def schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


def card_id(card: dict) -> str:
    """The card's id: the first 10 characters of base32(sha256(the JSON without "id" and "name", sorted keys, no
    spaces)), in lower case.  The same data gives the same id."""
    body = {k: v for k, v in card.items() if k not in ("id", "name")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b32encode(hashlib.sha256(canon).digest()).decode("ascii").lower()[:10]


def _type_ok(v, t) -> bool:
    if isinstance(t, list):
        return any(_type_ok(v, x) for x in t)
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return False


def _check(v, s: dict, where: str, errors: list):
    if "anyOf" in s:
        if not any(not _errors_of(v, sub, where) for sub in s["anyOf"]):
            sub_errors = [_errors_of(v, sub, where) for sub in s["anyOf"]]
            errors.extend(min(sub_errors, key=len))
        return
    if "const" in s and v != s["const"]:
        errors.append(f"{where}: must be {s['const']!r}.")
        return
    if "type" in s and not _type_ok(v, s["type"]):
        errors.append(f"{where}: must be {' or '.join(s['type']) if isinstance(s['type'], list) else s['type']}.")
        return
    if v is None:
        return
    if "enum" in s and v not in s["enum"]:
        errors.append(f"{where}: {v!r} is not one of {s['enum']}.")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{where}: {v} is below {s['minimum']}.")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{where}: {v} is above {s['maximum']}.")
    if isinstance(v, str):
        if "maxLength" in s and len(v) > s["maxLength"]:
            errors.append(f"{where}: longer than {s['maxLength']} characters.")
        if "pattern" in s and not re.search(s["pattern"], v):
            errors.append(f"{where}: {v[:40]!r} has characters or a form that the card does not allow.")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errors.append(f"{where}: fewer than {s['minItems']} items.")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{where}: more than {s['maxItems']} items.")
        if "items" in s:
            for i, x in enumerate(v):
                _check(x, s["items"], f"{where}[{i}]", errors)
    if isinstance(v, dict):
        props = s.get("properties", {})
        for k in s.get("required", []):
            if k not in v:
                errors.append(f"{where}.{k}: missing.")
        if s.get("additionalProperties") is False:
            for k in v:
                if k not in props:
                    errors.append(f"{where}.{k}: not a field of the card.")
        for k, sub in props.items():
            if k in v:
                _check(v[k], sub, f"{where}.{k}", errors)


def _errors_of(v, s, where):
    e = []
    _check(v, s, where, e)
    return e


def validate(card, today: _dt.date | None = None, check_id: bool = True) -> list[str]:
    """The reasons the card is not valid; an empty list when it is."""
    errors: list[str] = []
    try:
        size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return ["card: not JSON data."]
    if size > MAX_BYTES:
        return [f"card: {size} bytes, more than {MAX_BYTES}."]
    _check(card, schema(), "card", errors)
    if errors or not isinstance(card, dict):
        return errors
    for i, d in enumerate(card["decode"]):
        if not d["min"] <= d["tps"] <= d["max"]:
            errors.append(f"card.decode[{i}]: the median {d['tps']} is not between min and max.")
        w, ms = d.get("window_ms"), d.get("ms_per_window")
        if w and ms:
            total = sum(w.values())
            if abs(total - ms) > 0.05 * ms + 0.05:
                errors.append(f"card.decode[{i}].window_ms: the parts add up to {total:.2f} ms, not {ms} ms (5 %).")
    p = card["pc"]["pcie"]
    if p["measured_gbs"] is not None and p["gen"] and p["width"]:
        nominal = NOMINAL_GBS[p["gen"]] * p["width"]
        if p["measured_gbs"] > 1.1 * nominal:
            errors.append(f"card.pc.pcie.measured_gbs: {p['measured_gbs']} GB/s is more than 1.1 x the nominal "
                          f"{nominal:.1f} GB/s of Gen {p['gen']} x{p['width']}.")
    try:
        day = _dt.date.fromisoformat(card["date"])
    except ValueError:
        errors.append("card.date: not a date.")
    else:
        if day > (today or _dt.date.today()) + _dt.timedelta(days=1):
            errors.append(f"card.date: {card['date']} is in the future.")
    if check_id and card["id"] != card_id(card):
        errors.append(f"card.id: {card['id']} is not the id of this data ({card_id(card)}).")
    return errors


def main(argv=None) -> int:
    bad = 0
    for name in (argv if argv is not None else sys.argv[1:]):
        try:
            card = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{name}: cannot read ({type(e).__name__}).")
            bad += 1
            continue
        errs = validate(card)
        print(f"{name}: {'valid' if not errs else 'not valid'}")
        for e in errs:
            print(f"  {e}")
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
