# AERIS

**Agentic Execution, Routing & Intervention System**

Air Traffic Control for AI agents.

AERIS is **not** an agent framework. It does not replace LangGraph, AutoGen,
CrewAI, the OpenAI Agents SDK, Google ADK, or a custom loop. It is an
external control layer that can watch an agent while it runs, notice
degradation, and intervene before the flight reaches a terminal failure.

The research question is intentionally falsifiable:

> Can an external control layer observe an AI agent during execution,
> detect degradation or abnormal behavior, and dynamically reroute,
> pause, retry, escalate, or request human intervention before the
> agent reaches a terminal failure?

V0 does **not** claim that the answer is yes. It gives you a local,
deterministic simulator and an evaluation harness that can show the
hypothesis failing.

## Aerospace analogy

| Aerospace | AERIS |
| --- | --- |
| Aircraft | Agent execution |
| Flight | One task execution |
| Mission | User objective |
| Flight plan | Planned execution path |
| Waypoint | Intermediate step |
| Airway / route | Execution strategy |
| Radar | Runtime telemetry |
| ATC | Control plane |
| Sensors | Agent instrumentation |
| Weather | External uncertainty |
| Turbulence | Runtime instability |
| Hazard | Detected execution risk |
| Diversion | Reroute |
| Holding pattern | Retry / wait |
| Mayday | Critical failure |
| Pilot | Agent runtime |
| Human controller | Human-in-the-loop |
| Black box / FDR | Immutable execution trace |
| Destination | Success criteria |

## Architecture

```
                MISSION
                   |
                   v
             FLIGHT PLAN
                   |
                   v
              AGENT FLIGHT
                   |
        +----------+----------+
        |                     |
        v                     v
     RADAR                TELEMETRY
        |
        v
  HAZARD DETECTOR
        |
        v
    ATC CONTROL
        |
   +----+-----+-------+
   |          |       |
CONTINUE   REROUTE   HOLD
                      |
                   HUMAN ATC
        |
        v
 FLIGHT RECORDER
```

Radar observes. It does not steer.

The hazard detector applies thresholds. It does not call an LLM.
The catalog in `aeris/policies/catalog.py` states the signal, threshold,
severity, scope, and whether a repeated observation escalates.

The route planner scores airways and explains every term, including
side-effect risk and previous failures. It does not fly.

The ATC controller chooses `CONTINUE`, `RETRY`, `HOLD`, `REROUTE`,
`COMPENSATE`, `ESCALATE_HUMAN`, or `ABORT`. Compensation is an operation
the director performs before a retry or reroute when a reversible write
already committed. Irreversible writes are not retried automatically.

The flight recorder is append-only and hash-chained. The chain is
tamper-evident, not a signature. See [THREAT_MODEL.md](THREAT_MODEL.md)
and [SAFETY.md](SAFETY.md).

## Quick start

Python 3.12+. No cloud account. No LLM API key.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python examples/run_latency_reroute.py
python examples/run_experiment.py
python examples/first_flight.py
python -m uvicorn aeris.api.app:app --port 8000
```

`run_experiment.py` compares CONTROL and AERIS on the same scenarios,
the same faults, and a `FakeClock`. It prints rates. It does not accept
the hypothesis. `first_flight.py` runs a local Python agent: Tool A
fails, ATC diverts to Tool B. No API key.

Canonical simulator demo: Alpha hits high latency at waypoint 2, radar
emits `HIGH_LATENCY`, ATC diverts to Bravo, the mission completes, and
the recorder keeps the whole history.

## HTTP API

| Method | Path |
| --- | --- |
| POST | `/missions` |
| POST | `/flights` |
| GET | `/flights` |
| GET | `/flights/{id}` |
| GET | `/flights/{id}/timeline` |
| GET | `/flights/{id}/telemetry` |
| GET | `/flights/{id}/hazards` |
| GET | `/flights/{id}/decisions` |
| GET | `/flights/{id}/interventions` |
| GET | `/flights/{id}/side-effects` |
| GET | `/flights/{id}/integrity` |
| GET | `/flights/{id}/human-request` |
| POST | `/flights/{id}/control/continue` |
| POST | `/flights/{id}/control/retry` |
| POST | `/flights/{id}/control/hold` |
| POST | `/flights/{id}/control/reroute` |
| POST | `/flights/{id}/control/abort` |
| GET | `/health` |

Control calls send `role`: `OBSERVER`, `CONTROLLER`, or `ADMIN`.
The default is `CONTROLLER`. Abort requires `ADMIN`. Domain rejections
are HTTP 403 or 409, not 500.

`POST /flights` accepts a `scenario_id` (`happy_path`, `latency_reroute`,
`persistent_tool_failure`, `timeout_critical`, `false_low_confidence`,
`idempotent_retry`, `unsafe_irreversible_retry`, ...). The body may also
set `mode` to `AERIS` or `CONTROL`.

## What V0 demonstrates

On deterministic fixtures, AERIS can retry a transient tool failure,
divert a slow or persistently failing route, refuse an automatic retry
of an irreversible write, retry an idempotent write without a second
commit, compensate a reversible write, and reject an unauthorized
control call. The recorder can show that a history was edited.

## What V0 does not demonstrate

It does not show that the same behavior holds for a live LLM. The
default experiment's positive recovery delta is a fixture result, not a
general claim. `false_low_confidence` is included so AERIS can do worse
than CONTROL by diverting on an untrusted self-report. `all_routes_fail`
still ends in failure. p-values are not computed over identical repeats.

## What V0 deliberately is not

- Not a multi-agent operating system
- Not a Kubernetes control plane
- Not a prompt framework
- Not a learned policy
- Not dependent on Kafka, Redis, Postgres, or a vector database

Those can appear later behind the same domain models. See
[ARCHITECTURE.md](ARCHITECTURE.md), [CONCEPTS.md](CONCEPTS.md),
[RESEARCH.md](RESEARCH.md), [ROADMAP.md](ROADMAP.md),
[THREAT_MODEL.md](THREAT_MODEL.md), and [SAFETY.md](SAFETY.md).

## License

MIT
