"""Run the canonical latency-diversion scenario without an LLM."""

from __future__ import annotations

import asyncio
import json

from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.recorder.replay import timeline_view
from aeris.routing.planner import RoutePlanner
from aeris.simulation.scenarios import get_scenario


async def main() -> None:
    scenario = get_scenario("latency_reroute")
    policy = ThresholdPolicy(human_on_critical=False, hold_ms=0)
    clock = FakeClock()
    recorder = InMemoryRecorder()
    planner = RoutePlanner()
    director = FlightDirector(
        recorder=recorder,
        runtime=scenario.runtime(),
        planner=planner,
        radar=RadarEngine(clock=clock),
        controller=ATCController(planner=planner, policy=policy, clock=clock),
        policy=policy,
        clock=clock,
    )
    mission = director.create_mission(scenario.objective, scenario.success_criteria)
    flight = director.create_flight(mission)
    await director.run(flight, scenario.routes)
    timeline = await recorder.timeline(flight.flight_id)
    print(
        json.dumps(
            {
                "flight_id": flight.flight_id,
                "state": flight.state.value,
                "route": flight.current_route().name,
                "route_changes": flight.route_changes,
                "events": timeline_view(timeline),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
