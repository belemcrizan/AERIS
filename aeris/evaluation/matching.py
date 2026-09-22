"""Deterministic fault-to-hazard matching.

V0 asked "did a TOOL_FAILURE appear somewhere in a flight that had a
fault?". That rewards any hazard that happens to come next. V1 matches
fault *instances* to hazard *events*:

A hazard H is attributed to fault F when all of these hold:
  1. H.type is in COMPATIBLE[F.fault_type]
  2. H.route_id == F.route_id (when both are known)
  3. H.waypoint_id == F.waypoint_id, unless the fault type is flight-scoped
     (NO_PROGRESS, REPEATED_ACTION, BUDGET_OVERRUN)
  4. F.onset <= H.timestamp <= F.onset + window_ms
Among several candidates the fault with the latest onset wins; ties break
on the recorder sequence. No LLM is involved.

Definitions (all per flight, then aggregated):
  TP  ground-truth faults with at least one attributed hazard
  FN  ground-truth faults with no attributed hazard (unmatched faults)
  FP  hazards attributed to no fault, plus hazards attributed to a
      fault whose ground_truth is false (a misleading injected signal)
  duplicate  further hazards attributed to an already-detected true fault;
      they are reported but excluded from precision
  MTTD  first attributed hazard timestamp - fault onset
  MTTI  first non-CONTINUE decision after that hazard whose evidence names
        a compatible hazard type - that hazard timestamp
  MTTR  successful FLIGHT_COMPLETED timestamp - fault onset
  late  MTTD > late_after_ms
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aeris.core.enums import ControlAction, EventType, FaultType, HazardType

COMPATIBLE: dict[FaultType, frozenset[HazardType]] = {
    FaultType.LATENCY: frozenset({HazardType.HIGH_LATENCY, HazardType.TIMEOUT_RISK}),
    FaultType.TIMEOUT: frozenset(
        {HazardType.TIMEOUT_RISK, HazardType.HIGH_LATENCY, HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}
    ),
    FaultType.TRANSIENT_FAILURE: frozenset({HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}),
    FaultType.PERSISTENT_FAILURE: frozenset({HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}),
    FaultType.RATE_LIMIT: frozenset({HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}),
    FaultType.MALFORMED_RESPONSE: frozenset({HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}),
    FaultType.LOST_RESPONSE_AFTER_COMMIT: frozenset({HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}),
    FaultType.STALE_RESPONSE: frozenset({HazardType.STALE_DATA}),
    FaultType.FALSE_CONFIDENCE: frozenset({HazardType.LOW_CONFIDENCE}),
    FaultType.NO_PROGRESS: frozenset(
        {HazardType.NO_PROGRESS, HazardType.REPEATED_ACTION, HazardType.TOOL_FAILURE, HazardType.ROUTE_FAILURE}
    ),
    FaultType.REPEATED_ACTION: frozenset({HazardType.REPEATED_ACTION, HazardType.NO_PROGRESS}),
    FaultType.BUDGET_OVERRUN: frozenset({HazardType.BUDGET_RISK}),
}

FLIGHT_SCOPED = frozenset({FaultType.NO_PROGRESS, FaultType.REPEATED_ACTION, FaultType.BUDGET_OVERRUN})


class MatchingPolicy(BaseModel):
    window_ms: float = 60_000.0
    late_after_ms: float = 10_000.0


class FaultMatch(BaseModel):
    fault_id: str
    schedule_id: str
    fault_type: FaultType
    ground_truth: bool
    route_id: str | None = None
    waypoint_id: str | None = None
    onset: datetime
    hazard_id: str | None = None
    hazard_type: str | None = None
    detected_at: datetime | None = None
    mttd_ms: float | None = None
    late: bool = False
    duplicate_hazard_ids: list[str] = Field(default_factory=list)
    intervention_decision_id: str | None = None
    intervention_action: str | None = None
    mtti_ms: float | None = None
    mttr_ms: float | None = None


class HazardAttribution(BaseModel):
    hazard_id: str
    hazard_type: str
    timestamp: datetime
    source_fault_id: str | None = None
    role: str  # first | duplicate | unmatched | misleading


class EventMatchReport(BaseModel):
    flight_id: str
    faults: list[FaultMatch] = Field(default_factory=list)
    hazards: list[HazardAttribution] = Field(default_factory=list)
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    duplicate_hazards: int = 0
    unmatched_hazards: int = 0
    unmatched_faults: int = 0
    late_detections: int = 0
    manifested_schedule_ids: list[str] = Field(default_factory=list)
    pre_intervention_schedule_ids: list[str] = Field(default_factory=list)
    first_intervention_seq: int | None = None

    @property
    def mttd_samples(self) -> list[float]:
        return [match.mttd_ms for match in self.faults if match.mttd_ms is not None]

    @property
    def mtti_samples(self) -> list[float]:
        return [match.mtti_ms for match in self.faults if match.mtti_ms is not None]

    @property
    def mttr_samples(self) -> list[float]:
        return [match.mttr_ms for match in self.faults if match.mttr_ms is not None]


def _ms(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() * 1000


def _decision_hazard_types(payload: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for item in payload.get("evidence", {}).get("hazards", []) or []:
        if isinstance(item, dict) and item.get("type"):
            types.add(str(item["type"]))
        elif isinstance(item, str):
            types.add(item)
    return types


def match_timeline(events: list[Any], policy: MatchingPolicy | None = None) -> EventMatchReport:
    policy = policy or MatchingPolicy()
    flight_id = events[0].flight_id if events else ""
    faults: list[tuple[int, FaultMatch]] = []
    hazards: list[tuple[int, Any]] = []
    decisions: list[tuple[int, Any]] = []
    completed_ok: datetime | None = None
    for event in events:
        if event.event_type == EventType.FAULT_INJECTED:
            payload = event.payload
            if not payload.get("fault_id"):
                continue
            faults.append(
                (
                    event.seq,
                    FaultMatch(
                        fault_id=payload["fault_id"],
                        schedule_id=payload.get("schedule_id", payload["fault_id"]),
                        fault_type=FaultType(payload["fault_type"]),
                        ground_truth=bool(payload.get("ground_truth", True)),
                        route_id=payload.get("route_id"),
                        waypoint_id=payload.get("waypoint_id"),
                        onset=event.timestamp,
                    ),
                )
            )
        elif event.event_type == EventType.HAZARD:
            hazards.append((event.seq, event))
        elif event.event_type == EventType.DECISION:
            decisions.append((event.seq, event))
        elif event.event_type == EventType.FLIGHT_COMPLETED and event.payload.get("success"):
            completed_ok = event.timestamp

    report = EventMatchReport(flight_id=flight_id)
    first_hazard_seq: dict[str, int] = {}
    for seq, event in hazards:
        payload = event.payload
        hazard_type = str(payload.get("type"))
        hazard_route = payload.get("route_id") or event.route_id
        hazard_waypoint = payload.get("waypoint_id") or event.waypoint_id
        best: tuple[int, FaultMatch] | None = None
        for fault_seq, fault in faults:
            if fault_seq > seq:
                continue
            if HazardType(hazard_type) not in COMPATIBLE.get(fault.fault_type, frozenset()):
                continue
            if fault.route_id and hazard_route and fault.route_id != hazard_route:
                continue
            if (
                fault.fault_type not in FLIGHT_SCOPED
                and fault.waypoint_id
                and hazard_waypoint
                and fault.waypoint_id != hazard_waypoint
            ):
                continue
            elapsed = _ms(fault.onset, event.timestamp)
            if elapsed < 0 or elapsed > policy.window_ms:
                continue
            if best is None or (fault.onset, fault_seq) > (best[1].onset, best[0]):
                best = (fault_seq, fault)
        hazard_id = str(payload.get("hazard_id", f"seq-{seq}"))
        if best is None:
            report.hazards.append(
                HazardAttribution(
                    hazard_id=hazard_id, hazard_type=hazard_type, timestamp=event.timestamp, role="unmatched"
                )
            )
            report.unmatched_hazards += 1
            report.false_positives += 1
            continue
        fault = best[1]
        if not fault.ground_truth:
            role = "misleading"
            report.false_positives += 1
            if fault.hazard_id is None:
                fault.hazard_id = hazard_id
                fault.hazard_type = hazard_type
                fault.detected_at = event.timestamp
        elif fault.hazard_id is None:
            role = "first"
            fault.hazard_id = hazard_id
            fault.hazard_type = hazard_type
            fault.detected_at = event.timestamp
            fault.mttd_ms = _ms(fault.onset, event.timestamp)
            fault.late = fault.mttd_ms > policy.late_after_ms
            first_hazard_seq[fault.fault_id] = seq
        else:
            role = "duplicate"
            fault.duplicate_hazard_ids.append(hazard_id)
            report.duplicate_hazards += 1
        report.hazards.append(
            HazardAttribution(
                hazard_id=hazard_id,
                hazard_type=hazard_type,
                timestamp=event.timestamp,
                source_fault_id=fault.fault_id,
                role=role,
            )
        )

    interventions = [
        (seq, event)
        for seq, event in decisions
        if event.payload.get("action") not in {None, ControlAction.CONTINUE.value}
    ]
    report.first_intervention_seq = interventions[0][0] if interventions else None

    for _, fault in faults:
        if fault.ground_truth:
            if fault.hazard_id is None:
                report.false_negatives += 1
                report.unmatched_faults += 1
            else:
                report.true_positives += 1
                if fault.late:
                    report.late_detections += 1
                hazard_seq = first_hazard_seq[fault.fault_id]
                compatible = {item.value for item in COMPATIBLE[fault.fault_type]}
                for seq, decision in interventions:
                    if seq <= hazard_seq:
                        continue
                    if not (_decision_hazard_types(decision.payload) & compatible):
                        continue
                    fault.intervention_decision_id = decision.payload.get("decision_id")
                    fault.intervention_action = decision.payload.get("action")
                    fault.mtti_ms = _ms(fault.detected_at, decision.timestamp)
                    break
        if completed_ok is not None and fault.ground_truth:
            fault.mttr_ms = _ms(fault.onset, completed_ok)
        report.faults.append(fault)

    report.manifested_schedule_ids = sorted({fault.schedule_id for _, fault in faults})
    cutoff = report.first_intervention_seq
    report.pre_intervention_schedule_ids = sorted(
        {fault.schedule_id for seq, fault in faults if cutoff is None or seq < cutoff}
    )
    return report


def attributed_hazards(events: list[Any], report: EventMatchReport) -> list[dict[str, Any]]:
    """Hazard payloads with ``source_fault_id`` filled from the match report."""

    by_id = {item.hazard_id: item.source_fault_id for item in report.hazards}
    result = []
    for event in events:
        if event.event_type != EventType.HAZARD:
            continue
        payload = dict(event.payload)
        payload["source_fault_id"] = by_id.get(str(payload.get("hazard_id")))
        result.append(payload)
    return result
