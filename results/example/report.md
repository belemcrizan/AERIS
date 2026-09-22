# AERIS support-case benchmark report

- experiment: `support-experiment/1.0.0`
- model: `scripted-support` (stochastic: False)
- seeds: [0]
- arms: CONTROL, AERIS_FULL, AERIS_NO_CONTEXT, AERIS_NO_DIVERSITY, AERIS_NO_SIDE_EFFECT_GATE
- runtime adapter: `llm-tool-agent/1.0.0`, scenario set: `support-campaign/1.0.0`, tool fixtures: `support-fixtures/1.0.0`, evaluator: `support-evaluator/1.0.0`
- agent prompt hash: `e7ef44306f556f85`

Per-arm configuration identity:

| arm | policy_version | policy_hash | planner_weights_hash | baseline |
|---|---|---|---|---|
| CONTROL | aeris-policy-1.0.0 | `42b1ed5c45a0b50f` | `5d43037f73ecdd63` | `-` |
| AERIS_FULL | aeris-policy-1.0.0 | `05c3569838c4bb92` | `5d43037f73ecdd63` | `9115fdc3fd9ef554` |
| AERIS_NO_CONTEXT | aeris-policy-1.0.0 | `42b1ed5c45a0b50f` | `5d43037f73ecdd63` | `-` |
| AERIS_NO_DIVERSITY | aeris-policy-1.0.0 | `162dd53577e82722` | `95b00335f2684894` | `9115fdc3fd9ef554` |
| AERIS_NO_SIDE_EFFECT_GATE | aeris-policy-1.0.0 | `ed20ab80e70c082f` | `5d43037f73ecdd63` | `9115fdc3fd9ef554` |

Calibration: 15 baselines learned from healthy CONTROL flights on seeds 10000..10009 (fingerprint `9115fdc3fd9ef554`). Validation seeds 20000..: 0/10 healthy flights changed by AERIS_FULL. Test seeds never overlap either range.

## Statistical status

This run used a deterministic fixture model. Repeating it does not create independent samples, so no confidence interval or significance claim is made. Counts below are exact for this fixture.

## Observed facts

### Outcomes per arm

| arm | n | task correct | terminal failures | recovered / faulted | duplicate credits | healthy flights changed | reroutes | median latency | median cost | cost / success |
|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL | 18 | 8 (44.4%) | 8 | 5 / 15 | 1 | 0 | 0 | 6,338 ms | $0.00393 | $0.00896 |
| AERIS_FULL | 18 | 14 (77.8%) | 4 | 12 / 16 | 0 | 1 | 11 | 8,681 ms | $0.00572 | $0.00805 |
| AERIS_NO_CONTEXT | 18 | 13 (72.2%) | 5 | 11 / 16 | 0 | 1 | 11 | 9,051 ms | $0.00816 | $0.01063 |
| AERIS_NO_DIVERSITY | 18 | 14 (77.8%) | 4 | 12 / 16 | 0 | 1 | 14 | 9,529 ms | $0.00583 | $0.00923 |
| AERIS_NO_SIDE_EFFECT_GATE | 18 | 14 (77.8%) | 3 | 12 / 16 | 1 | 1 | 11 | 8,681 ms | $0.00572 | $0.00808 |

### Paired deltas vs CONTROL

| arm | pairs | CONTROL success | arm success | absolute delta | CONTROL failures | arm failures | relative failure reduction | recovery delta | median latency overhead | median cost overhead | harmful | duplicates avoided | incremental cost / recovered task |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AERIS_FULL | 18 | 44.4% | 77.8% | +33.3 pp | 10 | 4 | 60.0% | +7 | +1,105 ms | +$0.00190 | 1 / 17 | +1 | $0.00586 |
| AERIS_NO_CONTEXT | 18 | 44.4% | 72.2% | +27.8 pp | 10 | 5 | 50.0% | +6 | +2,422 ms | +$0.00424 | 1 / 17 | +1 | $0.01108 |
| AERIS_NO_DIVERSITY | 18 | 44.4% | 77.8% | +33.3 pp | 10 | 4 | 60.0% | +7 | +1,940 ms | +$0.00252 | 1 / 17 | +1 | $0.00822 |
| AERIS_NO_SIDE_EFFECT_GATE | 18 | 44.4% | 77.8% | +33.3 pp | 10 | 4 | 60.0% | +7 | +1,105 ms | +$0.00190 | 1 / 17 | +0 | $0.00592 |

