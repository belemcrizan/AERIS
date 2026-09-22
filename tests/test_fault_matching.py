"""Deterministic fault-instance to hazard-event matching."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from support_helpers import SupportRig

from aeris.core.enums import EventType
from aeris.evaluation.matching import MatchingPolicy, attributed_hazards, match_timeline
from aeris.recorder.base import RecordedEvent

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _Timeline:
    def __init__(self) -> None:
        self.events: list[RecordedEvent] = []

    def add(self, kind: EventType, at_ms: float, payload: dict, *, route="r1", waypoint="w1") -> None:
        self.events.append(
            RecordedEvent(
                seq=len(self.events) + 1,
                flight_id="flt",
                event_type=kind,
                timestamp=T0 + timedelta(milliseconds=at_ms),
                payload=payload,
                route_id=route,
                waypoint_id=waypoint,
            )
        )

    def fault(self, at_ms, fault_type, *, fault_id="f1", ground_truth=True, **where):
        self.add(
            EventType.FAULT_INJECTED,
            at_ms,
            {
                "fault_id": fault_id,
                "schedule_id": f"s-{fault_id}",
                "fault_type": fault_type,
                "ground_truth": ground_truth,
                "route_id": where.get("route", "r1"),
                "waypoint_id": where.get("waypoint", "w1"),
            },
            **where,
        )

    def hazard(self, at_ms, hazard_type, *, hazard_id, **where):
        self.add(EventType.HAZARD, at_ms, {"hazard_id": hazard_id, "type": hazard_type}, **where)

    def decision(self, at_ms, action, hazard_types):
        self.add(
            EventType.DECISION,
            at_ms,
            {"decision_id": f"d{at_ms}", "action": action, "evidence": {"hazards": [{"type": t} for t in hazard_types]}},
        )


def test_compatible_hazard_on_the_same_waypoint_is_a_true_positive():
    tl = _Timeline()
    tl.fault(0, "TIMEOUT")
    tl.hazard(5000, "TIMEOUT_RISK", hazard_id="h1")
    tl.decision(5000, "REROUTE", ["TIMEOUT_RISK"])
    tl.add(EventType.FLIGHT_COMPLETED, 9000, {"success": True})
    report = match_timeline(tl.events)
    assert (report.true_positives, report.false_positives, report.false_negatives) == (1, 0, 0)
    fault = report.faults[0]
    assert fault.mttd_ms == 5000 and fault.mtti_ms == 0 and fault.mttr_ms == 9000
    assert attributed_hazards(tl.events, report)[0]["source_fault_id"] == "f1"


def test_the_next_unrelated_hazard_is_not_matched():
    tl = _Timeline()
    tl.fault(0, "TIMEOUT")
    tl.hazard(100, "STALE_DATA", hazard_id="h1")
    report = match_timeline(tl.events)
    assert report.true_positives == 0
    assert report.unmatched_hazards == 1 and report.false_positives == 1
    assert report.unmatched_faults == 1 and report.false_negatives == 1


def test_hazard_on_another_waypoint_is_not_matched():
    tl = _Timeline()
    tl.fault(0, "PERSISTENT_FAILURE", waypoint="w1")
    tl.hazard(100, "TOOL_FAILURE", hazard_id="h1", waypoint="w2")
    report = match_timeline(tl.events)
    assert report.true_positives == 0 and report.unmatched_faults == 1


def test_late_and_duplicate_detections_are_counted_but_not_rewarded():
    tl = _Timeline()
    tl.fault(0, "LATENCY")
    tl.hazard(12_000, "HIGH_LATENCY", hazard_id="h1")
    tl.hazard(13_000, "HIGH_LATENCY", hazard_id="h2")
    report = match_timeline(tl.events, MatchingPolicy(late_after_ms=10_000))
    assert report.true_positives == 1
    assert report.late_detections == 1
    assert report.duplicate_hazards == 1
    assert report.false_positives == 0


def test_hazard_outside_the_window_is_unmatched():
    tl = _Timeline()
    tl.fault(0, "LATENCY")
    tl.hazard(70_000, "HIGH_LATENCY", hazard_id="h1")
    report = match_timeline(tl.events, MatchingPolicy(window_ms=60_000))
    assert report.true_positives == 0 and report.unmatched_hazards == 1


def test_hazard_matching_a_misleading_signal_is_a_false_positive():
    tl = _Timeline()
    tl.fault(0, "FALSE_CONFIDENCE", ground_truth=False)
    tl.hazard(100, "LOW_CONFIDENCE", hazard_id="h1")
    report = match_timeline(tl.events)
    assert report.false_positives == 1 and report.true_positives == 0 and report.false_negatives == 0
    assert report.hazards[0].role == "misleading"


def test_mtti_skips_decisions_about_unrelated_hazards():
    tl = _Timeline()
    tl.fault(0, "TIMEOUT")
    tl.hazard(1000, "TIMEOUT_RISK", hazard_id="h1")
    tl.decision(1500, "HOLD", ["BUDGET_RISK"])
    tl.decision(2500, "REROUTE", ["TIMEOUT_RISK"])
    report = match_timeline(tl.events)
    assert report.faults[0].mtti_ms == 1500
    assert report.faults[0].intervention_action == "REROUTE"


def test_mttr_is_undefined_when_the_flight_fails():
    tl = _Timeline()
    tl.fault(0, "TIMEOUT")
    tl.hazard(10, "TIMEOUT_RISK", hazard_id="h1")
    tl.add(EventType.FLIGHT_COMPLETED, 50, {"success": False})
    assert match_timeline(tl.events).faults[0].mttr_ms is None


async def test_every_injected_fault_has_a_unique_id_and_hazards_can_cite_it():
    rig = SupportRig(scenario="incident_db_persistent")
    await rig.run()
    timeline = await rig.recorder.timeline(rig.flight.flight_id)
    faults = [event.payload for event in timeline if event.event_type is EventType.FAULT_INJECTED]
    assert faults
    assert len({fault["fault_id"] for fault in faults}) == len(faults)
    assert all(fault["route_id"] and fault["waypoint_id"] for fault in faults)
    report = match_timeline(timeline)
    cited = {row["source_fault_id"] for row in attributed_hazards(timeline, report)} - {None}
    assert cited <= {fault["fault_id"] for fault in faults}
    assert cited
