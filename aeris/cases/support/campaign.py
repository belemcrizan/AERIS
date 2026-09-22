"""The support-case fault campaign.

Each scenario is a fault schedule plus a route set. Schedules are data, so
CONTROL and every AERIS arm replay the same one. ``expectation`` is the
pre-registered guess recorded in EXPERIMENT_PROTOCOL.md; reports print it
next to the observed outcome and never adjust anything to match it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeris.cases.support import world
from aeris.cases.support.case import RouteSet
from aeris.cases.support.toolbox import FaultSpec
from aeris.core.enums import FaultType


class CaseScenario(BaseModel):
    scenario_id: str
    description: str
    category: str
    route_set: RouteSet = RouteSet.INDEPENDENT
    keyed_credit: bool = True
    faults: list[FaultSpec] = Field(default_factory=list)
    expectation: str = ""

    @property
    def injects_failure(self) -> bool:
        return any(fault.ground_truth for fault in self.faults)


def _fault(schedule_id: str, fault_type: FaultType, target: str, **kwargs) -> FaultSpec:
    return FaultSpec(schedule_id=schedule_id, fault_type=fault_type, target=target, **kwargs)


def campaign() -> dict[str, CaseScenario]:
    scenarios = [
        CaseScenario(
            scenario_id="healthy",
            description="No fault.",
            category="baseline",
            expectation="Both arms correct; AERIS should not intervene.",
        ),
        CaseScenario(
            scenario_id="status_latency_8s",
            description="Status API A adds 8 s of latency on every call.",
            category="positive",
            faults=[_fault("f-lat8", FaultType.LATENCY, "svc:status_api_a", latency_ms=8000)],
            expectation="CONTROL slow but correct; AERIS reroutes. Latency trade, not a rescue.",
        ),
        CaseScenario(
            scenario_id="status_timeout",
            description="Status API A times out on every call.",
            category="positive",
            faults=[_fault("f-timeout", FaultType.TIMEOUT, "svc:status_api_a", severity="CRITICAL")],
            expectation="CONTROL fails after its own retries; AERIS reroutes to Bravo.",
        ),
        CaseScenario(
            scenario_id="status_transient",
            description="Status API A returns 503 once.",
            category="positive",
            faults=[_fault("f-transient", FaultType.TRANSIENT_FAILURE, "svc:status_api_a", calls=[1])],
            expectation="Both recover by retrying; AERIS adds a control step.",
        ),
        CaseScenario(
            scenario_id="status_rate_limit",
            description="Status API A rate-limits the first two calls.",
            category="positive",
            faults=[_fault("f-429", FaultType.RATE_LIMIT, "svc:status_api_a", calls=[1, 2])],
            expectation="CONTROL's third self-retry succeeds; AERIS retries, may reroute.",
        ),
        CaseScenario(
            scenario_id="status_malformed",
            description="Status API A returns an HTML error page once.",
            category="positive",
            faults=[_fault("f-malformed", FaultType.MALFORMED_RESPONSE, "svc:status_api_a", calls=[1])],
            expectation="Both recover by retrying.",
        ),
        CaseScenario(
            scenario_id="incident_db_persistent",
            description="Primary incident DB is down for the whole trial.",
            category="positive",
            faults=[_fault("f-incdb", FaultType.PERSISTENT_FAILURE, "svc:incident_db_a")],
            expectation="CONTROL fails; AERIS retries, then reroutes to Bravo.",
        ),
        CaseScenario(
            scenario_id="stale_incident",
            description="Primary incident DB serves last month's resolved incident.",
            category="positive",
            faults=[
                _fault(
                    "f-stale",
                    FaultType.STALE_RESPONSE,
                    "svc:incident_db_a",
                    replacement=world.INCIDENT_STALE,
                )
            ],
            expectation="CONTROL answers with the wrong incident; AERIS flags STALE_DATA and reroutes.",
        ),
        CaseScenario(
            scenario_id="runbook_no_progress",
            description="Hosted runbook search keeps answering PENDING.",
            category="positive",
            faults=[_fault("f-pending", FaultType.NO_PROGRESS, "svc:runbook_search_api")],
            expectation="CONTROL stalls and fails; AERIS reroutes to the local index.",
        ),
        CaseScenario(
            scenario_id="credit_lost_response_unkeyed",
            description="Credit commits, response is lost; the ledger tool has no idempotency.",
            category="side_effect",
            keyed_credit=False,
            faults=[
                _fault(
                    "f-lost-unkeyed",
                    FaultType.LOST_RESPONSE_AFTER_COMMIT,
                    "svc:billing_ledger",
                    calls=[1],
                    severity="CRITICAL",
                )
            ],
            expectation=(
                "CONTROL's self-retry double-credits. AERIS blocks the retry: no duplicate, "
                "but the flight does not complete. NO_SIDE_EFFECT_GATE should duplicate."
            ),
        ),
        CaseScenario(
            scenario_id="credit_lost_response_keyed",
            description="Credit commits, response is lost; the ledger honours idempotency keys.",
            category="side_effect",
            keyed_credit=True,
            faults=[
                _fault(
                    "f-lost-keyed",
                    FaultType.LOST_RESPONSE_AFTER_COMMIT,
                    "svc:billing_ledger",
                    calls=[1],
                    severity="CRITICAL",
                )
            ],
            expectation="Both arms replay the same key: one ledger entry. AERIS adds no safety here.",
        ),
        CaseScenario(
            scenario_id="shared_dependency_outage",
            description="Hosted runbook search down; the only fallback also uses it.",
            category="failure_domain",
            route_set=RouteSet.SHARED,
            faults=[_fault("f-search-shared", FaultType.PERSISTENT_FAILURE, "svc:runbook_search_api")],
            expectation="Nobody recovers. AERIS should refuse the non-independent diversion.",
        ),
        CaseScenario(
            scenario_id="independent_fallback_outage",
            description="Hosted runbook search down; fallback uses a local index.",
            category="failure_domain",
            route_set=RouteSet.INDEPENDENT,
            faults=[_fault("f-search-indep", FaultType.PERSISTENT_FAILURE, "svc:runbook_search_api")],
            expectation="CONTROL fails; AERIS reroutes to Bravo and may recover.",
        ),
        CaseScenario(
            scenario_id="mixed_fallbacks_outage",
            description="Hosted runbook search down; one shared and one independent fallback exist.",
            category="failure_domain",
            route_set=RouteSet.BOTH,
            faults=[_fault("f-search-both", FaultType.PERSISTENT_FAILURE, "svc:runbook_search_api")],
            expectation="AERIS_FULL picks Bravo; AERIS_NO_DIVERSITY may waste a diversion on Bravo-shared.",
        ),
        CaseScenario(
            scenario_id="all_routes_fail",
            description="The CRM that every route depends on is down.",
            category="failure_domain",
            faults=[_fault("f-crm", FaultType.PERSISTENT_FAILURE, "svc:crm")],
            expectation="Both fail. AERIS should stop early rather than burn diversions.",
        ),
        CaseScenario(
            scenario_id="false_low_confidence",
            description="Runbook search reports 0.12 match confidence on a correct result.",
            category="negative_control",
            faults=[
                _fault(
                    "f-falseconf",
                    FaultType.FALSE_CONFIDENCE,
                    "svc:runbook_search_api",
                    ground_truth=False,
                    confidence=0.12,
                )
            ],
            expectation="CONTROL correct. AERIS reroutes on an untrusted signal: overhead, no gain.",
        ),
        CaseScenario(
            scenario_id="false_confidence_bad_fallback",
            description="False low confidence on Alpha while the fallback telemetry is stale.",
            category="negative_control",
            faults=[
                _fault(
                    "f-falseconf2",
                    FaultType.FALSE_CONFIDENCE,
                    "svc:runbook_search_api",
                    ground_truth=False,
                    confidence=0.12,
                ),
                _fault(
                    "f-fallback-stale",
                    FaultType.STALE_RESPONSE,
                    "svc:local_telemetry",
                    replacement=world.TELEMETRY_STALE,
                ),
            ],
            expectation="CONTROL correct; AERIS diverts into a worse route. Expected HARMFUL.",
        ),
        CaseScenario(
            scenario_id="temporary_latency",
            description="Status API A is slow (2.6 s extra) only on the first call.",
            category="negative_control",
            faults=[_fault("f-templat", FaultType.LATENCY, "svc:status_api_a", calls=[1], latency_ms=2600)],
            expectation="Would recover naturally. Any AERIS diversion is unnecessary.",
        ),
    ]
    return {scenario.scenario_id: scenario for scenario in scenarios}