### Intervention utility (derived from paired outcomes)

| arm | intervened flights | beneficial | neutral | harmful | unresolved |
|---|---|---|---|---|---|
| AERIS_FULL | 17 | 8 (47.1%) | 8 (47.1%) | 1 (5.9%) | 0 (0.0%) |
| AERIS_NO_CONTEXT | 17 | 7 (41.2%) | 9 (52.9%) | 1 (5.9%) | 0 (0.0%) |
| AERIS_NO_DIVERSITY | 17 | 8 (47.1%) | 8 (47.1%) | 1 (5.9%) | 0 (0.0%) |
| AERIS_NO_SIDE_EFFECT_GATE | 17 | 7 (41.2%) | 9 (52.9%) | 1 (5.9%) | 0 (0.0%) |

### Detection (event-level fault matching)

| arm | event precision | event recall | FP | FN | duplicate | unmatched hazards | unmatched faults | late | mean MTTD | mean MTTI | mean MTTR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL | 50.0% | 73.3% | 11 | 4 | 1 | 9 | 4 | 1 | 4,367 ms | n/a | 6,319 ms |
| AERIS_FULL | 19.8% | 100.0% | 65 | 0 | 12 | 63 | 0 | 0 | 2,437 ms | 0 ms | 8,758 ms |
| AERIS_NO_CONTEXT | 33.3% | 100.0% | 32 | 0 | 12 | 30 | 0 | 0 | 2,437 ms | 0 ms | 10,582 ms |
| AERIS_NO_DIVERSITY | 14.4% | 100.0% | 95 | 0 | 12 | 93 | 0 | 0 | 2,437 ms | 0 ms | 9,441 ms |
| AERIS_NO_SIDE_EFFECT_GATE | 19.8% | 100.0% | 65 | 0 | 12 | 63 | 0 | 0 | 2,437 ms | 0 ms | 8,281 ms |

### Per scenario

| scenario | category | CONTROL | AERIS_FULL | AERIS_NO_CONTEXT | AERIS_NO_DIVERSITY | AERIS_NO_SIDE_EFFECT_GATE | pre-registered expectation |
|---|---|---|---|---|---|---|---|
| healthy | baseline | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | Both arms correct; AERIS should not intervene. |
| status_latency_8s | positive | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | CONTROL slow but correct; AERIS reroutes. Latency trade, not a rescue. |
| status_timeout | positive | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | CONTROL fails after its own retries; AERIS reroutes to Bravo. |
| status_transient | positive | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | Both recover by retrying; AERIS adds a control step. |
| status_rate_limit | positive | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | CONTROL's third self-retry succeeds; AERIS retries, may reroute. |
| status_malformed | positive | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | Both recover by retrying. |
| incident_db_persistent | positive | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | CONTROL fails; AERIS retries, then reroutes to Bravo. |
| stale_incident | positive | 0/1 | 1/1 B1 | 0/1 N1 | 1/1 B1 | 1/1 B1 | CONTROL answers with the wrong incident; AERIS flags STALE_DATA and reroutes. |
| runbook_no_progress | positive | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | CONTROL stalls and fails; AERIS reroutes to the local index. |
| credit_lost_response_unkeyed | side_effect | 0/1 | 0/1 B1 | 0/1 B1 | 0/1 B1 | 0/1 N1 | CONTROL's self-retry double-credits. AERIS blocks the retry: no duplicate, but the flight does not complete. NO_SIDE_EFFECT_GATE should duplicate. |
| credit_lost_response_keyed | side_effect | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | Both arms replay the same key: one ledger entry. AERIS adds no safety here. |
| shared_dependency_outage | failure_domain | 0/1 | 0/1 N1 | 0/1 N1 | 0/1 N1 | 0/1 N1 | Nobody recovers. AERIS should refuse the non-independent diversion. |
| independent_fallback_outage | failure_domain | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | CONTROL fails; AERIS reroutes to Bravo and may recover. |
| mixed_fallbacks_outage | failure_domain | 0/1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | 1/1 B1 | AERIS_FULL picks Bravo; AERIS_NO_DIVERSITY may waste a diversion on Bravo-shared. |
| all_routes_fail | failure_domain | 0/1 | 0/1 N1 | 0/1 N1 | 0/1 N1 | 0/1 N1 | Both fail. AERIS should stop early rather than burn diversions. |
| false_low_confidence | negative_control | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | CONTROL correct. AERIS reroutes on an untrusted signal: overhead, no gain. |
| false_confidence_bad_fallback | negative_control | 1/1 | 0/1 H1 | 0/1 H1 | 0/1 H1 | 0/1 H1 | CONTROL correct; AERIS diverts into a worse route. Expected HARMFUL. |
| temporary_latency | negative_control | 1/1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | 1/1 N1 | Would recover naturally. Any AERIS diversion is unnecessary. |

