"""Rebuild a readable flight snapshot from the append-only log."""

from __future__ import annotations

from typing import Any

from aeris.core.enums import EventType
from aeris.recorder.base import FlightRecorder, RecordedEvent


async def reconstruct_flight(recorder: FlightRecorder, flight_id: str) -> dict[str, Any]:
    events = await recorder.timeline(flight_id)
    return reconstruct_from_events(flight_id, events)


def reconstruct_from_events(flight_id: str, events: list[RecordedEvent]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "flight_id": flight_id,
        "mission_id": None,
        "state": None,
        "current_route_id": None,
        "plan": None,
        "result": None,
        "transitions": [],
        "hazard_count": 0,
        "decision_count": 0,
        "human_interventions": 0,
    }
    for event in events:
        if event.mission_id:
            snapshot["mission_id"] = event.mission_id
        if event.event_type == EventType.FLIGHT_CREATED:
            snapshot.update(
                {
                    "agent": event.payload.get("agent"),
                    "created_at": event.payload.get("created_at"),
                    "state": event.payload.get("state"),
                }
            )
        elif event.event_type == EventType.PLAN_CREATED:
            snapshot["plan"] = event.payload
            snapshot["current_route_id"] = event.payload.get("selected_route_id")
        elif event.event_type == EventType.STATE_TRANSITION:
            snapshot["state"] = event.payload.get("to_state")
            snapshot["transitions"].append(event.payload)
        elif event.event_type == EventType.HAZARD:
            snapshot["hazard_count"] += 1
        elif event.event_type == EventType.DECISION:
            snapshot["decision_count"] += 1
            if event.payload.get("new_route"):
                snapshot["current_route_id"] = event.payload.get("new_route")
        elif event.event_type == EventType.HUMAN_INTERVENTION:
            snapshot["human_interventions"] += 1
        elif event.event_type == EventType.FLIGHT_COMPLETED:
            snapshot["result"] = event.payload
    snapshot["event_count"] = len(events)
    return snapshot


def timeline_view(events: list[RecordedEvent]) -> list[dict[str, Any]]:
    return [
        {
            "seq": event.seq,
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "mission_id": event.mission_id,
            "route_id": event.route_id,
            "waypoint_id": event.waypoint_id,
            "payload": event.payload,
        }
        for event in events
    ]
