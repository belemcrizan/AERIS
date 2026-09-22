"""Explicit numeric thresholds. Changing a value here is a policy change."""

from __future__ import annotations

from pydantic import BaseModel


class ThresholdPolicy(BaseModel):
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
    human_on_critical: bool = True
    hold_ms: int = 50