Cells: task-correct flights / flights. B/N/H/U = beneficial / neutral / harmful / unresolved.

### Negative and unfavourable results

- `false_low_confidence` / AERIS_FULL: intervened on 1 flight(s) with no ground-truth fault (unnecessary change).
- `false_low_confidence` / AERIS_NO_CONTEXT: intervened on 1 flight(s) with no ground-truth fault (unnecessary change).
- `false_low_confidence` / AERIS_NO_DIVERSITY: intervened on 1 flight(s) with no ground-truth fault (unnecessary change).
- `false_low_confidence` / AERIS_NO_SIDE_EFFECT_GATE: intervened on 1 flight(s) with no ground-truth fault (unnecessary change).
- `false_confidence_bad_fallback` / AERIS_FULL: task correct 0/1 vs CONTROL 1/1; harmful interventions 1.
- `false_confidence_bad_fallback` / AERIS_NO_CONTEXT: task correct 0/1 vs CONTROL 1/1; harmful interventions 1.
- `false_confidence_bad_fallback` / AERIS_NO_DIVERSITY: task correct 0/1 vs CONTROL 1/1; harmful interventions 1.
- `false_confidence_bad_fallback` / AERIS_NO_SIDE_EFFECT_GATE: task correct 0/1 vs CONTROL 1/1; harmful interventions 1.
- AERIS_FULL median paired latency overhead is +1105 ms across all pairs.
- AERIS_FULL raised 65 false-positive hazards vs 11 under CONTROL's static detector; most are contextual BUDGET_RISK cautions from a token range calibrated only on clean single-route flights.

## Interpretation

- On this scenario mix, AERIS_FULL's task-correct rate was higher than CONTROL's (+33.3 pp over 18 pairs). The mix is chosen, not sampled from production, so the number describes the campaign, not a deployment.
- AERIS_FULL was correct more often than CONTROL in: status_timeout, status_rate_limit, incident_db_persistent, stale_incident, runbook_no_progress, independent_fallback_outage, mixed_fallbacks_outage.
- AERIS_FULL was correct less often than CONTROL in: false_confidence_bad_fallback. Read the traces before attributing a cause.
- Diversity ablation: compare reroutes and latency in the failure_domain scenarios; AERIS_NO_DIVERSITY median latency overhead +1,940 ms vs AERIS_FULL +1,105 ms.
- Side-effect gate ablation: duplicates avoided vs CONTROL +1 with the gate, +0 without it.

## Limitations

- One agent architecture (a direct tool-calling loop) and one case study.
- Tool faults are synthetic and injected by a proxy; real outages are messier and correlated.
- CONTROL is one baseline (agent-owned retries, no supervisor). A stronger baseline, e.g. a retry-with-backoff wrapper, may close part of the gap.
- Task correctness is checked against four fixed facts; answer quality beyond them is not scored.
- Contextual baselines come from a small calibration set of healthy flights.
- Latency for tools is virtual (declared, not measured); model latency is measured when live.
- Intervention utility is per flight, not per decision.
- The scripted fixture model is not an LLM. It exercises the control path, not LLM behaviour.

