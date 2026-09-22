"""Contextual baselines for the radar.

A static 2000 ms threshold is wrong for a tool that always takes 3 s and
too lax for one that normally takes 50 ms. A baseline is keyed by
(runtime, route, waypoint, tool) and stores robust statistics:

    robust_z = (x - median) / (1.4826 * MAD)

No Gaussian assumption and no learned model. Baselines are either
declared or learned from a calibration set that must not overlap the
traces used for final scoring (see EXPERIMENT_PROTOCOL.md).
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from pydantic import BaseModel, Field

from aeris.core.hashing import stable_hash

MAD_SCALE = 1.4826
WILDCARD = "*"


class BaselineKey(BaseModel):
    runtime: str = WILDCARD
    route: str = WILDCARD
    waypoint: str = WILDCARD
    tool: str = WILDCARD

    def text(self) -> str:
        return f"{self.runtime}|{self.route}|{self.waypoint}|{self.tool}"


class ContextBaseline(BaseModel):
    key: BaselineKey
    n: int = 0
    latency_median_ms: float
    latency_mad_ms: float = 0.0
    latency_tolerance: float | None = Field(
        default=None, description="Optional per-key robust-z threshold override"
    )
    expected_cost: float | None = None
    token_min: int | None = None
    token_max: int | None = None
    source: str = "declared"

    @property
    def expected_latency_ms(self) -> float:
        return self.latency_median_ms


def mad(values: list[float]) -> float:
    if not values:
        return 0.0
    center = median(values)
    return float(median([abs(value - center) for value in values]))


def robust_z(value: float, center: float, spread: float, *, min_spread: float) -> float:
    denominator = MAD_SCALE * max(spread, min_spread)
    return (value - center) / denominator


class _Sample(BaseModel):
    latency_ms: float
    tokens: int | None = None
    cost: float | None = None


class BaselineStore:
    """Declared or learned baselines. ``freeze`` stops learning."""

    def __init__(self) -> None:
        self._baselines: dict[str, ContextBaseline] = {}
        self._samples: dict[str, list[_Sample]] = defaultdict(list)
        self._keys: dict[str, BaselineKey] = {}
        self.frozen = False

    def declare(self, baseline: ContextBaseline) -> None:
        self._baselines[baseline.key.text()] = baseline

    def observe(
        self,
        key: BaselineKey,
        latency_ms: float,
        *,
        tokens: int | None = None,
        cost: float | None = None,
    ) -> None:
        if self.frozen:
            raise RuntimeError("baseline store is frozen; calibration is over")
        text = key.text()
        self._keys[text] = key
        self._samples[text].append(_Sample(latency_ms=latency_ms, tokens=tokens, cost=cost))

    def freeze(self) -> None:
        for text, samples in self._samples.items():
            latencies = [sample.latency_ms for sample in samples]
            tokens = [sample.tokens for sample in samples if sample.tokens is not None]
            costs = [sample.cost for sample in samples if sample.cost is not None]
            self._baselines[text] = ContextBaseline(
                key=self._keys[text],
                n=len(samples),
                latency_median_ms=float(median(latencies)),
                latency_mad_ms=mad(latencies),
                expected_cost=float(median(costs)) if costs else None,
                token_min=min(tokens) if tokens else None,
                token_max=max(tokens) if tokens else None,
                source="learned",
            )
        self.frozen = True

    def lookup(self, key: BaselineKey) -> ContextBaseline | None:
        candidates = (
            key,
            key.model_copy(update={"tool": WILDCARD}),
            key.model_copy(update={"route": WILDCARD}),
            BaselineKey(runtime=key.runtime, tool=key.tool),
        )
        for candidate in candidates:
            found = self._baselines.get(candidate.text())
            if found is not None:
                return found
        return None

    def all(self) -> list[ContextBaseline]:
        return [self._baselines[key] for key in sorted(self._baselines)]

    def fingerprint(self) -> str:
        return stable_hash([baseline.model_dump(mode="json") for baseline in self.all()])
