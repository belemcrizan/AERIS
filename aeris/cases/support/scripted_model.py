"""ScriptedSupportModel: an offline, seeded stand-in for the live LLM.

It is NOT evidence about LLM behaviour. It exists so the case study, the
fault campaign and every metric path run in CI without a key. Its policy
is intentionally simple and naive:

- in a tool phase, call each offered tool once (extra tools first);
- after a tool error, call the same tool again (typical agent behaviour);
- in the final phase, answer from the most recent tool output for each
  fact, including stale data. It does not second-guess tool output.

Latency is seeded log-normal jitter around a base, so paired arms with
the same seed see the same draws until their trajectories diverge.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

from aeris.adapters.llm import ModelPricing, ModelResponse, ToolCall, estimate_tokens

SCRIPTED_MODEL_VERSION = "scripted-support/1.0.0"


class ScriptedSupportModel:
    stochastic = False

    def __init__(
        self,
        *,
        seed: int = 0,
        base_latency_ms: float = 700.0,
        jitter_sigma: float = 0.25,
        pricing: ModelPricing | None = None,
    ) -> None:
        self.name = "scripted-support"
        self.pricing = pricing or ModelPricing()
        self._rng = random.Random(seed)
        self._base_latency_ms = base_latency_ms
        self._sigma = jitter_sigma
        self._calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        temperature: float,
        seed: int | None,
    ) -> ModelResponse:
        self._calls += 1
        latency = self._base_latency_ms * math.exp(self._rng.gauss(0.0, self._sigma))
        offered = [tool["function"]["name"] for tool in tools]
        phase_start = _last_phase_index(messages)
        phase_messages = messages[phase_start:]
        succeeded, failed_last = _phase_tool_state(phase_messages)

        if offered:
            target = failed_last if failed_last in offered else None
            if target is None:
                target = next((name for name in offered if name not in succeeded), None)
            if target is not None:
                arguments = _arguments_for(target, messages)
                return self._response(
                    messages,
                    tool_calls=[
                        ToolCall(
                            call_id=f"call_{self._calls}",
                            name=target,
                            arguments=arguments,
                            raw_arguments=json.dumps(arguments),
                        )
                    ],
                    latency=latency,
                )
            return self._response(messages, content="Phase complete.", latency=latency)

        answer = _compose_answer(messages)
        return self._response(messages, content=json.dumps(answer), latency=latency)

    def _response(
        self,
        messages: list[dict[str, Any]],
        *,
        latency: float,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> ModelResponse:
        output = content or json.dumps([call.model_dump() for call in tool_calls or []])
        return ModelResponse(
            content=content,
            tool_calls=tool_calls or [],
            input_tokens=estimate_tokens(messages),
            output_tokens=estimate_tokens(output),
            latency_ms=latency,
            model=self.name,
            model_version=SCRIPTED_MODEL_VERSION,
            usage_reported=False,
        )


def _last_phase_index(messages: list[dict[str, Any]]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "user" and str(message.get("content", "")).startswith("PHASE "):
            return index
    return 0


def _phase_tool_state(messages: list[dict[str, Any]]) -> tuple[set[str], str | None]:
    names: dict[str, str] = {}
    succeeded: set[str] = set()
    failed_last: str | None = None
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        elif message.get("role") == "tool":
            name = names.get(message.get("tool_call_id", ""))
            payload = _load(message.get("content"))
            if isinstance(payload, dict) and "error" in payload:
                failed_last = name
            else:
                if name:
                    succeeded.add(name)
                failed_last = None
    return succeeded, failed_last


def _load(text: Any) -> Any:
    try:
        return json.loads(text) if isinstance(text, str) else text
    except json.JSONDecodeError:
        return None


def _tool_outputs(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest successful output per tool name, across the whole conversation."""
    names: dict[str, str] = {}
    outputs: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        elif message.get("role") == "tool":
            payload = _load(message.get("content"))
            name = names.get(message.get("tool_call_id", ""))
            if name and isinstance(payload, dict) and "error" not in payload:
                outputs[name] = payload
    return outputs


def _arguments_for(tool: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    outputs = _tool_outputs(messages)
    account = outputs.get("get_customer_account", {})
    if tool == "get_customer_account":
        return {"customer_id": "CUST-1042"}
    if tool in {"get_service_status", "get_telemetry_status", "query_incident_database", "lookup_cached_incident"}:
        services = account.get("services") or ["svc-payments-eu"]
        return {"service_id": services[0]}
    if tool in {"search_runbook", "search_local_runbook"}:
        return {"query": "EU payments outage failover and SLA credit"}
    if tool == "apply_service_credit":
        fee = float(account.get("monthly_fee_usd") or 0.0)
        return {
            "customer_id": account.get("customer_id", "CUST-1042"),
            "amount_usd": round(fee * 0.10, 2),
            "reason": "SLA credit for outage over 60 minutes",
        }
    return {}


def _latest_with(messages: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Most recent successful tool output that carries ``field``, whichever tool produced it."""
    names: dict[str, str] = {}
    latest: dict[str, Any] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        elif message.get("role") == "tool" and names.get(message.get("tool_call_id", "")):
            payload = _load(message.get("content"))
            if isinstance(payload, dict) and "error" not in payload and field in payload:
                latest = payload
    return latest


def _compose_answer(messages: list[dict[str, Any]]) -> dict[str, Any]:
    outputs = _tool_outputs(messages)
    status = _latest_with(messages, "state")
    incident = _latest_with(messages, "incident_id")
    runbook = _latest_with(messages, "recommended_action")
    credit = outputs.get("apply_service_credit")
    return {
        "service_state": status.get("state", "UNKNOWN"),
        "incident_id": incident.get("incident_id"),
        "recommended_action": runbook.get("recommended_action"),
        "credit_applied": credit is not None,
        "credit_amount_usd": credit.get("amount_usd") if credit else None,
        "summary": "Diagnosis assembled from tool outputs.",
    }
