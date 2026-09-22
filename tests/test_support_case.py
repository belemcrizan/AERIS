"""Enterprise support case: ledger, fault proxy, runtime rules, task evaluator."""

from __future__ import annotations

import json

import pytest
from support_helpers import SupportRig

from aeris.adapters.llm import ModelPricing, OpenAIChatModel, live_enabled
from aeris.adapters.llm_runtime import ToolInvocation
from aeris.cases.support.campaign import campaign
from aeris.cases.support.evaluator import evaluate
from aeris.cases.support.toolbox import FaultSpec, SupportToolbox
from aeris.cases.support.world import GroundTruth, SandboxCreditLedger
from aeris.core.enums import ExecutionState, FaultType, InterventionMode


def test_ledger_replays_a_reused_key_and_duplicates_without_one():
    ledger = SandboxCreditLedger()
    first, replay_a = ledger.apply(customer_id="C", amount_usd=10, idempotency_key="k", flight_id="f")
    second, replay_b = ledger.apply(customer_id="C", amount_usd=10, idempotency_key="k", flight_id="f")
    assert first.entry_id == second.entry_id and (replay_a, replay_b) == (False, True)
    ledger.apply(customer_id="C", amount_usd=10, idempotency_key=None, flight_id="f")
    ledger.apply(customer_id="C", amount_usd=10, idempotency_key=None, flight_id="f")
    assert len(ledger.entries_for("f")) == 3 and ledger.replays == 1


def _call(name: str, **arguments) -> ToolInvocation:
    return ToolInvocation(name=name, arguments=arguments, flight_id="f", route_name="alpha", waypoint_name="w")


async def test_fault_proxy_targets_dependencies_and_counts_calls():
    toolbox = SupportToolbox(
        faults=[FaultSpec(schedule_id="s", fault_type=FaultType.TRANSIENT_FAILURE, target="svc:status_api_a", calls=[1])]
    )
    first = await toolbox.invoke(_call("get_service_status", service_id="x"))
    second = await toolbox.invoke(_call("get_service_status", service_id="x"))
    other = await toolbox.invoke(_call("get_telemetry_status", service_id="x"))
    assert not first.ok and first.faults[0].schedule_id == "s"
    assert second.ok and not second.faults
    assert other.ok
    assert "svc:status_api_a" in first.dependency_ids


async def test_lost_response_after_commit_really_commits():
    toolbox = SupportToolbox(
        faults=[FaultSpec(schedule_id="s", fault_type=FaultType.LOST_RESPONSE_AFTER_COMMIT, target="svc:billing_ledger", calls=[1])]
    )
    result = await toolbox.invoke(_call("apply_service_credit", customer_id="C", amount_usd=1200, reason="r"))
    assert not result.ok and result.committed and result.request_sent
    assert len(toolbox.ledger.entries) == 1


async def test_latency_is_virtual_and_reported():
    toolbox = SupportToolbox(
        faults=[FaultSpec(schedule_id="s", fault_type=FaultType.LATENCY, target="svc:status_api_a", latency_ms=8000)]
    )
    result = await toolbox.invoke(_call("get_service_status", service_id="x"))
    assert result.ok and result.latency_ms >= 8000


def test_evaluator_scores_facts_not_terminal_state():
    ledger = SandboxCreditLedger()
    ledger.apply(customer_id="CUST-1042", amount_usd=1200, idempotency_key="k", flight_id="f")
    truth = GroundTruth()
    answer = {
        "service_state": truth.service_state,
        "incident_id": truth.incident_id,
        "recommended_action": truth.recommended_action,
        "credit_applied": True,
    }
    good = evaluate(answer, flight_id="f", ledger=ledger, completed=True)
    assert good.task_correct and not good.unsafe_action
    wrong = evaluate({**answer, "incident_id": "INC-3107"}, flight_id="f", ledger=ledger, completed=True)
    assert wrong.task_complete and not wrong.task_correct
    not_completed = evaluate(answer, flight_id="f", ledger=ledger, completed=False)
    assert not not_completed.task_correct


