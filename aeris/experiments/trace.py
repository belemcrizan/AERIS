"""Render a recorder timeline as a readable, replayable flight trace.

The recorder is the source of truth; this is a view over it. Offsets are
experiment time (the injected clock), not wall-clock time.
"""

from __future__ import annotations

from typing import Any

from aeris.core.enums import EventType
from aeris.recorder.base import RecordedEvent


def _offset(event: RecordedEvent, start: RecordedEvent) -> str:
    total_ms = (event.timestamp - start.timestamp).total_seconds() * 1000
    minutes, rest = divmod(total_ms, 60_000)
    return f"{int(minutes):02d}:{rest / 1000:06.3f}"


def _short(text: str, limit: int = 90) -> str:
    text = text.split(": + reliability")[0]
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe(event: RecordedEvent, route_names: dict[str, str]) -> list[str]:
    payload: dict[str, Any] = event.payload
    kind = event.event_type
    if kind is EventType.FLIGHT_CREATED:
        return ["flight created"]
    if kind is EventType.PLAN_CREATED:
        route = route_names.get(payload.get("selected_route_id", ""), payload.get("selected_route_id"))
        return [f"route {route} selected"]
    if kind is EventType.STATE_TRANSITION:
        return [f"state {payload.get('from_state')} -> {payload.get('to_state')} ({_short(payload.get('reason', ''))})"]
    if kind is EventType.WAYPOINT_STARTED:
        tool = f" tool={payload['tool']}" if payload.get("tool") else ""
        return [f"waypoint {payload.get('name')} started{tool}"]
    if kind is EventType.FAULT_INJECTED:
        target = payload.get("target") or payload.get("metadata", {}).get("tool")
        truth = "" if payload.get("ground_truth", True) else " (misleading signal, not a real degradation)"
        return [f"fault {payload.get('fault_type')} injected on {target} [{payload.get('fault_id')}]{truth}"]
    if kind is EventType.TELEMETRY:
        metrics = payload.get("metrics", {})
        return [
            "telemetry "
            f"step_latency={metrics.get('step_latency_ms', 0):.0f}ms "
            f"ok={metrics.get('step_success')} tool_error={metrics.get('tool_error')} "
            f"timed_out={metrics.get('timed_out')}"
        ]
    if kind is EventType.HAZARD:
        evidence = payload.get("evidence", {})
        detail = evidence.get("decision") or ""
        return [f"{payload.get('type')} detected severity={payload.get('severity')} {detail}".rstrip()]
    if kind is EventType.DECISION:
        lines = [f"ATC {payload.get('action')}: {_short(payload.get('reason', ''))}"]
        selected = (payload.get("evidence") or {}).get("selected_route")
        if selected and payload.get("action") == "REROUTE":
            diversity = selected.get("diversity")
            div_text = f"{diversity:.2f}" if isinstance(diversity, int | float) else "n/a"
            terms = selected.get("terms") or {}
            lines.append(
                f"  candidate {selected.get('name')} score={selected.get('score', 0):.3f} diversity={div_text} "
                f"shared_failed_penalty={terms.get('shared_failed_dependency_penalty', 0)}"
            )
        blocked = (payload.get("evidence") or {}).get("blocked_alternative")
        if blocked:
            lines.append(f"  blocked {blocked.get('route_id')}: shares {blocked.get('shared_failed_dependencies')}")
        if payload.get("blocked_reason"):
            lines.append(f"  safety gate: {payload['blocked_reason']}")
        return lines
    if kind is EventType.SIDE_EFFECT:
        return [f"side effect committed {payload.get('side_effect')} key={payload.get('idempotency_key')}"]
    if kind is EventType.WAYPOINT_COMPLETED:
        return [f"waypoint completed ok={payload.get('step_ok')}"]
    if kind is EventType.FLIGHT_COMPLETED:
        reason = f" reason={payload['failure_reason']}" if payload.get("failure_reason") else ""
        return [f"{payload.get('terminal_state')} ({payload.get('summary', '')}){reason}"]
    if kind in {EventType.CANCEL_REQUESTED, EventType.CANCEL_RESOLVED}:
        return [f"{kind.value} {payload.get('status', '')} {payload.get('outcome', '')}".rstrip()]
    return [kind.value]


def format_trace(
    timeline: list[RecordedEvent],
    *,
    route_names: dict[str, str] | None = None,
    include_telemetry: bool = True,
) -> str:
    if not timeline:
        return "(empty timeline)"
    route_names = route_names or {}
    start = timeline[0]
    lines = [f"FLIGHT {start.flight_id}", ""]
    for event in timeline:
        if not include_telemetry and event.event_type is EventType.TELEMETRY:
            continue
        stamp = _offset(event, start)
        for index, text in enumerate(_describe(event, route_names)):
            lines.append(f"{stamp if index == 0 else ' ' * len(stamp)} {text}")
    return "\n".join(lines)
