"""Small, assumption-light statistics for paired trials.

Only a percentile bootstrap over paired differences. No p-values: the
protocol reports effect sizes with intervals and leaves "significant" to
the reader, and only when the samples are genuinely stochastic.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from statistics import median

from pydantic import BaseModel

MIN_PAIRS_FOR_CI = 30


class Interval(BaseModel):
    estimate: float
    low: float
    high: float
    n: int
    resamples: int
    confidence: float = 0.95
    method: str = "paired percentile bootstrap"


def median_or_none(values: Sequence[float]) -> float | None:
    return float(median(values)) if values else None


def mean_or_none(values: Sequence[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def bootstrap_ci(
    differences: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> Interval | None:
    """Percentile bootstrap CI of ``statistic`` over paired differences."""
    n = len(differences)
    if n == 0:
        return None
    rng = random.Random(seed)
    values = list(differences)
    draws = sorted(statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    tail = (1.0 - confidence) / 2.0
    low = draws[int(tail * (resamples - 1))]
    high = draws[int((1.0 - tail) * (resamples - 1))]
    return Interval(
        estimate=float(statistic(values)),
        low=float(low),
        high=float(high),
        n=n,
        resamples=resamples,
        confidence=confidence,
    )


def ci_allowed(n_pairs: int, stochastic: bool) -> tuple[bool, str]:
    if not stochastic:
        return False, "deterministic fixture: repeated runs are not independent samples"
    if n_pairs < MIN_PAIRS_FOR_CI:
        return False, f"only {n_pairs} pairs; protocol requires >= {MIN_PAIRS_FOR_CI} before reporting a CI"
    return True, ""
