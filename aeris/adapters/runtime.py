"""Framework-agnostic agent runtime contract.

LangGraph, AutoGen, CrewAI, the OpenAI Agents SDK, Google ADK, or a custom
loop can implement AgentRuntime. AERIS does not assume every runtime can
cancel, compensate, or retry safely.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from aeris.core.enums import RuntimeCapability
from aeris.core.models import Flight, StepObservation, Waypoint


class CancelToken(BaseModel):
    """Cooperative cancellation flag.

    Setting ``cancelled`` asks the runtime to stop at its next checkpoint.
    Python cannot hard-preempt a running function. ``checkpoint`` records
    whether the cancel was requested before the tool body (``before``) or
    after the runtime had entered it (``during``). ``observed`` is set by a
    runtime that actually honoured the token; the director never assumes it.
    """

    cancelled: bool = False
    checkpoint: str = "before"
    reason: str | None = None
    observed: bool = False

    def request(self, checkpoint: str = "before", reason: str | None = None) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.checkpoint = checkpoint
        self.reason = reason

    def acknowledge(self) -> None:
        self.observed = True


class RuntimeCapabilities(BaseModel):
    can_cancel: bool = False
    can_retry: bool = True
    can_compensate: bool = False
    supports_idempotency: bool = False
    supports_progress: bool = True
    supports_streaming: bool = False

    def names(self) -> list[str]:
        flags = {
            RuntimeCapability.CAN_CANCEL: self.can_cancel,
            RuntimeCapability.CAN_RETRY: self.can_retry,
            RuntimeCapability.CAN_COMPENSATE: self.can_compensate,
            RuntimeCapability.SUPPORTS_IDEMPOTENCY: self.supports_idempotency,
            RuntimeCapability.SUPPORTS_PROGRESS: self.supports_progress,
            RuntimeCapability.SUPPORTS_STREAMING: self.supports_streaming,
        }
        return [capability.value for capability, enabled in flags.items() if enabled]


class ExecutionContext(BaseModel):
    flight: Flight
    waypoint: Waypoint
    attempt: int = 0
    cancel_token: CancelToken | None = None

    def should_stop(self) -> bool:
        """Cooperative checkpoint for runtimes that can cancel."""

        token = self.cancel_token
        if token is None or not token.cancelled:
            return False
        token.acknowledge()
        return True


class CompensationResult(BaseModel):
    success: bool
    waypoint_id: str
    detail: str
    failure_reason: str | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        """Run one waypoint. Must not raise for expected tool failures."""
        ...

    def capabilities(self) -> RuntimeCapabilities:
        """Declare which interventions this runtime can actually perform."""
        ...


def default_capabilities() -> RuntimeCapabilities:
    return RuntimeCapabilities()


def capability_field_names() -> list[str]:
    return list(RuntimeCapabilities.model_fields)
