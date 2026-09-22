# AERIS architecture

Sections 1–8 are the locked V0 design, including what was rejected.
Section 9 describes the V1 additions. V1 did not replace any V0 module;
it extended models and added packages beside them.

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
  human/          HITL request model, authorization, console view (V1)
  adapters/       AgentRuntime protocol; LLM chat model + tool-agent runtime (V1)
  simulation/     fake agent + scenarios
  evaluation/     CONTROL vs AERIS harness; fault matching, pairing (V1)
  cases/support/  enterprise support case: world, toolbox, routes, evaluator, campaign (V1)
  experiments/    paired runner, stats, traces, reports (V1)
  api/            FastAPI + console page
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
| `evaluation` | Compare arms, match faults to hazards, label intervention utility | Declare victory |
| `cases` | A concrete mission with tools, fixtures, fault proxy, evaluator | Be imported by `core`, `control`, or `radar` |
| `experiments` | Run paired trials, compute deltas and intervals, write reports | Tune policy on test seeds |
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
hashes are left unchanged. On its own it does not authenticate the
writer or detect truncation of the tail. V1 adds optional checkpoints
for both; see §9 and THREAT_MODEL.md.

## 9. V1 additions

The loop is unchanged: observe, detect, decide, verify safety,
intervene, record, measure. What changed is what each step knows.

```
REAL AGENT (LLMToolAgentRuntime | SimulatedAgent | SimplePythonAgentRuntime)
      |
  StepObservation  (+ faults, dependency_ids, tokens, cost)
      |
    RADAR ------------- contextual baseline (median/MAD per runtime/route/waypoint/tool)
      |                  signal provenance
  HAZARD DETECTOR       (STATIC_THRESHOLD | CONTEXTUAL_THRESHOLD)
      |
     ATC  <-- RoutePlanner: explained score incl. failure-domain diversity
      |                     and shared-failed-dependency penalty
  SAFETY GATE (side effects, idempotency, compensation)
      |
  FlightDirector (per-flight lock, control_version, cancel registry)
      |
  FLIGHT RECORDER (+ optional signed checkpoints)
      |
  EVALUATION: fault matching, pairing, utility, cost, task evaluator
```

### Security defaults

`authorize` resolves the role first and rejects a missing or unknown
role. There is no `role or ADMIN` anywhere. The API schema makes `role`
required. The domain safety checks (`_reject_unsafe_human_action`) run
after authorization, so `ADMIN` cannot retry a committed irreversible
write.

### Concurrency model

The simplest design that holds for one process: an `asyncio.Lock` per
flight around every mutation (`run`, `resume`, `cancel`), and an integer
`control_version` that each applied human action increments. A call that
carries a stale `expected_version` raises `ControlConflict`. A human
action against a flight that is not `WAITING_HUMAN` fails before taking
the lock, so it cannot queue behind a running flight. No distributed
lock.

### Cancellation

`FlightDirector` keeps an active-execution registry (flight → current
`CancelToken`). `cancel()` records `CANCEL_REQUESTED`, sets the token,
and the flight loop resolves the outcome at the next boundary: before
start, between waypoints, inside the waypoint if the runtime observed
the token, or too late if a write committed or the flight had ended.
`CANCEL_RESOLVED` carries the outcome. See SAFETY.md for the table.

### Fault instances and matching

`FaultInstance` (fault_id, flight, route, waypoint, type, onset,
recovery, injected, ground_truth, severity, target, metadata) is emitted
by whoever injects the fault: the simulator or the support toolbox proxy.
The director stamps ids, route, waypoint, and onset and records a
`FAULT_INJECTED` event. Fault instances are evaluation data: the
detector and ATC never read them, because a controller that sees ground
truth would be measuring nothing. `Hazard` has an optional
`source_fault_id` field, but the online path leaves it empty.
Attribution happens after the flight in `aeris/evaluation/matching.py`
(`HazardAttribution.source_fault_id`), with explicit rules, and that is
what the metrics use.

### Contextual radar

`BaselineStore` holds declared or learned `ContextBaseline`s keyed by
(runtime, route, waypoint, tool). Learning uses median and MAD; the
detector computes `z = (x − median) / (1.4826 · MAD)` with a MAD floor,
and flags `HIGH_LATENCY` at `robust_z_threshold`. Tokens use a range with
a tolerance factor. When no baseline exists for a key, the static
threshold applies. Baselines can be frozen and fingerprinted.

### Route diversity and history

`RouteDependencies` declares model provider, model family, tool
provider, data source, region, network, and service ids.
`failure_domain_diversity(a, b)` returns a score in [0, 1] and a
per-attribute breakdown with fixed weights. The flight keeps
`route_history` (`RouteFailureRecord`: route, hazard type, tool,
provider, dependency ids, count, most recent). The planner score is

```
score = 1.5·reliability − 0.5·latency_norm − 0.3·cost_norm
        − 1.0·[route already failed on this flight]
        − (0.1·hazards + 0.3·critical_hazards) − 0.2·side_effect_risk
        + 0.6·diversity(current, candidate)
        − 1.0·[candidate uses a dependency that already failed]
        − 0.4·[human route]
```

(`latency_norm = min(latency / 5000 ms, 1)`, `cost_norm = min(cost / 10, 1)`.)
Every term is in the decision evidence. With
`avoid_shared_failure_domain`, ATC does not divert to a candidate that
uses any dependency that already failed on this flight; it records "no
independent diversion" and falls back to the next safe action. The
`AERIS_NO_DIVERSITY` ablation zeroes both diversity terms and disables
that rule.

### Live runtime

`LLMToolAgentRuntime` implements `AgentRuntime` over a `ChatModel` and a
`ToolExecutor`. `OpenAIChatModel` is the only network client and uses
`urllib`. `ScriptedSupportModel` implements the same protocol offline.
Nothing under `core`, `radar`, `policies`, `routing`, or `control`
imports either. See LIVE_AGENT.md.

### Experiment

`aeris/experiments/support.py` runs, per scenario and seed, one CONTROL
flight and one flight per AERIS arm with the same fault schedule, seed,
prompt, temperature, and fixtures, and records a `TrialPair`. Each
record carries policy version and hash, planner weights hash, route
config hash, prompt hash, adapter, scenario, fixture and evaluator
versions, model and reported model version, temperature, seed, and
baseline fingerprint. See EXPERIMENT_PROTOCOL.md and BENCHMARK.md.

## Implementation notes

- Python 3.12+, FastAPI, Pydantic v2, asyncio, SQLite, OpenTelemetry, pytest.
- Default OTel export is off (`AERIS_OTEL_ENABLED=false`).
- HOLD sleeps via `Clock`; tests use `FakeClock`.
