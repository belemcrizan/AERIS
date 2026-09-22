# Safety

Operational rules for intervention. The threat model is in
[THREAT_MODEL.md](THREAT_MODEL.md). This page is the control policy.

## Who decides

Radar does not steer. The hazard detector does not call a model.
The route planner proposes a score it can explain. ATC decides.
The flight director applies the decision only after the side-effect
check. The runtime executes. A human is an external authority, and the
API is not allowed to bypass that check.

## Side effects

| Class | Automatic retry | Automatic reroute |
| --- | --- | --- |
| `READ_ONLY` | allowed | allowed |
| `IDEMPOTENT_WRITE` with a key the runtime honors | allowed; the same key is reused | allowed |
| `IDEMPOTENT_WRITE` without a key | rejected | rejected if the write is the committed effect being abandoned without compensation |
| `REVERSIBLE_WRITE` already committed | compensate, then retry | compensate, then reroute |
| `IRREVERSIBLE_WRITE` already committed | rejected | rejected |

Rejection becomes `ESCALATE_HUMAN` when `escalate_irreversible` or
`human_on_critical` is set, otherwise `ABORT`. The flight result records
`unsafe_retry_blocked` or `unsafe_reroute_blocked` when the flight ends
on that policy.

A human retry of a committed irreversible write is rejected with
`UnsafeRetry` even for `ADMIN`. Authority does not invent idempotency.

The `side_effect_gate` policy flag exists only for the
`AERIS_NO_SIDE_EFFECT_GATE` ablation, so the experiment can show what the
gate prevents. It defaults to on. Turning it off in production is a
decision to accept duplicate writes.

## Concurrency

Every control path for a flight takes that flight's `asyncio.Lock`.
Each applied human action increments `flight.control_version`. A call
that sends `expected_version` is rejected with `ControlConflict` (HTTP
409) if the version moved, so two operators clicking RETRY on the same
request produce one retry. A human action on a flight ATC is still
flying is rejected immediately instead of queueing behind the lock.

Invariant: one logical decision produces at most one physical side
effect. The tests cover ABORT vs RETRY, ABORT vs REROUTE, double RETRY,
double REROUTE, double compensation approval, and duplicate HTTP calls.

The lock is in-process. Two API processes on the same database are not
protected. There is no distributed lock, by design, for this milestone.

## Compensation

Compensation is an operation, not a state. The director calls
`runtime.compensate` for each open reversible effect, records the
result, and only then applies the follow-up action.

- success: the effect is marked compensated and the retry or reroute proceeds
- failure: the flight ends `FAILED` with `compensation_failure` and does not reroute
- runtime lacks `CAN_COMPENSATE`: same failure, no pretend rollback

There are no distributed transactions.

## Budget

`max_retries`, `max_route_changes`, `max_holds`, `max_cost`,
`max_execution_time_ms`, and `budget_tokens` are mission policy.
A reroute that would exceed remaining cost or time is not flown.
The decision is recorded. With `human_on_critical`, that hold is an
escalation; otherwise it is an abort with `budget_exhaustion`.

## Cancellation

Path: API `POST /flights/{id}/control/cancel` → `FlightDirector.cancel`
→ active-execution registry → `CancelToken` → runtime checkpoint →
termination result → recorder. Cancel requires `ADMIN` and is
idempotent: a second request returns the first outcome.

`CANCEL_REQUESTED` is recorded when the request arrives.
`CANCEL_RESOLVED` is recorded with what actually happened:

| Outcome | Status | Meaning |
| --- | --- | --- |
| `CANCEL_BEFORE_START` | `CANCELLED` | no waypoint ran |
| `CANCEL_BETWEEN_WAYPOINTS` | `CANCELLED` | AERIS refused to start the next waypoint |
| `CANCEL_DURING_WAYPOINT` | `CANCELLED` | the runtime observed the token at a checkpoint |
| `CANCEL_NOT_SUPPORTED` | `CANCEL_UNSUPPORTED` | the runtime lacks `CAN_CANCEL`; the waypoint ran to the end, then the flight stopped |
| `CANCEL_AFTER_SIDE_EFFECT_COMMIT` | `CANCEL_TOO_LATE` | a write already committed; the flight stops, the write stands |
| `CANCEL_AFTER_TERMINAL` | `CANCEL_TOO_LATE` | the flight had already finished |

AERIS never records "cancelled" for an action the runtime completed.
Cancellation is cooperative. Python cannot preempt a call that does not
poll, and an in-flight HTTP request is not interrupted.

## Humans

A `HumanInterventionRequest` is written before the flight sits in
`WAITING_HUMAN`. It carries the route, waypoint, state, hazards,
evidence, alternatives, recent telemetry, side-effect state, recommended
action, and allowed actions.

| Role | May |
| --- | --- |
| `OBSERVER` | read |
| `CONTROLLER` | continue, hold, safe retry, safe reroute, approve or deny compensation |
| `ADMIN` | the controller set, plus abort and cancel |

The role is required. A missing or unknown role is rejected; it is never
mapped to a default, and never to `ADMIN`. Absence of authorization data
must not increase privilege.

The HTTP layer maps `UnauthorizedIntervention` to 403, a missing or
invalid role in the body to 422, and other domain errors (`UnsafeRetry`,
`UnsafeReroute`, `InvalidTransition`, `BudgetExceeded`,
`CompensationFailed`, `ControlConflict`) to 409.
