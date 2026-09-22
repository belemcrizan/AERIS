# AERIS V0 architecture

This document is the locked V0 design. It records what was rejected as
well as what was kept.

## 1. Analysis of the proposed architecture

The original sketch is coherent as a **metaphor and research program**.
Mapped onto a first implementation, several parts overlap.

### Radar vs telemetry vs OpenTelemetry

Three observation words were doing different jobs:

| Word | Job in V0 |
| --- | --- |
| Radar | Normalize raw runtime signals into `TelemetryEvent` |
| Domain telemetry | The event schema itself |
| OpenTelemetry | Spans around AERIS internals (`flight_id`, `mission_id`, `route_id`) |

If radar also decided what to do, ATC would be a rubber stamp. Radar
therefore has no policy.

### RoutePlanner vs ATCController

If both pick a route, the system has two brains.

- `RoutePlanner` **proposes** scored candidates and must explain the score.
- `ATCController` **decides** among CONTINUE / RETRY / HOLD / REROUTE /
  ESCALATE_HUMAN / ABORT, using hazards, remaining retries, and policy.

### DEGRADED vs RUNNING

A flight cannot be in two exclusive machine states. `DEGRADED` means
"still flying, hazards present, intervention not yet applied". After
CONTINUE on a degraded flight, the machine may return to `RUNNING`.

### RETRY vs HOLD vs REROUTE

These are **actions**, not synonyms.

- RETRY: same waypoint, same route, increment retry counters, brief HOLDING.
- HOLD: wait, then resume the same waypoint.
- REROUTE: mark the current airway failed, switch route, restart that
  route from waypoint 0 (a diversion, not an in-place patch).

### CONTROL experiment arm

"Without AERIS" must still use the same simulator and the same recorder.
The CONTROL arm records radar and hazards but the controller always
returns CONTINUE. That is how a failed tool call becomes a terminal
failure instead of a recovery.

### Process model

V0 is **one process**. FastAPI is a window onto `FlightDirector`. There
is no worker queue, no sidecar mesh, no replica set. Cloud-ready here
means "the domain model does not assume localhost", not "we shipped a
platform".

## 2. Contradictions and YAGNI

Rejected for V0 because they are not required to test the hypothesis:

- Kafka / NATS / Redis
- PostgreSQL (SQLite is enough for an append-only log)
- Kubernetes
- Vector databases
- Reinforcement learning
- Graph-based planners with search
- Multi-agent coordination
- LLM-based hazard detection (it would make the detector opaque)
- A real agent-framework adapter is not required to test the hypothesis.
  `SimplePythonAgentRuntime` is a local tool loop, not a framework port.
- `COMPENSATING` as a flight state. Compensation is an operation inside
  the intervention that required it. A new state would add transitions
  without changing who decides.

Kept, even though they add files:

- Separate packages (`radar`, `routing`, `control`, ...) so later
  implementations can be swapped without rewriting models.
- An injectable `Clock` so tests do not sleep on the wall clock.
- Append-only events plus a fold for `GET /flights/{id}`.

## 3. V0 directory structure

```
aeris/
  core/           domain models, ids, clock, state machine
  radar/          RadarEngine (observer only)
  policies/       thresholds + HazardDetector
  routing/        RoutePlanner and scoring
  control/        ATCController + FlightDirector
  telemetry/      OpenTelemetry helpers
  recorder/       append-only log (memory + sqlite) and replay
  human/          HITL request model
  adapters/       AgentRuntime protocol
  simulation/     fake agent + scenarios
  evaluation/     CONTROL vs AERIS harness
  api/            FastAPI
  config.py
tests/
examples/
```

## 4. Module responsibilities

