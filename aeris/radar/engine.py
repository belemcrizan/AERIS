"""RadarEngine: convert raw runtime signals into normalized TelemetryEvents.

No routing, no policy, no control decisions live here.
"""

from __future__ import annotations

from pydantic import BaseModel

from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import SignalProvenance
from aeris.core.ids import new_id
from aeris.core.models import Flight, StepObservation, TelemetryEvent, TelemetryMetrics


class RadarTrack(BaseModel):
    """Per-flight accumulator the radar uses to derive counters."""

    flight_id: str
    started_at_ms: float = 0.0
    last_progress: float = 0.0
    steps_without_progress: int = 0
    tool_error_count: int = 0
    repeated_action_count: int = 0
    last_action: str | None = None
    last_latency_ms: float = 0.0
    token_usage: int = 0
    step_count: int = 0


class RadarEngine:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._tracks: dict[str, RadarTrack] = {}

    def track(self, flight_id: str) -> RadarTrack:
        if flight_id not in self._tracks:
            self._tracks[flight_id] = RadarTrack(flight_id=flight_id)
        return self._tracks[flight_id]

    def reset_route_local(self, flight_id: str) -> None:
        """Clear per-airway counters after a diversion. Flight totals stay."""
        if flight_id not in self._tracks:
            return
        track = self._tracks[flight_id]
        track.last_progress = 0.0
        track.steps_without_progress = 0
        track.repeated_action_count = 0
        track.last_action = None
        track.last_latency_ms = 0.0

    def observe(self, flight: Flight, observation: StepObservation) -> TelemetryEvent:
        track = self.track(flight.flight_id)
        track.step_count += 1
        track.last_latency_ms = observation.latency_ms
        if track.started_at_ms == 0.0:
            track.started_at_ms = observation.latency_ms
        else:
            track.started_at_ms += observation.latency_ms

        if observation.tool_error:
            track.tool_error_count += 1

        if observation.token_usage:
            track.token_usage += observation.token_usage

        if observation.progress <= track.last_progress:
            track.steps_without_progress += 1
        else:
            track.steps_without_progress = 0
            track.last_progress = observation.progress

        if observation.repeat_signal:
            track.repeated_action_count += 2
        elif observation.action and observation.action == track.last_action:
            track.repeated_action_count += 1
        elif observation.action:
            track.last_action = observation.action
            track.repeated_action_count = 0

        metrics = TelemetryMetrics(
            latency_ms=track.started_at_ms,
            step_latency_ms=observation.latency_ms,
            retry_count=flight.retry_count,
            tool_error_count=track.tool_error_count,
            route_changes=flight.route_changes,
            token_usage=track.token_usage or observation.token_usage,
            confidence=observation.confidence,
            data_freshness_s=observation.data_freshness_s,
            execution_time_ms=track.started_at_ms,
            progress=observation.progress,
            repeated_action_count=track.repeated_action_count,
            steps_without_progress=track.steps_without_progress,
            last_action=track.last_action,
            step_success=observation.success and not observation.tool_error and not observation.timeout,
            timed_out=observation.timeout,
            tool_error=observation.tool_error,
            step_token_usage=observation.token_usage,
            step_cost=observation.model_cost + observation.tool_cost,
        )
        provenance = {
            "step_latency_ms": SignalProvenance.MEASURED,
            "latency_ms": SignalProvenance.MEASURED,
            "execution_time_ms": SignalProvenance.MEASURED,
            "tool_error": SignalProvenance.RUNTIME_REPORTED,
            "timed_out": SignalProvenance.RUNTIME_REPORTED,
            "confidence": SignalProvenance.RUNTIME_REPORTED,
            "progress": SignalProvenance.RUNTIME_REPORTED,
            "token_usage": SignalProvenance.RUNTIME_REPORTED,
            "data_freshness_s": SignalProvenance.TOOL_REPORTED,
            "repeated_action_count": SignalProvenance.DERIVED,
            "steps_without_progress": SignalProvenance.DERIVED,
            "retry_count": SignalProvenance.DERIVED,
            "route_changes": SignalProvenance.DERIVED,
            "step_success": SignalProvenance.DERIVED,
            "step_token_usage": SignalProvenance.RUNTIME_REPORTED,
            "step_cost": SignalProvenance.DERIVED,
        }
        return TelemetryEvent(
            event_id=new_id("tel"),
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            route_id=observation.route_id,
            waypoint_id=observation.waypoint_id,
            timestamp=self._clock.now(),
            metrics=metrics,
            provenance=provenance,
        )
