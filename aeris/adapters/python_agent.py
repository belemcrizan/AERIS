"""One real, local agent loop.

``SimplePythonAgentRuntime`` dispatches a waypoint name to a Python callable.
It does not call an LLM and does not need a network. Tests and CI stay
offline. An external model can be added later behind the same contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from aeris.adapters.runtime import (
    CompensationResult,
    ExecutionContext,
    RuntimeCapabilities,
)
from aeris.core.enums import SideEffectClass
from aeris.core.models import StepObservation


class SimplePythonAgentRuntime:
    """Deterministic tool-dispatch agent.

    Route Alpha calling ``tool_a`` and Route Bravo calling ``tool_b`` is the
    First Flight shape: AERIS observes a tool failure and can divert.
    """

    def __init__(
        self,
        tools: dict[str, Callable[[], str]] | None = None,
        *,
        failing_tools: set[str] | None = None,
        can_cancel: bool = True,
        checkpoints: int = 1,
    ) -> None:
        self.tools = tools or {}
        self.failing_tools = failing_tools or set()
        self.can_cancel = can_cancel
        self.checkpoints = checkpoints
        self.invocations: list[str] = []
        self.completed: list[str] = []
        self._compensated: list[str] = []

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            can_cancel=self.can_cancel,
            can_retry=True,
            can_compensate=True,
            supports_idempotency=True,
            supports_progress=True,
            supports_streaming=False,
        )

    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        waypoint = context.waypoint
        flight = context.flight
        name = waypoint.name
        token = context.cancel_token

        if token is not None and token.cancelled and token.checkpoint == "before":
            return self._observation(
                context,
                success=False,
                error="cancelled_before_execution",
                cancelled=True,
                output=None,
            )

        self.invocations.append(name)
        for _ in range(self.checkpoints):
            await asyncio.sleep(0)
            if (
                self.can_cancel
                and token is not None
                and token.cancelled
                and token.checkpoint == "during"
            ):
                return self._observation(
                    context,
                    success=False,
                    error="cancelled_during_execution",
                    cancelled=True,
                    output=None,
                )

        if not self.can_cancel and token is not None and token.cancelled:
            # The runtime cannot interrupt itself. The call still finishes.
            pass

        if name in self.failing_tools:
            return self._observation(
                context,
                success=False,
                error="tool_error",
                tool_error=True,
                output=None,
                fault_injected=True,
                fault_kind="tool_failure",
            )

        tool = self.tools.get(name)
        output = tool() if tool is not None else f"completed {name}"
        self.completed.append(name)
        total = max(len(flight.current_route().waypoints), 1)
        progress = (waypoint.index + 1) / total
        return self._observation(
            context,
            success=True,
            output=output,
            progress=progress,
        )

    async def compensate(self, context: ExecutionContext) -> CompensationResult:
        name = context.waypoint.compensation_name or context.waypoint.name
        self._compensated.append(name)
        return CompensationResult(
            success=True,
            waypoint_id=context.waypoint.waypoint_id,
            detail=f"compensated {name}",
        )

    def _observation(
        self,
        context: ExecutionContext,
        *,
        success: bool,
        output: str | None,
        error: str | None = None,
        tool_error: bool = False,
        cancelled: bool = False,
        progress: float = 0.0,
        fault_injected: bool = False,
        fault_kind: str | None = None,
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
            latency_ms=25.0,
            success=success,
            tool_error=tool_error,
            timeout=False,
            token_usage=0,
            confidence=0.9,
            data_freshness_s=1.0,
            progress=progress,
            action=f"{route.name}:{waypoint.name}",
            output=output,
            error=error,
            side_effect_class=waypoint.side_effect,
            side_effect_committed=success and waypoint.side_effect is not SideEffectClass.READ_ONLY,
            idempotency_key=waypoint.idempotency_key,
            compensation_available=waypoint.compensation_name is not None,
            fault_injected=fault_injected,
            fault_kind=fault_kind,
            cancelled=cancelled,
        )
