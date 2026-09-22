"""Enterprise support / incident resolution: mission, phases and routes.

ROUTE ALPHA (primary)    status API A -> incident DB A -> hosted runbook search
ROUTE BRAVO (fallback)   local telemetry -> local incident cache -> local runbook index
ROUTE BRAVO-SHARED       local telemetry -> local incident cache -> hosted runbook search

Bravo-shared exists for the failure-domain experiment: it *looks* like a
fallback but shares the hosted runbook service with Alpha. All routes share
the CRM, the billing ledger and the single LLM; those are declared, so the
diversity score cannot pretend otherwise.
"""

from __future__ import annotations

from enum import StrEnum

from aeris.adapters.llm_runtime import PhaseConfig
from aeris.cases.support import world
from aeris.cases.support.toolbox import TOOLS
from aeris.core.enums import SideEffectClass
from aeris.core.hashing import stable_hash
from aeris.core.models import Route, RouteDependencies, Waypoint

SCENARIO_VERSION = "support-campaign/1.0.0"

OBJECTIVE = (
    "A customer reports that their enterprise service is unavailable and asks for "
    "diagnosis, status, and the safest next action."
)
SUCCESS_CRITERIA = (
    "Correct service state, the active incident id, the runbook's recommended action, "
    "and exactly one SLA credit when policy requires it."
)

SYSTEM_PROMPT = (
    "You are an enterprise support agent. Use only the tools offered in each phase. "
    "Call one tool at a time. Never invent incident ids or service states; if a tool "
    "fails you may try it again. In the final phase reply with a single JSON object with "
    "keys: service_state, incident_id, recommended_action, credit_applied (bool), "
    "credit_amount_usd (number or null), summary."
)

MISSION_PROMPT = (
    f"Ticket from customer {world.CUSTOMER_ID}: '{OBJECTIVE}' "
    f"The affected service is {world.SERVICE_ID}. Diagnose the service state, find the "
    "active incident, retrieve the runbook guidance, apply the SLA credit if the runbook "
    "says it is due, then answer."
)

PHASES: dict[str, PhaseConfig] = {
    "check_status": PhaseConfig(
        instruction="Look up the customer account, then get the current service status.",
        extra_tools=["get_customer_account"],
    ),
    "lookup_incident": PhaseConfig(instruction="Find the active incident for the affected service."),
    "consult_runbook": PhaseConfig(instruction="Retrieve the runbook guidance for this outage."),
    "apply_credit": PhaseConfig(
        instruction=(
            "If the runbook says an SLA credit is due, apply it exactly once "
            "(10% of the monthly fee)."
        ),
    ),
    "respond": PhaseConfig(instruction="Write the final JSON answer.", final=True, max_turns=1),
}


class RouteSet(StrEnum):
    INDEPENDENT = "independent"
    SHARED = "shared"
    BOTH = "both"


_ROUTE_TOOLS: dict[str, list[str]] = {
    "alpha": ["get_service_status", "query_incident_database", "search_runbook"],
    "bravo": ["get_telemetry_status", "lookup_cached_incident", "search_local_runbook"],
    "bravo_shared": ["get_telemetry_status", "lookup_cached_incident", "search_runbook"],
}

_ROUTE_META: dict[str, dict] = {
    "alpha": {
        "name": "alpha",
        "description": "Primary: status API A, incident DB A, hosted runbook search",
        "reliability": 0.95,
        "latency": 3000.0,
        "cost": 0.004,
        "tool_provider": "hosted-saas",
        "data_source": "hosted",
        "region": "eu-west-1",
        "network": "public-internet",
    },
    "bravo": {
        "name": "bravo",
        "description": "Independent fallback: local telemetry, cached incidents, local runbook index",
        "reliability": 0.85,
        "latency": 3600.0,
        "cost": 0.006,
        "tool_provider": "local",
        "data_source": "local-sandbox",
        "region": "local",
        "network": "loopback",
    },
    "bravo_shared": {
        "name": "bravo-shared",
        "description": "Fallback that still uses the hosted runbook search",
        "reliability": 0.85,
        "latency": 3500.0,
        "cost": 0.006,
        "tool_provider": "mixed",
        "data_source": "mixed",
        "region": "local",
        "network": "public-internet",
    },
}


def credit_idempotency_key() -> str:
    return f"credit-{world.CUSTOMER_ID}-{world.GroundTruth().incident_id}"


def _waypoints(route_key: str, *, keyed_credit: bool) -> list[Waypoint]:
    status_tool, incident_tool, runbook_tool = _ROUTE_TOOLS[route_key]

    def deps(*tools: str) -> list[str]:
        return sorted({dep for tool in tools for dep in TOOLS[tool].depends_on})

    credit_class = SideEffectClass.IDEMPOTENT_WRITE if keyed_credit else SideEffectClass.IRREVERSIBLE_WRITE
    return [
        Waypoint(
            waypoint_id=f"{route_key}-wp0",
            name="check_status",
            index=0,
            tool=status_tool,
            depends_on=deps("get_customer_account", status_tool),
        ),
        Waypoint(
            waypoint_id=f"{route_key}-wp1",
            name="lookup_incident",
            index=1,
            tool=incident_tool,
            depends_on=deps(incident_tool),
        ),
        Waypoint(
            waypoint_id=f"{route_key}-wp2",
            name="consult_runbook",
            index=2,
            tool=runbook_tool,
            depends_on=deps(runbook_tool),
        ),
        Waypoint(
            waypoint_id=f"{route_key}-wp3",
            name="apply_credit",
            index=3,
            tool="apply_service_credit",
            depends_on=deps("apply_service_credit"),
            side_effect=credit_class,
            supports_idempotency=keyed_credit,
            idempotency_key=credit_idempotency_key() if keyed_credit else None,
        ),
        Waypoint(waypoint_id=f"{route_key}-wp4", name="respond", index=4),
    ]


def build_route(
    route_key: str,
    *,
    keyed_credit: bool = True,
    model_provider: str = "scripted",
    model_family: str = "scripted-support",
) -> Route:
    meta = _ROUTE_META[route_key]
    waypoints = _waypoints(route_key, keyed_credit=keyed_credit)
    services = sorted({dep for waypoint in waypoints for dep in waypoint.depends_on})
    return Route(
        route_id=f"route-{route_key}",
        name=meta["name"],
        description=meta["description"],
        waypoints=waypoints,
        estimated_reliability=meta["reliability"],
        estimated_latency_ms=meta["latency"],
        estimated_cost=meta["cost"],
        side_effect_risk=0.2 if not keyed_credit else 0.05,
        dependencies=RouteDependencies(
            model_provider=model_provider,
            model_family=model_family,
            tool_provider=meta["tool_provider"],
            data_source=meta["data_source"],
            region=meta["region"],
            network_dependency=meta["network"],
            shared_service_ids=services,
        ),
    )


def build_routes(
    route_set: RouteSet = RouteSet.INDEPENDENT,
    *,
    keyed_credit: bool = True,
    model_provider: str = "scripted",
    model_family: str = "scripted-support",
) -> list[Route]:
    keys = {
        RouteSet.INDEPENDENT: ["alpha", "bravo"],
        RouteSet.SHARED: ["alpha", "bravo_shared"],
        RouteSet.BOTH: ["alpha", "bravo_shared", "bravo"],
    }[route_set]
    return [
        build_route(key, keyed_credit=keyed_credit, model_provider=model_provider, model_family=model_family)
        for key in keys
    ]


def route_config_hash(routes: list[Route]) -> str:
    return stable_hash([route.model_dump(mode="json") for route in routes])
