from aeris.core.enums import ExecutionState
from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight
from aeris.core.state_machine import InvalidTransition, StateMachine
from aeris.core.clock import FakeClock


def _flight(clock: FakeClock) -> Flight:
    now = clock.now()
    return Flight(
        flight_id=new_id("flt"),
        mission_id=new_id("msn"),
        agent=Agent(agent_id=new_id("agt"), name="test"),
        created_at=now,
        updated_at=now,
    )


def test_nominal_path_created_planned_running_completed():
    clock = FakeClock()
    sm = StateMachine(clock)
    flight = _flight(clock)
    assert flight.state is ExecutionState.CREATED
    sm.transition(flight, ExecutionState.PLANNED, "plan")
    sm.transition(flight, ExecutionState.RUNNING, "go")
    sm.transition(flight, ExecutionState.COMPLETED, "done")
    assert flight.state is ExecutionState.COMPLETED


def test_invalid_transition_is_rejected():
    clock = FakeClock()
    sm = StateMachine(clock)
    flight = _flight(clock)
    sm.transition(flight, ExecutionState.PLANNED, "plan")
    sm.transition(flight, ExecutionState.RUNNING, "go")
    try:
        sm.transition(flight, ExecutionState.CREATED, "rewind")
        raise AssertionError("invalid transition must be rejected")
    except InvalidTransition as exc:
        assert exc.from_state is ExecutionState.RUNNING
        assert exc.to_state is ExecutionState.CREATED


def test_terminal_states_have_no_exits():
    clock = FakeClock()
    sm = StateMachine(clock)
    flight = _flight(clock)
    sm.transition(flight, ExecutionState.PLANNED, "plan")
    sm.transition(flight, ExecutionState.RUNNING, "go")
    sm.transition(flight, ExecutionState.ABORTED, "mayday")
    try:
        sm.transition(flight, ExecutionState.RUNNING, "restart")
        raise AssertionError("aborted flights cannot resume")
    except InvalidTransition:
        pass


def test_same_state_is_a_noop():
    clock = FakeClock()
    sm = StateMachine(clock)
    flight = _flight(clock)
    assert sm.transition(flight, ExecutionState.CREATED, "noop") is None
