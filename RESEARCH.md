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

A later version may plug more `AgentRuntime` adapters into the same
recorder and harness, freeze policies **before** looking at metrics, and
pre-register the scenario mix. `SimplePythonAgentRuntime` shows the
contract on a local tool loop. It is not evidence about LLM agents.

Until a seeded stochastic runtime is measured, treat V0 numbers as a
test of the instrument.

## Measurement, not just recovery

Recovery rate is not sufficient. The harness also reports detector
precision, recall, false-positive rate, false-negative rate, mean time
to detect (fault injection timestamp to the next hazard), mean time to
intervention (that hazard to the first non-CONTINUE decision), and mean
time to recovery (fault injection to a successful completion).

Times use the injected clock. Tests do not sleep.

`intervention_precision` is useful interventions divided by all
interventions. A healthy flight that is rerouted counts against AERIS.
`false_low_confidence` is the fixture where that happens: Alpha is fine,
the agent reports low confidence, Bravo then fails, CONTROL completes,
and AERIS does not.

Undefined rates are null. Examples: precision when nothing was flagged,
compensation success when no compensation ran, mean time to intervention
on the CONTROL arm.

## Statistics

These fixtures are deterministic. Repeating them does not create
independent samples, so V0 does not report p-values. A later stochastic
runtime needs seeded repetitions designed before the run.

## Failure provenance

A failed flight keeps a reason: `tool_failure`, `timeout`,
`budget_exhaustion`, `route_exhaustion`, `unsafe_retry_blocked`,
`unsafe_reroute_blocked`, `compensation_failure`, `human_abort`,
`policy_abort`, `invalid_runtime_output`, or `cancelled`. `FAILED` alone
is not a postmortem.

## CONTROL

CONTROL uses the same runtime, scenario, injected fault, route
candidates, clock, and initial conditions. It records radar, hazards,
and decisions. Every decision is CONTINUE. It is not a weakened agent.
