"""Per-flight and aggregate experiment metrics.

Detection counts are event-level (fault instance vs hazard event), see
``aeris.evaluation.matching``. Rates that would divide by zero are null.
Deterministic fixtures do not get p-values or confidence intervals:
repeating a deterministic fixture does not create a sample.
"""

from __future__ import annotations

from pydantic import BaseModel

from aeris.core.enums import ExecutionState, InterventionOutcome
from aeris.evaluation.pairing import utility_rates


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
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0
    duplicate_hazards: int = 0
    unmatched_hazards: int = 0
    unmatched_faults: int = 0
    late_detections: int = 0
    faults_injected: int = 0
    mttd_ms: float | None = None
    mtti_ms: float | None = None
    mttr_ms: float | None = None
    interventions: int = 0
    useful_interventions: int = 0
    unnecessary_interventions: int = 0
    intervention_outcome: InterventionOutcome | None = None
    side_effect_incidents: int = 0
    compensations: int = 0
    compensation_failures: int = 0
    failure_reason: str | None = None
    total_cost: float = 0.0


class AggregateMetrics(BaseModel):
    n: int
    task_success_rate: float
    terminal_failure_rate: float
    mean_latency: float
    number_of_retries: float
    number_of_route_changes: float
    human_interventions: float
    recovery_rate: float | None
    detector_precision: float | None = None
    detector_recall: float | None = None
    false_positive_rate: float | None = None
    false_negative_rate: float | None = None
    true_positive_count: int = 0
    false_positive_count: int = 0
    true_negative_count: int = 0
    false_negative_count: int = 0
    duplicate_hazard_count: int = 0
    unmatched_hazard_count: int = 0
    unmatched_fault_count: int = 0
    late_detection_count: int = 0
    mean_time_to_detect: float | None = None
    mean_time_to_intervention: float | None = None
    mean_time_to_recovery: float | None = None
    mean_retries: float = 0.0
    mean_route_changes: float = 0.0
    human_intervention_rate: float | None = None
    unnecessary_intervention_rate: float | None = None
    intervention_precision: float | None = None
    beneficial_intervention_rate: float | None = None
    harmful_intervention_rate: float | None = None
    neutral_intervention_rate: float | None = None
    unresolved_intervention_rate: float | None = None
    side_effect_incident_count: int = 0
    compensation_success_rate: float | None = None


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def summarize(rows: list[FlightMetrics]) -> AggregateMetrics:
    n = len(rows)
    empty = AggregateMetrics(
        n=0,
        task_success_rate=0.0,
        terminal_failure_rate=0.0,
        mean_latency=0.0,
        number_of_retries=0.0,
        number_of_route_changes=0.0,
        human_interventions=0.0,
        recovery_rate=None,
    )
    if n == 0:
        return empty
    injected = [row for row in rows if row.injected_failure]
    recovered = [row for row in injected if row.recovered]
    tp = sum(row.true_positives for row in rows)
    fp = sum(row.false_positives for row in rows)
    tn = sum(row.true_negatives for row in rows)
    fn = sum(row.false_negatives for row in rows)
    interventions = sum(row.interventions for row in rows)
    useful = sum(row.useful_interventions for row in rows)
    unnecessary = sum(row.unnecessary_interventions for row in rows)
    compensations = sum(row.compensations for row in rows)
    compensation_failures = sum(row.compensation_failures for row in rows)
    utility = utility_rates([row.intervention_outcome for row in rows])
    return AggregateMetrics(
        n=n,
        task_success_rate=sum(row.success for row in rows) / n,
        terminal_failure_rate=sum(not row.success for row in rows) / n,
        mean_latency=sum(row.latency_ms for row in rows) / n,
        number_of_retries=sum(row.retries for row in rows) / n,
        number_of_route_changes=sum(row.route_changes for row in rows) / n,
        human_interventions=sum(row.human_interventions for row in rows) / n,
        recovery_rate=_ratio(len(recovered), len(injected)),
        detector_precision=_ratio(tp, tp + fp),
        detector_recall=_ratio(tp, tp + fn),
        false_positive_rate=_ratio(fp, fp + tn),
        false_negative_rate=_ratio(fn, fn + tp),
        true_positive_count=tp,
        false_positive_count=fp,
        true_negative_count=tn,
        false_negative_count=fn,
        duplicate_hazard_count=sum(row.duplicate_hazards for row in rows),
        unmatched_hazard_count=sum(row.unmatched_hazards for row in rows),
        unmatched_fault_count=sum(row.unmatched_faults for row in rows),
        late_detection_count=sum(row.late_detections for row in rows),
        mean_time_to_detect=_mean([row.mttd_ms for row in rows if row.mttd_ms is not None]),
        mean_time_to_intervention=_mean([row.mtti_ms for row in rows if row.mtti_ms is not None]),
        mean_time_to_recovery=_mean([row.mttr_ms for row in rows if row.mttr_ms is not None]),
        mean_retries=sum(row.retries for row in rows) / n,
        mean_route_changes=sum(row.route_changes for row in rows) / n,
        human_intervention_rate=_ratio(sum(row.human_interventions > 0 for row in rows), n),
        unnecessary_intervention_rate=_ratio(unnecessary, interventions),
        intervention_precision=_ratio(useful, interventions),
        beneficial_intervention_rate=utility["beneficial_intervention_rate"],
        harmful_intervention_rate=utility["harmful_intervention_rate"],
        neutral_intervention_rate=utility["neutral_intervention_rate"],
        unresolved_intervention_rate=utility["unresolved_intervention_rate"],
        side_effect_incident_count=sum(row.side_effect_incidents for row in rows),
        compensation_success_rate=_ratio(compensations, compensations + compensation_failures),
    )
