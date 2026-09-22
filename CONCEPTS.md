# Concepts

AERIS borrows ATC language so that runtime control stays inspectable.
This page is the glossary for V0 and V1. V1 terms are at the end.

## Mission, flight, route

A **mission** is the destination: what the user asked for, and how we
would know it arrived.

A **flight** is one attempt to get there. It has a unique `flight_id`.
Retries and diversions stay on the same flight. A later independent
attempt is a different flight.

A **route** (airway) is a strategy: model, tools, or human escalation.
V0 ships three airways in the retrieve-information mission:

- **Alpha** — primary model, primary tool
- **Bravo** — fallback model, alternative tool
- **Charlie** — human escalation

A **waypoint** is a step on that airway. Changing route in V0 restarts
the new airway from waypoint 0. That is a diversion, not a mid-air
splice of two incompatible plans.

## Radar and hazards

Radar is a sensor package. It counts latency, retries, tool errors,
route changes, tokens, confidence, freshness, progress, and repeated
actions. It does not decide.

A **hazard** is a threshold crossing with evidence. Severity is one of
`INFO`, `CAUTION`, `WARNING`, `CRITICAL`. Recommended actions ride on
the hazard; ATC may override them using policy (for example: no
alternative route left, so ABORT instead of REROUTE).

V0 hazard types:

| Type | Signal | Condition | Scope |
| --- | --- | --- | --- |
| `HIGH_LATENCY` | measured step latency | at or above `high_latency_ms`, below timeout | route-local, clears when the signal drops, severity rises if it repeats |
| `TIMEOUT_RISK` | timeout flag or step latency | timeout, or latency at or above `timeout_risk_ms` | route-local, always critical |
| `TOOL_FAILURE` | runtime-reported tool error | tool error, or a failed step while retries remain | route-local |
| `REPEATED_ACTION` | radar-derived repeat counter | counter at or above threshold | route-local |
| `LOW_CONFIDENCE` | runtime-reported confidence | below `low_confidence` | route-local. The number is not ground truth |
| `STALE_DATA` | tool-reported freshness | at or above `stale_data_s` | route-local |
| `NO_PROGRESS` | radar-derived stall counter | steps without progress at or above threshold | route-local |
| `ROUTE_FAILURE` | failed step after retries are exhausted | not a timeout | route-local |
| `BUDGET_RISK` | tokens or measured execution time | warning ratio, or the hard limit | flight-global, does not clear on reroute |
| `SIDE_EFFECT_RISK` | committed side-effect class | retry or reroute would repeat or abandon an unsafe write | flight-global |

Detector confidence is 1.0 when the rule matches. That is not the agent's
self-reported confidence. Definitions live in `aeris/policies/catalog.py`.

## Side effects

A waypoint declares one of:

- `READ_ONLY` — retry and reroute are allowed
- `IDEMPOTENT_WRITE` — retry is allowed when an idempotency key exists
- `REVERSIBLE_WRITE` — retry or reroute runs compensation first
- `IRREVERSIBLE_WRITE` — automatic retry and reroute are rejected

An idempotent write without a key is not retried. Compensation is an
operation the director runs before the follow-up retry or reroute. It
is not a flight state.

## ATC decisions

Every decision carries `decision_id`, `flight_id`, `action`, `reason`,
`evidence`, `previous_route`, `new_route`, and `timestamp`.

If you cannot explain why Alpha was abandoned for Bravo, the diversion
did not happen as far as science is concerned.

## Human ATC

`WAITING_HUMAN` is a holding fix. The recorded request includes the
route, waypoint, hazards, evidence, alternatives, recent telemetry,
side-effect state, and the actions that role is allowed to take.

Roles are `OBSERVER`, `CONTROLLER`, and `ADMIN`. The role must be
given; there is no default. Observers cannot steer. Controllers can
continue, hold, and retry or reroute when the side-effect policy agrees.
Only an admin can abort or cancel. An irreversible write that already
committed cannot be retried just because the request says so.

## Flight recorder

The recorder is append-only SQLite, or an in-memory log in tests. Each
event stores `previous_hash` and `event_hash`:

