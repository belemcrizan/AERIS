"""Deterministic HazardDetector.

V0 uses thresholds only. An LLM must not be introduced here: detection has
to be inspectable and replayable for the experiment to be falsifiable.
"""

from __future__ import annotations

from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ControlAction, HazardSeverity, HazardType
from aeris.core.ids import new_id
from aeris.core.models import Flight, Hazard, TelemetryEvent
from aeris.policies.catalog import spec_for
from aeris.policies.thresholds import ThresholdPolicy

_SEVERITY_LADDER = (
    HazardSeverity.INFO,
    HazardSeverity.CAUTION,
    HazardSeverity.WARNING,
    HazardSeverity.CRITICAL,
)


class HazardDetector:
    def __init__(
        self,
        policy: ThresholdPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.policy = policy or ThresholdPolicy()
        self._clock = clock or SystemClock()
        self._occurrences: dict[tuple[str, HazardType], int] = {}

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
                        {"token_usage": metrics.token_usage, "execution_time_ms": metrics.execution_time_ms},
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
                        {"token_usage": metrics.token_usage, "execution_time_ms": metrics.execution_time_ms},
                    )
                )
        if metrics.execution_time_ms >= self.policy.max_execution_time_ms and not any(
            hazard.type is HazardType.BUDGET_RISK and hazard.severity is HazardSeverity.CRITICAL
            for hazard in hazards
        ):
            hazards.append(
                self._hazard(
                    flight,
                    HazardType.BUDGET_RISK,
                    HazardSeverity.CRITICAL,
                    ControlAction.ABORT,
                    now,
                    {"execution_time_ms": metrics.execution_time_ms},
                )
            )

        return hazards

    def reset_route_local(self, flight_id: str) -> None:
        """Drop route-local occurrence counts after a diversion."""

        stale = [
            key
            for key in self._occurrences
            if key[0] == flight_id and spec_for(key[1]).scope.value == "ROUTE_LOCAL"
        ]
        for key in stale:
            del self._occurrences[key]

    def _hazard(
        self,
        flight: Flight,
        hazard_type: HazardType,
        severity: HazardSeverity,
        action: ControlAction,
        timestamp,
        evidence: dict,
    ) -> Hazard:
        spec = spec_for(hazard_type)
        key = (flight.flight_id, hazard_type)
        occurrence = self._occurrences.get(key, 0) + 1
        self._occurrences[key] = occurrence
        severity = self._escalate(severity, occurrence, spec.escalates_on_repeat)
        evidence = {
            **evidence,
            "occurrence": occurrence,
            "scope": spec.scope.value,
            "signal_provenance": spec.provenance.value,
            "detector_confidence": 1.0,
        }
        return Hazard(
            hazard_id=new_id("haz"),
            flight_id=flight.flight_id,
            type=hazard_type,
            severity=severity,
            timestamp=timestamp,
            evidence=evidence,
            confidence=1.0,
            recommended_action=action,
            scope=spec.scope,
            auto_clear=spec.auto_clear,
            occurrence=occurrence,
        )

    @staticmethod
    def _escalate(
        severity: HazardSeverity,
        occurrence: int,
        enabled: bool,
    ) -> HazardSeverity:
        if not enabled or occurrence < 2:
            return severity
        index = _SEVERITY_LADDER.index(severity)
        bumped = min(index + (occurrence - 1), len(_SEVERITY_LADDER) - 1)
        return _SEVERITY_LADDER[bumped]
