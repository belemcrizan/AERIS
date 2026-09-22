"""AERIS First Flight: a local Python agent, two tools, one diversion.

No API key and no network. Tool A fails. AERIS records the hazard and
flies Route Bravo, which calls Tool B.
"""

from __future__ import annotations

import asyncio
import json

from aeris.adapters.python_agent import SimplePythonAgentRuntime
from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import ExecutionState
from aeris.core.ids import new_id
from aeris.core.models import Route, Waypoint
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner


def _route(name: str, tool: str, reliability: float) -> Route:
    return Route(
        route_id=new_id("rte"),
        name=name,
        description=f"{name} calls {tool}",
        waypoints=[Waypoint(waypoint_id=new_id("wp"), name=tool, index=0, description=tool)],
        estimated_reliability=reliability,
        estimated_latency_ms=200,
        estimated_cost=1.0,
    )


async def main() -> None:
    clock = FakeClock()
    policy = ThresholdPolicy(human_on_critical=False, hold_ms=0, max_retries=0)
    runtime = SimplePythonAgentRuntime(
        tools={
            "tool_a": lambda: "alpha-result",
            "tool_b": lambda: "bravo-result",
        },
        failing_tools={"tool_a"},
    )
    planner = RoutePlanner()
    director = FlightDirector(
        recorder=InMemoryRecorder(),
        runtime=runtime,
        planner=planner,
        radar=RadarEngine(clock=clock),
        detector=HazardDetector(policy=policy, clock=clock),
        controller=ATCController(planner=planner, policy=policy, clock=clock),
        policy=policy,
        clock=clock,
    )
    mission = director.create_mission(
        "Call a tool and return its result.",
        "Route Bravo completes after Tool A fails.",
    )
    await director.record_mission(mission)
    flight = director.create_flight(mission)
    await director.run(flight, [_route("alpha", "tool_a", 0.9), _route("bravo", "tool_b", 0.8)])
    print(
        json.dumps(
            {
                "state": flight.state.value,
                "route": flight.current_route().name if flight.state is ExecutionState.COMPLETED else None,
                "route_changes": flight.route_changes,
                "failure_reason": None
                if flight.result is None or flight.result.failure_reason is None
                else flight.result.failure_reason.value,
                "tool_invocations": runtime.invocations,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
