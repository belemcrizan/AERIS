"""LLMToolAgentRuntime: the one stochastic runtime adapter.

A direct model-API loop with function calling. It is deliberately not an
agent framework: each waypoint is a *phase* in which the model may call
the tools that phase offers. The model decides which call to make and how
to answer; AERIS only sees the resulting StepObservation.

Two rules keep AERIS external to the reasoning loop:
- By default a tool error ends the waypoint and surfaces to the control
  plane, so retry policy is observable. With ``agent_owns_retries=True``
  (the CONTROL arm) the error goes back to the model instead, which may
  call the tool again on its own, as an unsupervised tool-calling agent would.
- Idempotency keys are injected by the runtime, never chosen by the model,
  and only for waypoints whose tool supports them.

Side-effect honesty: when a write request left the client but no response
came back, the runtime reports ``side_effect_committed=True`` (it *may*
have committed). Treating unknown as committed is what makes the safety
gate conservative.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from aeris.adapters.llm import ChatModel
from aeris.adapters.runtime import ExecutionContext, RuntimeCapabilities
from aeris.core.enums import SideEffectClass
from aeris.core.hashing import stable_hash
from aeris.core.ids import new_id
from aeris.core.models import FaultInstance, StepObservation

RUNTIME_ADAPTER_VERSION = "llm-tool-agent/1.0.0"


class PhaseConfig(BaseModel):
    instruction: str
    extra_tools: list[str] = Field(default_factory=list)
    tool_optional: bool = False
    final: bool = False
    max_turns: int = 3


class ToolInvocation(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    flight_id: str
    route_name: str
    waypoint_name: str
    attempt: int = 0
    idempotency_key: str | None = None


class ToolResult(BaseModel):
    ok: bool
    content: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    timeout: bool = False
    request_sent: bool = True
    committed: bool = False
    replayed: bool = False
    freshness_s: float | None = None
    confidence: float | None = None
    pending: bool = False
    cost: float = 0.0
    faults: list[FaultInstance] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)


class ToolExecutor(Protocol):
    def schemas(self, names: list[str]) -> list[dict[str, Any]]: ...

    async def invoke(self, call: ToolInvocation) -> ToolResult: ...


class _StepTotals(BaseModel):
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_cost: float = 0.0
    tool_cost: float = 0.0
    tool_calls: int = 0
    faults: list[FaultInstance] = Field(default_factory=list)
    confidence: float | None = None
    freshness_s: float | None = None
    last_action: str = ""
    last_tool: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)


class LLMToolAgentRuntime:
    def __init__(
        self,
        model: ChatModel,
        executor: ToolExecutor,
        *,
        system_prompt: str,
        mission_prompt: str,
        phases: dict[str, PhaseConfig],
        temperature: float = 0.0,
        seed: int | None = None,
        runtime_kind: str = "llm-tool-agent",
        can_cancel: bool = True,
        agent_owns_retries: bool = False,
    ) -> None:
        self.agent_owns_retries = agent_owns_retries
        self.model = model
        self.executor = executor
        self.system_prompt = system_prompt
        self.mission_prompt = mission_prompt
        self.phases = phases
        self.temperature = temperature
        self.seed = seed
        self.runtime_kind = runtime_kind
        self.can_cancel = can_cancel
        self.conversations: dict[str, list[dict[str, Any]]] = {}
        self.answers: dict[str, dict[str, Any] | None] = {}
        self.tool_log: dict[str, list[dict[str, Any]]] = {}
        self.model_versions: set[str] = set()
        self._satisfied: dict[str, set[str]] = {}
        self._last_route: dict[str, str] = {}

    def prompt_hash(self) -> str:
        return stable_hash(
            {
                "system": self.system_prompt,
                "mission": self.mission_prompt,
                "phases": {name: phase.model_dump() for name, phase in sorted(self.phases.items())},
            }
        )

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            can_cancel=self.can_cancel,
            can_retry=True,
            can_compensate=False,
            supports_idempotency=True,
            supports_progress=True,
            supports_streaming=False,
        )

    def _conversation(self, flight_id: str) -> list[dict[str, Any]]:
        if flight_id not in self.conversations:
            self.conversations[flight_id] = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.mission_prompt},
            ]
        return self.conversations[flight_id]

    async def execute_waypoint(self, context: ExecutionContext) -> StepObservation:
        flight = context.flight
        waypoint = context.waypoint
        route = flight.current_route()
        phase = self.phases[waypoint.name]
        messages = self._conversation(flight.flight_id)
        satisfied = self._satisfied.setdefault(flight.flight_id, set())
        totals = _StepTotals()

        previous_route = self._last_route.get(flight.flight_id)
        if previous_route is not None and previous_route != route.route_id:
            satisfied.clear()
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"CONTROL NOTICE: execution moved to route {route.name}. "
                        "Earlier tool results may be incomplete. Use only the tools offered in each phase."
                    ),
                }
            )
        self._last_route[flight.flight_id] = route.route_id

        allowed = [] if phase.final else [*phase.extra_tools, *([waypoint.tool] if waypoint.tool else [])]
        messages.append(
            {
                "role": "user",
                "content": f"PHASE {waypoint.name}: {phase.instruction}\nTools available now: {allowed or 'none'}",
            }
        )
        schemas = self.executor.schemas(allowed)
        writes = waypoint.side_effect is not SideEffectClass.READ_ONLY
        idempotency_key: str | None = None
        if writes and waypoint.supports_idempotency:
            idempotency_key = waypoint.idempotency_key or f"{waypoint.waypoint_id}-{new_id('req')}"
        maybe_committed = False
        last_failure: ToolResult | None = None

        for _ in range(phase.max_turns):
            if self.can_cancel and context.should_stop():
                return self._observation(context, totals, success=False, error="cancelled_during_execution", cancelled=True)
            response = await self.model.complete(
                messages, tools=schemas, temperature=self.temperature, seed=self.seed
            )
            totals.latency_ms += response.latency_ms
            totals.input_tokens += response.input_tokens
            totals.output_tokens += response.output_tokens
            totals.model_cost += self.model.pricing.cost(response.input_tokens, response.output_tokens)
            if response.model_version:
                self.model_versions.add(response.model_version)
            if response.error:
                return self._observation(
                    context, totals, success=False, error=f"invalid_runtime_output: {response.error}"
                )

            if response.tool_calls:
                call = response.tool_calls[0]
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": call.raw_arguments or json.dumps(call.arguments)},
                            }
                        ],
                    }
                )
                totals.last_action = f"{route.name}:{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                if call.name not in allowed:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": json.dumps({"error": f"tool {call.name} is not available in this phase"}),
                        }
                    )
                    continue
                result = await self.executor.invoke(
                    ToolInvocation(
                        name=call.name,
                        arguments=call.arguments,
                        flight_id=flight.flight_id,
                        route_name=route.name,
                        waypoint_name=waypoint.name,
                        attempt=context.attempt,
                        idempotency_key=idempotency_key if call.name == waypoint.tool else None,
                    )
                )
                totals.tool_calls += 1
                totals.last_tool = call.name
                totals.dependency_ids = list(result.dependency_ids)
                totals.latency_ms += result.latency_ms
                totals.tool_cost += result.cost
                totals.faults.extend(result.faults)
                if result.confidence is not None:
                    totals.confidence = (
                        result.confidence if totals.confidence is None else min(totals.confidence, result.confidence)
                    )
                if result.freshness_s is not None:
                    totals.freshness_s = max(totals.freshness_s or 0.0, result.freshness_s)
                content = result.content if result.ok else {"error": result.error}
                self.tool_log.setdefault(flight.flight_id, []).append(
                    {
                        "route": route.name,
                        "waypoint": waypoint.name,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "ok": result.ok,
                        "content": content,
                    }
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.call_id, "content": json.dumps(content, default=str)}
                )
                if not result.ok:
                    if writes and call.name == waypoint.tool and (result.committed or result.request_sent):
                        maybe_committed = True
                    if self.agent_owns_retries:
                        last_failure = result
                        continue
                    return self._observation(
                        context,
                        totals,
                        success=False,
                        error=result.error,
                        tool_error=not result.timeout,
                        timeout=result.timeout,
                        committed=maybe_committed,
                        idempotency_key=idempotency_key,
                    )
                if result.pending:
                    continue
                if call.name == waypoint.tool:
                    satisfied.add(waypoint.name)
                    return self._observation(
                        context,
                        totals,
                        success=True,
                        output=json.dumps(content, default=str)[:500],
                        committed=writes and (result.committed or maybe_committed),
                        duplicate_suppressed=result.replayed,
                        idempotency_key=idempotency_key,
                    )
                continue

            text = response.content or ""
            messages.append({"role": "assistant", "content": text})
            totals.last_action = f"{route.name}:{waypoint.name}:text"
            if phase.final:
                self.answers[flight.flight_id] = _parse_answer(text)
                satisfied.add(waypoint.name)
                return self._observation(context, totals, success=True, output=text[:500])
            if phase.tool_optional:
                satisfied.add(waypoint.name)
                return self._observation(context, totals, success=True, output=text[:500])
            messages.append(
                {"role": "user", "content": f"This phase requires calling {waypoint.tool}."}
            )

        if last_failure is not None:
            return self._observation(
                context,
                totals,
                success=False,
                error=last_failure.error,
                tool_error=not last_failure.timeout,
                timeout=last_failure.timeout,
                committed=maybe_committed,
                idempotency_key=idempotency_key,
            )
        return self._observation(
            context, totals, success=False, error="phase_incomplete", repeat=True, committed=maybe_committed
        )

    def _observation(
        self,
        context: ExecutionContext,
        totals: _StepTotals,
        *,
        success: bool,
        error: str | None = None,
        output: str | None = None,
        tool_error: bool = False,
        timeout: bool = False,
        cancelled: bool = False,
        committed: bool = False,
        duplicate_suppressed: bool = False,
        idempotency_key: str | None = None,
        repeat: bool = False,
    ) -> StepObservation:
        flight = context.flight
        waypoint = context.waypoint
        route = flight.current_route()
        satisfied = self._satisfied.get(flight.flight_id, set())
        progress = len(satisfied & {item.name for item in route.waypoints}) / max(len(route.waypoints), 1)
        return StepObservation(
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            route_id=route.route_id,
            waypoint_id=waypoint.waypoint_id,
            waypoint_index=waypoint.index,
            latency_ms=totals.latency_ms,
            success=success,
            tool_error=tool_error or (not success and not timeout and not cancelled and error == "phase_incomplete"),
            timeout=timeout,
            token_usage=totals.input_tokens + totals.output_tokens,
            confidence=totals.confidence,
            data_freshness_s=totals.freshness_s,
            progress=progress,
            action=totals.last_action or f"{route.name}:{waypoint.name}",
            output=output,
            error=error,
            repeat_signal=repeat,
            side_effect_class=waypoint.side_effect,
            side_effect_committed=committed,
            idempotency_key=idempotency_key if waypoint.side_effect is not SideEffectClass.READ_ONLY else None,
            duplicate_suppressed=duplicate_suppressed,
            fault_injected=bool(totals.faults),
            fault_kind=totals.faults[0].fault_type.value.lower() if totals.faults else None,
            cancelled=cancelled,
            faults=totals.faults,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            tool_calls=totals.tool_calls,
            model_cost=totals.model_cost,
            tool_cost=totals.tool_cost,
            tool=totals.last_tool or waypoint.tool,
            dependency_ids=totals.dependency_ids,
        )


def _parse_answer(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
