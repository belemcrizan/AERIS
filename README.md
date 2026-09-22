# AERIS

**Agentic Execution, Routing & Intervention System**

Air Traffic Control for AI agents.

AERIS is **not** an agent framework. It does not replace LangGraph, AutoGen,
CrewAI, the OpenAI Agents SDK, Google ADK, or a custom loop. It is an
external control layer that can watch an agent while it runs, notice
degradation, and intervene before the flight reaches a terminal failure.

Agent frameworks decide how an agent *can* execute. AERIS observes how an
execution *is* behaving and may interfere with it.

The research question is intentionally falsifiable:

> Does an external closed-loop runtime control layer improve the
> reliability of a real stochastic agent under controlled runtime
> degradation, without excessive unnecessary intervention, cost,
> latency, or side-effect risk?

AERIS is allowed to lose. The benchmark contains scenarios built so that
it does, and those stay in every report.

## What AERIS is, and is not

It is a loop outside the agent:

```
OBSERVE -> DETECT -> DECIDE -> VERIFY SAFETY -> INTERVENE -> RECORD -> MEASURE
```

It is not an agent framework, a prompt framework, a planner, a workflow
engine, an LLM judge steering another LLM, or a multi-agent operating
system. The detector is thresholds. The planner is an explained score.
No model sits in the control path.

## Where it stands

| | Status |
| --- | --- |
| V0: deterministic simulator, ATC loop, side-effect safety, recorder | done |
| V1: one real-model adapter, enterprise support case, fault campaign, paired experiment | implemented, run offline with a scripted model |
| V1 live experiment with a real LLM | **not executed yet**; protocol pre-registered in [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md) |

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
tamper-evident. Optional signed checkpoints (HMAC-SHA256 or Ed25519)
add authentication and tail-truncation detection. See
[THREAT_MODEL.md](THREAT_MODEL.md) and [SAFETY.md](SAFETY.md).

V1 additions, all behind the same models:

- **Fault instances.** Every injected fault has a unique `fault_id`,
  onset, target, and a `ground_truth` flag. ATC never sees them. After
  the flight, a deterministic matcher attributes each hazard to a
  `source_fault_id` by type, route, waypoint, and time window, which
  gives event-level precision and recall, MTTD, MTTI, MTTR.
- **Intervention utility.** Each intervened AERIS flight is labelled
  BENEFICIAL, NEUTRAL, HARMFUL, or UNRESOLVED from its paired CONTROL
  flight, never from the controller's own opinion.
- **Contextual radar.** Per runtime/route/waypoint/tool baselines
  (median and MAD), a robust z-score for latency, and a token range.
  Every contextual hazard states observed value, baseline, deviation,
  threshold.
- **Failure-domain diversity.** Routes declare provider, model family,
  tool provider, data source, region, network, and service dependencies.
  The planner logs a diversity score in [0, 1] per candidate, remembers
  which dependency failed on which route, and penalizes a candidate that
  shares it.
- **Concurrency and cancellation.** One lock per flight and an optimistic
  `control_version`, so two simultaneous controls produce one effect.
  Cancellation runs from the API to a cooperative checkpoint in the
  runtime and records the request and the outcome separately.

## Quick start

