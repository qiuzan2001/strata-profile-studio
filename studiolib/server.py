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

from . import __version__, calibparse, compare as compare_mod, settings as settings_mod
from .flow import FlowError
from .poller import MSG_KEY, MSG_PROFILE_OFF
from .reports import REPORT_ID_RE, ReportError
from .state import NOTE_CHARS
from .strata import StrataError

MAX_POST = 64 * 1024
STATIC = {"app.js": "text/javascript; charset=utf-8", "charts.js": "text/javascript; charset=utf-8",
          "insights.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8",
          "favicon.svg": "image/svg+xml",
          "vendor/chart-4.4.1.umd.js": "text/javascript; charset=utf-8"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
LOOPBACK = ("127.0.0.1", "::1", "localhost")
LONG_MAX = 4_000_000


def is_loopback(host: str) -> bool:
    return host in LOOPBACK


class StudioApp:
    """What the handlers share."""

    def __init__(self, *, strata_url, results_dir, config_path, key, studio_key, host, port, client, poller,
                 flow, reports, state, web_dir, started_at, allow_hosts=()):
        self.strata_url, self.results_dir, self.config_path = strata_url, results_dir, config_path
        self.key_set = bool(key)
        self.studio_key = studio_key                   # None: no bearer needed on /api/
        self.host, self.port = host, port
        self.client, self.poller, self.flow, self.reports, self.state = client, poller, flow, reports, state
        self.web_dir = Path(web_dir)
        self.started_at = started_at
        self.allow_hosts = [h.strip().lower() for h in allow_hosts if h and h.strip()]

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
                           "key_set": self.key_set, "auth_required": self.studio_key is not None},
                "strata": p.strata_block(),
                "flow": {"id": snap["id"], "state": snap["state"], "stage": snap["stage"]},
                "estimates": est}

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
                return self._json(200, app.poller.live(max(0, since)))
            if path == "/api/reports":
                return self._json(200, app.poller.scan_reports())
            m = re.match(r"^/api/reports/([^/]+)$", path)
            if m:
                return self._report(urllib.parse.unquote(m.group(1)))
            if path == "/api/compare":
                return self._compare((query.get("a") or [""])[0], (query.get("b") or [""])[0])
            if path == "/api/settings":
                return self._json(200, app.settings_doc())
            if path == "/api/tune-flow":
                return self._json(200, app.tune_flow_doc())
            if path == "/api/run-profile":
                return self._json(200, app.run_doc())
            if path == "/api/tunes":
                return self._json(200, {"tunes": app.state.tunes()})
            self._error(404, "Not found.")

        def _report(self, rid: str):
            try:
                rep = app.reports.get(rid)
            except ReportError as e:
                return self._error(404, str(e))
            self._json(200, rep)

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
