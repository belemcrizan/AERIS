"""Helpers for driving the support case in tests. No network, no wall-clock sleeps."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aeris.adapters.llm_runtime import LLMToolAgentRuntime, ToolInvocation, ToolResult
from aeris.cases.support import case
from aeris.cases.support.campaign import campaign
from aeris.cases.support.scripted_model import ScriptedSupportModel
from aeris.cases.support.toolbox import FaultSpec, SupportToolbox
from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import InterventionMode
from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight, Route
from aeris.experiments.support import experiment_policy
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner


class GatedModel(ScriptedSupportModel):
    """Scripted model that can pause before chosen calls (1-based)."""

    def __init__(self, *, pause_on: set[int] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pause_on = pause_on or set()
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.calls_seen = 0

    async def complete(self, messages, *, tools, temperature, seed):
        self.calls_seen += 1
        if self.calls_seen in self.pause_on:
            self.reached.set()
            await self.release.wait()
        return await super().complete(messages, tools=tools, temperature=temperature, seed=seed)


class HookedToolbox(SupportToolbox):
    """Runs ``hook`` after a named tool executes, before its result returns."""

    def __init__(
        self,
        *,
        tool: str,
        hook: Callable[[ToolInvocation, ToolResult], Awaitable[None]],
        faults: list[FaultSpec] | None = None,
    ) -> None:
        super().__init__(faults=faults)
        self.tool = tool
        self.hook = hook

    async def invoke(self, call: ToolInvocation) -> ToolResult:
        result = await super().invoke(call)
        if call.name == self.tool:
            await self.hook(call, result)
        return result


class SupportRig:
    def __init__(
        self,
        *,
        model: ScriptedSupportModel | None = None,
        toolbox: SupportToolbox | None = None,
        scenario: str = "healthy",
        policy: ThresholdPolicy | None = None,
        mode: InterventionMode = InterventionMode.AERIS,
        can_cancel: bool = True,
        agent_owns_retries: bool = False,
        routes: list[Route] | None = None,
    ) -> None:
        spec = campaign()[scenario]
        self.scenario = spec
        self.clock = FakeClock()
        self.recorder = InMemoryRecorder()
        self.model = model or ScriptedSupportModel(seed=0)
        self.toolbox = toolbox or SupportToolbox(faults=list(spec.faults))
        self.policy = policy or experiment_policy()
        self.runtime = LLMToolAgentRuntime(
            self.model,
            self.toolbox,
            system_prompt=case.SYSTEM_PROMPT,
            mission_prompt=case.MISSION_PROMPT,
            phases=case.PHASES,
            can_cancel=can_cancel,
            agent_owns_retries=agent_owns_retries,
        )
        self.planner = RoutePlanner()
        self.director = FlightDirector(
            recorder=self.recorder,
            runtime=self.runtime,
            planner=self.planner,
            radar=RadarEngine(clock=self.clock),
            detector=HazardDetector(policy=self.policy, clock=self.clock),
            controller=ATCController(planner=self.planner, policy=self.policy, clock=self.clock, mode=mode),
            policy=self.policy,
            clock=self.clock,
        )
        self.routes = routes or case.build_routes(spec.route_set, keyed_credit=spec.keyed_credit)
        mission = self.director.create_mission(case.OBJECTIVE, case.SUCCESS_CRITERIA)
        self.flight: Flight = self.director.create_flight(
            mission, agent=Agent(agent_id=new_id("agt"), name="support", runtime_kind="llm-tool-agent")
        )

    async def run(self) -> Flight:
        return await self.director.run(self.flight, self.routes)

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self.run())

    async def events(self, event_type) -> list:
        return [event for event in await self.recorder.timeline(self.flight.flight_id) if event.event_type == event_type]


async def settle(predicate: Callable[[], bool], *, limit: int = 1000) -> None:
    """Yield to the loop until ``predicate`` holds. Deterministic: no timers."""
    for _ in range(limit):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")
