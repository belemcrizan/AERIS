from aeris.core.clock import FakeClock
from aeris.core.enums import ExecutionState
from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight
from aeris.core.state_machine import InvalidTransition, StateMachine


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


def test_every_legal_transition_succeeds_and_every_illegal_one_fails():
    from aeris.core.state_machine import ALLOWED

    paths = {
        ExecutionState.CREATED: [],
        ExecutionState.PLANNED: [ExecutionState.PLANNED],
        ExecutionState.RUNNING: [ExecutionState.PLANNED, ExecutionState.RUNNING],
        ExecutionState.DEGRADED: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.DEGRADED],
        ExecutionState.HOLDING: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.HOLDING],
        ExecutionState.REROUTING: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.REROUTING],
        ExecutionState.WAITING_HUMAN: [
            ExecutionState.PLANNED,
            ExecutionState.RUNNING,
            ExecutionState.WAITING_HUMAN,
        ],
        ExecutionState.COMPLETED: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.COMPLETED],
        ExecutionState.FAILED: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.FAILED],
        ExecutionState.ABORTED: [ExecutionState.PLANNED, ExecutionState.RUNNING, ExecutionState.ABORTED],
    }
    clock = FakeClock()
    sm = StateMachine(clock)
    for source, steps in paths.items():
        for target in ExecutionState:
            flight = _flight(clock)
            for step in steps:
                sm.transition(flight, step, "setup")
            assert flight.state is source
            if target is source:
                assert sm.transition(flight, target, "same") is None
            elif target in ALLOWED[source]:
                sm.transition(flight, target, "legal")
                assert flight.state is target
            else:
                try:
                    sm.transition(flight, target, "illegal")
                    raise AssertionError(f"{source} -> {target} must fail")
                except InvalidTransition:
                    assert flight.state is source
