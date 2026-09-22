"""Simulated agent runtime with injectable degradation.

The simulator is deterministic. Fault timestamps are not wall-clock sleeps:
the flight director advances the injected Clock by ``latency_ms``.
"""

from __future__ import annotations

from pydantic import BaseModel

from aeris.adapters.runtime import CompensationResult, ExecutionContext, RuntimeCapabilities
from aeris.core.enums import SideEffectClass
from aeris.core.models import StepObservation


class InjectedFault(BaseModel):
    waypoint_index: int | None = None
    waypoint_name: str | None = None
    route_name: str | None = None
    latency_ms: float | None = None
    timeout: bool = False
    tool_failure: bool = False
    low_confidence: float | None = None
    reported_confidence: float | None = None
    stale_data_s: float | None = None
    repeated_action: bool = False
    no_progress: bool = False
    fail: bool = False
    persistent: bool = True
    token_usage: int | None = None
    lose_response: bool = False
    commit_side_effect: bool = False
    compensation_fails: bool = False
    consumed: int = 0


class SimulatedAgent:
    def __init__(
        self,
        faults: list[InjectedFault] | None = None,
        *,
        base_latency_ms: float = 40.0,
        default_confidence: float = 0.9,
        default_freshness_s: float = 10.0,
        can_cancel: bool = False,
    ) -> None:
        self.faults = faults or []
        self.base_latency_ms = base_latency_ms
        self.default_confidence = default_confidence
        self.default_freshness_s = default_freshness_s
        self.can_cancel = can_cancel
        self.fail_next_compensation = any(fault.compensation_fails for fault in self.faults)
        self.commits: dict[str, int] = {}
        self._idempotency: dict[str, StepObservation] = {}
        self.invocations: list[str] = []

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            can_cancel=self.can_cancel,
            can_retry=True,
            can_compensate=True,
            supports_idempotency=True,
            supports_progress=True,
            supports_streaming=False,
        )

    def reset(self) -> None:
        for fault in self.faults:
            fault.consumed = 0
        self.commits.clear()
        self._idempotency.clear()
        self.invocations.clear()

    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        flight = context.flight
        waypoint = context.waypoint
        route = flight.current_route()
        token = context.cancel_token
        if self.can_cancel and token is not None and token.cancelled and token.checkpoint == "before":
            return self._base_observation(
                context,
                latency=self.base_latency_ms,
                success=False,
                error="cancelled_before_execution",
                cancelled=True,
            )

        key = waypoint.idempotency_key if waypoint.supports_idempotency else None
        if key and key in self._idempotency:
            cached = self._idempotency[key].model_copy(deep=True)
            cached.duplicate_suppressed = True
            cached.side_effect_committed = False
            cached.success = True
            cached.tool_error = False
            cached.timeout = False
            cached.error = None
            cached.fault_injected = False
            cached.output = cached.output or f"idempotent replay {key}"
            return cached

        self.invocations.append(waypoint.waypoint_id)
        if self.can_cancel and token is not None and token.cancelled and token.checkpoint == "during":
            return self._base_observation(
                context,
                latency=self.base_latency_ms,
                success=False,
                error="cancelled_during_execution",
                cancelled=True,
            )

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
        fault_kind = None
        repeat = False
        no_progress = False

        if fault is not None:
            fault.consumed += 1
            fault_kind = "injected"
            if fault.latency_ms is not None:
                latency = fault.latency_ms
                fault_kind = "latency"
            if fault.timeout:
                timeout = True
                success = False
                error = "timeout"
                fault_kind = "timeout"
            if fault.tool_failure:
                tool_error = True
                success = False
                error = "tool_error"
                fault_kind = "tool_failure"
            if fault.fail and success:
                success = False
                error = error or "injected_failure"
            if fault.reported_confidence is not None:
                confidence = fault.reported_confidence
            elif fault.low_confidence is not None:
                confidence = fault.low_confidence
                fault_kind = fault_kind or "low_confidence"
            if fault.stale_data_s is not None:
                freshness = fault.stale_data_s
                fault_kind = fault_kind or "stale_data"
            if fault.repeated_action:
                action = "repeat:lookup"
                repeat = True
                fault_kind = "repeated_action"
            if fault.no_progress:
                no_progress = True
                fault_kind = fault_kind or "no_progress"
            if fault.token_usage is not None:
                tokens = fault.token_usage
                fault_kind = fault_kind or "budget"
            if fault.lose_response:
                fault_kind = "response_lost"

        total = max(len(route.waypoints), 1)
        if no_progress:
            progress = 0.0
        else:
            progress = (waypoint.index + 1) / total if success else waypoint.index / total

        side_effect = waypoint.side_effect
        should_commit = side_effect is not SideEffectClass.READ_ONLY and (
            success or (fault is not None and fault.commit_side_effect) or (fault is not None and fault.lose_response)
        )
        committed = False
        if should_commit:
            commit_key = key or waypoint.waypoint_id
            self.commits[commit_key] = self.commits.get(commit_key, 0) + 1
            committed = True

        observation = StepObservation(
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            route_id=route.route_id,
            waypoint_id=waypoint.waypoint_id,
            waypoint_index=waypoint.index,
            latency_ms=latency,
            success=success,
            tool_error=tool_error,
            timeout=timeout,
            token_usage=tokens,
            confidence=confidence,
            data_freshness_s=freshness,
            progress=progress,
            action=action,
            output=output if success else None,
            error=error,
            repeat_signal=repeat,
            side_effect_class=side_effect,
            side_effect_committed=committed,
            idempotency_key=key,
            compensation_available=bool(waypoint.compensation_name),
            fault_injected=fault is not None,
            fault_kind=fault_kind,
        )

        if key and committed:
            stored = observation.model_copy(deep=True)
            stored.success = True
            stored.tool_error = False
            stored.timeout = False
            stored.error = None
            stored.output = output
            stored.duplicate_suppressed = False
            self._idempotency[key] = stored

        if fault is not None and fault.lose_response:
            observation.success = False
            observation.tool_error = True
            observation.error = "response_lost"
            observation.output = None
            observation.side_effect_committed = committed

        return observation

    async def compensate(self, context: ExecutionContext) -> CompensationResult:
        waypoint = context.waypoint
        if self.fail_next_compensation:
            self.fail_next_compensation = False
            return CompensationResult(
                success=False,
                waypoint_id=waypoint.waypoint_id,
                detail="compensation failed",
                failure_reason="compensation_failure",
            )
        if not waypoint.compensation_name:
            return CompensationResult(
                success=False,
                waypoint_id=waypoint.waypoint_id,
                detail="waypoint defines no compensation",
                failure_reason="compensation_unavailable",
            )
        key = waypoint.idempotency_key or waypoint.waypoint_id
        if self.commits.get(key, 0) > 0:
            self.commits[key] -= 1
        self._idempotency.pop(key, None)
        return CompensationResult(
            success=True,
            waypoint_id=waypoint.waypoint_id,
            detail=f"compensated {waypoint.compensation_name}",
        )

    def _base_observation(
        self,
        context: ExecutionContext,
        *,
        latency: float,
        success: bool,
        error: str | None,
        cancelled: bool,
    ) -> StepObservation:
        flight = context.flight
        waypoint = context.waypoint
        route = flight.current_route()
        return StepObservation(
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            route_id=route.route_id,
            waypoint_id=waypoint.waypoint_id,
            waypoint_index=waypoint.index,
            latency_ms=latency,
            success=success,
            error=error,
            cancelled=cancelled,
            action=f"{route.name}:{waypoint.name}",
            side_effect_class=waypoint.side_effect,
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
