# Experiment protocol (pre-registration)

This file fixes the live support-case experiment **before** any live
result exists. At the time of writing no live run has been executed.
The only results in the repository come from the deterministic scripted
model (`results/example/`), which is an instrument check, not evidence.

If a value below changes, the change is a new protocol version: bump
`PROTOCOL_VERSION` in this file, record why, and do not mix results
across versions.

- Protocol version: `support-protocol/1.0.0`
- Experiment version: `support-experiment/1.0.0` (`aeris/experiments/support.py`)
- Scenario set: `support-campaign/1.0.0` (`aeris/cases/support/campaign.py`)
- Tool fixtures: `support-fixtures/1.0.0`
- Evaluator: `support-evaluator/1.0.0`
- Runtime adapter: `llm-tool-agent/1.0.0`
- Policy version: `aeris-policy-1.0.0`

## Question

Does an external closed-loop runtime control layer improve the
reliability of a real stochastic tool-calling agent under controlled
runtime degradation, without excessive unnecessary intervention, cost,
latency, or side-effect risk?

AERIS is allowed to lose. A null or negative primary result is a valid
outcome and is published the same way as a positive one.

## Arms

| Arm | What changes |
| --- | --- |
| `CONTROL` | No ATC decisions. The agent sees tool errors and retries on its own within its turn budget. Radar and the detector still run and are recorded. |
| `AERIS_FULL` | ATC on. Tool errors surface to AERIS, which decides. Contextual radar when calibration produced baselines. Diversity-aware planner. Side-effect gate on. |
| `AERIS_NO_CONTEXT` | As FULL, static thresholds only. |
| `AERIS_NO_DIVERSITY` | As FULL, planner ignores failure domains and shared failed dependencies. |
| `AERIS_NO_SIDE_EFFECT_GATE` | As FULL, retries and reroutes skip the side-effect guard. |

CONTROL is one baseline: an agent that owns its retries with no
supervisor. It is not "a strong alternative supervisor". A
retry-with-backoff wrapper would be a stronger baseline and is future
work.

## Hypotheses

Primary:

- **H1.** Over the fault-bearing scenarios (categories `positive`,
  `failure_domain`, `side_effect`), AERIS_FULL has a higher task-correct
  rate than CONTROL. Test: paired difference in task-correct rate,
  95% percentile bootstrap CI (2,000 resamples over pairs). H1 is
  supported only if the CI lower bound is above 0.

Secondary (reported regardless of direction):

- **H2. Harm.** AERIS_FULL's harmful-intervention rate is at most 10% of
  intervened flights.
- **H3. Unnecessary change.** AERIS_FULL changes at most 5% of flights
  in the `baseline` scenario (no fault).
- **H4. Side effects.** In `credit_lost_response_unkeyed`, CONTROL and
  AERIS_NO_SIDE_EFFECT_GATE produce duplicate credits and AERIS_FULL
  produces none.
- **H5. Failure-domain diversity.** In `mixed_fallbacks_outage`,
  AERIS_FULL recovers at least as often as AERIS_NO_DIVERSITY with fewer
  or equal reroutes. In `shared_dependency_outage`, rerouting gives no
  recovery benefit in any arm.
- **H6. Contextual radar.** AERIS_FULL event-level precision is at least
  AERIS_NO_CONTEXT's, at equal recall.
- **H7. Untrusted confidence.** In `false_confidence_bad_fallback`,
  AERIS is expected to be HARMFUL. This is a negative control. It must
  stay in the campaign and in the report.

## Engineering significance (fixed now)

Statistical and engineering significance are reported separately.
Before seeing results, these are the thresholds for "worth it":

- task-correct delta of at least +10 percentage points on fault scenarios;
- median latency overhead per paired flight of at most +3,000 ms;
- median cost overhead of at most +100% of CONTROL's median cost;
- harmful interventions at most 10% of intervened flights.

A result can be statistically supported and still fail these, or the
reverse. Both are reported.

## Scenario set and fault distribution

The 18 scenarios in `aeris/cases/support/campaign.py`, unchanged, each
with its pre-registered expectation string. Categories:

| Category | Scenarios |
| --- | --- |
| baseline | `healthy` |
| positive | `status_latency_8s`, `status_timeout`, `status_transient`, `status_rate_limit`, `status_malformed`, `incident_db_persistent`, `stale_incident`, `runbook_no_progress` |
| side_effect | `credit_lost_response_unkeyed`, `credit_lost_response_keyed` |
| failure_domain | `shared_dependency_outage`, `independent_fallback_outage`, `mixed_fallbacks_outage`, `all_routes_fail` |
| negative_control | `false_low_confidence`, `false_confidence_bad_fallback`, `temporary_latency` |

The mix is chosen, not sampled from production. Results describe this
campaign. Every scenario gets the same number of paired trials.

Faults are injected by a proxy around tools, never inside the model
prompt. A scenario's fault schedule is a list of `FaultSpec`s keyed by
dependency and call number; CONTROL and every AERIS arm use the same
schedule for the same seed.

## Sample size and seeds

- Live: **30 paired trials per scenario** (`--seeds 30`, seeds `0..29`).
  50 if cost allows; the choice is made before the run and written in
  the run's command line.
- Calibration seeds: `10000..10009` (healthy CONTROL flights only).
- Validation seeds: `20000..20009` (healthy AERIS_FULL flights, false-alarm
  rate reported, never used to adjust anything).
