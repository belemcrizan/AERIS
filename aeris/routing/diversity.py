"""Failure-domain diversity between two routes.

D(current, candidate) is in [0, 1]. 0 means the candidate shares every
known failure domain with the current route, so diverting to it is unlikely
to escape the fault. 1 means no known shared domain. The score is a
weighted mean over attributes both routes declare, so every term can be
printed. Unknown metadata yields ``None``, not an optimistic guess.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeris.core.models import Route

ATTRIBUTE_WEIGHTS: dict[str, float] = {
    "model_provider": 0.20,
    "model_family": 0.10,
    "tool_provider": 0.15,
    "data_source": 0.15,
    "region": 0.10,
    "network_dependency": 0.05,
    "dependencies": 0.25,
}

_SCALAR_ATTRIBUTES = (
    "model_provider",
    "model_family",
    "tool_provider",
    "data_source",
    "region",
    "network_dependency",
)


class AttributeDiversity(BaseModel):
    attribute: str
    current: str | list[str] | None
    candidate: str | list[str] | None
    diversity: float
    weight: float


class DiversityReport(BaseModel):
    score: float | None
    terms: list[AttributeDiversity] = Field(default_factory=list)
    shared: list[str] = Field(default_factory=list)

    def explain(self) -> str:
        if self.score is None:
            return "failure-domain diversity unknown (no dependency metadata)"
        parts = [f"{term.attribute}={term.diversity:.2f}x{term.weight:.2f}" for term in self.terms]
        shared = f"; shared {sorted(self.shared)}" if self.shared else ""
        return f"diversity={self.score:.2f} ({', '.join(parts)}){shared}"


def _service_ids(route: Route) -> set[str]:
    ids: set[str] = set()
    for waypoint in route.waypoints:
        ids |= waypoint.dependency_ids()
    if route.dependencies is not None:
        ids |= set(route.dependencies.shared_service_ids)
    return ids


def failure_domain_diversity(current: Route, candidate: Route) -> DiversityReport:
    terms: list[AttributeDiversity] = []
    shared: list[str] = []
    cur_deps = current.dependencies
    cand_deps = candidate.dependencies
    if cur_deps is not None and cand_deps is not None:
        for attribute in _SCALAR_ATTRIBUTES:
            a = getattr(cur_deps, attribute)
            b = getattr(cand_deps, attribute)
            if a is None or b is None:
                continue
            same = a == b
            if same:
                shared.append(f"{attribute}:{a}")
            terms.append(
                AttributeDiversity(
                    attribute=attribute,
                    current=a,
                    candidate=b,
                    diversity=0.0 if same else 1.0,
                    weight=ATTRIBUTE_WEIGHTS[attribute],
                )
            )
    cur_ids = _service_ids(current)
    cand_ids = _service_ids(candidate)
    if cur_ids and cand_ids:
        overlap = cur_ids & cand_ids
        union = cur_ids | cand_ids
        shared.extend(sorted(overlap))
        terms.append(
            AttributeDiversity(
                attribute="dependencies",
                current=sorted(cur_ids),
                candidate=sorted(cand_ids),
                diversity=1.0 - len(overlap) / len(union),
                weight=ATTRIBUTE_WEIGHTS["dependencies"],
            )
        )
    if not terms:
        return DiversityReport(score=None)
    total_weight = sum(term.weight for term in terms)
    score = sum(term.diversity * term.weight for term in terms) / total_weight
    return DiversityReport(score=round(score, 4), terms=terms, shared=shared)
