"""Simulated agent runtime with injectable degradation."""

from __future__ import annotations

from pydantic import BaseModel

from aeris.adapters.runtime import ExecutionContext
from aeris.core.models import StepObservation


class InjectedFault(BaseModel):
    waypoint_index: int | None = None
    waypoint_name: str | None = None
    route_name: str | None = None
    latency_ms: float | None = None
    timeout: bool = False
    tool_failure: bool = False
    low_confidence: float | None = None
    stale_data_s: float | None = None
    repeated_action: bool = False
    fail: bool = False
    persistent: bool = True
    token_usage: int | None = None
    consumed: int = 0


class SimulatedAgent:
    def __init__(
        self,
        faults: list[InjectedFault] | None = None,
        *,
        base_latency_ms: float = 40.0,
        default_confidence: float = 0.9,
        default_freshness_s: float = 10.0,
    ) -> None:
        self.faults = faults or []
        self.base_latency_ms = base_latency_ms
        self.default_confidence = default_confidence
        self.default_freshness_s = default_freshness_s

    def reset(self) -> None:
        for fault in self.faults:
            fault.consumed = 0

    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        flight = context.flight
        waypoint = context.waypoint
        route = flight.current_route()
        fault = self._match(route.name, waypoint)
        latency = self.base_latency_ms
        success = True
        tool_error = False
        timeout = False
        confidence = self.default_confidence
        freshness = self.default_freshness_s
        action = f"{route.name}:{waypoint.name}"
        output = f"completed {waypoint.name} via {route.name}"
        error = None
        tokens = 25

        if fault is not None:
            fault.consumed += 1
            if fault.latency_ms is not None:
                latency = fault.latency_ms
            if fault.timeout:
                timeout = True
                success = False
                error = "timeout"
            if fault.tool_failure:
                tool_error = True
                success = False
                error = "tool_error"
            if fault.fail:
                success = False
                error = error or "injected_failure"
            if fault.low_confidence is not None:
                confidence = fault.low_confidence
            if fault.stale_data_s is not None:
                freshness = fault.stale_data_s
            if fault.repeated_action:
                action = "repeat:lookup"
            if fault.token_usage is not None:
                tokens = fault.token_usage
            else:
                tokens = 25

        total = max(len(route.waypoints), 1)
        progress = (waypoint.index + 1) / total if success else waypoint.index / total

        return StepObservation(
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            route_id=route.route_id,
            waypoint_id=waypoint.waypoint_id,
            waypoint_index=waypoint.index,
            latency_ms=latency,
            success=success,
            tool_error=tool_error,
            timeout=timeout,
            token_usage=tokens if fault is not None else 25,
            confidence=confidence,
            data_freshness_s=freshness,
            progress=progress,
            action=action,
            output=output if success else None,
            error=error,
            repeat_signal=bool(fault.repeated_action) if fault is not None else False,
        )

    def _match(self, route_name: str, waypoint) -> InjectedFault | None:
        for fault in self.faults:
            if not fault.persistent and fault.consumed > 0:
                continue
            if fault.route_name and fault.route_name != route_name:
                continue
            if fault.waypoint_index is not None and fault.waypoint_index != waypoint.index:
                continue
            if fault.waypoint_name and fault.waypoint_name != waypoint.name:
                continue
            return fault
        return None