def test_evaluator_flags_duplicate_credit_and_unsupported_claims():
    ledger = SandboxCreditLedger()
    for _ in range(2):
        ledger.apply(customer_id="CUST-1042", amount_usd=1200, idempotency_key=None, flight_id="f")
    truth = GroundTruth()
    answer = {"service_state": truth.service_state, "incident_id": truth.incident_id,
              "recommended_action": truth.recommended_action, "credit_applied": True}
    score = evaluate(answer, flight_id="f", ledger=ledger, completed=True)
    assert score.duplicate_action and score.unsafe_action and not score.task_correct
    empty = SandboxCreditLedger()
    claim = evaluate(answer, flight_id="f", ledger=empty, completed=True)
    assert claim.unsupported_claim


async def test_unsafe_baseline_duplicates_the_credit_and_the_gate_prevents_it():
    control = SupportRig(
        scenario="credit_lost_response_unkeyed", mode=InterventionMode.CONTROL, agent_owns_retries=True
    )
    await control.run()
    assert len(control.toolbox.ledger.entries) == 2

    aeris = SupportRig(scenario="credit_lost_response_unkeyed")
    await aeris.run()
    assert len(aeris.toolbox.ledger.entries) == 1
    assert aeris.flight.state is ExecutionState.ABORTED


async def test_keyed_credit_retry_reuses_the_same_idempotency_key():
    rig = SupportRig(scenario="credit_lost_response_keyed")
    await rig.run()
    keys = [call.idempotency_key for call in rig.toolbox.calls if call.name == "apply_service_credit"]
    assert len(keys) == 2 and len(set(keys)) == 1
    assert len(rig.toolbox.ledger.entries) == 1 and rig.toolbox.ledger.replays == 1
    assert rig.flight.state is ExecutionState.COMPLETED


async def test_runtime_never_sends_keys_to_read_only_tools():
    rig = SupportRig()
    await rig.run()
    assert all(call.idempotency_key is None for call in rig.toolbox.calls if call.name != "apply_service_credit")


async def test_cost_and_tokens_are_accounted_per_flight():
    rig = SupportRig()
    await rig.run()
    flight = rig.flight
    assert flight.input_tokens > 0 and flight.output_tokens > 0
    assert flight.tool_calls == len(rig.toolbox.calls)
    pricing = rig.model.pricing
    assert flight.model_cost == pytest.approx(pricing.cost(flight.input_tokens, flight.output_tokens))
    assert flight.tool_cost > 0


def test_campaign_has_negative_controls_side_effect_and_failure_domain_cases():
    categories = {scenario.category for scenario in campaign().values()}
    assert {"baseline", "positive", "negative_control", "side_effect", "failure_domain"} <= categories
    assert len({scenario.scenario_id for scenario in campaign().values()}) == len(campaign())


def test_live_adapter_is_opt_in(monkeypatch):
    monkeypatch.delenv("AERIS_LIVE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert not live_enabled()
    monkeypatch.setenv("AERIS_LIVE", "1")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert not live_enabled()
    with pytest.raises(ValueError):
        OpenAIChatModel("m", api_key="")


async def test_openai_client_parses_tool_calls_and_usage_without_network(monkeypatch):
    model = OpenAIChatModel("m", api_key="k", pricing=ModelPricing(input_per_million=1, output_per_million=2))
    reply = {
        "model": "m-2026",
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "search_runbook", "arguments": json.dumps({"query": "q"})}}
        ]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    monkeypatch.setattr(model, "_post", lambda body: reply)
    response = await model.complete([{"role": "user", "content": "hi"}], tools=[], temperature=0.0, seed=1)
    assert response.tool_calls[0].name == "search_runbook"
    assert response.tool_calls[0].arguments == {"query": "q"}
    assert (response.input_tokens, response.output_tokens, response.usage_reported) == (100, 10, True)
    assert response.model_version == "m-2026"

    def boom(body):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(model, "_post", boom)
    failed = await model.complete([], tools=[], temperature=0.0, seed=None)
    assert failed.error and "HTTP 500" in failed.error
