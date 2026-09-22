# Research

## Question

Can an external, framework-agnostic control layer observe an agent
during execution, detect degradation, and intervene (reroute, retry,
hold, escalate, abort) before terminal failure?

## Hypothesis (V0)

AERIS increases **recovery rate** under injected runtime failures
relative to an otherwise identical CONTROL arm that observes but never
intervenes.

This is a hypothesis, not a result.

## Why a simulator first

Using a live LLM would confound the measurement:

- sampling noise
- network jitter
- vendor-side retries
- non-replayable traces

V0 injects latency, timeouts, tool failures, low confidence, stale data,
and repeated actions into a fake agent. The injection is deterministic.
The detector is a threshold function. The controller is a total order
on recommended actions plus a few policy guards.

If AERIS cannot recover a persistent Alpha tool failure by diverting to
Bravo in that fixture, it will not magically recover a production agent.

## Design for falsification

The evaluation harness (`aeris.evaluation`) always runs both arms.

| Fixture | What a true hypothesis looks like | What falsification looks like |
| --- | --- | --- |
| `happy_path` | both succeed | AERIS aborts a healthy flight |
| `persistent_tool_failure` | CONTROL fails, AERIS completes via Bravo | AERIS also fails, or CONTROL already succeeds |
| `all_routes_fail` | both fail | AERIS reports COMPLETED |
| `tool_failure_retry` | AERIS completes with retries, CONTROL fails | AERIS loops or aborts |
| `timeout_critical` | AERIS escalates or diverts | AERIS continues into FAILED while claiming control |

`recovery_rate` is
`completed flights / flights with an injected terminal failure`.
`delta_recovery_rate = AERIS - CONTROL`. A zero or negative delta on
this fixture set falsifies the V0 hypothesis **for these faults**.

The harness prints numbers. It does not print a publication claim.

## Threats to validity

- Simulator faults are independent of real LLM failure modes.
- Restarting a diverted route from waypoint 0 may overstate recovery
  cost or understate partial progress.
- Thresholds were chosen to make the demo visible, not fitted on a
  hold-out set. Tuning them on the same fixtures would be circular.
- CONTROL is "no intervention", not "a different sophisticated watcher".

## What would count as a later result

A later version may plug a real `AgentRuntime` into the same recorder
and harness, freeze policies **before** looking at metrics, and
pre-register the scenario mix. Until then, treat V0 numbers as a test
of the instrument, not of agents in the wild.
