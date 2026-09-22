"""Explicit numeric thresholds. Changing a value here is a policy change."""

from __future__ import annotations

from pydantic import BaseModel

from aeris.core.enums import RadarMode
from aeris.core.hashing import stable_hash

POLICY_VERSION = "aeris-policy-1.0.0"


class ThresholdPolicy(BaseModel):
    version: str = POLICY_VERSION
    high_latency_ms: float = 2000.0
    timeout_risk_ms: float = 8000.0
    low_confidence: float = 0.4
    stale_data_s: float = 3600.0
    no_progress_steps: int = 2
    repeated_action_count: int = 2
    tool_error_count: int = 1
    budget_tokens: int = 100_000
    budget_warning_ratio: float = 0.8
    max_retries: int = 2
    max_route_changes: int = 3
    max_holds: int = 2
    max_execution_time_ms: float = 120_000.0
    max_cost: float = 50.0
    human_on_critical: bool = True
    escalate_irreversible: bool = True
    hold_ms: int = 50
    radar_mode: RadarMode = RadarMode.STATIC_THRESHOLD
    robust_z_threshold: float = 5.0
    min_mad_ms: float = 25.0
    token_range_tolerance: float = 1.5
    avoid_shared_failure_domain: bool = True
    side_effect_gate: bool = True
    continue_on_step_error: bool = False

    def policy_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))
