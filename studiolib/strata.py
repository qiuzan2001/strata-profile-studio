"""The Strata client.  A fixed route table: the Studio never forwards a path the page names, it builds each
request itself.  Every request carries the key (when set) as a bearer token, `Accept: application/json`, a
User-Agent of its own and no Origin, so Strata treats it as a client that is no browser."""
from __future__ import annotations

import http.client
import json
import urllib.parse

from . import __version__

MAX_BODY = 8 * 1024 * 1024

# name -> (method, path, timeout attribute of Timing)
ROUTES = {
    "health": ("GET", "/health", "call_timeout"),
    "metrics": ("GET", "/metrics", "call_timeout"),
    "last": ("GET", "/profile/last", "call_timeout"),
    "status": ("GET", "/profile/status", "call_timeout"),
    "history": ("GET", "/profile/history", "call_timeout"),
    "settings": ("GET", "/profile/settings", "call_timeout"),
    "run": ("POST", "/profile/run", "post_timeout"),
    "tune": ("POST", "/profile/tune", "post_timeout"),
    "chat": ("POST", "/v1/chat/completions", "reload_attempt_timeout"),
}


class StrataError(Exception):
    """kind: unreachable | key_needed | too_large | bad_response.  `status` for HTTP-level ones."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


class Response:
    __slots__ = ("status", "data", "headers")

    def __init__(self, status: int, data, headers: dict):
        self.status, self.data, self.headers = status, data, headers

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def message(self) -> str:
        """Strata's own error sentence, when it gave one."""
        d = self.data
        if isinstance(d, dict):
            e = d.get("error")
            if isinstance(e, dict) and e.get("message"):
                return str(e["message"])
            if isinstance(e, str):
                return e
            if d.get("message"):
                return str(d["message"])
        return f"Strata answered {self.status}."


class StrataClient:
    def __init__(self, base_url: str, key: str | None, timing):
        u = urllib.parse.urlsplit(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"--strata must be an http:// or https:// address, not {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.prefix = u.path.rstrip("/")
        self._key = key or None
        self.timing = timing

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def call(self, route: str, *, query: dict | None = None, body: dict | None = None,
             timeout: float | None = None) -> Response:
        """One call.  Raises StrataError for unreachable (connect error, timeout), key_needed (401), too_large."""
        method, path, tmo = ROUTES[route]
        if timeout is None:
            timeout = getattr(self.timing, tmo)
        target = self.prefix + path
        if query:
            target += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json", "User-Agent": f"strata-profile-studio/{__version__}"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        payload = None
        if method == "POST":
            payload = json.dumps(body if body is not None else {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=max(0.05, timeout))
        try:
            try:
                conn.putrequest(method, target, skip_host=False, skip_accept_encoding=True)
                for k, v in headers.items():
                    conn.putheader(k, v)
                conn.endheaders(payload)
                resp = conn.getresponse()
                raw = resp.read(MAX_BODY + 1)
            except (OSError, http.client.HTTPException) as e:
                raise StrataError("unreachable", f"Strata does not answer ({type(e).__name__}).") from None
            if len(raw) > MAX_BODY:
                raise StrataError("too_large", "Strata's answer is larger than 8 MiB; the Studio ignored it.",
                                  resp.status)
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            data = None
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    data = None
            if resp.status == 401:
                raise StrataError("key_needed", "Strata needs an API key.", 401)
            return Response(resp.status, data, hdrs)
        finally:
            conn.close()
