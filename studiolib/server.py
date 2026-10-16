"""The Studio's HTTP server: the page, its JSON routes, and the checks in front of them.

Nothing here forwards a path the page names.  Every route builds its own answer from the poller, the report
store, the state file or the flow, and every error is {"error": {"message": "<sentence>"}}.
"""
from __future__ import annotations

import hmac
import http.server
import json
import re
import socket
import urllib.parse
from pathlib import Path

from . import __version__, calibparse, compare as compare_mod, configwrite, settings as settings_mod
from .flow import FlowError
from .poller import MSG_KEY, MSG_PROFILE_OFF
from .reports import REPORT_ID_RE, ReportError
from .state import NOTE_CHARS
from .strata import StrataError

MAX_POST = 64 * 1024
STATIC = {name: "text/javascript; charset=utf-8" for name in
          ("lib.js", "charts.js", "diagram.js", "panel.js", "now.js", "runs.js", "compare.js", "expertmap.js", "tune.js",
           "settings.js", "app.js",
           "vendor/chart-4.4.1.umd.js")}
STATIC.update({"style.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"})
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
LOOPBACK = ("127.0.0.1", "::1", "localhost")
LONG_MAX = 4_000_000


def is_loopback(host: str) -> bool:
    return host in LOOPBACK


class StudioApp:
    """What the handlers share."""

    def __init__(self, *, strata_url, results_dir, config_path, key, studio_key, host, port, client, poller,
                 flow, reports, state, web_dir, started_at, allow_hosts=(), engine_log=None, read_only=False, host_monitor=None, profiles=None,
                 defaults_run=None):
        self.strata_url, self.results_dir, self.config_path = strata_url, results_dir, config_path
        self.key_set = bool(key)
        self.studio_key = studio_key                   # None: no bearer needed on /api/
        self.host, self.port = host, port
        self.client, self.poller, self.flow, self.reports, self.state = client, poller, flow, reports, state
        self.web_dir = Path(web_dir)
        self.started_at = started_at
        self.allow_hosts = [h.strip().lower() for h in allow_hosts if h and h.strip()]
        self.engine_log = engine_log
        self.read_only = bool(read_only)
        self.host_monitor, self.profiles, self.defaults_run = host_monitor, profiles, defaults_run

    @staticmethod
    def split_host(header: str) -> tuple[str, str | None]:
        """"name:8444" -> ("name", "8444"); "[::1]:8090" -> ("[::1]", "8090"); "name" -> ("name", None)."""
        header = header.strip().lower()
        if header.startswith("["):
            name, _, rest = header.partition("]")
            return name + "]", (rest[1:] if rest.startswith(":") else None)
        name, sep, port = header.partition(":")
        return name, (port if sep else None)

    def proxy_host(self, header: str) -> bool:
        """The Host header names one of the --allow-host entries (an entry without a port allows any port)."""
        name, port = self.split_host(header)
        for entry in self.allow_hosts:
            ename, eport = self.split_host(entry)
            if ename == name and (eport is None or eport == port):
                return True
        return False

    def host_ok(self, header: str) -> bool:
        header = (header or "").strip().lower()
        return header in self.allowed_hosts() or self.proxy_host(header)

    def allowed_hosts(self) -> set[str]:
        p = self.port
        hosts = {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}
        h = self.host
        hosts.add(f"[{h}]:{p}" if ":" in h else f"{h}:{p}")
        return hosts

    # ---- the pieces of GET /api/state
    def state_doc(self) -> dict:
        p = self.poller
        snap = self.flow.snapshot()
        est = dict(p.estimates)
        if est["profile_s"] is None:
            newest = next((r for r in p.scan_reports()["reports"] if r["ok"] and r["elapsed_s"] is not None), None)
            est["profile_s"] = newest["elapsed_s"] if newest else None
        if est["tune_s"] is None:
            est["tune_s"] = next((t["tune_s"] for t in self.state.tunes() if t.get("tune_s") is not None), None)
        return {"studio": {"version": __version__, "started_at": self.started_at, "strata_url": self.strata_url,
                           "results_dir": str(self.results_dir), "results_dir_exists": self.reports.exists(),
                           "config_path": str(self.config_path) if self.config_path else None,
                           "key_set": self.key_set, "auth_required": self.studio_key is not None,
                           "read_only": self.read_only},
                "strata": p.strata_block(),
                "flow": {"id": snap["id"], "state": snap["state"], "stage": snap["stage"]},
                "estimates": est}

    def run_windows(self) -> list[dict]:
        """The time spans of the profile runs and tunes the Studio knows of, newest first: {"from", "to", "kind",
        "report", "tune"}.  A report's span is its folder's mtime minus its elapsed time; a tune's stages are laid
        end to end from its start."""
        spans = []
        for r in self.poller.scan_reports()["reports"]:
            if r.get("ok") and isinstance(r.get("mtime"), (int, float)) and isinstance(r.get("elapsed_s"), (int, float)):
                spans.append({"from": r["mtime"] - r["elapsed_s"] - 2.0, "to": r["mtime"] + 2.0, "kind": "profile",
                              "report": r["id"], "tune": (r.get("tune") or {}).get("id")})
        for t in self.state.tunes():
            at = t.get("started_at")
            if not isinstance(at, (int, float)):
                continue
            for st in t.get("stages") or []:
                el = st.get("elapsed_s") if isinstance(st.get("elapsed_s"), (int, float)) else 0.0
                if st.get("key") in ("tune", "reload"):
                    spans.append({"from": at, "to": at + el, "kind": st["key"], "report": None, "tune": t.get("id")})
                at += el
        snap = self.flow.snapshot()
        for st in snap.get("stages") or []:
            if st.get("key") in ("tune", "reload") and isinstance(st.get("started_at"), (int, float)):
                spans.append({"from": st["started_at"], "to": st["started_at"] + (st.get("elapsed_s") or 0.0),
                              "kind": st["key"], "report": None, "tune": snap.get("id")})
        raw = self.poller.tune_status()
        for kind, block in (("profile", raw), ("tune", raw.get("tune") if isinstance(raw.get("tune"), dict) else {})):
            if isinstance(block.get("started_at"), (int, float)) and isinstance(block.get("elapsed_s"), (int, float)):
                spans.append({"from": block["started_at"], "to": block["started_at"] + block["elapsed_s"],
                              "kind": kind, "report": None, "tune": None, "strata": True})
        spans.sort(key=lambda x: x["from"], reverse=True)
        return spans

    def request_index(self) -> dict:
        """(prompt tokens, reused, generated, decode tok/s) -> (report id, request id), over every report: a live
        sample with the same four numbers is that report's request."""
        scan = self.poller.scan_reports()
        key = tuple((r["id"], r.get("mtime")) for r in scan["reports"] if r.get("ok"))
        if getattr(self, "_req_index", (None, None))[0] == key:
            return self._req_index[1]
        index = {}
        for e in self.reports.pool():
            for q in e["rep"].get("requests") or []:
                k = (q.get("prompt_tokens"), q.get("reused"), q.get("generated"), q.get("decode_tok_s"))
                if None not in k[:3]:
                    index.setdefault(k, []).append((e["id"], q["id"], q["label"]))
        self._req_index = (key, index)
        return index

    def live_doc(self, since: int) -> dict:
        doc = self.poller.live(since)
        spans = self.run_windows()
        index = self.request_index()
        for x in doc["samples"]:
            run = None
            cands = index.get((x.get("prompt_tokens"), x.get("reused"), x.get("generated"), x.get("decode_tok_s"))) or []
            t = x.get("seen_at")
            inside = [c for c in cands if any(sp["report"] == c[0] and isinstance(t, (int, float)) and
                                              sp["from"] <= t <= sp["to"] for sp in spans)]
            hit = (inside or cands or [None])[0]       # the run whose time span holds it, else the newest such run
            x["report_request"] = {"report": hit[0], "id": hit[1], "label": hit[2]} if hit else None
            t = x.get("seen_at")
            if isinstance(t, (int, float)):
                hits = [sp for sp in spans if sp["from"] <= t <= sp["to"]]
                # a report's span wins over the tune it belongs to: it names the request
                hits.sort(key=lambda sp: (sp["report"] is None, sp["to"] - sp["from"]))
                if hits:
                    sp = hits[0]
                    run = {"kind": sp["kind"], "report": sp["report"], "tune": sp["tune"]}
            if run is None and x.get("tag"):
                tag = x["tag"]
                run = {"kind": "profile" if tag.startswith("profile") else tag, "report": None, "tune": None}
            if run is None and hit:
                run = {"kind": "profile", "report": hit[0], "tune": None}
            x["run"] = run
        doc["spans"] = [{k: v for k, v in sp.items() if k != "strata"} for sp in spans[:100]]
        return doc

    def run_doc(self) -> dict:
        """The running (or last) profile run: its state and output lines, for the Reports view."""
        raw = self.poller.tune_status()
        return {"state": raw.get("state") or "idle", "elapsed_s": raw.get("elapsed_s"),
                "lines": [str(x)[:300] for x in (raw.get("lines") or [])[-400:]]}

    def tune_flow_doc(self) -> dict:
        """The flow; while no flow runs and a tune started elsewhere is going, that tune's lines and candidates,
        read only (state stays idle)."""
        snap = self.flow.snapshot()
        if snap["state"] == "idle" and not self.poller.tune_by_studio:
            tune = (self.poller.tune_status().get("tune") or {})
            if tune.get("state") == "running":
                lines = [str(x)[:300] for x in (tune.get("lines") or [])][-400:]
                snap["lines"] = lines
                snap["candidates"] = calibparse.parse(lines)
        return snap

    def hardware_doc(self) -> dict:
        doc = self.poller.hardware_doc()
        log = self.engine_log.facts() if self.engine_log else {"path": None, "readable": False, "probe": None,
                                                                 "vram_warning": None, "probes": 0}
        doc["log"] = log
        doc["window"] = self.last_window()
        return doc

    def last_window(self) -> dict | None:
        """The newest sample with a decode split: what the PC diagram highlights."""
        doc = self.poller.live(0)
        for x in reversed(doc["samples"]):
            if x.get("window_ms") or x.get("prompt_groups_ms"):
                return {k: x.get(k) for k in ("seq", "seen_at", "window_ms", "ms_per_window", "prompt_groups_ms",
                                              "prompt_ms", "decode_ms", "decode_tok_s", "hit_rate", "pcie_share",
                                              "cpu_experts", "cpu_entries", "vram_hits", "pcie_per_window", "swaps",
                                              "kv_hit", "tokens_per_window", "windows", "hits", "lookups")}
        return None

    def tunes_doc(self) -> dict:
        """The tune records, each with its candidates parsed again from its stored lines by this version of the
        parser, against what the engine ran with before the tune (the settings' "before" column)."""
        out = []
        for t in self.state.tunes():
            lines = t.get("lines_tail") or []
            rows = {r.get("flag"): r for r in ((t.get("settings") or {}).get("rows") or []) if isinstance(r, dict)}
            if lines:
                was = {}
                for flag, key in (("--pcie-frac", "pcie_frac"), ("--spec-min-p", "spec_min_p"),
                                  ("--pool-workers", "pool_workers")):
                    if (rows.get(flag) or {}).get("before") is not None:
                        was[key] = rows[flag]["before"]
                tier = [(rows.get(f) or {}).get("before") for f in ("--adapt-every", "--adapt-swaps", "--adapt-decay")]
                if rows:
                    was["adapt"] = "/".join(tier) if all(tier) else "default"
                t["candidates"] = calibparse.parse(lines, was)
            out.append(t)
        return {"tunes": out}

    def host_doc(self) -> dict:
        if self.host_monitor is None:
            return {"at": None, "cpu": None, "gpu": None, "memory": None, "disk": None, "history": {}, "core_history": {},
                    "notes": {"cpu": "Host readings are off."}, "enabled": False, "os": None, "backend": "none",
                    "gpu_source": "none", "gpu_error": None, "pool_cpus": None, "host_cpu": None}
        pool = (self.engine_log.facts() if self.engine_log else {}).get("pool") or {}
        return self.host_monitor.doc(pool.get("cpus"), pool.get("host_cpu"))

    def experts_doc(self, ranks: bool) -> dict:
        if self.profiles is None:
            return {"config": False}
        slots = (self.poller.engine or {}).get("expert_slots")
        return self.profiles.doc(slots if isinstance(slots, int) else None, include_ranks=ranks)

    def busy_reason(self) -> str | None:
        if self.defaults_run is not None and self.defaults_run.state == "running":
            return "A profile run with the engine defaults is going."
        if self.flow.running():
            return "A tune is running; Strata writes the config file at its end. Wait for it to finish."
        tune = self.poller.tune_status().get("tune")
        if isinstance(tune, dict) and tune.get("state") == "running":
            return "Strata is tuning; it writes the config file at the end. Wait for it to finish."
        return None

    def settings_doc(self) -> dict:
        p = self.poller
        loaded = p.health.get("loaded")
        prof = p.get_settings() if p.routes.get("settings") else None
        engine = p.get_engine() if prof is None and p.status not in ("unreachable", "key_needed") else None
        return settings_mod.build(prof, engine, self.config_path, loaded)


