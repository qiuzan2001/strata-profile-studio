"""A/B statistics for a tune on the user's own workload.

One measure is one number per round (for example the seconds that a replay of the user's requests takes). The arms
(the current settings and the candidates) run in the same rounds, in a random order inside each round, so a slow
minute of the PC hits every arm. The Studio compares an arm with the current settings round by round (paired), and it
keeps a change only when the change is larger than the noise of this PC.

The noise floor comes from an A/A test: two arms with the same settings. The median paired difference between them is
what the PC does by itself.

No random number is hidden: the order and the bootstrap take a seed.
"""
from __future__ import annotations

import random
import statistics

MIN_ROUNDS = 5
BOOTSTRAP = 2000


def schedule(arms: list[str], rounds: int, seed: int = 1) -> list[list[str]]:
    """The order of the arms in each round: every arm once per round, in a random order."""
    rng = random.Random(seed)
    out = []
    for _ in range(rounds):
        order = list(arms)
        rng.shuffle(order)
        out.append(order)
    return out


def paired_changes(base: list[float], cand: list[float]) -> list[float]:
    """The change of the candidate against the base in each round, as a fraction of the base (0.05 = 5% more)."""
    return [(c - b) / b for b, c in zip(base, cand) if b]


def _median_ci(values: list[float], level: float = 0.95, seed: int = 1) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    meds = sorted(statistics.median(rng.choices(values, k=n)) for _ in range(BOOTSTRAP))
    lo = meds[int(((1 - level) / 2) * BOOTSTRAP)]
    hi = meds[min(BOOTSTRAP - 1, int((1 - (1 - level) / 2) * BOOTSTRAP))]
    return lo, hi


def noise_floor(a1: list[float], a2: list[float]) -> float:
    """The typical paired difference between two arms with the same settings, as a fraction: the median of the absolute
    differences. 0 for no pair. The median, not the largest, because one spoiled round must not raise the bar for all
    the rest; the interval of the verdict already guards against a change that one round alone made."""
    d = paired_changes(a1, a2)
    return statistics.median(abs(x) for x in d) if d else 0.0


def verdict(base: list[float], cand: list[float], floor: float, lower_is_better: bool = True, min_gain: float = 0.0,
            seed: int = 1) -> dict:
    """Is the candidate better than the base, worse, or no different?

    `delta` is the median paired change of the candidate (a fraction). For a measure where lower is better (seconds), a
    negative delta is an improvement. `gain` is the improvement as a positive fraction. The verdict is "better" when the
    95% interval of the median change lies wholly on the better side, and the change is larger than the noise floor
    and than `min_gain`.
    """
    n = min(len(base), len(cand))
    out = {"rounds": n, "delta": None, "gain": None, "ci": None, "floor": floor, "verdict": "not enough rounds"}
    if n < MIN_ROUNDS:
        return out
    d = paired_changes(base[:n], cand[:n])
    if len(d) < MIN_ROUNDS:
        return out
    delta = statistics.median(d)
    lo, hi = _median_ci(d, seed=seed)
    sign = -1.0 if lower_is_better else 1.0
    gain = sign * delta
    ci_gain = (sign * hi, sign * lo) if lower_is_better else (lo, hi)      # the interval of the gain, low to high
    need = max(floor, min_gain)
    out.update(delta=delta, gain=gain, ci=[lo, hi])
    if ci_gain[0] > 0 and gain > need:
        out["verdict"] = "better"
    elif ci_gain[1] < 0 and -gain > need:
        out["verdict"] = "worse"
    else:
        out["verdict"] = "no change"
    return out
