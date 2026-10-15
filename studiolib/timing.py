"""Every interval and timeout of the Studio in one object, so tests can run the flow in milliseconds."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Timing:
    # polling cadences (studio.py -> Strata), seconds
    health: float = 5.0
    last: float = 1.0
    history: float = 5.0
    status_busy: float = 2.0
    status_idle: float = 10.0
    metrics_off: float = 5.0
    metrics_on: float = 30.0
    probe: float = 60.0
    settings_cache: float = 10.0
    scan: float = 10.0
    tick: float = 0.1
    # Strata calls
    call_timeout: float = 5.0
    post_timeout: float = 30.0
    reload_attempt_timeout: float = 900.0
    # the tune flow
    flow_poll: float = 2.0
    check_timeout: float = 10.0
    before_timeout: float = 600.0
    tune_timeout: float = 3600.0
    reload_timeout: float = 900.0
    after_timeout: float = 600.0
    busy_retries: int = 3
    busy_gap: float = 20.0
    retry_after_default: float = 5.0
    sample_wait: float = 3.0

    def scaled(self, factor: float) -> "Timing":
        """Every duration times `factor` (the retry count stays)."""
        return replace(self, **{f.name: getattr(self, f.name) * factor for f in fields(self)
                                if f.name != "busy_retries"})
