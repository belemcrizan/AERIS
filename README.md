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

The route planner scores airways and explains the score. It does not fly.

The ATC controller chooses `CONTINUE`, `RETRY`, `HOLD`, `REROUTE`,
`ESCALATE_HUMAN`, or `ABORT`. In V0 that choice is deterministic.

The flight recorder is append-only. The complete flight is replayable.

## Quick start

Python 3.12+. No cloud account. No LLM API key.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python examples/run_latency_reroute.py
python examples/run_experiment.py
python -m uvicorn aeris.api.app:app --port 8000
```

Canonical demo: Alpha hits high latency at waypoint 2, radar emits
`HIGH_LATENCY`, ATC diverts to Bravo, the mission completes, and the
recorder keeps the whole history.

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
| POST | `/flights/{id}/control/continue` |
| POST | `/flights/{id}/control/retry` |
| POST | `/flights/{id}/control/reroute` |
| POST | `/flights/{id}/control/abort` |
| GET | `/health` |

`POST /flights` accepts a `scenario_id` (`happy_path`, `latency_reroute`,
`persistent_tool_failure`, `timeout_critical`, ...). The body may also set
`mode` to `AERIS` or `CONTROL`.

## What V0 deliberately is not

- Not a multi-agent operating system
- Not a Kubernetes control plane
- Not a prompt framework
- Not a learned policy
- Not dependent on Kafka, Redis, Postgres, or a vector database

Those can appear later behind the same domain models. See
[ARCHITECTURE.md](ARCHITECTURE.md), [CONCEPTS.md](CONCEPTS.md),
[RESEARCH.md](RESEARCH.md), and [ROADMAP.md](ROADMAP.md).

## License

MIT
