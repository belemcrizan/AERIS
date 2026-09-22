"""Paired experiment runner, utility labels, stats gating, config identity, reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from aeris.cases.support.campaign import campaign
from aeris.cases.support.scripted_model import ScriptedSupportModel
from aeris.core.enums import ExperimentArm, InterventionOutcome
from aeris.core.hashing import stable_hash
from aeris.evaluation.pairing import TrialOutcome, classify_intervention
from aeris.experiments.report import render_markdown, write_results
from aeris.experiments.stats import MIN_PAIRS_FOR_CI, bootstrap_ci, ci_allowed
from aeris.experiments.support import arm_setup, experiment_policy, run_campaign, run_trial
from aeris.policies.thresholds import ThresholdPolicy


def scripted(seed: int) -> ScriptedSupportModel:
    return ScriptedSupportModel(seed=seed)


def test_stable_hash_ignores_key_order_and_tracks_values():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
    assert ThresholdPolicy().policy_hash() == ThresholdPolicy().policy_hash()
    assert ThresholdPolicy().policy_hash() != ThresholdPolicy(high_latency_ms=1999).policy_hash()


def test_ablation_arms_change_exactly_their_feature():
    base = experiment_policy()
    no_gate = arm_setup(ExperimentArm.AERIS_NO_SIDE_EFFECT_GATE, base, have_baselines=True)
    no_div = arm_setup(ExperimentArm.AERIS_NO_DIVERSITY, base, have_baselines=True)
    no_ctx = arm_setup(ExperimentArm.AERIS_NO_CONTEXT, base, have_baselines=True)
    control = arm_setup(ExperimentArm.CONTROL, base, have_baselines=True)
    assert not no_gate.policy.side_effect_gate and no_gate.contextual
    assert not no_div.use_diversity and not no_div.policy.avoid_shared_failure_domain
    assert not no_ctx.contextual
    assert control.agent_owns_retries and not arm_setup(ExperimentArm.AERIS_FULL, base, have_baselines=True).agent_owns_retries


def _outcome(**kwargs) -> TrialOutcome:
    defaults = {"flight_id": "f", "arm": "x", "acceptable": True, "completed": True, "interventions": 1,
                "manifested_schedule_ids": ["s"], "pre_intervention_schedule_ids": ["s"]}
    return TrialOutcome(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("control", "aeris", "expected"),
    [
        ({"acceptable": False}, {}, InterventionOutcome.BENEFICIAL),
        ({}, {"acceptable": False}, InterventionOutcome.HARMFUL),
        ({}, {}, InterventionOutcome.NEUTRAL),
        ({"acceptable": False, "duplicate_side_effects": 1}, {"acceptable": False}, InterventionOutcome.BENEFICIAL),
        ({"manifested_schedule_ids": []}, {}, InterventionOutcome.UNRESOLVED),
    ],
)
def test_intervention_utility_comes_from_the_pair(control, aeris, expected):
    label, note = classify_intervention(_outcome(**control), _outcome(**aeris))
    assert label is expected and note


def test_no_intervention_gets_no_label():
    assert classify_intervention(_outcome(), _outcome(interventions=0))[0] is None


def test_bootstrap_is_gated_on_stochasticity_and_sample_count():
    assert not ci_allowed(100, stochastic=False)[0]
    assert not ci_allowed(MIN_PAIRS_FOR_CI - 1, stochastic=True)[0]
    assert ci_allowed(MIN_PAIRS_FOR_CI, stochastic=True)[0]
    interval = bootstrap_ci([1.0] * 20 + [0.0] * 20, resamples=500)
    assert interval.low <= interval.estimate <= interval.high
    assert bootstrap_ci([]) is None


async def test_trial_records_configuration_identity_and_costs():
    run = await run_trial(campaign()["healthy"], ExperimentArm.AERIS_FULL, seed=3, model_factory=scripted)
    config = run.record.config
    for value in (config.policy_hash, config.route_config_hash, config.agent_prompt_hash, config.planner_weights_hash):
        assert value and len(value) == 16
    assert config.policy_version and config.runtime_adapter_version and config.scenario_version
    assert config.seed == 3 and config.stochastic_model is False
    assert run.record.total_cost == pytest.approx(run.record.model_cost + run.record.tool_cost)
    assert run.record.task_correct


async def test_campaign_pairs_arms_keeps_negative_results_and_writes_outputs(tmp_path: Path):
    ids = ["healthy", "status_timeout", "credit_lost_response_unkeyed", "false_confidence_bad_fallback"]
    result = await run_campaign(scripted, scenario_ids=ids, seeds=[0])
    by = result.by_scenario
    assert by["status_timeout"][ExperimentArm.AERIS_FULL.value].utility["beneficial"] == 1
    assert by["false_confidence_bad_fallback"][ExperimentArm.AERIS_FULL.value].utility["harmful"] == 1
    gate = by["credit_lost_response_unkeyed"]
    assert gate[ExperimentArm.CONTROL.value].duplicate_side_effects == 1
    assert gate[ExperimentArm.AERIS_FULL.value].duplicate_side_effects == 0
    assert gate[ExperimentArm.AERIS_NO_SIDE_EFFECT_GATE.value].duplicate_side_effects == 1
    assert by["healthy"][ExperimentArm.AERIS_FULL.value].interventions == 0
    assert result.calibration is not None and result.calibration.baselines
    assert all(delta.success_delta_ci is None for delta in result.deltas.values())

    paths = write_results(result, tmp_path)
    rows = [json.loads(line) for line in paths["trials"].read_text().splitlines()]
    assert len(rows) == len(ids) * len(result.arms)
    aggregate = json.loads(paths["aggregate"].read_text())
    assert set(aggregate["configuration"]) == set(result.arms)
    with paths["paired"].open() as handle:
        assert len(list(csv.DictReader(handle))) == len(ids) * (len(result.arms) - 1)
    report = paths["report"].read_text(encoding="utf-8")
    for heading in ("## Observed facts", "## Interpretation", "## Limitations", "Negative and unfavourable"):
        assert heading in report
    assert "false_confidence_bad_fallback" in report.split("### Negative and unfavourable results")[1]
    assert "no confidence interval" in report
    assert render_markdown(result) == report