```
hash = SHA256(canonical_json(event) + previous_hash)
```

Verification fails if a payload is edited or a middle event is removed
without rewriting the chain. It does not detect a deleted tail, and it
is not a signature: anyone who can rewrite the later hashes can forge a
consistent history. See [THREAT_MODEL.md](THREAT_MODEL.md).

## Provenance

Radar labels signals as `MEASURED`, `RUNTIME_REPORTED`, `TOOL_REPORTED`,
or `DERIVED`. Latency is measured by AERIS. Confidence and progress are
runtime-reported and are not treated as ground truth.

## Adapters

`AgentRuntime` declares capabilities: cancel, retry, compensate,
idempotency, progress, streaming. ATC will not ask a runtime for an
intervention it cannot perform. Cancellation is cooperative. AERIS can
refuse to start the next waypoint. A runtime may stop at a checkpoint.
Python cannot preempt a call that does not poll.

`SimplePythonAgentRuntime` dispatches a waypoint name to a Python
callable and does not call an LLM. `LLMToolAgentRuntime` (V1) runs each
waypoint as a phase of a tool-calling conversation with a `ChatModel`:
either the live `OpenAIChatModel` or the offline `ScriptedSupportModel`.
The scenario simulator remains the deterministic V0 runtime. AERIS core
does not import an agent framework.

## V1 terms

**Fault instance.** One injected fault on one flight: `fault_id`,
route, waypoint, type, onset, optional recovery, severity, target, and
`ground_truth`. A misleading signal (for example a false low confidence
on a healthy route) is injected with `ground_truth=false`, so flagging it
counts as a false positive. Controllers never see fault instances.

**Fault-to-hazard matching.** After a flight, each hazard is attributed
to at most one fault: compatible type, same route, same waypoint (unless
the fault is flight-scoped), within 60 s of onset. Unattributed hazards
are false positives. Faults with no hazard are false negatives.

**MTTD / MTTI / MTTR.** Time from fault onset to the first matched
hazard; from that hazard to the first non-CONTINUE decision that names
a compatible hazard; from onset to successful completion.

**Intervention utility.** For each AERIS flight that intervened,
compare with the paired CONTROL flight. AERIS acceptable and CONTROL not:
BENEFICIAL. The reverse: HARMFUL. Same outcome: NEUTRAL, unless one side
caused more duplicate or unsafe effects. UNRESOLVED when CONTROL never
hit the fault that triggered the intervention.

**Trial pair.** One CONTROL flight and one AERIS flight with the same
scenario, seed, prompt, temperature, fixtures, and fault schedule. With
a real model the trajectories still differ; pairing is analytic.

**Contextual baseline.** Expected latency (median, MAD) and token range
for one (runtime, route, waypoint, tool). Latency is flagged when the
robust z-score `(x − median) / (1.4826 · MAD)` reaches the threshold.
The hazard evidence carries observed value, median, MAD, z, and
threshold. `STATIC_THRESHOLD` mode ignores baselines.

**Failure domain.** What a route depends on: model provider, model
family, tool provider, data source, region, network, service ids. Two
routes that share them are not independent, however different their
names. **Diversity** is a weighted share of attributes that differ, in
[0, 1], with a per-attribute explanation.

**Route failure history.** Per flight: which route failed, on which
hazard, at which tool and provider, on which dependencies, how often.
The planner penalizes a candidate that uses a dependency that already
failed.

**Control version.** An integer on the flight, incremented by every
applied human action. A caller may send the version it saw; if it moved,
the action is rejected rather than applied twice.

**Cancellation outcome.** What actually happened to a cancel request:
before start, between waypoints, during a waypoint, not supported, after
a side-effect commit, or after the flight ended. Request and outcome are
separate events.

**Checkpoint.** A record of a flight's last sequence number and hash,
stored apart from the events and optionally signed. It lets a verifier
notice that the tail of the log was deleted.

**Configuration identity.** Hashes of the policy, planner weights, route
set, and agent prompt, plus versions of the adapter, scenarios, fixtures,
and evaluator. Every experiment record carries them.
