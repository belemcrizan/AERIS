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

Refusing to start the next waypoint is an AERIS decision and does not
need runtime support.

Interrupting the current waypoint needs `CAN_CANCEL` and a runtime that
polls a cancel token. V0 does not claim hard cancellation. A
non-cancellable runtime finishes the call; the timeline shows that the
cancel was not applied mid-step.

## Humans

A `HumanInterventionRequest` is written before the flight sits in
`WAITING_HUMAN`. It carries the route, waypoint, state, hazards,
evidence, alternatives, recent telemetry, side-effect state, recommended
action, and allowed actions.

| Role | May |
| --- | --- |
| `OBSERVER` | read |
| `CONTROLLER` | continue, hold, safe retry, safe reroute, approve or deny compensation |
| `ADMIN` | the controller set, plus abort |

The HTTP layer maps `UnauthorizedIntervention` to 403 and other domain
errors (`UnsafeRetry`, `UnsafeReroute`, `InvalidTransition`,
`BudgetExceeded`, `CompensationFailed`) to 409.
