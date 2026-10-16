"""Reset on the Settings page, and the engine's pack rule before each config write.

The case from the user's PC (2026-10-15): "Reset all to the engine defaults" removed --spec 4, --expert-cache auto,
--max-context 262144, --kv-resident 32768 and STRATA_PREFILL_CPU_SHARE_MAX from a native (IQ) pack's config. The
engine then refused to start: "a native (IQ) pack: it needs --native SHARD1, --spec T (T >= 2) and --prefill CHUNK".
The config below has the shape of that file (setup.py's args for a Swift IQ3_S pack) plus calibrated values.
"""
import hashlib
import json
import unittest

import support  # noqa: F401 - the import path
from studiolib import configwrite, settings
from support import tmpdir

SETUP_ARGS = ["--pack", "/s/packs/swift-iq3_s", "--native", "/s/models/IQ3_S-00001-of-00002.gguf",
              "--ple-gguf", "/s/models/IQ3_S-00001-of-00002.gguf", "--expert-profile", "/s/data/expert-profile.bin",
              "--expert-cache", "auto", "--prefill", "auto", "--spec", "4", "--mtp", "/s/packs/swift-iq3_s/rt",
              "--max-context", "262144", "--kv", "int8", "--kv-resident", "32768"]
CALIBRATED = ["--pcie-frac", "0.45", "--spec-min-p", "0.6", "--pool-workers", "7", "--adapt-every", "1",
              "--adapt-swaps", "80", "--adapt-decay", "0.97"]
SETUP_FLAGS = ("--spec", "--prefill", "--expert-cache", "--max-context", "--kv-resident", "--kv", "--mtp", "--native",
               "--pack", "--ple-gguf", "--expert-profile")


def write_cfg(test, args=None, env=None):
    p = tmpdir(test) / "strata-swift-iq3_s.json"
    p.write_text(json.dumps({"exe": "/s/engine/strata", "args": args or SETUP_ARGS + CALIBRATED,
                             "env": env if env is not None else {"STRATA_PREFILL_CPU_SHARE_MAX": "2048"}}, indent=2) + "\n")
    return p


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def without(args, *flags):
    out = list(args)
    for f in flags:
        if f in out:
            i = out.index(f)
            del out[i:i + 2]
    return out


