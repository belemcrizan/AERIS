"""Framework-agnostic agent runtime contract.

LangGraph, AutoGen, CrewAI, the OpenAI Agents SDK, Google ADK, or a custom
loop can implement AgentRuntime later. V0 only needs the simulator.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from aeris.core.models import Flight, StepObservation, Waypoint


class ExecutionContext(BaseModel):
    flight: Flight
    waypoint: Waypoint
    attempt: int = 0


@runtime_checkable
class AgentRuntime(Protocol):
    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        """Run one waypoint. Must not raise for expected tool failures."""
        ...
