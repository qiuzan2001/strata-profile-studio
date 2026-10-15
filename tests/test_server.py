import hashlib
import json
import os
import re
import subprocess
import sys
import unittest

import support
from fakestrata import FakeStrata, request_doc
from studiolib.requests import REQUEST_KEYS
from studiolib.state import State, StateError
from support import ROOT, request, start_studio, wait_for

KEY = "k3y-for-both-7788"
REPORT_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-profile-[a-z0-9.-]+$")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)
        self.results = support.copy_fixtures(self.tmp / "results")
        self.fake = FakeStrata(self.results, history=True, settings=True).start()
        self.addCleanup(self.fake.stop)

    def studio(self, **kw):
        s = start_studio(self, self.fake, self.results, state_path=self.tmp / "state.json", **kw)
        wait_for(lambda: s.poller.status == "ok" and s.poller.profile and s.poller.ring, what="a connected poller")
        return s


class Routes(Base):
    """Every /api route answers with exactly the keys the design writes."""

    def test_state_keys(self):
        s = self.studio()
        st, hdrs, doc = request(s, "GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(set(doc), {"studio", "strata", "flow", "estimates"})
        self.assertEqual(set(doc["studio"]), {"version", "started_at", "strata_url", "results_dir", "results_dir_exists",
                                              "config_path", "key_set", "auth_required", "read_only"})
        self.assertEqual(set(doc["strata"]), {"status", "checked_at", "message", "model", "loaded", "max_context",
                                              "api_key", "profile", "routes", "run", "tune"})
        self.assertEqual(set(doc["strata"]["tune"]), {"available", "unavailable_reason", "state", "elapsed_s",
                                                      "started_by_studio"})
        self.assertEqual(set(doc["strata"]["run"]), {"state", "elapsed_s"})
        self.assertEqual(set(doc["strata"]["routes"]), {"history", "settings"})
        self.assertEqual(set(doc["flow"]), {"id", "state", "stage"})
        self.assertEqual(set(doc["estimates"]), {"profile_s", "tune_s"})
        self.assertEqual(doc["strata"]["status"], "ok")
        self.assertEqual(doc["strata"]["routes"], {"history": True, "settings": True})
        self.assertEqual(doc["estimates"]["profile_s"], 47.9)         # the newest report's elapsed time
        self.assertEqual(hdrs["cache-control"], "no-store")

    def test_live_keys_and_sample_keys(self):
        s = self.studio()
        _, _, doc = request(s, "GET", "/api/live?since=0")
        self.assertEqual(set(doc), {"max", "last_seq", "samples", "spans"})
        self.assertEqual(doc["max"], 300)
        sample = doc["samples"][0]
        self.assertEqual(set(sample), {"seq", "seen_at", "source", "tag", "open", "after_load", "kind", "run", "late",
                                       "report_request",
                                       *REQUEST_KEYS})
        self.assertEqual(sample["window_ms"]["adapt wait"], 1.44)
        self.assertEqual(sample["source"], "last")
        status, _, _ = request(s, "GET", "/api/live?since=abc")
        self.assertEqual(status, 400)

    def test_reports_and_one_report_keys(self):
        s = self.studio()
        _, _, doc = request(s, "GET", "/api/reports")
        self.assertEqual(set(doc), {"dir", "exists", "reports"})
        self.assertEqual(set(doc["reports"][0]), {"id", "date", "order", "gpu", "model", "quant", "context",
                                                  "strata_version", "elapsed_s", "chat_tps", "ok", "error",
                                                  "settings_short", "tune", "note", "headline", "mtime"})
        _, _, rep = request(s, "GET", "/api/reports/" + support.A)
        self.assertEqual(set(rep), {"id", "path", "date", "machine", "params", "elapsed_s", "settings", "prompts",
                                    "decode", "agent", "chat_runs", "findings", "skipped", "failures",
                                    "note", "tune", "environment", "requests"})
        self.assertEqual(set(rep["machine"]), {"gpu", "gpu_vram_mib", "cpu", "ram_gib", "model", "quant", "context",
                                               "strata_version", "engine_version"})
        self.assertEqual(set(rep["settings"]), {"source", "rows"})
        self.assertEqual(set(rep["prompts"][0]), {"label", "tokens", "prompt_tps", "gpu_ms", "groups_ms", "groups_share"})
        self.assertEqual(set(rep["decode"][0]), {"kind", "context", "tps", "hit_rate", "pcie_share", "ms_per_window",
                                                 "tokens_per_window", "window_ms", "cpu_experts", "cpu_entries"})
        self.assertEqual(set(rep["agent"][0]), {"turn", "prompt_tokens", "reused", "read", "prompt_tps", "decode_tps",
                                                "hit_rate", "ms_per_window", "window_ms"})
        self.assertEqual(rep["path"], str(self.results / support.A))

    def test_compare_keys(self):
        s = self.studio()
        st, _, doc = request(s, "GET", f"/api/compare?a={support.A}&b={support.B}")
        self.assertEqual(st, 200)
        self.assertEqual(set(doc), {"a", "b", "warnings", "floors", "verdicts", "rows", "settings", "sign"})
        self.assertEqual(set(doc["floors"]), {"minimum", "assumed", "pairs"})
        self.assertEqual(set(doc["verdicts"][0]), {"group", "key", "state", "text", "floor_note"})
        self.assertIn("assumed floor", doc["verdicts"][0]["text"])
        self.assertEqual(set(doc["rows"][0]), {"group", "key", "metric", "a", "b", "delta", "pct", "better", "floor",
                                               "floor_kind", "floor_assumed", "state"})
        self.assertEqual(set(doc["settings"][0]), {"flag", "a", "b", "changed"})
        for bad in (f"a={support.A}&b={support.BROKEN}", f"a={support.A}&b=nope", "a=../x&b=y", ""):
            st, _, doc = request(s, "GET", "/api/compare?" + bad)
            self.assertEqual(st, 404, bad)
            self.assertIn("message", doc["error"])

    def test_compare_takes_its_noise_floor_from_same_settings_reports_in_the_folder(self):
        R = "2026-10-17-profile-rtx-5070-ti-swift-1-5-iq3-s"
        support.copy_fixtures(self.results, (R, R + "-3", R + "-4"))
        s = self.studio()
        st, _, doc = request(s, "GET", f"/api/compare?a={R}-3&b={R}-4")
        self.assertEqual(st, 200)
        self.assertEqual(doc["floors"]["pairs"], [{"a": R + "-3", "b": R + "-4", "label": "runs 3 and 4, same settings"}])
        v = doc["verdicts"][0]
        self.assertEqual(v["floor_note"], "noise floor from runs 3 and 4, same settings")
        self.assertNotIn("assumed floor", v["text"])

    def test_settings_keys_with_profile_settings_and_without(self):
        s = self.studio()
        _, _, doc = request(s, "GET", "/api/settings")
        self.assertEqual(set(doc), {"source", "file", "loaded", "rows", "note"})
        self.assertEqual(doc["source"], "profile/settings")
        self.assertEqual(set(doc["rows"][0]), {"id", "name", "now", "next_load", "run_config", "calibrated", "about"})
        self.assertEqual(doc["rows"][0]["now"], "0.55")
        self.assertIn("PCIe", doc["rows"][0]["about"])

    def test_settings_from_metrics_and_from_config(self):
        cfg = self.tmp / "strata-x.json"
        cfg.write_text(json.dumps({"args": ["--pcie-frac", "0.35", "--spec-min-p", "0.7"], "env": {}}))
        self.fake.settings_on = False
        s = self.studio(config=str(cfg))
        _, _, doc = request(s, "GET", "/api/settings")
        self.assertEqual(doc["source"], "metrics")
        rows = {r["id"]: r for r in doc["rows"]}
        self.assertEqual([r["id"] for r in doc["rows"]], ["pcie_frac", "spec_min_p", "pool_workers", "kv_resident",
                                                          "max_context", "expert_cache"])
        self.assertEqual((rows["pcie_frac"]["now"], rows["pcie_frac"]["run_config"]), ("0.55", "0.35"))
        self.assertEqual(rows["expert_cache"]["now"], "3880 experts (7586 MiB)")
        self.assertIn("/metrics", doc["note"])
        # unreachable: from the config
        self.fake.stop()
        wait_for(lambda: s.poller.status == "unreachable", what="unreachable")
        _, _, doc = request(s, "GET", "/api/settings")
        self.assertEqual(doc["source"], "config")
        self.assertEqual({r["id"]: r for r in doc["rows"]}["pcie_frac"]["run_config"], "0.35")
        st = request(s, "GET", "/api/state")[2]["strata"]
        self.assertEqual(st["status"], "unreachable")
        self.assertIn("Strata does not answer at", st["message"])
        # Reports and Compare still work
        self.assertEqual(request(s, "GET", f"/api/compare?a={support.A}&b={support.B}")[0], 200)

    def test_settings_none(self):
        self.fake.stop()
        s = start_studio(self, self.fake, self.results, state_path=self.tmp / "state.json")
        wait_for(lambda: s.poller.checked_at, what="a first check")
        _, _, doc = request(s, "GET", "/api/settings")
        self.assertEqual((doc["source"], doc["rows"]), ("none", []))
        self.assertIn("no --config", doc["note"])

    def test_tune_flow_idle_and_tunes_and_run_profile(self):
        s = self.studio()
        _, _, doc = request(s, "GET", "/api/tune-flow")
        self.assertEqual(set(doc), {"id", "state", "stage", "started_at", "elapsed_s", "stages", "lines", "candidates",
                                    "summary", "tok_s", "before_report", "after_report", "settings", "reload", "error",
                                    "compare", "can_cancel", "can_retry_after"})
        self.assertEqual(doc["state"], "idle")
        self.assertEqual(request(s, "GET", "/api/tunes")[2], {"tunes": []})
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": None})
        self.assertEqual((st, doc), (200, {"status": "started"}))
        run = self.fake.requests_to("/profile/run")[0]
        self.assertEqual(run["query"], "")
        wait_for(lambda: request(s, "GET", "/api/run-profile")[2]["state"] == "done", what="the run to end")
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": 4096})
        self.assertEqual(st, 200)
        self.assertEqual(self.fake.requests_to("/profile/run")[1]["query"], "long=4096")
        # Strata's 409 is passed on with its own sentence
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": None})
        self.assertEqual(st, 409)
        self.assertIn("already going", doc["error"]["message"])
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": "lots"})
        self.assertEqual(st, 400)

    def test_notes(self):
        s = self.studio()
        st, _, doc = request(s, "POST", "/api/notes", {"kind": "report", "id": support.A, "text": "before the tune"})
        self.assertEqual((st, doc), (200, {"ok": True}))
        self.assertEqual(request(s, "GET", "/api/reports/" + support.A)[2]["note"], "before the tune")
        listed = {r["id"]: r for r in request(s, "GET", "/api/reports")[2]["reports"]}
        self.assertEqual(listed[support.A]["note"], "before the tune")
        request(s, "POST", "/api/notes", {"kind": "report", "id": support.A, "text": ""})
        self.assertEqual(request(s, "GET", "/api/reports/" + support.A)[2]["note"], "")
        self.assertEqual(request(s, "POST", "/api/notes", {"kind": "report", "id": support.A, "text": "x" * 2001})[0], 400)
        self.assertEqual(request(s, "POST", "/api/notes", {"kind": "report", "id": "../etc", "text": "x"})[0], 404)
        self.assertEqual(request(s, "POST", "/api/notes", {"kind": "tune", "id": "tune-none", "text": "x"})[0], 404)
        self.assertEqual(request(s, "POST", "/api/notes", {"kind": "other", "id": "x", "text": "x"})[0], 400)

    def test_report_ids_with_dots_or_symlinks_are_404_over_http(self):
        outside = support.tmpdir(self)
        support.copy_fixtures(outside, (support.A,))
        os.symlink(outside / support.A, self.results / "2026-10-16-profile-link")
        s = self.studio()
        for rid in ("..%2F" + support.A, "2026-10-16-profile-link", "%2e%2e", "2026-10-15-profile-..", "nope"):
            st, _, doc = request(s, "GET", "/api/reports/" + rid)
            self.assertEqual(st, 404, rid)
        st, _, doc = request(s, "GET", "/api/reports/" + support.BROKEN)
        self.assertEqual((st, doc["error"]["message"]), (404, "results.json could not be read"))
        self.assertEqual(request(s, "GET", "/api/nothing")[0], 404)


class Checks(Base):
    def test_host_check(self):
        s = self.studio()
        for host in ("evil.example", "evil.example:%d" % s.port, "127.0.0.1", "127.0.0.1:1"):
            st, _, doc = request(s, "GET", "/api/state", headers={"Host": host})
            self.assertEqual(st, 403, host)
        st, _, _ = request(s, "GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(st, 403)
        for host in (f"localhost:{s.port}", f"127.0.0.1:{s.port}", f"[::1]:{s.port}"):
            self.assertEqual(request(s, "GET", "/api/state", headers={"Host": host})[0], 200, host)

    def test_origin_and_content_type_on_posts(self):
        s = self.studio()
        st, _, _ = request(s, "POST", "/api/tune-flow", {"long": None}, headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = request(s, "POST", "/api/tune-flow", raw=b"", headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)                             # curl -X POST with no body, as in the checklist
        st, _, _ = request(s, "POST", "/api/notes", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(st, 415)
        st, _, _ = request(s, "POST", "/api/notes", raw=b"{}")
        self.assertEqual(st, 415)
        st, _, _ = request(s, "POST", "/api/notes", raw=b"x" * 70000, headers={"Content-Type": "application/json"})
        self.assertEqual(st, 413)
        st, _, _ = request(s, "POST", "/api/notes", raw=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(st, 400)
        own = {"Origin": f"http://127.0.0.1:{s.port}"}
        st, _, _ = request(s, "POST", "/api/notes", {"kind": "report", "id": support.A, "text": "ok"}, headers=own)
        self.assertEqual(st, 200)
        self.assertEqual(len(self.fake.requests_to("/profile/tune")), 0)

    def test_non_loopback_host_without_a_key_exits(self):
        r = subprocess.run([sys.executable, str(ROOT / "studio.py"), "--host", "0.0.0.0", "--port", "0",
                            "--results", str(self.results), "--state", str(self.tmp / "s.json")],
                           capture_output=True, text=True, timeout=20, env={k: v for k, v in os.environ.items()
                                                                           if k != "STRATA_KEY"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--key", r.stderr)

    def test_with_a_key_the_bearer_is_required(self):
        fake = FakeStrata(self.results, key=KEY).start()
        self.addCleanup(fake.stop)
        s = start_studio(self, fake, self.results, key=KEY, host="127.0.0.2", state_path=self.tmp / "k.json")
        h = {"Host": f"127.0.0.2:{s.port}"}
        st, hdrs, doc = request(s, "GET", "/api/state", headers=h, host="127.0.0.2")
        self.assertEqual(st, 401)
        self.assertEqual(hdrs["www-authenticate"], "Bearer")
        st, _, _ = request(s, "GET", "/api/state", headers={**h, "Authorization": "Bearer wrong"}, host="127.0.0.2")
        self.assertEqual(st, 401)
        st, _, doc = request(s, "GET", "/api/state", headers={**h, "Authorization": f"Bearer {KEY}"}, host="127.0.0.2")
        self.assertEqual(st, 200)
        self.assertTrue(doc["studio"]["auth_required"])
        # POSTs too
        st, _, _ = request(s, "POST", "/api/notes", {"kind": "report", "id": support.A, "text": "x"}, headers=h,
                           host="127.0.0.2")
        self.assertEqual(st, 401)
        # the page itself loads without it (it has to ask for the key)
        self.assertEqual(request(s, "GET", "/", headers=h, host="127.0.0.2")[0], 200)

    def test_strata_needs_a_key_and_the_studio_has_none(self):
        fake = FakeStrata(self.results, key=KEY).start()
        self.addCleanup(fake.stop)
        s = start_studio(self, fake, self.results, state_path=self.tmp / "n.json")
        wait_for(lambda: s.poller.status == "key_needed", what="key_needed")
        st = request(s, "GET", "/api/state")[2]["strata"]
        self.assertEqual(st["status"], "key_needed")
        self.assertIn("Strata needs an API key", st["message"])
        self.assertTrue(st["api_key"])
        self.assertEqual(request(s, "GET", f"/api/compare?a={support.A}&b={support.B}")[0], 200)
        st, _, doc = request(s, "POST", "/api/tune-flow", {"long": None})
        self.assertEqual(st, 412)                              # never 401: that is the Studio's own key's code
        st, _, doc = request(s, "POST", "/api/run-profile", {"long": None})
        self.assertEqual(st, 412)

    def test_a_wrong_key_is_key_needed_too(self):
        fake = FakeStrata(self.results, key=KEY).start()
        self.addCleanup(fake.stop)
        s = start_studio(self, fake, self.results, key="not-the-key", state_path=self.tmp / "w.json")
        wait_for(lambda: s.poller.status == "key_needed", what="key_needed")

    def test_security_headers_and_static_whitelist(self):
        s = self.studio()
        st, h, body = request(s, "GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", h["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["referrer-policy"], "no-referrer")
        for name, ctype in (("app.js", "javascript"), ("charts.js", "javascript"), ("now.js", "javascript"),
                            ("diagram.js", "javascript"), ("style.css", "text/css"),
                            ("favicon.svg", "image/svg+xml"), ("vendor/chart-4.4.1.umd.js", "javascript")):
            st, h, _ = request(s, "GET", "/static/" + name)
            self.assertEqual(st, 200, name)
            self.assertIn(ctype, h["content-type"])
        for name in ("../studio.py", "vendor/README.md", "index.html", "vendor/LICENSE-chart.js.md", "nope.js",
                     "%2e%2e/studio.py", "vendor/../app.js"):
            self.assertEqual(request(s, "GET", "/static/" + name)[0], 404, name)
        self.assertEqual(request(s, "GET", "/studio.py")[0], 404)
        self.assertEqual(request(s, "GET", "/studiolib/server.py")[0], 404)


class Proxy(Base):
    """--allow-host: a reverse proxy (tailscale serve) in front of the loopback listener."""

    def test_default_stays_strict_and_an_allowed_name_is_accepted(self):
        s = self.studio()
        self.assertEqual(request(s, "GET", "/api/state", headers={"Host": "ub01.example.ts.net:8444"})[0], 403)
        import studio
        s2 = studio.Studio(self.fake.url, str(self.results), port=0, state_path=str(self.tmp / "p.json"),
                           timing=support.fast_timing(), allow_hosts=["ub01.example.ts.net:8444", "other.example"])
        s2.start()
        self.addCleanup(s2.stop)
        for host, want in (("ub01.example.ts.net:8444", 200), ("UB01.example.ts.net:8444", 200),
                           ("ub01.example.ts.net:9999", 403), ("ub01.example.ts.net", 403),
                           ("other.example", 200), ("other.example:1234", 200), ("evil.example", 403),
                           (f"127.0.0.1:{s2.port}", 200)):
            self.assertEqual(request(s2, "GET", "/api/state", headers={"Host": host})[0], want, host)
        # still bound to 127.0.0.1 only
        self.assertEqual(s2.server.server_address[0], "127.0.0.1")
        # the page's POSTs carry Origin https://<name> behind a TLS-terminating proxy
        h = {"Host": "ub01.example.ts.net:8444"}
        body = {"kind": "report", "id": support.A, "text": "via proxy"}
        self.assertEqual(request(s2, "POST", "/api/notes", body, headers={**h, "Origin": "https://ub01.example.ts.net:8444"})[0], 200)
        self.assertEqual(request(s2, "POST", "/api/notes", body, headers={**h, "Origin": "http://ub01.example.ts.net:8444"})[0], 200)
        self.assertEqual(request(s2, "POST", "/api/notes", body, headers={**h, "Origin": "https://evil.example"})[0], 403)
        # https is accepted only for an allowed name, not for the local ones
        own = {"Host": f"127.0.0.1:{s2.port}", "Origin": f"https://127.0.0.1:{s2.port}"}
        self.assertEqual(request(s2, "POST", "/api/notes", body, headers=own)[0], 403)

    def test_a_key_makes_the_bearer_required_behind_a_proxy(self):
        fake = FakeStrata(self.results, key=KEY).start()
        self.addCleanup(fake.stop)
        import studio
        s = studio.Studio(fake.url, str(self.results), key=KEY, port=0, state_path=str(self.tmp / "pk.json"),
                          timing=support.fast_timing(), allow_hosts=["ub01.example.ts.net"])
        s.start()
        self.addCleanup(s.stop)
        h = {"Host": "ub01.example.ts.net"}
        self.assertEqual(request(s, "GET", "/api/state", headers=h)[0], 401)
        self.assertEqual(request(s, "GET", "/api/state", headers={**h, "Authorization": f"Bearer {KEY}"})[0], 200)

    def test_cli_help_names_the_flag(self):
        r = subprocess.run([sys.executable, str(ROOT / "studio.py"), "--help"], capture_output=True, text=True, timeout=20)
        self.assertIn("--allow-host", r.stdout)


class LiveDedupe(Base):
    def test_same_stamp_updates_in_place_and_history_backfills_once(self):
        s = self.studio()
        p = s.poller
        base = len(p.ring)
        # a request whose hit/kv lines have not been written yet
        self.fake.set_last(request_doc(prompt_tokens=500, generated=40, prompt_ms=400.0, decode_ms=700.0, with_hit=False))
        wait_for(lambda: len(p.ring) == base + 1, what="a new sample")
        sample = p.ring[-1]
        self.assertTrue(sample["open"])
        self.assertIsNone(sample["hit_rate"])
        seq = sample["seq"]
        # the lines arrive: the same request, now with its hit rate
        self.fake.set_last(request_doc(prompt_tokens=500, generated=40, prompt_ms=400.0, decode_ms=700.0, hit=0.8))
        wait_for(lambda: p.ring[-1]["hit_rate"] == 0.8, what="the update")
        self.assertEqual(len(p.ring), base + 1)
        self.assertEqual((p.ring[-1]["seq"], p.ring[-1]["open"]), (seq, False))
        # a page asking since the newest seq still gets the updated sample, for a while
        for old in p.ring[:-1]:
            old["_updated"] = 0.0                                  # only the newest was touched just now
        got = request(s, "GET", f"/api/live?since={seq}")[2]["samples"]
        self.assertEqual([(x["seq"], x["hit_rate"]) for x in got], [(seq, 0.8)])
        # history: one entry already in the ring and two new ones
        now = p.clock()
        entry = lambda n, t, **kw: {"n": n, "seen_at": t, "open": False, "prompt_tokens": 100 + n, "reused": 0,   # noqa: E731
                                    "read": 100 + n, "prompt_tok_s": 500.0, "prompt_ms": 200.0 + n,
                                    "prompt_gpu_ms": 150.0, "prompt_top": "attention", "generated": 20,
                                    "decode_tok_s": 70.0, "decode_ms": 285.0 + n, "ms_per_window": 28.0,
                                    "tokens_per_window": 2.0, "ms": {"GPU": 9.0, "CPU experts": 15.0, "draft": 2.0,
                                                                     "adapt wait": 1.0, "other": 1.0},
                                    "hit_rate": 0.7, "pcie_share": 0.1, "kv_hit": 0.9, **kw}
        dup = {"n": 9, "seen_at": now, "open": False, "prompt_tokens": 500, "reused": 0, "read": 500,
               "prompt_tok_s": 1250.0, "prompt_ms": 400.0, "generated": 40, "decode_tok_s": 57.1, "decode_ms": 700.0}
        self.fake.history_entries = [entry(1, now - 30), entry(2, now - 20), dup]
        wait_for(lambda: len(p.ring) == base + 3, what="the backfill")
        import time
        time.sleep(0.4)                                            # several history polls: nothing is added twice
        self.assertEqual(len(p.ring), base + 3)
        sources = [x["source"] for x in p.ring]
        self.assertEqual(sources.count("history"), 2)
        hist = [x for x in p.ring if x["source"] == "history"]
        self.assertTrue(all(x["prompt_groups_ms"] is None for x in hist))
        self.assertEqual(hist[0]["window_ms"]["GPU"], 9.0)
        seen = [x["seen_at"] for x in p.ring]
        self.assertEqual(seen, sorted(seen))                       # inserted in seen_at order

    def test_profile_off_fills_samples_from_metrics(self):
        self.fake.profile = False
        self.fake.metrics_requests = [{"time": 1000.0, "duration_s": 3.0, "prompt_tokens": 70, "reused": 0,
                                       "prompt_read": 70, "prompt_ms": 140.0, "decode_ms": 500.0,
                                       "decode_tok_s": 61.2, "output_tokens": 30, "hit_rate": 0.7, "pcie_share": 0.1}]
        s = start_studio(self, self.fake, self.results, state_path=self.tmp / "m.json")
        wait_for(lambda: s.poller.ring, what="a metrics sample")
        x = s.poller.ring[0]
        self.assertEqual((x["source"], x["prompt_tok_s"], x["decode_tok_s"], x["generated"], x["window_ms"]),
                         ("metrics", 500.0, 61.2, 30, None))
        self.assertEqual(request(s, "GET", "/api/state")[2]["strata"]["status"], "profile_off")
        import time
        time.sleep(0.4)
        self.assertEqual(len(s.poller.ring), 1)                     # the same request is not added again

    def test_ring_is_bounded_to_300(self):
        s = self.studio()
        p = s.poller
        for i in range(320):
            p._append({"prompt_tokens": i}, ("t", i), "last", float(i), False)
        self.assertEqual(len(p.ring), 300)
        self.assertEqual(request(s, "GET", "/api/live?since=0")[2]["max"], 300)

    def test_after_load_flag_goes_to_the_next_sample(self):
        s = self.studio()
        p = s.poller
        self.fake.loaded = False
        wait_for(lambda: p.health.get("loaded") is False, what="unloaded")
        self.fake.loaded = True
        wait_for(lambda: p._after_load, what="the flag")
        self.fake.set_last(request_doc(prompt_tokens=77, generated=11, prompt_ms=90.0, decode_ms=150.0))
        wait_for(lambda: p.ring[-1]["prompt_tokens"] == 77, what="the sample")
        self.assertTrue(p.ring[-1]["after_load"])


class StateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = support.tmpdir(self)

    def tune(self, i, **kw):
        return {"id": f"tune-{i:04d}", "started_at": 1000.0 + i, "outcome": "succeeded", **kw}

    def test_51_tunes_keep_50_newest(self):
        st = State(self.tmp / "s.json")
        for i in range(51):
            st.save_tune(self.tune(i))
        st.write(force=True)
        data = json.loads((self.tmp / "s.json").read_text())
        self.assertEqual(len(data["tunes"]), 50)
        self.assertEqual(data["tunes"][0]["id"], "tune-0050")
        self.assertNotIn("tune-0000", [t["id"] for t in data["tunes"]])
        self.assertEqual(data["schema"], 1)

    def test_an_oversize_file_is_trimmed_to_a_mebibyte(self):
        st = State(self.tmp / "s.json")
        for i in range(50):
            st.save_tune(self.tune(i, lines_tail=["x" * 300] * 400, candidates={"steps": ["y" * 5000] * 20}))
        st.write(force=True)
        size = (self.tmp / "s.json").stat().st_size
        self.assertLessEqual(size, 1024 * 1024)
        data = json.loads((self.tmp / "s.json").read_text())
        newest, oldest = data["tunes"][0], data["tunes"][-1]
        self.assertIn("lines_tail", newest)                         # the newest keep theirs
        self.assertNotIn("lines_tail", oldest)                      # the oldest lost theirs first

    def test_a_big_existing_file_is_trimmed_on_the_next_write(self):
        big = {"schema": 1, "tunes": [self.tune(i, lines_tail=["z" * 300] * 400) for i in range(60)],
               "notes": {f"n{i}": "t" * 2500 for i in range(600)}, "report_meta": {}, "active_flow": None}
        (self.tmp / "s.json").write_text(json.dumps(big))
        st = State(self.tmp / "s.json")
        st.load()
        self.assertEqual(len(st.tunes()), 50)
        self.assertEqual(len(st.data["notes"]), 500)
        self.assertTrue(all(len(v) <= 2000 for v in st.data["notes"].values()))
        st.write(force=True)
        self.assertLessEqual((self.tmp / "s.json").stat().st_size, 1024 * 1024)

    def test_a_corrupt_file_is_set_aside(self):
        (self.tmp / "s.json").write_text("{nope")
        st = State(self.tmp / "s.json")
        st.load()
        self.assertTrue((self.tmp / "s.json.bad").exists())
        self.assertFalse((self.tmp / "s.json").exists())
        self.assertEqual(st.tunes(), [])
        self.assertIn("s.json.bad", st.warning)

    def test_an_unknown_schema_refuses(self):
        (self.tmp / "s.json").write_text(json.dumps({"schema": 2, "tunes": []}))
        with self.assertRaises(StateError) as cm:
            State(self.tmp / "s.json").load()
        self.assertIn("schema 2", str(cm.exception))
        self.assertTrue((self.tmp / "s.json").exists())            # left alone

    def test_a_studio_refuses_to_start_on_an_unknown_schema(self):
        (self.tmp / "s.json").write_text(json.dumps({"schema": 9}))
        import studio
        with self.assertRaises(SystemExit):
            studio.Studio("http://127.0.0.1:1", str(self.tmp), state_path=str(self.tmp / "s.json"), port=0)

    def test_writes_are_atomic_and_rate_limited(self):
        now = [100.0]
        st = State(self.tmp / "s.json", clock=lambda: now[0])
        st.set_note("report", "2026-10-15-profile-x", "one")
        self.assertTrue((self.tmp / "s.json").exists())
        first = (self.tmp / "s.json").read_text()
        now[0] = 100.4
        st.set_note("report", "2026-10-15-profile-x", "two")
        self.assertEqual((self.tmp / "s.json").read_text(), first)          # held back within a second
        now[0] = 101.5
        st.flush()
        self.assertIn("two", (self.tmp / "s.json").read_text())
        self.assertFalse((self.tmp / "s.json.tmp").exists())

    def test_bounds_of_meta(self):
        st = State(self.tmp / "s.json")
        for i in range(520):
            st.data["report_meta"][f"r{i}"] = {"captured_at": float(i), "rows": []}
        st.dirty = True
        st.write(force=True)
        meta = json.loads((self.tmp / "s.json").read_text())["report_meta"]
        self.assertEqual(len(meta), 500)
        self.assertNotIn("r0", meta)
        self.assertIn("r519", meta)


class Vendored(unittest.TestCase):
    def test_chartjs_hash_matches_the_readme(self):
        f = ROOT / "web" / "vendor" / "chart-4.4.1.umd.js"
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        readme = (ROOT / "web" / "vendor" / "README.md").read_text()
        self.assertIn(digest, readme)
        self.assertTrue(f.read_text(encoding="utf-8").startswith("/*!\n * Chart.js v4.4.1"))
        self.assertTrue((ROOT / "web" / "vendor" / "LICENSE-chart.js.md").read_text().startswith("The MIT License"))

    def test_no_innerhtml_family_in_the_page_scripts(self):
        bad = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")
        files = sorted((ROOT / "web").glob("*.js")) + [ROOT / "web" / "index.html"]
        self.assertGreaterEqual(len(files), 3)
        for f in files:
            text = f.read_text(encoding="utf-8")
            for word in bad:
                self.assertNotIn(word, text, f"{f.name} uses {word}")

    def test_page_has_four_views_a_panel_and_a_dialog(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        nav = re.findall(r'<a class="tab" href="#(\w+)"', html)
        self.assertEqual(nav, ["now", "runs", "tune", "settings"])
        self.assertEqual(re.findall(r'<section id="view-(\w+)"', html), ["now", "runs", "tune", "settings"])
        for needle in ('id="panel"', '<dialog id="dialog">', 'id="banner"', 'id="status"', 'id="theme"'):
            self.assertIn(needle, html)

    def test_every_script_the_page_loads_is_served(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        from studiolib.server import STATIC
        for src in re.findall(r'<script src="/static/([^"]+)"', html):
            self.assertIn(src, STATIC)
            self.assertTrue((ROOT / "web" / src).is_file(), src)
        self.assertNotIn("chart-4.4.1", html)                 # vendored for its tests, not loaded

    def test_the_part_colours_are_tokens_with_a_dark_step(self):
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        for token in ("--k-gpu", "--k-cpu", "--k-draft", "--k-adapt", "--k-other", "--k-pcie", "--k-dequant",
                      "--k-gemm", "--k-attn", "--k-gdn", "--good", "--bad", "--noise"):
            self.assertEqual(css.count(token + ":"), 3, token)  # light, dark (OS), dark (toggle)
        self.assertIn('@media (prefers-color-scheme: dark)', css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_the_page_has_no_inline_script_or_style(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>")
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"\sstyle=")
        for f in (ROOT / "web").glob("*.js"):
            self.assertNotRegex(f.read_text(encoding="utf-8"), r"https?://(?!127\.0\.0\.1|localhost|www\.w3\.org)", f.name)

    def test_tables_only_in_details(self):
        """A table is built by one helper (S.table); Now builds one only behind its "All numbers" switch."""
        js = {f.name: f.read_text(encoding="utf-8") for f in (ROOT / "web").glob("*.js")}
        self.assertEqual(sum(t.count("el('table'") for t in js.values()), 1)
        now = js["now.js"]
        self.assertEqual(now.count("S.table("), 1)
        self.assertIn("if (ui.table) grid.appendChild(tableCard(A, winAll));", now)


if __name__ == "__main__":
    unittest.main()
