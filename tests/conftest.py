"""Shared fixtures. Tests never touch the network."""

from __future__ import annotations

import pytest

from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import InterventionMode
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner
from aeris.simulation.scenarios import Scenario, get_scenario


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def policy() -> ThresholdPolicy:
    return ThresholdPolicy(human_on_critical=False, hold_ms=0)


@pytest.fixture
def recorder() -> InMemoryRecorder:
    return InMemoryRecorder()


async def run_scenario(
    scenario: Scenario | str,
    *,
    mode: InterventionMode = InterventionMode.AERIS,
    policy: ThresholdPolicy | None = None,
    human_on_critical: bool | None = None,
) -> tuple[FlightDirector, object]:
    if isinstance(scenario, str):
        scenario = get_scenario(scenario)
    policy = policy or ThresholdPolicy(
        human_on_critical=False if human_on_critical is None else human_on_critical,
        hold_ms=0,
    )
    if human_on_critical is not None:
        policy.human_on_critical = human_on_critical
    clock = FakeClock()
    recorder = InMemoryRecorder()
    planner = RoutePlanner()
    director = FlightDirector(
        recorder=recorder,
        runtime=scenario.runtime(),
        planner=planner,
        radar=RadarEngine(clock=clock),
        detector=HazardDetector(policy=policy, clock=clock),
        controller=ATCController(planner=planner, policy=policy, clock=clock, mode=mode),
        policy=policy,
        clock=clock,
    )
    mission = director.create_mission(scenario.objective, scenario.success_criteria)
    await director.record_mission(mission)
    flight = director.create_flight(mission)
    await director.run(flight, scenario.routes)
    return director, flight
