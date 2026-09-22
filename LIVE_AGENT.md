# Live agent

AERIS has exactly one live runtime adapter: a direct tool-calling loop
against an OpenAI-compatible chat-completions endpoint. It is optional.
CI and the test suite never call it.

## Why a direct loop

The adapter had to add the least coupling. An agent framework (OpenAI
Agents SDK, ADK, LangGraph) would bring its own retry, planning, and
tool semantics, and AERIS would be measuring the framework. A direct loop
over HTTP uses only the standard library (`urllib`) and sits behind the same `AgentRuntime` protocol as the
simulator. `aeris/core` does not import it.

| File | Role |
| --- | --- |
| `aeris/adapters/llm.py` | `ChatModel` protocol, `OpenAIChatModel` (stdlib HTTP), `ModelPricing`, `live_enabled()` |
| `aeris/adapters/llm_runtime.py` | `LLMToolAgentRuntime`: runs one waypoint as a phase of a conversation, calls tools through a `ToolExecutor`, returns a `StepObservation` |
| `aeris/cases/support/scripted_model.py` | Deterministic offline `ChatModel` with the same interface, used in CI and for the fixture example |

## What the runtime does

Each waypoint is a phase. The runtime tells the model which tools are
available in this phase, lets it call one tool at a time for up to
`max_turns` turns (3 by default), and returns when the phase's tool
succeeds, the final answer is written, or the budget is used up.

- **AERIS arms** (`agent_owns_retries=False`): the first tool error ends
  the waypoint and surfaces to AERIS as a failed observation. ATC decides
  whether to retry, reroute, or stop.
- **CONTROL arm** (`agent_owns_retries=True`): the model sees the error
  and may call the tool again within its turn budget. No supervisor.

Idempotency keys are generated only when the waypoint declares
`supports_idempotency`. A waypoint that doesn't declare it sends no key,
so a non-idempotent retry really does duplicate. That is what makes the
unsafe baseline in `credit_lost_response_unkeyed` real.

Cancellation is cooperative: the runtime checks the cancel token before
every model turn when it declares `CAN_CANCEL`. An in-flight HTTP call is
not interrupted.

Recorded per step: input and output tokens (reported by the API, or
marked unreported), estimated model cost, tool calls, tool cost,
dependency ids of the last tool, confidence and freshness when the tool
reports them, and the model version string the API returned.

## Running it

Requires both environment variables. Either one alone is refused.

```bash
export AERIS_LIVE=1
export OPENAI_API_KEY=sk-...
# optional
export AERIS_LIVE_MODEL=gpt-4o-mini
export AERIS_LIVE_TEMPERATURE=0.7
export OPENAI_BASE_URL=https://api.openai.com/v1
export AERIS_LIVE_PRICE_IN=0.15     # USD per million input tokens
export AERIS_LIVE_PRICE_OUT=0.60    # USD per million output tokens
```

One pair, with report and trace:

```bash
python examples/live_support_experiment.py --live --flight status_timeout
```

The pre-registered campaign (see [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md)):

```bash
python examples/live_support_experiment.py --live --seeds 30 --out results/live-run-1
```

PowerShell:

```powershell
$env:AERIS_LIVE = "1"; $env:OPENAI_API_KEY = "sk-..."
python examples/live_support_experiment.py --live --seeds 30 --out results/live-run-1
```

A manual GitHub Actions workflow (`.github/workflows/live-experiment.yml`)
runs the same command with the key from a repository secret. It never
runs on push or pull request.

## Cost

Five arms × 18 scenarios × 30 seeds = 2,700 flights, plus 20 calibration
and validation flights. A flight is roughly 5–15 model calls of a few
hundred to a few thousand tokens. With a small model this is on the
order of single-digit dollars; with a frontier model it can be much more.
Reduce `--arms` or `--scenarios` first if cost matters, and say so in the
report.

## What the live run does not control

- Tool faults are synthetic; tools are local fixtures behind a proxy.
- Tool latency is declared per tool, not measured. Model latency is measured.
- Provider-side retries, caching, and silent model updates are outside
  AERIS's view. The recorded model version is whatever the API returns.
- The request seed is best effort on hosted providers. Identical inputs
  can produce different outputs; pairs are analyzed, not replayed.

## Status

The live experiment has **not** been executed in this repository. No
live numbers are published. Everything under `results/example/` comes
from the scripted model.
