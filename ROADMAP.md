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

## V1 — a runtime that can actually fail in the wild

Done in this tree:

- explicit operator role on every control path; no implicit ADMIN
- per-flight lock plus optimistic `control_version`
- cancellation end to end, with request and outcome recorded separately
- fault instances, deterministic fault-to-hazard matching, event-level
  precision/recall, MTTD/MTTI/MTTR from matched events
- intervention utility from paired outcomes
- contextual radar (median/MAD baselines) with STATIC/CONTEXTUAL modes
- failure-domain diversity, route failure history, shared-dependency penalty
- optional signed checkpoints, tail-truncation detection
- one opt-in OpenAI-compatible adapter behind `AgentRuntime` (stdlib only)
- enterprise support case: tools, sandbox ledger, fault proxy, evaluator,
  18-scenario campaign including negative controls
- paired experiment runner with ablations, calibration/validation/test
  seed split, configuration hashes, cost accounting, bootstrap CIs gated
  on stochasticity and N, JSONL/JSON/CSV/Markdown reports
- small HTML console over a domain view
- pre-registered protocol

Not done:

- **the live experiment has not been run**
- contextual token range is too narrow as calibrated (see BENCHMARK.md)
- CONTROL is a single weak baseline; no retry-with-backoff wrapper arm
- policy is Python defaults, not files loaded from disk
- compensation is in-process and synchronous
- authorization is a role enum, not an identity system
- checkpoints have no external anchor

## V1.1 — next milestone

1. Run the pre-registered live campaign (N=30) on one small model and
   publish the report as-is, including unfavourable results.
2. Add a stronger baseline arm: the same agent with a retry-with-backoff
   wrapper and no AERIS. Without it, "AERIS beats CONTROL" partly means
   "any supervisor beats none".
3. Fix the token baseline calibration by learning from retried and
   rerouted healthy flights too, as a new protocol version, then re-run.
4. Repeat on a second model family to see whether the effect survives a
   change of model.

## V2 — distribution, only if a measured gap needs it

- Recorder implementation on Postgres or an event log
- Optional NATS/Kafka transport for telemetry
- Horizontal API replicas in front of the same flight log

The Pydantic models in `aeris.core` should not change shape for this.

## V3 — research that could fail in public

- Pre-registered evaluation on a frozen scenario mix (protocol exists; live run pending)
- Comparison against other intervention policies
- Only then: learned ranking, graph search, multi-agent traffic

Do not introduce RL or a vector store because they are fashionable.
Introduce them when a measured V0/V1 gap cannot be closed with
thresholds and scoring.

## Explicit non-goals

- Becoming an agent framework
- Hiding intervention inside the prompt
- Claiming reliability without a CONTROL arm