def arg(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


class Groups(unittest.TestCase):
    def test_every_editable_setting_has_a_group_and_a_reset(self):
        for rid, c in settings.CONTROLS.items():
            self.assertIn(c.get("group"), ("calibration", "setup", "user"), rid)
            self.assertIn("reset_to", c, rid)
        groups = {rid: c["group"] for rid, c in settings.CONTROLS.items()}
        for rid in ("pcie_frac", "spec_min_p", "pool_workers", "adapt_every", "adapt_swaps", "adapt_decay",
                    "prefill_cpu_share", "prefill_cpu_share_max"):
            self.assertEqual(groups[rid], "calibration", rid)
        for rid in ("spec", "expert_cache", "max_context", "kv_resident"):
            self.assertEqual(groups[rid], "setup", rid)
            self.assertNotIn("default", settings.CONTROLS[rid]["specials"], rid)    # no "remove" on the page

    def test_the_resets_are_the_calibrations_and_setups_own(self):
        # Strata's tools/calibrate.py DEFAULTS: --spec-min-p 0.5 (setup's), the rest the engine's choice
        self.assertEqual(settings.CONTROLS["spec_min_p"]["reset_to"], "0.5")
        self.assertIsNone(settings.CONTROLS["pcie_frac"]["reset_to"])
        self.assertIsNone(settings.CONTROLS["pool_workers"]["reset_to"])
        # setup.py: --spec 4 and --expert-cache auto always; --max-context and --kv-resident fit this PC: no reset
        self.assertEqual((settings.CONTROLS["spec"]["reset_to"], settings.CONTROLS["expert_cache"]["reset_to"]), ("4", "auto"))
        self.assertEqual(settings.CONTROLS["max_context"]["reset_to"], settings.KEEP)
        self.assertEqual(settings.CONTROLS["kv_resident"]["reset_to"], settings.KEEP)


class ResetCalibrated(unittest.TestCase):
    def test_the_page_reset_keeps_every_setup_flag(self):
        p = write_cfg(self)
        edits = [{"id": rid, "value": c["reset_to"]} for rid, c in settings.CONTROLS.items() if c["group"] == "calibration"]
        configwrite.apply_edits(p, edits, sha(p))
        cfg = json.loads(p.read_text())
        a = cfg["args"]
        for f in SETUP_FLAGS:
            self.assertEqual(arg(a, f), arg(SETUP_ARGS, f), f)
        self.assertEqual(arg(a, "--spec-min-p"), "0.5")
        for f in ("--pcie-frac", "--pool-workers", "--adapt-every", "--adapt-swaps", "--adapt-decay"):
            self.assertNotIn(f, a)
        self.assertNotIn("STRATA_PREFILL_CPU_SHARE_MAX", cfg["env"])
        self.assertIsNone(configwrite.pack_rule_error(cfg))


class SetupFlags(unittest.TestCase):
    def test_removing_a_setup_flag_is_refused(self):
        for rid in ("spec", "expert_cache", "max_context", "kv_resident"):
            p = write_cfg(self)
            before = p.read_bytes()
            with self.assertRaises(configwrite.ConfigWriteError) as e:
                configwrite.apply_edits(p, [{"id": rid, "value": None}], sha(p))
            self.assertEqual(e.exception.status, 400)
            self.assertIn("setup wrote this flag", e.exception.message)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse(p.with_name(p.name + ".bak").exists())

    def test_a_setup_flag_can_take_another_value(self):
        p = write_cfg(self)
        configwrite.apply_edits(p, [{"id": "max_context", "value": "131072"}], sha(p))
        self.assertEqual(arg(json.loads(p.read_text())["args"], "--max-context"), "131072")


class PackRule(unittest.TestCase):
    def test_the_broken_file_from_the_users_pc_is_refused(self):
        broken = {"args": without(SETUP_ARGS, "--spec", "--expert-cache", "--max-context", "--kv-resident")}
        why = configwrite.pack_rule_error(broken)
        self.assertIn("swift-iq3_s is a native (IQ) pack: it needs --native SHARD1, --spec T (T >= 2) and --prefill CHUNK", why)

    def test_a_write_that_breaks_the_rule_is_refused_with_the_engines_sentence(self):
        for edit in ({"id": "spec", "value": "1"}, {"id": "spec", "value": "0"}):
            p = write_cfg(self)
            before = p.read_bytes()
            with self.assertRaises(configwrite.ConfigWriteError) as e:
                configwrite.apply_edits(p, [edit], sha(p))
            self.assertEqual(e.exception.status, 400)
            self.assertIn("it needs --native SHARD1, --spec T (T >= 2) and --prefill CHUNK", e.exception.message)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse(p.with_name(p.name + ".bak").exists())

    def test_no_prefill_is_refused(self):
        args = without(SETUP_ARGS, "--prefill")
        self.assertIsNotNone(configwrite.pack_rule_error({"args": args}))
        self.assertIsNotNone(configwrite.pack_rule_error({"args": args + ["--prefill", "0"]}))
        self.assertIsNone(configwrite.pack_rule_error({"args": args + ["--prefill", "2048"]}))

    def test_a_pack_that_is_not_native_has_no_rule(self):
        self.assertIsNone(configwrite.pack_rule_error({"args": ["--pack", "/p/dense", "--spec", "0"]}))

    def test_a_valid_write_passes(self):
        p = write_cfg(self)
        configwrite.apply_edits(p, [{"id": "spec", "value": "6"}], sha(p))
        self.assertEqual(arg(json.loads(p.read_text())["args"], "--spec"), "6")


if __name__ == "__main__":
    unittest.main()