## Sample traces (first seed)

### all_routes_fail/AERIS_FULL

```text
FLIGHT flt_710c0bc9a869

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:00.000 fault PERSISTENT_FAILURE injected on svc:crm [fault_1669c9b36069]
00:01.006 TOOL_FAILURE detected severity=WARNING
00:01.006 state RUNNING -> DEGRADED (radar: TOOL_FAILURE)
00:01.006 ATC RETRY: retrying waypoint after TOOL_FAILURE
00:01.006 state DEGRADED -> HOLDING (retrying waypoint after TOOL_FAILURE)
00:01.006 state HOLDING -> RUNNING (retry cleared)
00:01.006 waypoint check_status started tool=get_service_status
00:01.620 TOOL_FAILURE detected severity=CRITICAL
00:01.620 NO_PROGRESS detected severity=WARNING
00:01.620 state RUNNING -> DEGRADED (radar: NO_PROGRESS)
00:01.620 ATC RETRY: retrying waypoint after TOOL_FAILURE
00:01.620 state DEGRADED -> HOLDING (retrying waypoint after TOOL_FAILURE)
00:01.620 state HOLDING -> RUNNING (retry cleared)
00:01.620 waypoint check_status started tool=get_service_status
00:02.330 TOOL_FAILURE detected severity=CRITICAL
00:02.330 REPEATED_ACTION detected severity=CAUTION
00:02.330 NO_PROGRESS detected severity=CRITICAL
00:02.330 state RUNNING -> DEGRADED (radar: TOOL_FAILURE)
00:02.330 ATC ABORT: no independent diversion: bravo shares failed dependency ['svc:crm']
            blocked route-bravo: shares ['svc:crm']
00:02.330 state DEGRADED -> ABORTED (no independent diversion: bravo shares failed dependency ['svc:crm'])
00:02.330 ABORTED (no independent diversion: bravo shares failed dependency ['svc:crm']) reason=route_exhaustion
```

### all_routes_fail/CONTROL

```text
FLIGHT flt_4158b1c1c4f9

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:00.000 fault PERSISTENT_FAILURE injected on svc:crm [fault_3c519eeca659]
00:02.330 HIGH_LATENCY detected severity=CAUTION
00:02.330 TOOL_FAILURE detected severity=WARNING
00:02.330 state RUNNING -> DEGRADED (radar: TOOL_FAILURE)
00:02.330 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:02.330 state DEGRADED -> FAILED (step failed without intervention)
00:02.330 FAILED (CONTROL arm: observation only, no ATC intervention) reason=tool_failure
```

### credit_lost_response_keyed/AERIS_FULL

```text
FLIGHT flt_bacb3d926e44

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:01.850 ATC CONTINUE: no hazards detected; remaining on current airway
00:01.850 waypoint completed ok=True
00:01.850 waypoint lookup_incident started tool=query_incident_database
00:02.840 ATC CONTINUE: no hazards detected; remaining on current airway
00:02.840 waypoint completed ok=True
00:02.840 waypoint consult_runbook started tool=search_runbook
00:04.108 ATC CONTINUE: no hazards detected; remaining on current airway
00:04.108 waypoint completed ok=True
00:04.108 waypoint apply_credit started tool=apply_service_credit
00:04.108 fault LOST_RESPONSE_AFTER_COMMIT injected on svc:billing_ledger [fault_766761e5f88a]
00:04.951 side effect committed IDEMPOTENT_WRITE key=credit-CUST-1042-INC-4821
00:04.951 TOOL_FAILURE detected severity=WARNING
00:04.951 state RUNNING -> DEGRADED (radar: TOOL_FAILURE)
00:04.951 ATC RETRY: retrying waypoint after TOOL_FAILURE
00:04.951 state DEGRADED -> HOLDING (retrying waypoint after TOOL_FAILURE)
00:04.951 state HOLDING -> RUNNING (retry cleared)
00:04.951 waypoint apply_credit started tool=apply_service_credit
00:05.938 ATC CONTINUE: no hazards detected; remaining on current airway
00:05.938 waypoint completed ok=True
00:05.938 waypoint respond started
00:06.671 ATC CONTINUE: no hazards detected; remaining on current airway
00:06.671 waypoint completed ok=True
00:06.671 state RUNNING -> COMPLETED (all waypoints reached)
00:06.671 COMPLETED (mission destination reached)
```

