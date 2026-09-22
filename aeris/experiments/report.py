"""Write campaign results and render human-readable reports.

Output layout (``results/`` by default, gitignored except the committed
fixture example):

    live_trials.jsonl    one TrialRecord per flight
    aggregate.json       per-arm and per-scenario summaries, paired deltas
    paired_results.csv   one row per CONTROL/arm pair
    report.md            OBSERVED / INTERPRETATION / LIMITATION

The report never states that AERIS "works". Interpretation sentences are
generated mechanically from the numbers and always sit next to the
limitations that bound them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from aeris.core.enums import ExperimentArm, InterventionOutcome
from aeris.experiments.support import ArmDelta, ArmSummary, CampaignResult, TrialRecord, TrialRun

CONTROL = ExperimentArm.CONTROL.value
FULL = ExperimentArm.AERIS_FULL.value


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 and unit.startswith("delta") else ""
    unit = unit.removeprefix("delta")
    return f"{sign}{value:,.{digits}f}{unit}"


def _usd(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}${value:.5f}"


def write_results(result: CampaignResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "trials": out / "live_trials.jsonl",
        "aggregate": out / "aggregate.json",
        "paired": out / "paired_results.csv",
        "report": out / "report.md",
    }
    with paths["trials"].open("w", encoding="utf-8") as handle:
        for record in result.records:
            handle.write(record.model_dump_json() + "\n")
    aggregate = {
        "experiment_version": result.experiment_version,
        "model": result.model,
        "stochastic_model": result.stochastic_model,
        "seeds": result.seeds,
        "arms": result.arms,
        "scenario_ids": result.scenario_ids,
        "configuration": _configuration(result),
        "calibration": result.calibration.model_dump(mode="json") if result.calibration else None,
        "overall": {name: summary.model_dump(mode="json") for name, summary in result.overall.items()},
        "deltas": {name: delta.model_dump(mode="json") for name, delta in result.deltas.items()},
        "by_scenario": {
            scenario: {name: summary.model_dump(mode="json") for name, summary in arms.items()}
            for scenario, arms in result.by_scenario.items()
        },
    }
    paths["aggregate"].write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")
    _write_pairs(result, paths["paired"])
    paths["report"].write_text(render_markdown(result), encoding="utf-8")
    return paths


def _configuration(result: CampaignResult) -> dict[str, dict[str, object]]:
    config: dict[str, dict[str, object]] = {}
    for record in result.records:
        if record.arm.value in config:
            continue
        values = record.config.model_dump(mode="json")
        values.pop("seed", None)
        values.pop("started_at", None)
        config[record.arm.value] = values
    return config


def _write_pairs(result: CampaignResult, path: Path) -> None:
    by_pair: dict[str, dict[str, TrialRecord]] = {}
    for record in result.records:
        by_pair.setdefault(record.pair_id, {})[record.arm.value] = record
    fields = [
        "pair_id", "scenario_id", "seed", "arm",
        "control_state", "arm_state", "control_task_correct", "arm_task_correct",
        "intervention_outcome", "attribution_note",
        "control_latency_ms", "arm_latency_ms", "latency_delta_ms",
        "control_cost_usd", "arm_cost_usd", "cost_delta_usd",
        "control_credit_entries", "arm_credit_entries",
        "arm_route_path", "arm_interventions",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pair_id, group in by_pair.items():
            control = group.get(CONTROL)
            if control is None:
                continue
            for arm, record in group.items():
                if arm == CONTROL:
                    continue
                writer.writerow(
                    {
                        "pair_id": pair_id,
                        "scenario_id": record.scenario_id,
                        "seed": record.seed,
                        "arm": arm,
                        "control_state": control.terminal_state.value,
                        "arm_state": record.terminal_state.value,
                        "control_task_correct": control.task_correct,
                        "arm_task_correct": record.task_correct,
                        "intervention_outcome": record.intervention_outcome.value
                        if record.intervention_outcome
                        else "",
                        "attribution_note": record.attribution_note,
                        "control_latency_ms": round(control.latency_ms, 1),
                        "arm_latency_ms": round(record.latency_ms, 1),
                        "latency_delta_ms": round(record.latency_ms - control.latency_ms, 1),
                        "control_cost_usd": round(control.total_cost, 6),
                        "arm_cost_usd": round(record.total_cost, 6),
                        "cost_delta_usd": round(record.total_cost - control.total_cost, 6),
                        "control_credit_entries": control.credit_entries,
                        "arm_credit_entries": record.credit_entries,
                        "arm_route_path": " > ".join(record.route_path),
                        "arm_interventions": " ".join(record.intervention_actions),
                    }
                )


# ------------------------------------------------------------ markdown


def _arm_table(summaries: dict[str, ArmSummary]) -> list[str]:
    lines = [
        "| arm | n | task correct | terminal failures | recovered / faulted | duplicate credits "
        "| healthy flights changed | reroutes | median latency | median cost | cost / success |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, row in summaries.items():
        lines.append(
            f"| {name} | {row.n} | {row.task_correct} ({_pct(row.task_correct_rate)}) | {row.terminal_failures} "
            f"| {row.recovered} / {row.faulted_flights} | {row.duplicate_side_effects} "
            f"| {row.healthy_flights_changed} | {row.reroutes} | {_num(row.median_latency_ms, ' ms')} "
            f"| {_usd(row.median_cost_usd)} | {_usd(row.cost_per_successful_task)} |"
        )
    return lines


def _detection_table(summaries: dict[str, ArmSummary]) -> list[str]:
    lines = [
        "| arm | event precision | event recall | FP | FN | duplicate | unmatched hazards "
        "| unmatched faults | late | mean MTTD | mean MTTI | mean MTTR |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, row in summaries.items():
        lines.append(
            f"| {name} | {_pct(row.event_precision)} | {_pct(row.event_recall)} | {row.false_positives} "
            f"| {row.false_negatives} | {row.duplicate_hazards} | {row.unmatched_hazards} | {row.unmatched_faults} "
            f"| {row.late_detections} | {_num(row.mean_mttd_ms, ' ms')} | {_num(row.mean_mtti_ms, ' ms')} "
            f"| {_num(row.mean_mttr_ms, ' ms')} |"
        )
    return lines


def _utility_table(summaries: dict[str, ArmSummary]) -> list[str]:
    lines = [
        "| arm | intervened flights | beneficial | neutral | harmful | unresolved |",
        "|---|---|---|---|---|---|",
    ]
    for name, row in summaries.items():
        if name == CONTROL:
            continue
        u = row.utility
        lines.append(
            f"| {name} | {u.get('intervened_flights', 0)} "
            f"| {u.get('beneficial', 0)} ({_pct(u.get('beneficial_intervention_rate'))}) "
            f"| {u.get('neutral', 0)} ({_pct(u.get('neutral_intervention_rate'))}) "
            f"| {u.get('harmful', 0)} ({_pct(u.get('harmful_intervention_rate'))}) "
            f"| {u.get('unresolved', 0)} ({_pct(u.get('unresolved_intervention_rate'))}) |"
        )
    return lines


def _delta_table(deltas: dict[str, ArmDelta]) -> list[str]:
    lines = [
        "| arm | pairs | CONTROL success | arm success | absolute delta | CONTROL failures | arm failures "
        "| relative failure reduction | recovery delta | median latency overhead | median cost overhead "
        "| harmful | duplicates avoided | incremental cost / recovered task |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, delta in deltas.items():
        pp = "n/a" if delta.absolute_success_delta_pp is None else f"{delta.absolute_success_delta_pp:+.1f} pp"
        lines.append(
            f"| {name} | {delta.pairs} | {_pct(delta.control_success_rate)} | {_pct(delta.arm_success_rate)} "
            f"| {pp} | {delta.control_terminal_failures} | {delta.arm_terminal_failures} "
            f"| {_pct(delta.relative_failure_reduction)} | {delta.absolute_recovery_delta:+d} "
            f"| {_num(delta.median_latency_overhead_ms, 'delta ms')} "
            f"| {_usd(delta.median_cost_overhead_usd, signed=True)} "
            f"| {delta.harmful_interventions} / {delta.intervened_flights} "
            f"| {delta.duplicate_side_effects_avoided:+d} | {_usd(delta.incremental_cost_per_recovered_task)} |"
        )
    return lines


def _scenario_table(result: CampaignResult) -> list[str]:
    arms = [arm for arm in result.arms if arm != CONTROL]
    header = "| scenario | category | CONTROL | " + " | ".join(arms) + " | pre-registered expectation |"
    lines = [header, "|" + "---|" * (4 + len(arms))]
    categories = {record.scenario_id: record.category for record in result.records}
    for scenario_id, summaries in result.by_scenario.items():
        control = summaries[CONTROL]
        cells = [f"{control.task_correct}/{control.n}"]
        for arm in arms:
            row = summaries[arm]
            u = row.utility
            tags = []
            for key in ("beneficial", "harmful", "neutral", "unresolved"):
                if u.get(key):
                    tags.append(f"{key[0].upper()}{u[key]}")
            cells.append(f"{row.task_correct}/{row.n} {' '.join(tags)}".rstrip())
        expectation = result.expectations.get(scenario_id, "").replace("|", "/")
        lines.append(f"| {scenario_id} | {categories.get(scenario_id, '')} | " + " | ".join(cells) + f" | {expectation} |")
    lines.append("")
    lines.append("Cells: task-correct flights / flights. B/N/H/U = beneficial / neutral / harmful / unresolved.")
    return lines


def _negative_results(result: CampaignResult) -> list[str]:
    lines: list[str] = []
    for scenario_id, summaries in result.by_scenario.items():
        control = summaries[CONTROL]
        for arm, row in summaries.items():
            if arm == CONTROL:
                continue
            harmful = row.utility.get("harmful") or 0
            if row.task_correct < control.task_correct or harmful:
                lines.append(
                    f"- `{scenario_id}` / {arm}: task correct {row.task_correct}/{row.n} vs CONTROL "
                    f"{control.task_correct}/{control.n}; harmful interventions {harmful}."
                )
            elif row.healthy_flights_changed and not row.faulted_flights:
                lines.append(
                    f"- `{scenario_id}` / {arm}: intervened on {row.healthy_flights_changed} flight(s) "
                    "with no ground-truth fault (unnecessary change)."
                )
    overhead = result.deltas.get(FULL)
    if overhead and overhead.median_latency_overhead_ms and overhead.median_latency_overhead_ms > 0:
        lines.append(
            f"- AERIS_FULL median paired latency overhead is {overhead.median_latency_overhead_ms:+.0f} ms "
            "across all pairs."
        )
    full = result.overall.get(FULL)
    control = result.overall.get(CONTROL)
    if full and control and full.false_positives > control.false_positives:
        lines.append(
            f"- AERIS_FULL raised {full.false_positives} false-positive hazards vs {control.false_positives} "
            "under CONTROL's static detector; most are contextual BUDGET_RISK cautions from a token range "
            "calibrated only on clean single-route flights."
        )
    return lines or ["- None observed in this run. That is itself suspicious; check the scenario set."]


def _interpretation(result: CampaignResult) -> list[str]:
    lines: list[str] = []
    delta = result.deltas.get(FULL)
    if delta is not None and delta.absolute_success_delta_pp is not None:
        direction = "higher" if delta.absolute_success_delta_pp > 0 else "not higher"
        lines.append(
            f"- On this scenario mix, AERIS_FULL's task-correct rate was {direction} than CONTROL's "
            f"({delta.absolute_success_delta_pp:+.1f} pp over {delta.pairs} pairs). The mix is chosen, "
            "not sampled from production, so the number describes the campaign, not a deployment."
        )
    by = result.by_scenario
    gains = [s for s, arms in by.items() if arms[FULL].task_correct > arms[CONTROL].task_correct] if FULL in result.arms else []
    losses = [s for s, arms in by.items() if arms[FULL].task_correct < arms[CONTROL].task_correct] if FULL in result.arms else []
    if gains:
        lines.append(f"- AERIS_FULL was correct more often than CONTROL in: {', '.join(gains)}.")
    if losses:
        lines.append(
            f"- AERIS_FULL was correct less often than CONTROL in: {', '.join(losses)}. "
            "Read the traces before attributing a cause."
        )
    diversity = result.deltas.get(ExperimentArm.AERIS_NO_DIVERSITY.value)
    if diversity and delta:
        lines.append(
            "- Diversity ablation: compare reroutes and latency in the failure_domain scenarios; "
            f"AERIS_NO_DIVERSITY median latency overhead {_num(diversity.median_latency_overhead_ms, 'delta ms')} "
            f"vs AERIS_FULL {_num(delta.median_latency_overhead_ms, 'delta ms')}."
        )
    gate = result.deltas.get(ExperimentArm.AERIS_NO_SIDE_EFFECT_GATE.value)
    if gate and delta:
        lines.append(
            f"- Side-effect gate ablation: duplicates avoided vs CONTROL {delta.duplicate_side_effects_avoided:+d} "
            f"with the gate, {gate.duplicate_side_effects_avoided:+d} without it."
        )
    return lines


def render_markdown(result: CampaignResult) -> str:
    first = result.records[0].config if result.records else None
    lines = [
        "# AERIS support-case benchmark report",
        "",
        f"- experiment: `{result.experiment_version}`",
        f"- model: `{result.model}` (stochastic: {result.stochastic_model})",
        f"- seeds: {result.seeds}",
        f"- arms: {', '.join(result.arms)}",
    ]
    if first is not None:
        lines += [
            f"- runtime adapter: `{first.runtime_adapter_version}`, scenario set: `{first.scenario_version}`, "
            f"tool fixtures: `{first.tool_fixture_version}`, evaluator: `{first.evaluator_version}`",
            f"- agent prompt hash: `{first.agent_prompt_hash}`",
        ]
    lines += ["", "Per-arm configuration identity:", ""]
    lines += ["| arm | policy_version | policy_hash | planner_weights_hash | baseline |", "|---|---|---|---|---|"]
    for arm, config in _configuration(result).items():
        lines.append(
            f"| {arm} | {config['policy_version']} | `{config['policy_hash']}` "
            f"| `{config['planner_weights_hash']}` | `{config.get('baseline_fingerprint') or '-'}` |"
        )
    if result.calibration is not None:
        cal = result.calibration
        lines += [
            "",
            f"Calibration: {len(cal.baselines)} baselines learned from healthy CONTROL flights on seeds "
            f"{cal.seeds[0]}..{cal.seeds[-1]} (fingerprint `{cal.fingerprint}`). Validation seeds "
            f"{cal.validation_seeds[0] if cal.validation_seeds else '-'}..: "
            f"{cal.validation_false_alarm_flights}/{cal.validation_flights} healthy flights changed by AERIS_FULL. "
            "Test seeds never overlap either range.",
        ]
    lines += ["", "## Statistical status", ""]
    if not result.stochastic_model:
        lines.append(
            "This run used a deterministic fixture model. Repeating it does not create independent samples, "
            "so no confidence interval or significance claim is made. Counts below are exact for this fixture."
        )
    else:
        notes = {delta.ci_note for delta in result.deltas.values() if delta.ci_note}
        lines.append(
            "Stochastic model. Confidence intervals are paired percentile bootstraps and are reported only "
            "when the protocol's minimum pair count is met. " + " ".join(sorted(notes))
        )
        for name, delta in result.deltas.items():
            if delta.success_delta_ci is not None:
                ci = delta.success_delta_ci
                lines.append(
                    f"- {name}: success delta {ci.estimate * 100:+.1f} pp, 95% CI [{ci.low * 100:+.1f}, "
                    f"{ci.high * 100:+.1f}] (n={ci.n})"
                )
            if delta.latency_overhead_ci is not None:
                ci = delta.latency_overhead_ci
                lines.append(
                    f"- {name}: median latency overhead {ci.estimate:+.0f} ms, 95% CI [{ci.low:+.0f}, {ci.high:+.0f}]"
                )
        lines.append(
            "An interval excluding zero is statistical evidence for this scenario mix only; engineering "
            "significance depends on the overhead and harm columns."
        )

    lines += ["", "## Observed facts", "", "### Outcomes per arm", ""]
    lines += _arm_table(result.overall)
    lines += ["", "### Paired deltas vs CONTROL", ""]
    lines += _delta_table(result.deltas)
    lines += ["", "### Intervention utility (derived from paired outcomes)", ""]
    lines += _utility_table(result.overall)
    lines += ["", "### Detection (event-level fault matching)", ""]
    lines += _detection_table(result.overall)
    lines += ["", "### Per scenario", ""]
    lines += _scenario_table(result)
    lines += ["", "### Negative and unfavourable results", ""]
    lines += _negative_results(result)
    lines += ["", "## Interpretation", ""]
    lines += _interpretation(result)
    lines += [
        "",
        "## Limitations",
        "",
        "- One agent architecture (a direct tool-calling loop) and one case study.",
        "- Tool faults are synthetic and injected by a proxy; real outages are messier and correlated.",
        "- CONTROL is one baseline (agent-owned retries, no supervisor). A stronger baseline, e.g. a "
        "retry-with-backoff wrapper, may close part of the gap.",
        "- Task correctness is checked against four fixed facts; answer quality beyond them is not scored.",
        "- Contextual baselines come from a small calibration set of healthy flights.",
        "- Latency for tools is virtual (declared, not measured); model latency is measured when live.",
        "- Intervention utility is per flight, not per decision.",
    ]
    if not result.stochastic_model:
        lines.append("- The scripted fixture model is not an LLM. It exercises the control path, not LLM behaviour.")
    if result.sample_traces:
        lines += ["", "## Sample traces (first seed)", ""]
        for key in sorted(result.sample_traces)[:6]:
            lines += [f"### {key}", "", "```text", result.sample_traces[key], "```", ""]
    return "\n".join(lines) + "\n"


def render_flight_report(control: TrialRun, aeris: TrialRun, *, scenario: str) -> str:
    """Side-by-side report for one CONTROL/AERIS pair. Not statistical evidence."""

    def block(run: TrialRun) -> list[str]:
        row = run.record
        return [
            f"state          {row.terminal_state.value}",
            f"task correct   {row.task_correct}   (checks: {row.evaluator_checks})",
            f"latency        {row.latency_ms:,.0f} ms",
            f"cost           ${row.total_cost:.5f}  (model ${row.model_cost:.5f}, tools ${row.tool_cost:.5f})",
            f"tokens         in={row.input_tokens} out={row.output_tokens}  tool calls={row.tool_calls}",
            f"hazards        {row.hazards}",
            f"route path     {' > '.join(row.route_path)}",
            f"interventions  {' '.join(row.intervention_actions) or 'none'}",
            f"credits        {row.credit_entries} ledger entr{'y' if row.credit_entries == 1 else 'ies'}"
            f"  (idempotent replays: {row.idempotent_replays})",
            f"MTTD / MTTI / MTTR  {_num(row.mttd_ms, ' ms')} / {_num(row.mtti_ms, ' ms')} / {_num(row.mttr_ms, ' ms')}",
        ]

    c, a = control.record, aeris.record
    outcome = a.intervention_outcome.value if isinstance(a.intervention_outcome, InterventionOutcome) else "none"
    lines = [
        "AERIS LIVE FLIGHT REPORT",
        "========================",
        "",
        f"Model:     {c.config.model} {c.config.model_versions or ''} (stochastic: {c.config.stochastic_model})",
        f"Scenario:  {scenario}",
        f"Seed:      {c.seed}   temperature: {c.config.temperature}",
        f"Hashes:    policy={a.config.policy_hash} routes={a.config.route_config_hash} "
        f"prompt={a.config.agent_prompt_hash}",
        "",
        "CONTROL:",
        *("  " + line for line in block(control)),
        "",
        f"{a.arm.value}:",
        *("  " + line for line in block(aeris)),
        "",
        "Delta:",
        f"  task correct   {int(a.task_correct) - int(c.task_correct):+d}",
        f"  latency        {a.latency_ms - c.latency_ms:+,.0f} ms",
        f"  cost           {a.total_cost - c.total_cost:+.5f} USD",
        f"  duplicate credits  {max(a.credit_entries - 1, 0) - max(c.credit_entries - 1, 0):+d}",
        f"  intervention outcome  {outcome} ({a.attribution_note})",
        "",
        "One pair is an anecdote, not a measurement. Run the campaign for rates.",
    ]
    return "\n".join(lines)
