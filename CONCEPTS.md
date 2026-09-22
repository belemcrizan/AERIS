# Concepts

AERIS borrows ATC language so that runtime control stays inspectable.
This page is the glossary for V0.

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

V0 hazard types: `HIGH_LATENCY`, `TIMEOUT_RISK`, `TOOL_FAILURE`,
`REPEATED_ACTION`, `LOW_CONFIDENCE`, `STALE_DATA`, `NO_PROGRESS`,
`ROUTE_FAILURE`, `BUDGET_RISK`.

## ATC decisions

Every decision carries `decision_id`, `flight_id`, `action`, `reason`,
`evidence`, `previous_route`, `new_route`, and `timestamp`.

If you cannot explain why Alpha was abandoned for Bravo, the diversion
did not happen as far as science is concerned.

## Human ATC

`WAITING_HUMAN` is a holding fix. The process does not guess. A human
controller may continue, retry, reroute, or abort. That action is
written to the recorder before the flight moves.

## Flight recorder

The recorder is a black box: append-only SQLite (or an in-memory log in
tests). `GET /flights/{id}/timeline` folds events in sequence. If a
field is not on the timeline, it did not happen.

## Adapters

`AgentRuntime.execute_waypoint` is the only integration surface. A
future LangGraph or ADK adapter should implement that protocol. AERIS
core must keep importing none of those libraries.
