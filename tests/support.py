"""Shared helpers: paths, a fast Timing, copying the fixture reports, starting a Studio against a fake Strata."""
from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from studiolib.timing import Timing          # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "reports"
A = "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s"
B = "2026-10-15-profile-rtx-5070-ti-swift-1-5-iq3-s-2"
BROKEN = "2026-10-14-profile-rtx-5070-ti-broken"
NOCFG = "2026-10-13-profile-rtx-5070-ti-swift-1-5-iq3-s"


def fast_timing(**kw) -> Timing:
    base = replace(Timing(), health=0.1, last=0.05, history=0.1, status_busy=0.05, status_idle=0.1, metrics_off=0.1,
                   metrics_on=0.3, probe=1.0, settings_cache=0.1, scan=0.2, tick=0.01, call_timeout=5.0,
                   flow_poll=0.03, check_timeout=5.0, before_timeout=10.0, tune_timeout=15.0, reload_timeout=10.0,
                   after_timeout=10.0, busy_gap=0.05, retry_after_default=0.05, sample_wait=0.6)
    return replace(base, **kw) if kw else base


def tmpdir(testcase) -> Path:
    d = Path(tempfile.mkdtemp(prefix="studio-test-"))
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def copy_fixtures(dst: Path, names=(A, B, BROKEN, NOCFG)) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copytree(FIXTURES / n, dst / n)
    return dst


def wait_for(pred, timeout: float = 10.0, step: float = 0.02, what: str = "the condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError(f"timed out waiting for {what}")


def request(studio_or_port, method: str, path: str, body=None, headers=None, raw: bytes | None = None, host=None):
    """-> (status, headers dict, parsed JSON or bytes)."""
    port = studio_or_port if isinstance(studio_or_port, int) else studio_or_port.port
    ip = host or "127.0.0.1"
    conn = http.client.HTTPConnection(ip, port, timeout=10)
    h = dict(headers or {})
    data = raw
    if body is not None and raw is None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        try:
            doc = json.loads(payload.decode()) if payload else None
        except ValueError:
            doc = payload
        return resp.status, hdrs, doc
    finally:
        conn.close()


def routes_root(testcase) -> Path:
    """A Strata folder of a build with the /profile routes (PR #2661): there /profile/status 404 means profiling is
    off, not stock mode."""
    d = tmpdir(testcase) / "strata-with-routes"
    (d / "tools").mkdir(parents=True)
    (d / "serve").mkdir()
    (d / "tools" / "strata_bench.py").write_text("")
    (d / "serve" / "profile_page.py").write_text("")
    return d


def start_studio(testcase, fake, results: Path, *, key=None, host="127.0.0.1", state_path=None, config=None,
                 timing=None, strata_url=None, strata_root=None):
    import studio
    s = studio.Studio(strata_url or fake.url, str(results), key=key, host=host, port=0, config=config,
                      state_path=str(state_path or (results.parent / (results.name + "-state.json"))),
                      timing=timing or fast_timing(), strata_root=strata_root)
    s.start()
    testcase.addCleanup(s.stop)
    return s
