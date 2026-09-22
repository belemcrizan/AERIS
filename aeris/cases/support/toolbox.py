"""Support-case tools and the fault-injection proxy around them.

Faults are injected *outside* the model: the proxy sits between the
runtime and the fixture, so the same schedule can be replayed against
CONTROL and AERIS. A fault targets a dependency id (``svc:...``) or a
tool id (``tool:...``). Every tool declares its dependencies, which is how
a shared-service outage reaches two nominally different routes.

Latency is virtual: the proxy reports it and the director advances the
experiment clock. Nothing sleeps for 8 seconds.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from aeris.adapters.llm_runtime import ToolInvocation, ToolResult
from aeris.cases.support import world
from aeris.cases.support.world import SandboxCreditLedger
from aeris.core.enums import FaultType
from aeris.core.models import FaultInstance


class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    depends_on: list[str]
    base_latency_ms: float
    cost_usd: float
    provider: str
    fixture: str | None = None
    writes: bool = False

    def dependency_ids(self) -> set[str]:
        return {*self.depends_on, f"tool:{self.name}"}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _params(**props: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in props.items()},
        "required": list(props),
    }


TOOLS: dict[str, ToolDef] = {
    tool.name: tool
    for tool in [
        ToolDef(
            name="get_customer_account",
            description="Look up a customer account, tier, and monthly fee.",
            parameters=_params(customer_id="string"),
            depends_on=["svc:crm"],
            base_latency_ms=120,
            cost_usd=0.0002,
            provider="internal-crm",
            fixture="account",
        ),
        ToolDef(
            name="get_service_status",
            description="Status page API (provider A) for a service.",
            parameters=_params(service_id="string"),
            depends_on=["svc:status_api_a"],
            base_latency_ms=350,
            cost_usd=0.001,
            provider="StatusCo",
            fixture="status_primary",
        ),
        ToolDef(
            name="get_telemetry_status",
            description="Local sandbox telemetry for a service (fallback).",
            parameters=_params(service_id="string"),
            depends_on=["svc:local_telemetry"],
            base_latency_ms=600,
            cost_usd=0.002,
            provider="local",
            fixture="telemetry_fallback",
        ),
        ToolDef(
            name="query_incident_database",
            description="Primary incident database: active incident for a service.",
            parameters=_params(service_id="string"),
            depends_on=["svc:incident_db_a"],
            base_latency_ms=400,
            cost_usd=0.001,
            provider="IncidentCloud",
            fixture="incident_primary",
        ),
        ToolDef(
            name="lookup_cached_incident",
            description="Local cached incident knowledge for a service (fallback).",
            parameters=_params(service_id="string"),
            depends_on=["svc:local_incident_cache"],
            base_latency_ms=150,
            cost_usd=0.0005,
            provider="local",
            fixture="incident_cached",
        ),
        ToolDef(
            name="search_runbook",
            description="Search the hosted runbook service.",
            parameters=_params(query="string"),
            depends_on=["svc:runbook_search_api"],
            base_latency_ms=500,
            cost_usd=0.001,
            provider="SearchAPI",
            fixture="runbook_primary",
        ),
        ToolDef(
            name="search_local_runbook",
            description="Search the local runbook index (fallback).",
            parameters=_params(query="string"),
            depends_on=["svc:local_runbook_index"],
            base_latency_ms=250,
            cost_usd=0.0005,
            provider="local",
            fixture="runbook_local",
        ),
        ToolDef(
            name="apply_service_credit",
            description="Apply an SLA service credit to a customer in the sandbox ledger.",
            parameters={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "amount_usd": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["customer_id", "amount_usd", "reason"],
            },
            depends_on=["svc:billing_ledger"],
            base_latency_ms=300,
            cost_usd=0.0,
            provider="internal-billing",
            writes=True,
        ),
    ]
}

FIXTURES: dict[str, dict[str, Any]] = {
    "account": world.CUSTOMER_ACCOUNT,
    "status_primary": world.STATUS_PRIMARY,
    "telemetry_fallback": world.TELEMETRY_FALLBACK,
    "incident_primary": world.INCIDENT_ACTIVE,
    "incident_cached": world.INCIDENT_CACHED,
    "runbook_primary": world.RUNBOOK,
    "runbook_local": world.RUNBOOK_LOCAL,
}


class FaultSpec(BaseModel):
    """One scheduled fault. ``calls`` are 1-based call numbers on the target; None = every call."""

    schedule_id: str
    fault_type: FaultType
    target: str
    calls: list[int] | None = None
    latency_ms: float | None = None
    ground_truth: bool = True
    severity: str = "WARNING"
    replacement: dict[str, Any] | None = None
    confidence: float | None = None


_TIMEOUT_BUDGET_MS = 5000.0


class FaultInjector:
    def __init__(self, specs: list[FaultSpec]) -> None:
        self.specs = specs
        self._calls: dict[str, int] = {}
        self.manifestations: list[dict[str, Any]] = []

    def active(self, tool: ToolDef) -> list[tuple[FaultSpec, int]]:
        hits = []
        ids = tool.dependency_ids()
        for spec in self.specs:
            if spec.target not in ids:
                continue
            count = self._calls.get(spec.schedule_id, 0) + 1
            self._calls[spec.schedule_id] = count
            if spec.calls is None or count in spec.calls:
                hits.append((spec, count))
        return hits


class SupportToolbox:
    """ToolExecutor for the support case: fixtures + ledger + fault proxy."""

    def __init__(self, faults: list[FaultSpec] | None = None, ledger: SandboxCreditLedger | None = None) -> None:
        self.injector = FaultInjector(faults or [])
        self.ledger = ledger or SandboxCreditLedger()
        self.calls: list[ToolInvocation] = []

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        return [TOOLS[name].schema() for name in names if name in TOOLS]

    async def invoke(self, call: ToolInvocation) -> ToolResult:
        self.calls.append(call)
        tool = TOOLS.get(call.name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool {call.name}", request_sent=False)
        result = self._proxy(tool, call)
        result.dependency_ids = sorted(tool.dependency_ids())
        return result

    def _proxy(self, tool: ToolDef, call: ToolInvocation) -> ToolResult:
        hits = self.injector.active(tool)
        faults = [
            FaultInstance(
                fault_id="",
                schedule_id=spec.schedule_id,
                flight_id=call.flight_id,
                fault_type=spec.fault_type,
                ground_truth=spec.ground_truth,
                severity=spec.severity,
                target=spec.target,
                metadata={"tool": tool.name, "call_number": number},
            )
            for spec, number in hits
        ]
        self.injector.manifestations.extend(
            {"schedule_id": spec.schedule_id, "tool": tool.name, "call": number} for spec, number in hits
        )
        latency = tool.base_latency_ms
        for spec, _ in hits:
            if spec.fault_type in {FaultType.TIMEOUT}:
                return ToolResult(
                    ok=False, error="timeout: no response within 5000 ms", timeout=True,
                    latency_ms=_TIMEOUT_BUDGET_MS, request_sent=True, cost=tool.cost_usd, faults=faults,
                )
            if spec.fault_type in {FaultType.TRANSIENT_FAILURE, FaultType.PERSISTENT_FAILURE}:
                return ToolResult(
                    ok=False, error=f"503 service unavailable ({spec.target})", latency_ms=latency,
                    request_sent=False, cost=tool.cost_usd, faults=faults,
                )
            if spec.fault_type is FaultType.RATE_LIMIT:
                return ToolResult(
                    ok=False, error="429 rate limited; retry after 1s", latency_ms=latency * 0.2,
                    request_sent=False, cost=0.0, faults=faults,
                )
            if spec.fault_type is FaultType.LATENCY and spec.latency_ms is not None:
                latency += spec.latency_ms

        content = self._execute(tool, call)
        replayed = bool(content.pop("_replayed", False)) if isinstance(content, dict) else False
        committed = tool.writes and not replayed
        freshness = content.get("as_of_age_s") if isinstance(content, dict) else None
        confidence = None
        pending = False
        for spec, _ in hits:
            if spec.fault_type is FaultType.STALE_RESPONSE and spec.replacement is not None:
                content = dict(spec.replacement)
                freshness = content.get("as_of_age_s")
            elif spec.fault_type is FaultType.FALSE_CONFIDENCE:
                content = {**content, "match_confidence": spec.confidence if spec.confidence is not None else 0.12}
                confidence = content["match_confidence"]
            elif spec.fault_type is FaultType.NO_PROGRESS:
                content = {"status": "PENDING", "detail": "index rebuilding; no data yet"}
                pending = True
            elif spec.fault_type is FaultType.MALFORMED_RESPONSE:
                return ToolResult(
                    ok=False, error="malformed_response: <html>502 Bad Gateway</html>",
                    latency_ms=latency, request_sent=True, committed=committed,
                    cost=tool.cost_usd, faults=faults,
                )
            elif spec.fault_type is FaultType.LOST_RESPONSE_AFTER_COMMIT:
                return ToolResult(
                    ok=False, error="connection reset after request was sent; outcome unknown",
                    latency_ms=latency, request_sent=True, committed=committed,
                    cost=tool.cost_usd, faults=faults,
                )
        if confidence is None and isinstance(content, dict):
            confidence = content.get("match_confidence")
        return ToolResult(
            ok=True,
            content=content,
            latency_ms=latency,
            committed=committed,
            replayed=replayed,
            freshness_s=float(freshness) if freshness is not None else None,
            confidence=confidence,
            pending=pending,
            cost=tool.cost_usd,
            faults=faults,
        )

    def _execute(self, tool: ToolDef, call: ToolInvocation) -> dict[str, Any]:
        if tool.name == "apply_service_credit":
            amount = float(call.arguments.get("amount_usd") or 0.0)
            entry, replayed = self.ledger.apply(
                customer_id=str(call.arguments.get("customer_id", "")),
                amount_usd=amount,
                idempotency_key=call.idempotency_key,
                flight_id=call.flight_id,
                reason=str(call.arguments.get("reason", "")),
            )
            return {
                "entry_id": entry.entry_id,
                "customer_id": entry.customer_id,
                "amount_usd": entry.amount_usd,
                "status": "APPLIED",
                "_replayed": replayed,
            }
        if tool.fixture is None:
            return {"error": "no fixture"}
        return dict(FIXTURES[tool.fixture])
