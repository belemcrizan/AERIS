# Roadmap

## V0 — this repository

Local-first ATC loop, simulator, SQLite recorder, FastAPI, pytest,
CONTROL vs AERIS harness. No LLM required.

Milestone checklist:

1. Domain models and state machine
2. Radar, hazards, planner, controller
3. Flight director and recorder
4. Simulator scenarios including a falsifying `all_routes_fail`
5. HTTP + HITL
6. Evaluation harness
7. Docs and tests that run offline

## V1 — real runtimes

- Adapters for at least one real framework (`AgentRuntime` only)
- Trace attributes from live token usage and tool errors
- Policy files loaded from disk, not just defaults
- Richer human console (still optional; API remains the source of truth)

## V2 — distribution without rewriting the domain

- Recorder implementation on Postgres or an event log
- Optional NATS/Kafka transport for telemetry
- Horizontal API replicas in front of the same flight log

The Pydantic models in `aeris.core` should not change shape for this.

## V3 — research that could fail in public

- Pre-registered evaluation on a frozen scenario mix
- Comparison against other intervention policies
- Only then: learned ranking, graph search, multi-agent traffic

Do not introduce RL or a vector store because they are fashionable.
Introduce them when a measured V0/V1 gap cannot be closed with
thresholds and scoring.

## Explicit non-goals

- Becoming an agent framework
- Hiding intervention inside the prompt
- Claiming reliability without a CONTROL arm