def make_handler(app: StudioApp):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"StrataProfileStudio/{__version__}"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):             # quiet: the console shows the start line and errors only
            pass

        # ----------------------------------------------------------------------------- plumbing
        def _send(self, status: int, body: bytes, ctype: str, api: bool = False, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if api:
                self.send_header("Cache-Control", "no-store")
            if self.command == "POST" and not getattr(self, "_body_read", False):
                self.send_header("Connection", "close")          # the body was not read: do not parse it as a request
                self.close_connection = True
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, doc, extra=None):
            self._send(status, json.dumps(doc, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                       api=True, extra=extra)

        def _error(self, status: int, message: str, extra=None):
            self._json(status, {"error": {"message": message}}, extra)

        # ---------------------------------------------------------------------------- the checks
        def _host_ok(self) -> bool:
            if app.host_ok(self.headers.get("Host") or ""):
                return True
            self._error(403, "This address is not one the Studio answers to (Host header check).")
            return False

        def _origin_ok(self) -> bool:
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True
            host = (self.headers.get("Host") or "").strip().lower()
            origin = origin.rstrip("/").lower()
            if origin == f"http://{host}":
                return True
            # a proxy that terminates TLS (tailscale serve): the page's origin is https://<the proxy's name>
            if origin == f"https://{host}" and app.proxy_host(host):
                return True
            self._error(403, "A page from another origin may not use the Studio.")
            return False

        def _key_ok(self) -> bool:
            if app.studio_key is None:
                return True
            auth = self.headers.get("Authorization") or ""
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if hmac.compare_digest(given.encode("utf-8"), app.studio_key.encode("utf-8")):
                return True
            self._error(401, "The Studio needs its key: send it as Authorization: Bearer <key>.",
                        {"WWW-Authenticate": "Bearer"})
            return False

        def _body(self) -> dict | None:
            """The POST body as a dict, or None after an error answer."""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._error(415, "Send the body as application/json.")
                return None
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                n = -1
            if n < 0 or n > MAX_POST:
                self._error(413, "The body is larger than 64 KiB.")
                return None
            raw = self.rfile.read(n) if n else b""
            self._body_read = True
            try:
                doc = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except ValueError:
                self._error(400, "The body is not valid JSON.")
                return None
            if not isinstance(doc, dict):
                self._error(400, "The body must be a JSON object.")
                return None
            return doc

        # ------------------------------------------------------------------------------- verbs
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return
            u = urllib.parse.urlsplit(self.path)
            path, query = u.path, urllib.parse.parse_qs(u.query)
            if path == "/" or path == "/index.html":
                return self._static("index.html", "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name in STATIC:
                    return self._static(name, STATIC[name])
                return self._error(404, "Not found.")
            if not path.startswith("/api/"):
                return self._error(404, "Not found.")
            if not self._key_ok():
                return
            try:
                self._api_get(path, query)
            except BrokenPipeError:
                pass

        def do_POST(self):
            self._body_read = False
            if not self._host_ok() or not self._origin_ok():
                return
            u = urllib.parse.urlsplit(self.path)
            if not u.path.startswith("/api/"):
                return self._error(404, "Not found.")
            if not self._key_ok():
                return
            if app.read_only:
                return self._error(403, "This Studio was started with --read-only: it starts nothing and writes "
                                        "nothing.")
            body = self._body()
            if body is None:
                return
            self._api_post(u.path, body)

        def do_PUT(self):
            self._error(405, "Method not allowed.")

        do_DELETE = do_PATCH = do_PUT

        def _static(self, name: str, ctype: str):
            f = (app.web_dir / name)
            try:
                data = f.read_bytes()
            except OSError:
                return self._error(404, "Not found.")
            self._send(200, data, ctype, extra={"Cache-Control": "no-cache"})

        # ------------------------------------------------------------------------------ GET /api
        def _api_get(self, path: str, query: dict):
            if path == "/api/state":
                return self._json(200, app.state_doc())
            if path == "/api/live":
                try:
                    since = int((query.get("since") or ["0"])[0])
                except ValueError:
                    return self._error(400, "since must be a whole number.")
                return self._json(200, app.live_doc(max(0, since)))
            if path == "/api/reports":
                return self._json(200, app.poller.scan_reports())
            m = re.match(r"^/api/reports/([^/]+)$", path)
            if m:
                return self._report(urllib.parse.unquote(m.group(1)))
            if path == "/api/compare":
                return self._compare((query.get("a") or [""])[0], (query.get("b") or [""])[0])
            if path == "/api/settings":
                return self._json(200, app.settings_doc())
            if path == "/api/settings/apply-preview":
                return self._apply_preview((query.get("report") or [""])[0])
            if path == "/api/hardware":
                return self._json(200, app.hardware_doc())
            if path == "/api/host":
                return self._json(200, app.host_doc())
            if path == "/api/experts":
                return self._json(200, app.experts_doc((query.get("ranks") or ["1"])[0] != "0"))
            if path == "/api/run-defaults":
                return self._json(200, app.defaults_run.snapshot() if app.defaults_run else {"state": "idle", "lines": []})
            if path == "/api/settings/file":
                try:
                    return self._json(200, configwrite.file_doc(app.config_path))
                except configwrite.ConfigWriteError as e:
                    return self._error(e.status, e.message)
            if path == "/api/tune-flow":
                return self._json(200, app.tune_flow_doc())
            if path == "/api/run-profile":
                return self._json(200, app.run_doc())
            if path == "/api/tunes":
                return self._json(200, app.tunes_doc())
            self._error(404, "Not found.")

        def _report(self, rid: str):
            try:
                rep = app.reports.get(rid)
            except ReportError as e:
                return self._error(404, str(e))
            self._json(200, rep)

        def _apply_preview(self, rid: str):
            try:
                rep = app.reports.get(rid)
            except ReportError as e:
                return self._error(404, str(e))
            try:
                doc = configwrite.preview(app.config_path, rep)
            except configwrite.ConfigWriteError as e:
                return self._error(e.status, e.message)
            doc["busy"] = app.busy_reason()
            self._json(200, doc)

        def _apply(self, body: dict):
            if body.get("confirm") is not True:
                return self._error(400, "The write needs \"confirm\": true.")
            rid = body.get("report")
            try:
                rep = app.reports.get(rid if isinstance(rid, str) else "")
            except ReportError as e:
                return self._error(404, str(e))
            try:
                doc = configwrite.apply(app.config_path, rep, body.get("flags"), body.get("sha"), app.busy_reason())
            except configwrite.ConfigWriteError as e:
                return self._error(e.status, e.message)
            self._json(200, doc)

        def _compare(self, a: str, b: str):
            try:
                ra, rb = app.reports.get(a), app.reports.get(b)
            except ReportError as e:
                return self._error(404, str(e))
            self._json(200, compare_mod.compute(ra, rb, app.reports.pool()))

        # ----------------------------------------------------------------------------- POST /api
        @staticmethod
        def _long_of(body: dict):
            """None, or a positive whole number of tokens."""
            v = body.get("long")
            if v is None:
                return None, None
            if isinstance(v, bool) or not isinstance(v, int) or not (0 < v <= LONG_MAX):
                return None, "long must be empty or a whole number of tokens."
            return v, None

        def _api_post(self, path: str, body: dict):
            if path == "/api/run-profile":
                return self._run_profile(body)
            if path == "/api/tune-flow":
                long, err = self._long_of(body)
                if err:
                    return self._error(400, err)
                if app.defaults_run is not None and app.defaults_run.state == "running":
                    return self._error(409, "A profile run with the engine defaults is going.")
                try:
                    ident = app.flow.start(long)
                except FlowError as e:
                    return self._error(e.status, e.message)
                return self._json(200, {"id": ident})
            if path == "/api/tune-flow/cancel":
                if app.flow.cancel():
                    return self._json(200, {"ok": True})
                return self._error(409, "The flow can be cancelled only before the tune has started.")
            if path == "/api/tune-flow/retry-after":
                try:
                    ident = app.flow.retry_after()
                except FlowError as e:
                    return self._error(e.status, e.message)
                return self._json(200, {"id": ident})
            if path == "/api/notes":
                return self._notes(body)
            if path == "/api/settings/apply":
                return self._apply(body)
            if path == "/api/settings/edit":
                if body.get("confirm") is not True:
                    return self._error(400, "The write needs \"confirm\": true.")
                try:
                    return self._json(200, configwrite.apply_edits(app.config_path, body.get("edits"), body.get("sha"),
                                                                   app.busy_reason()))
                except configwrite.ConfigWriteError as e:
                    return self._error(e.status, e.message)
            if path == "/api/run-defaults":
                return self._run_defaults()
            self._error(404, "Not found.")

        def _run_profile(self, body: dict):
            long, err = self._long_of(body)
            if err:
                return self._error(400, err)
            if app.flow.running():
                return self._error(409, "A tune is running; its own profile runs are part of it.")
            p = app.poller
            if p.status == "unreachable":
                return self._error(502, f"Strata does not answer at {app.strata_url}.")
            if p.status == "key_needed":
                return self._error(412, MSG_KEY)
            if p.profile is False:
                return self._error(412, MSG_PROFILE_OFF)
            try:
                r = app.client.call("run", query={"long": long} if long else None, body={})
            except StrataError as e:
                if e.kind == "key_needed":
                    return self._error(412, MSG_KEY)
                return self._error(502, f"Strata does not answer at {app.strata_url}.")
            if r.status == 404:
                return self._error(412, MSG_PROFILE_OFF)
            if r.status != 200:
                return self._error(r.status if 400 <= r.status < 600 and r.status != 401 else 502, r.message)
            p.studio_run = True
            p.refresh_status()
            self._json(200, {"status": "started"})

        def _run_defaults(self):
            if app.defaults_run is None:
                return self._error(412, "The Studio needs --config for a run with the engine defaults.")
            p = app.poller
            if p.status == "unreachable":
                return self._error(502, f"Strata does not answer at {app.strata_url}.")
            busy = None
            if app.flow.running():
                busy = "A tune is running. Wait for it to end."
            raw = p.tune_status()
            if (raw.get("state") == "running") or ((raw.get("tune") or {}).get("state") == "running"):
                busy = "Strata is running a profile or a tune. Wait for it to end."
            try:
                app.defaults_run.start(busy)
            except ValueError as e:
                return self._error(409, str(e))
            self._json(200, {"status": "started"})

        def _notes(self, body: dict):
            kind, ident, text = body.get("kind"), body.get("id"), body.get("text")
            if kind not in ("report", "tune") or not isinstance(ident, str) or not isinstance(text, str):
                return self._error(400, "A note needs kind (report or tune), id and text.")
            if len(text) > NOTE_CHARS:
                return self._error(400, f"A note is at most {NOTE_CHARS:,} characters.")
            if kind == "report":
                if not REPORT_ID_RE.match(ident) or app.reports.folder(ident) is None:
                    return self._error(404, "There is no such report.")
            ok = app.state.set_note(kind, ident, text)
            if not ok:
                return self._error(404, "There is no such tune.")
            app.poller.scan_cache = (0.0, None)
            self._json(200, {"ok": True})

    return Handler


class StudioServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str, port: int, handler):
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), handler)