### credit_lost_response_keyed/CONTROL

```text
FLIGHT flt_633885d7cc36

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:01.850 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:01.850 waypoint completed ok=True
00:01.850 waypoint lookup_incident started tool=query_incident_database
00:02.840 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:02.840 waypoint completed ok=True
00:02.840 waypoint consult_runbook started tool=search_runbook
00:04.108 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:04.108 waypoint completed ok=True
00:04.108 waypoint apply_credit started tool=apply_service_credit
00:04.108 fault LOST_RESPONSE_AFTER_COMMIT injected on svc:billing_ledger [fault_39079958825d]
00:05.938 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:05.938 waypoint completed ok=True
00:05.938 waypoint respond started
00:06.671 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:06.671 waypoint completed ok=True
00:06.671 state RUNNING -> COMPLETED (all waypoints reached)
00:06.671 COMPLETED (mission destination reached)
```

### credit_lost_response_unkeyed/AERIS_FULL

```text
FLIGHT flt_14a03fe92759

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:01.850 ATC CONTINUE: no hazards detected; remaining on current airway
00:01.850 waypoint completed ok=True
00:01.850 waypoint lookup_incident started tool=query_incident_database
00:02.840 ATC CONTINUE: no hazards detected; remaining on current airway
00:02.840 waypoint completed ok=True
00:02.840 waypoint consult_runbook started tool=search_runbook
00:04.108 ATC CONTINUE: no hazards detected; remaining on current airway
00:04.108 waypoint completed ok=True
00:04.108 waypoint apply_credit started tool=apply_service_credit
00:04.108 fault LOST_RESPONSE_AFTER_COMMIT injected on svc:billing_ledger [fault_ea4938da5ea1]
00:04.951 side effect committed IRREVERSIBLE_WRITE key=None
00:04.951 TOOL_FAILURE detected severity=WARNING
00:04.951 state RUNNING -> DEGRADED (radar: TOOL_FAILURE)
00:04.951 ATC ABORT: automatic retry of an irreversible write is prohibited
            safety gate: irreversible write already committed
00:04.951 state DEGRADED -> ABORTED (automatic retry of an irreversible write is prohibited)
00:04.951 ABORTED (automatic retry of an irreversible write is prohibited) reason=unsafe_retry_blocked
```

### credit_lost_response_unkeyed/CONTROL

```text
FLIGHT flt_79c7116e9a73

00:00.000 flight created
00:00.000 route alpha selected
00:00.000 state CREATED -> PLANNED (flight plan accepted)
00:00.000 state PLANNED -> RUNNING (cleared for takeoff)
00:00.000 waypoint check_status started tool=get_service_status
00:01.850 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:01.850 waypoint completed ok=True
00:01.850 waypoint lookup_incident started tool=query_incident_database
00:02.840 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:02.840 waypoint completed ok=True
00:02.840 waypoint consult_runbook started tool=search_runbook
00:04.108 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:04.108 waypoint completed ok=True
00:04.108 waypoint apply_credit started tool=apply_service_credit
00:04.108 fault LOST_RESPONSE_AFTER_COMMIT injected on svc:billing_ledger [fault_601ff2a016df]
00:05.938 side effect committed IRREVERSIBLE_WRITE key=None
00:05.938 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:05.938 waypoint completed ok=True
00:05.938 waypoint respond started
00:06.671 ATC CONTINUE: CONTROL arm: observation only, no ATC intervention
00:06.671 waypoint completed ok=True
00:06.671 state RUNNING -> COMPLETED (all waypoints reached)
00:06.671 COMPLETED (mission destination reached)
```

