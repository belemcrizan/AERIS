# Roadmap

## V0 — this repository

Local-first ATC loop, simulator, one local Python agent adapter,
SQLite recorder with a hash chain, FastAPI, pytest, CONTROL vs AERIS
harness. No LLM API key. No network in tests.

Done in this tree:

- side-effect classes, idempotency keys, compensation, unsafe retry/reroute rejection
- detector catalog and precision / recall / MTTD / MTTI / MTTR
- operator roles and human requests with context
- tamper-evident recorder
- `SimplePythonAgentRuntime` and `examples/first_flight.py`
- GitHub Actions running `ruff check` and `pytest`

Still thin:

- compensation is in-process and synchronous
- authorization is a role enum, not an identity system
- the hash chain has no external anchor
- the Python adapter does not call a model
- no live-agent experiment

## V1 — a runtime that can actually fail in the wild

- One opt-in adapter that calls a model, still behind `AgentRuntime`, tests remaining offline
- Freeze thresholds before measuring that adapter
- Seeded repetitions if the runtime is stochastic
- Policy files loaded from disk

## V2 — distribution, only if a measured gap needs it

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
