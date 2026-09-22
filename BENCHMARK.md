# Benchmark: enterprise support case

## The case

A customer reports that their enterprise service is unavailable and asks
for diagnosis, status, and the safest next action. The agent works
through five phases, one waypoint each:

1. `check_status`: account lookup, then service status
2. `lookup_incident`: the active incident
3. `consult_runbook`: runbook guidance
4. `apply_credit`: SLA credit on a **sandbox ledger** when the runbook says it is due
5. `respond`: a JSON answer

No real financial transaction exists anywhere in this code.

| Route | Status | Incident | Runbook |
| --- | --- | --- | --- |
| Alpha (primary) | status API A | incident DB A | hosted runbook search |
| Bravo (independent fallback) | local telemetry | local incident cache | local runbook index |
| Bravo-shared | local telemetry | local incident cache | **hosted runbook search** |

All routes share the CRM, the billing ledger, and the single LLM. Those
dependencies are declared, so the diversity score between Alpha and
Bravo is 0.65, not 1.0. Bravo-shared exists only for the failure-domain
experiment.

## Fault campaign

18 scenarios, each with a fault schedule and a pre-registered
expectation (`aeris/cases/support/campaign.py`). Faults are injected by a
proxy around tools (`aeris/cases/support/toolbox.py`), keyed by
dependency and call number, so every arm sees the same schedule.

Fault types: `LATENCY`, `TIMEOUT`, `TRANSIENT_FAILURE`,
`PERSISTENT_FAILURE`, `STALE_RESPONSE`, `MALFORMED_RESPONSE`,
`RATE_LIMIT`, `LOST_RESPONSE_AFTER_COMMIT`, `FALSE_CONFIDENCE`,
`NO_PROGRESS`.

Two dedicated experiments sit inside the campaign:

- **Failure domain.** `shared_dependency_outage` (Alpha and Bravo-shared
  both use the hosted runbook, which fails) against
  `independent_fallback_outage` (same outage, Bravo is independent), plus
  `mixed_fallbacks_outage` where both fallbacks exist and the planner has
  to choose.
- **Side effect.** `credit_lost_response_unkeyed`: the credit commits,
  the response is lost, the agent sees a failure. Without idempotency a
  retry double-credits. `credit_lost_response_keyed` is the same fault
  with an idempotency key the ledger honours.

Negative controls, kept on purpose: `false_low_confidence`,
`false_confidence_bad_fallback`, `temporary_latency`.

## Running

```bash
# offline, deterministic, no key (what CI runs, on a subset)
python examples/live_support_experiment.py --out results/example
python examples/live_support_experiment.py --flight credit_lost_response_unkeyed

# live, see LIVE_AGENT.md
AERIS_LIVE=1 OPENAI_API_KEY=... python examples/live_support_experiment.py --live --seeds 30
```

Output directory:

| File | Contents |
| --- | --- |
| `live_trials.jsonl` | one record per flight: arm, seed, config hashes, outcome, evaluator checks, cost, tokens, latency, hazards, detection metrics, utility label |
| `aggregate.json` | per-arm and per-scenario aggregates, paired deltas, calibration, configuration identity, sample traces |
| `paired_results.csv` | one row per (scenario, seed, AERIS arm) paired with its CONTROL flight |
| `report.md` | human-readable report: observed facts, interpretation, limitations |

`results/` is gitignored except `results/example/`, which is the
committed scripted-model run.

## Example result (scripted model, deterministic)

This is **not** a live result and **not** statistical evidence. The
scripted model is deterministic, so one seed per scenario gives exact
counts for the fixture, and repeating it adds nothing. It checks that
the instrument works and that unfavourable results show up.

From `results/example/report.md` (18 scenarios, 1 seed):

| Arm | Task correct | Terminal failures | Duplicate credits | Healthy flights changed | Median latency | Median cost |
| --- | --- | --- | --- | --- | --- | --- |
| CONTROL | 8 / 18 | 8 | 1 | 0 | 6,338 ms | $0.00393 |
| AERIS_FULL | 14 / 18 | 4 | 0 | 1 | 8,681 ms | $0.00572 |
| AERIS_NO_CONTEXT | 13 / 18 | 5 | 0 | 1 | 9,051 ms | $0.00816 |
| AERIS_NO_DIVERSITY | 14 / 18 | 4 | 0 | 1 | 9,529 ms | $0.00583 |
| AERIS_NO_SIDE_EFFECT_GATE | 14 / 18 | 3 | 1 | 1 | 8,681 ms | $0.00572 |

Observed:

- AERIS_FULL was task-correct on 14/18 scenarios against CONTROL's 8/18
  (+33.3 percentage points), with a median paired overhead of +1,105 ms
  and +$0.0019.
- Intervention utility for AERIS_FULL: 8 beneficial, 8 neutral, 1
  harmful, 0 unresolved, out of 17 intervened flights.
- `false_confidence_bad_fallback`: CONTROL correct, every AERIS arm
  wrong. That's the HARMFUL case.
- `false_low_confidence`: AERIS changed a healthy flight on an untrusted
  signal. Outcome unchanged, overhead added.
- `credit_lost_response_unkeyed`: CONTROL double-credited; AERIS_FULL
  blocked the retry (one ledger entry, flight aborted, not task-correct);
  AERIS_NO_SIDE_EFFECT_GATE double-credited like CONTROL.
- `shared_dependency_outage` and `all_routes_fail`: nobody recovered.
- AERIS_FULL raised 65 false-positive hazards against CONTROL's 11,
  mostly contextual `BUDGET_RISK` cautions. The token range was
  calibrated on clean single-route flights, and every retry or reroute
  pushes tokens above it. Event-level precision is 19.8% (FULL) vs 33.3%
  (NO_CONTEXT). On this fixture the contextual layer hurt precision.
- The diversity ablation did not change task correctness here; it
  changed reroute count (11 vs 14) and median latency overhead
  (+1,105 ms vs +1,940 ms).
- `status_rate_limit` contradicted its pre-registered expectation. See
  [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md).

Interpretation, bounded: on this fixture, runtime intervention helps for
hard tool outages with an independent fallback, blocks the duplicate
credit, and costs about a second per flight. It is harmful when it acts
on untrusted confidence and the fallback is worse. The contextual
token baseline is too narrow as calibrated.

Limitations: the scripted model is written by the same people who wrote
AERIS; its behaviour after a tool error is a design choice. One case,
one agent loop, synthetic faults, a chosen scenario mix.

## Reading a report

- **Observed facts** are counts from this run.
- **Interpretation** is a hedged reading of those counts.
- **Limitations** are why the reading may not transfer.
- Intervals appear only for stochastic runs with at least 30 pairs.
- A per-scenario cell like `1/1 B1` means 1 of 1 flights task-correct,
  one intervention labelled beneficial.
