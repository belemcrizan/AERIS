"""Per-flight and aggregate experiment metrics."""

from __future__ import annotations

from pydantic import BaseModel

from aeris.core.enums import ExecutionState


class FlightMetrics(BaseModel):
    scenario_id: str
    mode: str
    flight_id: str
    success: bool
    terminal_state: ExecutionState
    latency_ms: float
    retries: int
    route_changes: int
    human_interventions: int
    hazard_count: int
    injected_failure: bool
    recovered: bool


class AggregateMetrics(BaseModel):
    n: int
    task_success_rate: float
    terminal_failure_rate: float
    mean_latency: float
    number_of_retries: float
    number_of_route_changes: float
    human_interventions: float
    recovery_rate: float | None


def summarize(rows: list[FlightMetrics]) -> AggregateMetrics:
    n = len(rows)
    if n == 0:
        return AggregateMetrics(
            n=0,
            task_success_rate=0.0,
            terminal_failure_rate=0.0,
            mean_latency=0.0,
            number_of_retries=0.0,
            number_of_route_changes=0.0,
            human_interventions=0.0,
            recovery_rate=None,
        )
    injected = [row for row in rows if row.injected_failure]
    recovered = [row for row in injected if row.recovered]
    return AggregateMetrics(
        n=n,
        task_success_rate=sum(row.success for row in rows) / n,
        terminal_failure_rate=sum(not row.success for row in rows) / n,
        mean_latency=sum(row.latency_ms for row in rows) / n,
        number_of_retries=sum(row.retries for row in rows) / n,
        number_of_route_changes=sum(row.route_changes for row in rows) / n,
        human_interventions=sum(row.human_interventions for row in rows) / n,
        recovery_rate=(sum(1 for _ in recovered) / len(injected)) if injected else None,
    )
