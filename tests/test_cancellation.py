from aeris.adapters.python_agent import SimplePythonAgentRuntime
from aeris.adapters.runtime import CancelToken, ExecutionContext
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import ExecutionState, FailureReason
from aeris.core.ids import new_id
from aeris.core.models import Route, Waypoint
from aeris.policies.thresholds import ThresholdPolicy
from aeris.recorder.memory import InMemoryRecorder
from aeris.simulation.agent import SimulatedAgent


def _context(runtime_flight_ready: bool = True):
    from datetime import UTC, datetime

    from aeris.core.models import Agent, Flight

    now = datetime.now(UTC)
    waypoint = Waypoint(waypoint_id="wp", name="tool_a", index=0)
    route = Route(
        route_id="alpha",
        name="alpha",
        description="a",
        waypoints=[waypoint],
        estimated_reliability=0.9,
    )
    from aeris.core.models import FlightPlan

    now = datetime.now(UTC)
    flight = Flight(
        flight_id="flt",
        mission_id="msn",
        agent=Agent(agent_id="agt", name="py"),
        plan=FlightPlan(
            plan_id="plan",
            mission_id="msn",
            routes=[route],
            selected_route_id="alpha",
        ),
        current_route_id="alpha",
        created_at=now,
        updated_at=now,
    )
    return flight, waypoint


async def test_cancel_before_execution_does_not_call_the_tool():
    runtime = SimplePythonAgentRuntime(tools={"tool_a": lambda: "ok"}, can_cancel=True)
    flight, waypoint = _context()
    token = CancelToken()
    token.request("before")
    observation = await runtime.execute_waypoint(
        ExecutionContext(flight=flight, waypoint=waypoint, cancel_token=token)
    )
    assert observation.cancelled is True
    assert runtime.invocations == []


async def test_cancel_during_cooperative_execution():
    runtime = SimplePythonAgentRuntime(tools={"tool_a": lambda: "ok"}, can_cancel=True, checkpoints=1)
    flight, waypoint = _context()
    token = CancelToken()
    token.request("during")
    observation = await runtime.execute_waypoint(
        ExecutionContext(flight=flight, waypoint=waypoint, cancel_token=token)
    )
    assert observation.cancelled is True
    assert runtime.invocations == ["tool_a"]
    assert runtime.completed == []


async def test_non_cancellable_runtime_finishes_the_call():
    runtime = SimplePythonAgentRuntime(tools={"tool_a": lambda: "ok"}, can_cancel=False)
    flight, waypoint = _context()
    token = CancelToken()
    token.request("during")
    observation = await runtime.execute_waypoint(
        ExecutionContext(flight=flight, waypoint=waypoint, cancel_token=token)
    )
    assert observation.cancelled is False
    assert observation.success is True
    assert runtime.capabilities().can_cancel is False
    assert runtime.completed == ["tool_a"]


async def test_director_does_not_start_a_cancelled_flight():
    runtime = SimulatedAgent()
    clock = FakeClock()
    director = FlightDirector(
        recorder=InMemoryRecorder(),
        runtime=runtime,
        policy=ThresholdPolicy(hold_ms=0, human_on_critical=False),
        clock=clock,
    )
    mission = director.create_mission("cancel", "do not start")
    flight = director.create_flight(mission)
    flight.cancel_requested = True
    waypoint = Waypoint(waypoint_id=new_id("wp"), name="step", index=0)
    route = Route(
        route_id=new_id("rte"),
        name="alpha",
        description="a",
        waypoints=[waypoint],
        estimated_reliability=0.9,
    )
    await director.run(flight, [route])
    assert flight.state is ExecutionState.ABORTED
    assert flight.result is not None
    assert flight.result.failure_reason is FailureReason.CANCELLED
    assert runtime.invocations == []
