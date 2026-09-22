"""Sandbox world for the enterprise support / incident resolution case.

Every record is a local fixture. The credit ledger is an in-memory sandbox:
no real financial transaction is ever executed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from aeris.core.ids import new_id

TOOL_FIXTURE_VERSION = "support-fixtures/1.0.0"

CUSTOMER_ID = "CUST-1042"
SERVICE_ID = "svc-payments-eu"

CUSTOMER_ACCOUNT: dict[str, Any] = {
    "customer_id": CUSTOMER_ID,
    "name": "Northwind Logistics",
    "tier": "ENTERPRISE",
    "monthly_fee_usd": 12000.0,
    "services": [SERVICE_ID],
    "sla": {"availability": "99.95%", "credit_policy": "see runbook credit_guidance"},
}

STATUS_PRIMARY: dict[str, Any] = {
    "service_id": SERVICE_ID,
    "state": "MAJOR_OUTAGE",
    "since_minutes": 95,
    "region": "eu-west-1",
    "source": "status_api_a",
    "as_of_age_s": 30,
}

TELEMETRY_FALLBACK: dict[str, Any] = {
    "service_id": SERVICE_ID,
    "state": "MAJOR_OUTAGE",
    "error_rate": 0.71,
    "since_minutes": 94,
    "source": "local_telemetry",
    "as_of_age_s": 45,
}

TELEMETRY_STALE: dict[str, Any] = {
    "service_id": SERVICE_ID,
    "state": "OPERATIONAL",
    "error_rate": 0.001,
    "since_minutes": 0,
    "source": "local_telemetry",
    "as_of_age_s": 86_400,
}

INCIDENT_ACTIVE: dict[str, Any] = {
    "incident_id": "INC-4821",
    "status": "ACTIVE",
    "title": "EU payments API unavailable after failed deploy",
    "opened_minutes_ago": 93,
    "source": "incident_db_a",
    "as_of_age_s": 120,
}

INCIDENT_STALE: dict[str, Any] = {
    "incident_id": "INC-3107",
    "status": "RESOLVED",
    "title": "Intermittent latency in EU payments (last month)",
    "opened_minutes_ago": 43_200,
    "source": "incident_db_a",
    "as_of_age_s": 2_592_000,
}

INCIDENT_CACHED: dict[str, Any] = {**INCIDENT_ACTIVE, "source": "local_incident_cache", "as_of_age_s": 300}

RUNBOOK: dict[str, Any] = {
    "runbook_id": "RB-PAY-7",
    "recommended_action": "failover_to_secondary_region",
    "steps": [
        "confirm the incident is active",
        "fail over EU payments to eu-central-1",
        "post a customer status update",
    ],
    "credit_guidance": (
        "ENTERPRISE customers receive a credit of 10% of the monthly fee when an outage "
        "exceeds 60 minutes. Apply it once per incident."
    ),
    "source": "runbook_search_api",
}

RUNBOOK_LOCAL: dict[str, Any] = {**RUNBOOK, "source": "local_runbook_index"}


class GroundTruth(BaseModel):
    service_state: str = "MAJOR_OUTAGE"
    incident_id: str = "INC-4821"
    recommended_action: str = "failover_to_secondary_region"
    credit_due: bool = True
    credit_amount_usd: float = 1200.0


class LedgerEntry(BaseModel):
    entry_id: str
    customer_id: str
    amount_usd: float
    idempotency_key: str | None
    flight_id: str
    reason: str = ""


class SandboxCreditLedger:
    """Idempotent when the caller reuses a key; a fresh key means a fresh entry."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self._by_key: dict[str, LedgerEntry] = {}
        self.replays: int = 0

    def apply(
        self,
        *,
        customer_id: str,
        amount_usd: float,
        idempotency_key: str | None,
        flight_id: str,
        reason: str = "",
    ) -> tuple[LedgerEntry, bool]:
        if idempotency_key and idempotency_key in self._by_key:
            self.replays += 1
            return self._by_key[idempotency_key], True
        entry = LedgerEntry(
            entry_id=new_id("cr"),
            customer_id=customer_id,
            amount_usd=amount_usd,
            idempotency_key=idempotency_key,
            flight_id=flight_id,
            reason=reason,
        )
        self.entries.append(entry)
        if idempotency_key:
            self._by_key[idempotency_key] = entry
        return entry, False

    def entries_for(self, flight_id: str) -> list[LedgerEntry]:
        return [entry for entry in self.entries if entry.flight_id == flight_id]
