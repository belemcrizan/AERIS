"""Operational definitions for every HazardType.

The detector stays deterministic. Each spec says which signal is required,
how severity is chosen, what evidence is attached, and whether the hazard
is route-local. Confidence here is the detector's confidence that the
rule matched, not the agent's self-reported confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeris.core.enums import ControlAction, HazardScope, HazardType, SignalProvenance


@dataclass(frozen=True)
class HazardSpec:
    hazard_type: HazardType
    signal: str
    condition: str
    severity_rule: str
    evidence: tuple[str, ...]
    confidence: str
    recommended_action: str
    scope: HazardScope
    auto_clear: bool
    escalates_on_repeat: bool
    provenance: SignalProvenance


HAZARD_SPECS: dict[HazardType, HazardSpec] = {
    HazardType.HIGH_LATENCY: HazardSpec(
        hazard_type=HazardType.HIGH_LATENCY,
        signal="step_latency_ms",
        condition="step_latency_ms >= high_latency_ms and < timeout_risk_ms and not timed_out",
        severity_rule="CAUTION below 2x threshold, otherwise WARNING",
        evidence=("step_latency_ms",),
        confidence="1.0 when the numeric condition matches",
        recommended_action="HOLD if CAUTION, REROUTE if WARNING",
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.MEASURED,
    ),
    HazardType.TIMEOUT_RISK: HazardSpec(
        hazard_type=HazardType.TIMEOUT_RISK,
        signal="timed_out or step_latency_ms",
        condition="timed_out or step_latency_ms >= timeout_risk_ms",
        severity_rule="always CRITICAL",
        evidence=("step_latency_ms", "timed_out"),
        confidence="1.0 when the numeric condition matches",
        recommended_action=ControlAction.REROUTE.value,
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=False,
        provenance=SignalProvenance.MEASURED,
    ),
    HazardType.TOOL_FAILURE: HazardSpec(
        hazard_type=HazardType.TOOL_FAILURE,
        signal="tool_error",
        condition="tool_error is true, or step failed before retries are exhausted",
        severity_rule="WARNING; repeated observations escalate one level each time",
        evidence=("tool_error_count", "step_success"),
        confidence="1.0 when the runtime reports a tool error",
        recommended_action="RETRY while retries remain, else REROUTE",
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.RUNTIME_REPORTED,
    ),
    HazardType.REPEATED_ACTION: HazardSpec(
        hazard_type=HazardType.REPEATED_ACTION,
        signal="repeated_action_count",
        condition="repeated_action_count >= repeated_action_count threshold",
        severity_rule="CAUTION",
        evidence=("repeated_action_count",),
        confidence="1.0; the counter is derived by radar from action identity",
        recommended_action=ControlAction.REROUTE.value,
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.DERIVED,
    ),
    HazardType.LOW_CONFIDENCE: HazardSpec(
        hazard_type=HazardType.LOW_CONFIDENCE,
        signal="confidence",
        condition="runtime-reported confidence < low_confidence",
        severity_rule="WARNING",
        evidence=("confidence",),
        confidence="1.0 that the threshold matched; the confidence value itself is untrusted",
        recommended_action=ControlAction.REROUTE.value,
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=False,
        provenance=SignalProvenance.RUNTIME_REPORTED,
    ),
    HazardType.STALE_DATA: HazardSpec(
        hazard_type=HazardType.STALE_DATA,
        signal="data_freshness_s",
        condition="data_freshness_s >= stale_data_s",
        severity_rule="CAUTION",
        evidence=("data_freshness_s",),
        confidence="1.0 that the threshold matched; freshness may be tool-reported",
        recommended_action=ControlAction.REROUTE.value,
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.TOOL_REPORTED,
    ),
    HazardType.NO_PROGRESS: HazardSpec(
        hazard_type=HazardType.NO_PROGRESS,
        signal="steps_without_progress",
        condition="steps_without_progress >= no_progress_steps",
        severity_rule="WARNING",
        evidence=("steps_without_progress",),
        confidence="1.0; radar derives the counter from reported progress",
        recommended_action="RETRY while retries remain, else REROUTE",
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.DERIVED,
    ),
    HazardType.ROUTE_FAILURE: HazardSpec(
        hazard_type=HazardType.ROUTE_FAILURE,
        signal="step_success",
        condition="step failed, not a timeout, and retries are already exhausted",
        severity_rule="WARNING",
        evidence=("step_success", "consecutive_retries"),
        confidence="1.0 when the step result is a failure",
        recommended_action=ControlAction.REROUTE.value,
        scope=HazardScope.ROUTE_LOCAL,
        auto_clear=True,
        escalates_on_repeat=True,
        provenance=SignalProvenance.DERIVED,
    ),
    HazardType.BUDGET_RISK: HazardSpec(
        hazard_type=HazardType.BUDGET_RISK,
        signal="token_usage or execution_time_ms",
        condition="token_usage or execution_time_ms crosses warning or hard budget",
        severity_rule="WARNING at warning ratio, CRITICAL at the hard limit",
        evidence=("token_usage", "execution_time_ms"),
        confidence="1.0 for measured time; token_usage is runtime-reported",
        recommended_action="HOLD on warning, ABORT on the hard limit",
        scope=HazardScope.FLIGHT_GLOBAL,
        auto_clear=False,
        escalates_on_repeat=False,
        provenance=SignalProvenance.DERIVED,
    ),
    HazardType.SIDE_EFFECT_RISK: HazardSpec(
        hazard_type=HazardType.SIDE_EFFECT_RISK,
        signal="committed side-effect class",
        condition="retry or reroute would touch a committed irreversible or uncompensated write",
        severity_rule="CRITICAL",
        evidence=("side_effect", "committed"),
        confidence="1.0 when the waypoint declares the class and the effect was committed",
        recommended_action="ESCALATE_HUMAN when policy allows, otherwise ABORT",
        scope=HazardScope.FLIGHT_GLOBAL,
        auto_clear=False,
        escalates_on_repeat=False,
        provenance=SignalProvenance.DERIVED,
    ),
}


def spec_for(hazard_type: HazardType) -> HazardSpec:
    return HAZARD_SPECS[hazard_type]
