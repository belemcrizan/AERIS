"""Flight execution state machine.

Invalid transitions raise InvalidTransition. Every accepted transition is
returned so the recorder can persist it.
"""

from __future__ import annotations

from datetime import datetime

from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ExecutionState
from aeris.core.models import Flight, StateTransition

TERMINAL_STATES = {
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.ABORTED,
}

ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.CREATED: frozenset({ExecutionState.PLANNED, ExecutionState.ABORTED}),
    ExecutionState.PLANNED: frozenset(
        {ExecutionState.RUNNING, ExecutionState.WAITING_HUMAN, ExecutionState.ABORTED}
    ),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.DEGRADED,
            ExecutionState.HOLDING,
            ExecutionState.REROUTING,
            ExecutionState.WAITING_HUMAN,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.ABORTED,
        }
    ),
    ExecutionState.DEGRADED: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.HOLDING,
            ExecutionState.REROUTING,
            ExecutionState.WAITING_HUMAN,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.ABORTED,
        }
    ),
    ExecutionState.HOLDING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.DEGRADED,
            ExecutionState.REROUTING,
            ExecutionState.WAITING_HUMAN,
            ExecutionState.ABORTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.REROUTING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.DEGRADED,
            ExecutionState.WAITING_HUMAN,
            ExecutionState.ABORTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.WAITING_HUMAN: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.DEGRADED,
            ExecutionState.HOLDING,
            ExecutionState.REROUTING,
            ExecutionState.ABORTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.ABORTED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, from_state: ExecutionState, to_state: ExecutionState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"invalid flight transition {from_state} -> {to_state}")


class StateMachine:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def can_transition(self, current: ExecutionState, target: ExecutionState) -> bool:
        return target in ALLOWED[current]

    def is_terminal(self, state: ExecutionState) -> bool:
        return state in TERMINAL_STATES

    def transition(
        self,
        flight: Flight,
        target: ExecutionState,
        reason: str,
    ) -> StateTransition | None:
        if flight.state == target:
            return None
        if target not in ALLOWED[flight.state]:
            raise InvalidTransition(flight.state, target)
        now = self._clock.now()
        record = StateTransition(
            flight_id=flight.flight_id,
            from_state=flight.state,
            to_state=target,
            reason=reason,
            timestamp=now,
        )
        flight.state = target
        flight.updated_at = now
        return record