Python 3.12+. No cloud account. No LLM API key.

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest -q
python examples/run_latency_reroute.py
python examples/run_experiment.py
python examples/first_flight.py
python examples/live_support_experiment.py            # support case, scripted model, offline
python examples/live_support_experiment.py --flight status_timeout
python -m uvicorn aeris.api.app:app --port 8000       # then open /console
```

Everything above is offline. Tests never touch the network.

`run_experiment.py` compares CONTROL and AERIS on the same scenarios,
the same faults, and a `FakeClock`. It prints rates. It does not accept
the hypothesis. `first_flight.py` runs a local Python agent: Tool A
fails, ATC diverts to Tool B. No API key.

Canonical simulator demo: Alpha hits high latency at waypoint 2, radar
emits `HIGH_LATENCY`, ATC diverts to Bravo, the mission completes, and
the recorder keeps the whole history.

## The support case (V1)

An enterprise support agent diagnoses a reported outage, finds the
incident, reads the runbook, applies an SLA credit on a sandbox ledger
when due, and answers in JSON. Alpha uses hosted services; Bravo uses
local, independent ones. A fault proxy around the tools injects 10 fault
types across 18 scenarios, including a lost response after the credit
commits and three negative controls. An external evaluator checks four
ground-truth facts plus the ledger, so `COMPLETED` is not success. See
[BENCHMARK.md](BENCHMARK.md).

The live run is opt-in and needs both variables:

```bash
AERIS_LIVE=1 OPENAI_API_KEY=... python examples/live_support_experiment.py --live --seeds 30
```

See [LIVE_AGENT.md](LIVE_AGENT.md) for settings and cost.

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
| POST | `/flights/{id}/control/cancel` |
| GET | `/flights/{id}/console` |
| GET | `/console` |
| GET | `/health` |

Control calls must send `role`: `OBSERVER`, `CONTROLLER`, or `ADMIN`.
There is no default. A missing or unknown role is HTTP 422; a known role
without permission is 403. Abort and cancel require `ADMIN`. A control call may send
`expected_version`; if the flight's `control_version` moved, the call is
rejected with 409 instead of applied twice. Domain rejections are HTTP
403 or 409, not 500.

`POST /flights` accepts a `scenario_id` (`happy_path`, `latency_reroute`,
`persistent_tool_failure`, `timeout_critical`, `false_low_confidence`,
`idempotent_retry`, `unsafe_irreversible_retry`, ...). The body may also
set `mode` to `AERIS` or `CONTROL`, and `background: true` to return
immediately so the flight can be watched and cancelled while it runs.

`/console` is a small HTML page over `/flights/{id}/console`. It shows
the flight, hazards, evidence, provenance, telemetry, side effects,
alternatives with score and diversity, and the recommended action. Only
actions the domain allows are enabled; a disabled action says why, for
example "Retry unavailable: an irreversible write has already
committed." The page is a view. The domain decides.

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

## What V1 tested, and what it did not

Tested, offline, with a deterministic scripted model standing in for the
LLM (`results/example/`): the adapter, the fault proxy, the evaluator,
the pairing, the ablations, and the report all run end to end. On that
fixture AERIS_FULL was task-correct on 14/18 scenarios against CONTROL's
8/18, at a median +1.1 s and +$0.002 per flight. It blocked the duplicate
credit that CONTROL produced, was HARMFUL on
`false_confidence_bad_fallback`, and raised about six times as many
false-positive hazards as the static detector.

Not tested: a real LLM. Those fixture numbers say the instrument works.
They are not evidence that AERIS helps a real agent, and they carry no
confidence interval because repeating a deterministic run adds no
information.

## What results mean

- A live result means: for this model, this agent loop, this case, and
  this chosen fault mix, the paired difference was X with the stated
  interval. See [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md) for the
  frozen thresholds and hypotheses.
- It does not mean AERIS improves reliability in general, for other
  agents, for real outages, or at a cost you'd accept. Engineering and
  statistical significance are reported separately.

## Remaining limitations

- One agent loop, one case, synthetic tool faults, a chosen scenario mix.
- CONTROL is an agent with its own retries and no supervisor, not a
  stronger alternative policy.
- The scripted model's behaviour after an error was written by us.
- Contextual token baselines are too narrow as calibrated (many
  false-positive `BUDGET_RISK` cautions).
- Authorization is a role enum, not identity. Locks are in-process; one
  API process per flight log.
- Cancellation is cooperative; an in-flight HTTP call is not interrupted.
- Recorder checkpoints detect truncation only if the checkpoint store
  itself was not truncated too; there is no external anchor.

## What AERIS deliberately is not

- Not a multi-agent operating system
- Not a Kubernetes control plane
- Not a prompt framework
- Not a learned policy
- Not dependent on Kafka, Redis, Postgres, or a vector database

Those can appear later behind the same domain models. See
[ARCHITECTURE.md](ARCHITECTURE.md), [CONCEPTS.md](CONCEPTS.md),
[RESEARCH.md](RESEARCH.md), [ROADMAP.md](ROADMAP.md),
[THREAT_MODEL.md](THREAT_MODEL.md), [SAFETY.md](SAFETY.md),
[EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md),
[LIVE_AGENT.md](LIVE_AGENT.md), and [BENCHMARK.md](BENCHMARK.md).

## License

MIT
