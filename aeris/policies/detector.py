"""Deterministic HazardDetector.

V0 uses thresholds only. An LLM must not be introduced here: detection has
to be inspectable and replayable for the experiment to be falsifiable.
"""

from __future__ import annotations

from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ControlAction, HazardSeverity, HazardType
from aeris.core.ids import new_id
from aeris.core.models import Flight, Hazard, TelemetryEvent
from aeris.policies.thresholds import ThresholdPolicy


class HazardDetector:
    def __init__(
        self,
        policy: ThresholdPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.policy = policy or ThresholdPolicy()
        self._clock = clock or SystemClock()

    def detect(self, flight: Flight, telemetry: TelemetryEvent) -> list[Hazard]:
        metrics = telemetry.metrics
        now = self._clock.now()
        hazards: list[Hazard] = []

        if metrics.timed_out or metrics.step_latency_ms >= self.policy.timeout_risk_ms:
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.TIMEOUT_RISK,
                    HazardSeverity.CRITICAL,
                    ControlAction.REROUTE,
                    now,
                    {"step_latency_ms": metrics.step_latency_ms, "timed_out": metrics.timed_out},
                )
            )
        elif metrics.step_latency_ms >= self.policy.high_latency_ms:
            severity = (
                HazardSeverity.WARNING
                if metrics.step_latency_ms >= self.policy.high_latency_ms * 2
                else HazardSeverity.CAUTION
            )
            action = (
                ControlAction.REROUTE
                if severity == HazardSeverity.WARNING
                else ControlAction.HOLD
            )
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.HIGH_LATENCY,
                    severity,
                    action,
                    now,
                    {"step_latency_ms": metrics.step_latency_ms},
                )
            )

        if metrics.tool_error:
            retries_left = flight.consecutive_retries < self.policy.max_retries
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.TOOL_FAILURE,
                    HazardSeverity.WARNING,
                    ControlAction.RETRY if retries_left else ControlAction.REROUTE,
                    now,
                    {"tool_error_count": metrics.tool_error_count},
                )
            )
        elif not metrics.step_success and not metrics.timed_out:
            retries_left = flight.consecutive_retries < self.policy.max_retries
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.ROUTE_FAILURE if not retries_left else HazardType.TOOL_FAILURE,
                    HazardSeverity.WARNING,
                    ControlAction.RETRY if retries_left else ControlAction.REROUTE,
                    now,
                    {
                        "step_success": metrics.step_success,
                        "consecutive_retries": flight.consecutive_retries,
                    },
                )
            )

        if metrics.repeated_action_count >= self.policy.repeated_action_count:
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.REPEATED_ACTION,
                    HazardSeverity.CAUTION,
                    ControlAction.REROUTE,
                    now,
                    {"repeated_action_count": metrics.repeated_action_count},
                )
            )

        if metrics.confidence is not None and metrics.confidence < self.policy.low_confidence:
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.LOW_CONFIDENCE,
                    HazardSeverity.WARNING,
                    ControlAction.REROUTE,
                    now,
                    {"confidence": metrics.confidence},
                )
            )

        if (
            metrics.data_freshness_s is not None
            and metrics.data_freshness_s >= self.policy.stale_data_s
        ):
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.STALE_DATA,
                    HazardSeverity.CAUTION,
                    ControlAction.REROUTE,
                    now,
                    {"data_freshness_s": metrics.data_freshness_s},
                )
            )

        if metrics.steps_without_progress >= self.policy.no_progress_steps:
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.NO_PROGRESS,
                    HazardSeverity.WARNING,
                    ControlAction.RETRY
                    if flight.consecutive_retries < self.policy.max_retries
                    else ControlAction.REROUTE,
                    now,
                    {"steps_without_progress": metrics.steps_without_progress},
                )
            )

        if metrics.token_usage is not None:
            if metrics.token_usage >= self.policy.budget_tokens:
                hazards.append(
                    self._hazard(
                        flight,
                        HazardType.BUDGET_RISK,
                        HazardSeverity.CRITICAL,
                        ControlAction.ABORT,
                        now,
                        {"token_usage": metrics.token_usage},
                    )
                )
            elif metrics.token_usage >= int(self.policy.budget_tokens * self.policy.budget_warning_ratio):
                hazards.append(
                    self._hazard(
                        flight,
                        HazardType.BUDGET_RISK,
                        HazardSeverity.WARNING,
                        ControlAction.HOLD,
                        now,
                        {"token_usage": metrics.token_usage},
                    )
                )

        return hazards

    def _hazard(
        self,
        flight: Flight,
        hazard_type: HazardType,
        severity: HazardSeverity,
        action: ControlAction,
        timestamp,
        evidence: dict,
    ) -> Hazard:
        return Hazard(
            hazard_id=new_id("haz"),
            flight_id=flight.flight_id,
            type=hazard_type,
            severity=severity,
            timestamp=timestamp,
            evidence=evidence,
            confidence=1.0,
            recommended_action=action,
        )