- Test seeds: `0..N-1`. The three ranges never overlap.
- `MIN_PAIRS_FOR_CI = 30` in `aeris/experiments/stats.py`. Below that, no
  interval is printed.
- Deterministic fixture runs never get intervals, regardless of N.

## Model settings

- Model: `AERIS_LIVE_MODEL` (default `gpt-4o-mini`), via an
  OpenAI-compatible chat-completions endpoint.
- Temperature: `AERIS_LIVE_TEMPERATURE` (default `0.7`). Non-zero on
  purpose, so trials are actually stochastic.
- The request seed is the trial seed. Providers treat it as best effort.
- Model version reported by the API is recorded per trial.

Same seed, prompt, temperature, tool fixture, and fault schedule across a
pair do **not** make the two trajectories identical. Pairing is analytic.

## Metrics

Defined in code, restated here so they cannot drift silently.

- **task_correct**: external evaluator, four ground-truth checks
  (service state, incident id, recommended action, credit decision), and
  no duplicate credit. Terminal `COMPLETED` alone is not success.
- **terminal failure**: flight ended `FAILED` or `ABORTED`.
- **recovered / faulted**: task-correct flights among flights where a
  ground-truth fault manifested.
- **healthy flights changed**: flights with no ground-truth fault where
  AERIS made a non-CONTINUE decision.
- **event-level precision / recall**: fault-instance to hazard matching
  in `aeris/evaluation/matching.py` (type compatibility, route, waypoint,
  60 s window). Duplicates excluded from precision.
- **MTTD**: first attributed hazard − fault onset.
- **MTTI**: first non-CONTINUE decision naming a compatible hazard − that hazard.
- **MTTR**: successful completion − fault onset.
- **late detection**: MTTD above 10,000 ms.
- **intervention utility**: BENEFICIAL / NEUTRAL / HARMFUL / UNRESOLVED,
  derived from the pair (`aeris/evaluation/pairing.py`), never from the
  controller.
- **cost**: model tokens × `AERIS_LIVE_PRICE_IN` / `AERIS_LIVE_PRICE_OUT`
  per million, plus declared per-call tool cost. Estimated, not billed.
- **cost per successful task**, **incremental cost per recovered task**.
- **duplicate side effects**, **duplicates avoided** (ledger-derived).

## Frozen configuration

| Item | Value |
| --- | --- |
| AERIS_FULL policy hash (contextual) | `05c3569838c4bb92` |
| Static policy hash (CONTROL, NO_CONTEXT) | `42b1ed5c45a0b50f` |
| Planner weights hash | `5d43037f73ecdd63` |
| Agent prompt hash | `e7ef44306f556f85` |
| Route config hash, independent set, scripted provider | `6bd93e2d23515591` |

Route hashes include the declared model provider, so a live run has a
different route hash than the scripted run. That is expected; the
report prints the live value.

Thresholds (`aeris/policies/thresholds.py`): high latency 2,000 ms,
timeout risk 8,000 ms, low confidence 0.4, stale data 3,600 s,
no-progress 2 steps, repeated action 2, max retries 2, max route changes
3, robust-z threshold 5.0, MAD floor 25 ms, token range tolerance 1.5×.
The experiment sets `human_on_critical=False`, `escalate_irreversible=False`,
`hold_ms=0`, `max_execution_time_ms=180000` because no human is present.

Planner weights (`aeris/routing/planner.py`): reliability 1.5, latency
0.5, cost 0.3, previously failed route 1.0 (0.25 × 4), hazards 0.1 each
plus 0.3 per critical, side-effect 0.2, diversity 0.6, shared failed
dependency 1.0, human penalty 0.4.

Diversity attribute weights (`aeris/routing/diversity.py`): model
provider 0.20, model family 0.10, tool provider 0.15, data source 0.15,
region 0.10, network 0.05, shared service dependencies 0.25.

## Exclusion criteria

- No trial is excluded because of its outcome.
- A trial is excluded only if the harness itself crashed (a Python
  exception outside the agent, not a model or tool error). Excluded
  trials are counted and listed; they are not re-run with a new seed.
- Model API errors (HTTP failures, malformed tool calls) are part of the
  runtime and count as `invalid_runtime_output` failures for that arm.

## Stopping rule

- Fixed N per scenario, decided before the run. No optional stopping,
  no "run more seeds until significant".
- If a spending cap is hit, stop, publish what exists, and label the run
  partial. A partial run does not report intervals for scenarios below
  30 pairs.

## No tuning on the evaluation set

- Contextual baselines are learned only from calibration seeds.
- Validation seeds report a false-alarm rate; they do not change policy.
- Thresholds, planner weights, prompts, scenarios, and the evaluator are
  frozen by the hashes above. Changing any of them after seeing test
  results starts a new protocol version and a new run.

## Known deviations from expectations in the fixture run

The scripted-model example run already contradicts one pre-registered
expectation: in `status_rate_limit` CONTROL fails (expected: its third
self-retry succeeds). The phase turn budget is 3 and the account lookup
uses one turn, so CONTROL only gets two status calls, and both are
rate-limited. The expectation text is left as written. The resulting
BENEFICIAL label on that scenario is partly an artifact of CONTROL's
turn budget and should be read that way.