| Module | Responsibility | Must not do |
| --- | --- | --- |
| `core` | Vocabulary: Mission, Flight, Route, Hazard, Decision, state machine | I/O |
| `radar` | Accumulate counters, emit `TelemetryEvent` | Choose actions |
| `policies` | Thresholds and deterministic hazard emission | Call an LLM |
| `routing` | Score routes, explain scores | Execute waypoints |
| `control` | Decide and apply the flight loop | Depend on FastAPI |
| `recorder` | Append events, reconstruct timeline | Update/delete history |
| `human` | Shape of a controller action | Render a UI |
| `adapters` | `AgentRuntime` protocol | Import LangGraph et al. |
| `simulation` | Deterministic fake aircraft | Need network |
| `evaluation` | Compare arms, compute rates | Declare victory |
| `api` | HTTP facade | Own business rules |
| `telemetry` | OTel spans | Replace radar |

## 5. Domain models

See `aeris/core/models.py`.

- `Agent` — identity of the runtime, not a live object AERIS constructs.
- `Mission` — user objective and success criteria.
- `Flight` — one attempt, unique `flight_id`.
- `FlightPlan` — candidate routes plus the selected airway and rationale.
- `Route` / `Waypoint` — strategy and steps.
- `ExecutionState` — the machine in §6.
- `TelemetryEvent` / `TelemetryMetrics` — normalized radar output.
- `Hazard` — type, severity, evidence, recommended action.
- `ControlDecision` — action, reason, evidence, previous/new route.
- `HumanIntervention` — recorded operator action.
- `FlightResult` — terminal summary.

Cardinality: one Mission has many Flights. One Flight has one current
route and many recorded events.

## 6. State machine

```
CREATED → PLANNED → RUNNING ⇄ DEGRADED
                        ↓
              HOLDING / REROUTING / WAITING_HUMAN
                        ↓
              COMPLETED | FAILED | ABORTED
```

Invalid transitions raise `InvalidTransition` and are not recorded as
success. Every accepted transition is appended to the recorder.

Terminal states have no exits. A new attempt is a new `flight_id`.

## 7. Event flow

```
create mission
create flight (CREATED)
plan routes (PLANNED)
start (RUNNING)
for each waypoint:
    AgentRuntime.execute_waypoint
    RadarEngine.observe → TelemetryEvent
    HazardDetector.detect → Hazard[]
    maybe RUNNING → DEGRADED
    ATCController.decide → ControlDecision
    apply CONTINUE | RETRY | HOLD | REROUTE | ESCALATE_HUMAN | ABORT
destination or terminal failure
FlightRecorder contains the history
```

On `ESCALATE_HUMAN` the director records a `HumanInterventionRequest`
and stops. HTTP `/flights/{id}/control/*` checks role and side-effect
policy, then records the decision and resumes.

`COMPENSATE` is not a state. If a reversible write is committed, the
director runs `runtime.compensate` and records the result before the
follow-up retry or reroute. A failed compensation ends the flight with
`compensation_failure`.

## 8. Experiment

Two arms, same scenarios, same injected faults:

- **CONTROL**: radar on, controller always CONTINUE.
- **AERIS**: radar + hazards + routing + intervention.

Metrics include task success, terminal failure, recovery and its delta,
detector precision and recall, false-positive and false-negative rates,
mean time to detect, intervene, and recover, intervention precision,
and compensation success. A rate is null when the denominator is zero.

`recovery_rate` uses only scenarios that inject a terminal failure.
`all_routes_fail` falsifies "AERIS always saves the flight".
`false_low_confidence` falsifies "every intervention helps".

The harness never prints "hypothesis confirmed". Identical repeats are
not a statistical sample.

## Recorder integrity

`event_hash = SHA256(canonical_payload + previous_hash)` per flight.
This detects an edited payload or a deleted middle event if the stored
hashes are left unchanged. It does not authenticate the writer and does
not detect truncation of the tail. See THREAT_MODEL.md.

## Implementation notes

- Python 3.12+, FastAPI, Pydantic v2, asyncio, SQLite, OpenTelemetry, pytest.
- Default OTel export is off (`AERIS_OTEL_ENABLED=false`).
- HOLD sleeps via `Clock`; tests use `FakeClock`.
