"""FlightDirector: run one flight through plan -> observe -> detect -> decide -> act.

Compensation is an operation inside that loop, not a new flight state.
The director applies it only after ATC has already decided that a retry or
reroute is otherwise warranted and the side-effect guard requires it.

Concurrency model (V1): one asyncio lock per flight plus an optimistic
``control_version``. The flight loop holds the lock while it flies. A human
action captures the version it was issued against; it is applied only if
the version is unchanged when the lock is acquired, and applying it bumps
the version. Two identical clicks therefore produce one physical action.
Cancellation does not wait for the lock: it flips the cancel token of the
active waypoint, and the loop resolves the outcome at its next checkpoint.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from aeris.adapters.runtime import AgentRuntime, CancelToken, ExecutionContext, RuntimeCapabilities
from aeris.control.controller import ATCController
from aeris.control.safety import assess_action
from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import (
    CancelOutcome,
    CancelStatus,
    ControlAction,
    EventType,
    ExecutionState,
    FailureReason,
    FaultType,
    HazardSeverity,
    HazardType,
    OperatorRole,
)
from aeris.core.errors import ControlConflict, UnsafeReroute, UnsafeRetry
from aeris.core.ids import new_id
from aeris.core.models import (
    Agent,
    CommittedEffect,
    ControlDecision,
    FaultInstance,
    Flight,
    FlightResult,
    Hazard,
    HumanIntervention,
    Mission,
    Route,
    RouteFailureRecord,
    StepObservation,
    Waypoint,
)
from aeris.core.state_machine import StateMachine
from aeris.human.authorization import authorize, resolve_role
from aeris.human.intervention import (
    HumanInterventionRequest,
    request_allowed_actions,
    side_effect_state,
)
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.base import FlightRecorder
from aeris.routing.planner import RoutePlanner
from aeris.telemetry.otel import traced

_ROUTE_FAILURE_HAZARDS = frozenset(
    {
        HazardType.TOOL_FAILURE,
        HazardType.TIMEOUT_RISK,
        HazardType.ROUTE_FAILURE,
        HazardType.HIGH_LATENCY,
        HazardType.STALE_DATA,
    }
)


@dataclass
class ActiveExecution:
    flight_id: str
    waypoint_id: str
    token: CancelToken
    started_at: datetime


class FlightDirector:
    def __init__(
        self,
        recorder: FlightRecorder,
        runtime: AgentRuntime,
        *,
        planner: RoutePlanner | None = None,
        radar: RadarEngine | None = None,
        detector: HazardDetector | None = None,
        controller: ATCController | None = None,
        policy: ThresholdPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.recorder = recorder
        self.runtime = runtime
        self.clock = clock or SystemClock()
        self.policy = policy or ThresholdPolicy()
        self.planner = planner or RoutePlanner()
        self.radar = radar or RadarEngine(clock=self.clock)
        self.detector = detector or HazardDetector(policy=self.policy, clock=self.clock)
        self.controller = controller or ATCController(
            planner=self.planner, policy=self.policy, clock=self.clock
        )
        self.sm = StateMachine(clock=self.clock)
        self.flights: dict[str, Flight] = {}
        self.missions: dict[str, Mission] = {}
        self.human_requests: dict[str, HumanInterventionRequest] = {}
        self.active: dict[str, ActiveExecution] = {}
        self.faults: dict[str, dict[str, FaultInstance]] = {}
        self._recent_telemetry: dict[str, list[dict]] = {}
        self.recent_hazards: dict[str, list[dict]] = {}
        self.last_decisions: dict[str, ControlDecision] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._steps_started: dict[str, int] = {}

    def _lock(self, flight_id: str) -> asyncio.Lock:
        lock = self._locks.get(flight_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[flight_id] = lock
        return lock

    def create_mission(self, objective: str, success_criteria: str) -> Mission:
        mission = Mission(
            mission_id=new_id("msn"),
            objective=objective,
            success_criteria=success_criteria,
            created_at=self.clock.now(),
        )
        self.missions[mission.mission_id] = mission
        return mission

    def create_flight(self, mission: Mission, agent: Agent | None = None) -> Flight:
        now = self.clock.now()
        flight = Flight(
            flight_id=new_id("flt"),
            mission_id=mission.mission_id,
            agent=agent or Agent(agent_id=new_id("agt"), name="simulator", runtime_kind="simulator"),
            created_at=now,
            updated_at=now,
        )
        self._apply_capabilities(flight)
        self.flights[flight.flight_id] = flight
        self.missions.setdefault(mission.mission_id, mission)
        return flight

    def _apply_capabilities(self, flight: Flight) -> None:
        caps = self.runtime.capabilities() if hasattr(self.runtime, "capabilities") else RuntimeCapabilities()
        flight.runtime_can_compensate = caps.can_compensate
        flight.runtime_supports_idempotency = caps.supports_idempotency
        flight.runtime_can_retry = caps.can_retry
        flight.runtime_can_cancel = caps.can_cancel

    @traced("aeris.mission.create")
    async def record_mission(self, mission: Mission) -> None:
        await self.recorder.append(
            mission.mission_id,
            EventType.MISSION_CREATED,
            mission.model_dump(mode="json"),
            timestamp=mission.created_at,
            mission_id=mission.mission_id,
        )

    @traced("aeris.flight.create")
    async def record_flight_created(self, flight: Flight) -> None:
        await self.recorder.append(
            flight.flight_id,
            EventType.FLIGHT_CREATED,
            flight.model_dump(mode="json"),
            timestamp=flight.created_at,
            mission_id=flight.mission_id,
        )

    @traced("aeris.flight.run")
    async def run(self, flight: Flight, routes: list[Route]) -> Flight:
        self.flights.setdefault(flight.flight_id, flight)
        async with self._lock(flight.flight_id):
            return await self._run_locked(flight, routes)

    async def _run_locked(self, flight: Flight, routes: list[Route]) -> Flight:
        self._apply_capabilities(flight)
        await self.record_flight_created(flight)
        plan = self.planner.plan(flight.mission_id, routes)
        flight.plan = plan
        flight.current_route_id = plan.selected_route_id
        await self.recorder.append(
            flight.flight_id,
            EventType.PLAN_CREATED,
            {**plan.model_dump(mode="json"), "planner_weights": self.planner.weights()},
            timestamp=self.clock.now(),
            mission_id=flight.mission_id,
            route_id=plan.selected_route_id,
        )
        await self._transition(flight, ExecutionState.PLANNED, "flight plan accepted")
        if flight.cancel_requested:
            return await self._stop_for_cancel(flight, CancelOutcome.CANCEL_BEFORE_START)
        await self._transition(flight, ExecutionState.RUNNING, "cleared for takeoff")
        self._charge_route(flight)
        return await self._loop(flight)

    # ------------------------------------------------------------------ humans

    async def resume(
        self,
        flight: Flight,
        intervention: HumanIntervention,
        *,
        expected_version: int | None = None,
    ) -> Flight:
        authorize(intervention.role, intervention.action)
        # Fail fast instead of queueing behind a flight ATC is still flying.
        self._check_control(flight, intervention.action, expected_version)
        async with self._lock(flight.flight_id):
            self._check_control(flight, intervention.action, expected_version)
            self._reject_unsafe_human_action(flight, intervention.action)
            flight.control_version += 1
            return await self._resume_locked(flight, intervention)

    def _check_control(
        self,
        flight: Flight,
        action: ControlAction,
        expected_version: int | None,
    ) -> None:
        if self.sm.is_terminal(flight.state):
            raise ControlConflict(f"flight is {flight.state.value}; no control action applies")
        if flight.cancel_requested:
            raise ControlConflict("a cancel is pending; only its resolution may change this flight")
        if flight.state != ExecutionState.WAITING_HUMAN:
            raise ControlConflict(
                f"flight is {flight.state.value}, human control requires WAITING_HUMAN"
            )
        if expected_version is not None and expected_version != flight.control_version:
            raise ControlConflict(
                f"stale control version {expected_version}; flight is at {flight.control_version}"
            )

    async def _resume_locked(self, flight: Flight, intervention: HumanIntervention) -> Flight:
        await self.recorder.append(
            flight.flight_id,
            EventType.HUMAN_INTERVENTION,
            {**intervention.model_dump(mode="json"), "control_version": flight.control_version},
            timestamp=intervention.timestamp,
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
        )
        flight.human_interventions += 1
        flight.updated_at = self.clock.now()
        self.human_requests.pop(flight.flight_id, None)

        if intervention.action in {ControlAction.ABORT, ControlAction.DENY_COMPENSATION}:
            await self._transition(flight, ExecutionState.ABORTED, intervention.reason)
            return await self._finish(
                flight,
                success=False,
                summary=intervention.reason,
                failure_reason=FailureReason.HUMAN_ABORT,
            )
        if intervention.action == ControlAction.APPROVE_COMPENSATION:
            ok = await self._compensate_open(flight)
            if not ok:
                return flight
            await self._transition(flight, ExecutionState.RUNNING, "compensation approved")
            return await self._loop(flight)
        if intervention.action == ControlAction.RETRY:
            flight.consecutive_retries += 1
            flight.retry_count += 1
            await self._transition(flight, ExecutionState.HOLDING, "human ordered retry")
            await self.clock.sleep(self.policy.hold_ms / 1000)
            await self._transition(flight, ExecutionState.RUNNING, "human retry cleared")
            return await self._loop(flight)
        if intervention.action == ControlAction.HOLD:
            flight.hold_count += 1
            await self._transition(flight, ExecutionState.HOLDING, intervention.reason)
            await self.clock.sleep(self.policy.hold_ms / 1000)
            await self._transition(flight, ExecutionState.RUNNING, "human released hold")
            return await self._loop(flight)
        if intervention.action == ControlAction.REROUTE:
            target = intervention.route_id
            if target is None:
                nxt = self.planner.next_route(flight, [])
                target = nxt.route.route_id if nxt else None
            if target is None:
                await self._transition(flight, ExecutionState.FAILED, "human reroute with no airway")
                return await self._finish(
                    flight,
                    success=False,
                    summary="no route available",
                    failure_reason=FailureReason.ROUTE_EXHAUSTION,
                )
            await self._apply_reroute(flight, target, "human selected diversion")
            return await self._loop(flight)
        await self._transition(flight, ExecutionState.RUNNING, intervention.reason or "human continue")
        return await self._loop(flight)

    async def apply_human_action(
        self,
        flight_id: str,
        action: ControlAction,
        *,
        reason: str,
        role: OperatorRole | str | None,
        route_id: str | None = None,
        operator: str = "human",
        expected_version: int | None = None,
    ) -> Flight:
        resolved = resolve_role(role)
        flight = self.flights[flight_id]
        if expected_version is None:
            # Bind the action to the state the caller saw, so a duplicate call conflicts.
            expected_version = flight.control_version
        if (
            action == ControlAction.ABORT
            and not self.sm.is_terminal(flight.state)
            and flight.state != ExecutionState.WAITING_HUMAN
        ):
            return await self.cancel(flight_id, role=resolved, reason=reason, operator=operator)
        intervention = HumanIntervention(
            intervention_id=new_id("hum"),
            flight_id=flight_id,
            action=action,
            operator=operator,
            role=resolved,
            reason=reason,
            route_id=route_id,
            timestamp=self.clock.now(),
        )
        return await self.resume(flight, intervention, expected_version=expected_version)

    def _reject_unsafe_human_action(self, flight: Flight, action: ControlAction) -> None:
        if action not in {ControlAction.RETRY, ControlAction.REROUTE}:
            return
        try:
            waypoint = flight.current_waypoint()
        except Exception:
            waypoint = None
        assessment = assess_action(flight, action, waypoint)
        if assessment.allowed:
            return
        if action is ControlAction.RETRY:
            raise UnsafeRetry(assessment.detail or "retry rejected by side-effect policy")
        raise UnsafeReroute(assessment.detail or "reroute rejected by side-effect policy")

    # ------------------------------------------------------------ cancellation

    async def cancel(
        self,
        flight_id: str,
        *,
        role: OperatorRole | str | None,
        reason: str = "cancel requested",
        operator: str = "human",
    ) -> Flight:
        """Request cancellation. Idempotent: one request, one resolution."""

        resolved = resolve_role(role)
        authorize(resolved, ControlAction.ABORT)
        flight = self.flights[flight_id]
        if flight.cancel_requested:
            return flight
        flight.cancel_requested = True
        flight.control_version += 1
        terminal = self.sm.is_terminal(flight.state)
        active = self.active.get(flight_id)
        if terminal:
            status = CancelStatus.CANCEL_TOO_LATE
        elif active is not None:
            active.token.request("during", reason)
            status = (
                CancelStatus.CANCEL_ACKNOWLEDGED
                if flight.runtime_can_cancel
                else CancelStatus.CANCEL_UNSUPPORTED
            )
        else:
            status = CancelStatus.CANCEL_REQUESTED
        flight.cancel_status = status
        await self.recorder.append(
            flight.flight_id,
            EventType.CANCEL_REQUESTED,
            {
                "operator": operator,
                "role": resolved.value,
                "reason": reason,
                "state": flight.state.value,
                "status": status.value,
                "active_waypoint_id": active.waypoint_id if active else None,
                "runtime_can_cancel": flight.runtime_can_cancel,
            },
            timestamp=self.clock.now(),
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
            waypoint_id=active.waypoint_id if active else None,
        )
        if terminal:
            await self._resolve_cancel(
                flight,
                CancelOutcome.CANCEL_AFTER_TERMINAL,
                CancelStatus.CANCEL_TOO_LATE,
                f"flight was already {flight.state.value}",
            )
            return flight
        lock = self._lock(flight_id)
        if flight.state == ExecutionState.WAITING_HUMAN and not lock.locked():
            async with lock:
                if flight.state == ExecutionState.WAITING_HUMAN and flight.cancel_outcome is None:
                    await self._stop_for_cancel(flight, CancelOutcome.CANCEL_BETWEEN_WAYPOINTS)
        return flight

    async def _resolve_cancel(
        self,
        flight: Flight,
        outcome: CancelOutcome,
        status: CancelStatus,
        detail: str,
        **extra,
    ) -> None:
        if flight.cancel_outcome is not None:
            return
        flight.cancel_outcome = outcome
        flight.cancel_status = status
        await self.recorder.append(
            flight.flight_id,
            EventType.CANCEL_RESOLVED,
            {"outcome": outcome.value, "status": status.value, "detail": detail, **extra},
            timestamp=self.clock.now(),
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
        )

    async def _stop_for_cancel(self, flight: Flight, outcome: CancelOutcome) -> Flight:
        if outcome is CancelOutcome.CANCEL_BETWEEN_WAYPOINTS and not self._steps_started.get(flight.flight_id):
            outcome = CancelOutcome.CANCEL_BEFORE_START
        detail = (
            "cancelled before any waypoint started"
            if outcome is CancelOutcome.CANCEL_BEFORE_START
            else "cancelled at a waypoint boundary; no further waypoint was started"
        )
        await self._transition(flight, ExecutionState.ABORTED, detail)
        await self._resolve_cancel(flight, outcome, CancelStatus.CANCELLED, detail)
        return await self._finish(
            flight,
            success=False,
            summary=detail,
            failure_reason=FailureReason.CANCELLED,
        )

    async def _resolve_mid_step_cancel(
        self,
        flight: Flight,
        waypoint: Waypoint,
        observation: StepObservation,
        token: CancelToken,
    ) -> Flight:
        committed = observation.side_effect_committed and not observation.duplicate_suppressed
        step_ok = observation.success and not observation.tool_error and not observation.timeout
        route = flight.current_route()
        last = waypoint.index >= len(route.waypoints) - 1
        if committed:
            outcome = CancelOutcome.CANCEL_AFTER_SIDE_EFFECT_COMMIT
            status = CancelStatus.CANCEL_TOO_LATE
            detail = f"{waypoint.name} committed its side effect before the cancel took effect; it was not undone"
        elif observation.cancelled:
            outcome = CancelOutcome.CANCEL_DURING_WAYPOINT
            status = CancelStatus.CANCELLED
            detail = f"runtime stopped {waypoint.name} at a cooperative checkpoint"
        elif not flight.runtime_can_cancel:
            outcome = CancelOutcome.CANCEL_NOT_SUPPORTED
            status = CancelStatus.CANCEL_UNSUPPORTED
            detail = f"runtime cannot interrupt {waypoint.name}; it ran to completion"
        else:
            outcome = CancelOutcome.CANCEL_BETWEEN_WAYPOINTS
            status = CancelStatus.CANCELLED
            detail = f"{waypoint.name} finished before its checkpoint; stopped at the boundary"

        if step_ok and not observation.cancelled:
            await self.recorder.append(
                flight.flight_id,
                EventType.WAYPOINT_COMPLETED,
                {"waypoint_id": waypoint.waypoint_id, "output": observation.output, "under_cancel": True},
                timestamp=self.clock.now(),
                mission_id=flight.mission_id,
                route_id=flight.current_route_id,
                waypoint_id=waypoint.waypoint_id,
            )
            flight.current_waypoint_index += 1
            if last:
                await self._transition(flight, ExecutionState.COMPLETED, "destination reached before cancel applied")
                await self._resolve_cancel(
                    flight,
                    outcome,
                    CancelStatus.CANCEL_TOO_LATE,
                    "execution completed before the cancel could take effect",
                    runtime_observed_token=token.observed,
                )
                return await self._finish(flight, success=True, summary="mission destination reached")

        await self._transition(flight, ExecutionState.ABORTED, detail)
        await self._resolve_cancel(
            flight,
            outcome,
            status,
            detail,
            side_effect_committed=committed,
            runtime_observed_token=token.observed,
        )
        return await self._finish(
            flight,
            success=False,
            summary=detail,
            failure_reason=FailureReason.CANCELLED,
        )

    # -------------------------------------------------------------------- loop

    async def _loop(self, flight: Flight) -> Flight:
        while not self.sm.is_terminal(flight.state) and flight.state != ExecutionState.WAITING_HUMAN:
            if flight.cancel_requested:
                return await self._stop_for_cancel(flight, CancelOutcome.CANCEL_BETWEEN_WAYPOINTS)
            waypoint = flight.current_waypoint()
            if waypoint is None:
                await self._transition(flight, ExecutionState.COMPLETED, "all waypoints reached")
                return await self._finish(flight, success=True, summary="mission destination reached")

            await self.recorder.append(
                flight.flight_id,
                EventType.WAYPOINT_STARTED,
                waypoint.model_dump(mode="json"),
                timestamp=self.clock.now(),
                mission_id=flight.mission_id,
                route_id=flight.current_route_id,
                waypoint_id=waypoint.waypoint_id,
            )
            self._steps_started[flight.flight_id] = self._steps_started.get(flight.flight_id, 0) + 1

            started = self.clock.now()
            token = CancelToken()
            self.active[flight.flight_id] = ActiveExecution(
                flight_id=flight.flight_id,
                waypoint_id=waypoint.waypoint_id,
                token=token,
                started_at=started,
            )
            try:
                observation = await self._execute_waypoint(flight, waypoint, token)
            finally:
                self.active.pop(flight.flight_id, None)
            await self._record_faults(flight, waypoint, observation, started)
            self.clock.advance(observation.latency_ms)
            flight.execution_time_ms += observation.latency_ms
            self._account(flight, observation)
            self._remember_side_effect(flight, waypoint, observation)
            if observation.side_effect_committed and not observation.duplicate_suppressed and flight.committed_effects:
                await self._record_side_effect(flight, flight.committed_effects[-1])

            telemetry = self.radar.observe(flight, observation)
            recent = self._recent_telemetry.setdefault(flight.flight_id, [])
            recent.append(telemetry.model_dump(mode="json"))
            del recent[:-5]
            await self.recorder.append(
                flight.flight_id,
                EventType.TELEMETRY,
                telemetry.model_dump(mode="json"),
                timestamp=telemetry.timestamp,
                mission_id=flight.mission_id,
                route_id=flight.current_route_id,
                waypoint_id=waypoint.waypoint_id,
            )

            if token.cancelled or observation.cancelled:
                return await self._resolve_mid_step_cancel(flight, waypoint, observation, token)

            hazards = self.detector.detect(flight, telemetry)
            flight.hazard_count += len(hazards)
            for hazard in hazards:
                await self._record_hazard(flight, hazard)
            self._remember_route_failure(flight, waypoint, hazards, observation)

            if hazards and flight.state == ExecutionState.RUNNING:
                worst = max(hazards, key=lambda item: item.severity)
                if worst.severity in {HazardSeverity.CAUTION, HazardSeverity.WARNING, HazardSeverity.CRITICAL}:
                    await self._transition(flight, ExecutionState.DEGRADED, f"radar: {worst.type.value}")

            decision = self.controller.decide(flight, hazards)
            flight.control_version += 1
            await self._record_decision(flight, decision)
            step_ok = observation.success and not observation.tool_error and not observation.timeout
            await self._apply_decision(flight, decision, step_ok, observation)

            advance = step_ok or self.policy.continue_on_step_error
            if (
                advance
                and decision.action == ControlAction.CONTINUE
                and not self.sm.is_terminal(flight.state)
            ):
                await self.recorder.append(
                    flight.flight_id,
                    EventType.WAYPOINT_COMPLETED,
                    {
                        "waypoint_id": waypoint.waypoint_id,
                        "output": observation.output,
                        "step_ok": step_ok,
                        "error": observation.error,
                    },
                    timestamp=self.clock.now(),
                    mission_id=flight.mission_id,
                    route_id=flight.current_route_id,
                    waypoint_id=waypoint.waypoint_id,
                )
                flight.current_waypoint_index += 1
                flight.consecutive_retries = 0
                flight.updated_at = self.clock.now()

        if flight.state == ExecutionState.WAITING_HUMAN and flight.cancel_requested:
            return await self._stop_for_cancel(flight, CancelOutcome.CANCEL_BETWEEN_WAYPOINTS)
        if self.sm.is_terminal(flight.state) and flight.result is None:
            success = flight.state == ExecutionState.COMPLETED
            return await self._finish(
                flight,
                success=success,
                summary=flight.state.value,
                failure_reason=flight.failure_reason,
            )
        return flight

    @traced("aeris.waypoint.execute")
    async def _execute_waypoint(
        self,
        flight: Flight,
        waypoint: Waypoint,
        token: CancelToken,
    ) -> StepObservation:
        context = ExecutionContext(
            flight=flight,
            waypoint=waypoint,
            attempt=flight.consecutive_retries,
            cancel_token=token,
        )
        try:
            return await self.runtime.execute_waypoint(context)
        except Exception as exc:
            return StepObservation(
                flight_id=flight.flight_id,
                mission_id=flight.mission_id,
                route_id=flight.current_route_id or "",
                waypoint_id=waypoint.waypoint_id,
                waypoint_index=waypoint.index,
                latency_ms=0.0,
                success=False,
                error=f"invalid_runtime_output: {exc}",
                side_effect_class=waypoint.side_effect,
            )

    def _account(self, flight: Flight, observation: StepObservation) -> None:
        flight.input_tokens += observation.input_tokens
        flight.output_tokens += observation.output_tokens
        flight.tool_calls += observation.tool_calls
        flight.model_cost += observation.model_cost
        flight.tool_cost += observation.tool_cost

    async def _record_faults(
        self,
        flight: Flight,
        waypoint: Waypoint,
        observation: StepObservation,
        started: datetime,
    ) -> None:
        faults = list(observation.faults)
        if not faults and observation.fault_injected:
            faults = [
                FaultInstance(
                    fault_id="",
                    schedule_id=f"legacy:{flight.current_route_id}:{waypoint.name}:{observation.fault_kind}",
                    flight_id=flight.flight_id,
                    fault_type=_legacy_fault_type(observation.fault_kind),
                )
            ]
        seen = self.faults.setdefault(flight.flight_id, {})
        for fault in faults:
            if fault.schedule_id in seen:
                continue
            instance = fault.model_copy(
                update={
                    "fault_id": fault.fault_id or new_id("fault"),
                    "flight_id": flight.flight_id,
                    "route_id": fault.route_id or flight.current_route_id,
                    "waypoint_id": fault.waypoint_id or waypoint.waypoint_id,
                    "onset_timestamp": fault.onset_timestamp or started,
                }
            )
            seen[fault.schedule_id] = instance
            payload = instance.model_dump(mode="json")
            payload["fault_kind"] = observation.fault_kind or instance.fault_type.value.lower()
            await self.recorder.append(
                flight.flight_id,
                EventType.FAULT_INJECTED,
                payload,
                timestamp=instance.onset_timestamp,
                mission_id=flight.mission_id,
                route_id=instance.route_id,
                waypoint_id=instance.waypoint_id,
            )

    def _remember_route_failure(
        self,
        flight: Flight,
        waypoint: Waypoint,
        hazards: list[Hazard],
        observation: StepObservation,
    ) -> None:
        try:
            route = flight.current_route()
        except Exception:
            return
        provider = route.dependencies.model_provider if route.dependencies else None
        dependency_ids = sorted(observation.dependency_ids or waypoint.dependency_ids())
        tool = observation.tool or waypoint.tool
        now = self.clock.now()
        for hazard in hazards:
            if hazard.type not in _ROUTE_FAILURE_HAZARDS:
                continue
            existing = next(
                (
                    record
                    for record in flight.route_history
                    if record.route_id == route.route_id
                    and record.hazard_type == hazard.type
                    and record.tool == tool
                ),
                None,
            )
            if existing is not None:
                existing.occurrence_count += 1
                existing.most_recent = now
                continue
            flight.route_history.append(
                RouteFailureRecord(
                    route_id=route.route_id,
                    hazard_type=hazard.type,
                    tool=tool,
                    provider=provider,
                    dependency_ids=dependency_ids,
                    most_recent=now,
                )
            )

    def _remember_side_effect(
        self,
        flight: Flight,
        waypoint: Waypoint,
        observation: StepObservation,
    ) -> None:
        if not observation.side_effect_committed:
            return
        key = observation.idempotency_key or waypoint.waypoint_id
        already = any(
            (effect.idempotency_key or effect.waypoint_id) == key and not effect.compensated
            for effect in flight.committed_effects
        )
        if observation.duplicate_suppressed:
            return
        if already:
            flight.side_effect_incidents += 1
        flight.committed_effects.append(
            CommittedEffect(
                waypoint_id=waypoint.waypoint_id,
                waypoint_name=waypoint.name,
                route_id=observation.route_id,
                side_effect=observation.side_effect_class,
                idempotency_key=observation.idempotency_key,
                compensation_name=waypoint.compensation_name,
            )
        )

    async def _record_side_effect(self, flight: Flight, effect: CommittedEffect) -> None:
        await self.recorder.append(
            flight.flight_id,
            EventType.SIDE_EFFECT,
            effect.model_dump(mode="json"),
            timestamp=self.clock.now(),
            mission_id=flight.mission_id,
            route_id=effect.route_id,
            waypoint_id=effect.waypoint_id,
        )

    @traced("aeris.hazard.detect")
    async def _record_hazard(self, flight: Flight, hazard: Hazard) -> None:
        recent = self.recent_hazards.setdefault(flight.flight_id, [])
        recent.append(hazard.model_dump(mode="json"))
        del recent[:-10]
        await self.recorder.append(
            flight.flight_id,
            EventType.HAZARD,
            hazard.model_dump(mode="json"),
            timestamp=hazard.timestamp,
            mission_id=flight.mission_id,
            route_id=hazard.route_id or flight.current_route_id,
            waypoint_id=hazard.waypoint_id,
        )

    @traced("aeris.control.decision")
    async def _record_decision(self, flight: Flight, decision: ControlDecision) -> None:
        self.last_decisions[flight.flight_id] = decision
        await self.recorder.append(
            flight.flight_id,
            EventType.DECISION,
            {**decision.model_dump(mode="json"), "control_version": flight.control_version},
            timestamp=decision.timestamp,
            mission_id=flight.mission_id,
            route_id=decision.new_route or flight.current_route_id,
        )

    async def _apply_decision(
        self,
        flight: Flight,
        decision: ControlDecision,
        step_ok: bool,
        observation: StepObservation,
    ) -> None:
        action = decision.action
        if action == ControlAction.COMPENSATE:
            ok = await self._compensate_open(flight)
            if not ok:
                return
            follow = decision.follow_up or ControlAction.CONTINUE
            follow_decision = decision.model_copy(update={"action": follow, "compensation_required": False})
            await self._apply_decision(flight, follow_decision, step_ok=False, observation=observation)
            return
        if action == ControlAction.CONTINUE:
            if not step_ok and not self.policy.continue_on_step_error:
                reason = _failure_from_observation(observation)
                await self._transition(flight, ExecutionState.FAILED, "step failed without intervention")
                await self._finish(flight, success=False, summary=decision.reason, failure_reason=reason)
            elif flight.state == ExecutionState.DEGRADED:
                await self._transition(flight, ExecutionState.RUNNING, "hazards cleared or accepted")
            return
        if action == ControlAction.RETRY:
            flight.consecutive_retries += 1
            flight.retry_count += 1
            await self._transition(flight, ExecutionState.HOLDING, decision.reason)
            await self.clock.sleep(self.policy.hold_ms / 1000)
            await self._transition(flight, ExecutionState.RUNNING, "retry cleared")
            return
        if action == ControlAction.HOLD:
            flight.hold_count += 1
            await self._transition(flight, ExecutionState.HOLDING, decision.reason)
            await self.clock.sleep(self.policy.hold_ms / 1000)
            await self._transition(flight, ExecutionState.RUNNING, "holding pattern complete")
            return
        if action == ControlAction.REROUTE:
            if decision.new_route is None:
                await self._transition(flight, ExecutionState.FAILED, "reroute without a destination")
                await self._finish(
                    flight,
                    success=False,
                    summary=decision.reason,
                    failure_reason=FailureReason.ROUTE_EXHAUSTION,
                )
                return
            await self._apply_reroute(flight, decision.new_route, decision.reason)
            return
        if action == ControlAction.ESCALATE_HUMAN:
            await self._transition(flight, ExecutionState.WAITING_HUMAN, decision.reason)
            await self._open_human_request(flight, decision)
            return
        if action == ControlAction.ABORT:
            reason = _failure_from_decision(decision, observation)
            await self._transition(flight, ExecutionState.ABORTED, decision.reason)
            await self._finish(flight, success=False, summary=decision.reason, failure_reason=reason)

    async def _compensate_open(self, flight: Flight) -> bool:
        if not flight.runtime_can_compensate or not hasattr(self.runtime, "compensate"):
            flight.compensation_failures += 1
            await self._transition(flight, ExecutionState.FAILED, "runtime cannot compensate")
            await self._finish(
                flight,
                success=False,
                summary="compensation unsupported",
                failure_reason=FailureReason.COMPENSATION_FAILURE,
            )
            return False
        route = flight.current_route()
        pending = [
            effect
            for effect in flight.open_effects(route.route_id)
            if effect.compensation_name
        ]
        if not pending:
            return True
        for effect in pending:
            if effect.compensated:
                continue
            waypoint = next(item for item in route.waypoints if item.waypoint_id == effect.waypoint_id)
            context = ExecutionContext(flight=flight, waypoint=waypoint, attempt=flight.consecutive_retries)
            result = await self.runtime.compensate(context)
            payload = result.model_dump(mode="json")
            payload["original_waypoint"] = effect.waypoint_name
            payload["side_effect"] = effect.side_effect.value
            await self.recorder.append(
                flight.flight_id,
                EventType.COMPENSATION,
                payload,
                timestamp=self.clock.now(),
                mission_id=flight.mission_id,
                route_id=effect.route_id,
                waypoint_id=effect.waypoint_id,
            )
            if not result.success:
                flight.compensation_failures += 1
                await self._transition(flight, ExecutionState.FAILED, result.detail)
                await self._finish(
                    flight,
                    success=False,
                    summary=result.detail,
                    failure_reason=FailureReason.COMPENSATION_FAILURE,
                )
                return False
            effect.compensated = True
            flight.compensation_count += 1
        return True

    async def _open_human_request(self, flight: Flight, decision: ControlDecision) -> None:
        waypoint = None
        try:
            waypoint = flight.current_waypoint()
        except Exception:
            waypoint = None
        alternatives = []
        if flight.plan is not None:
            for scored in self.planner.score_alternatives(flight, []):
                alternatives.append(
                    {
                        "route_id": scored.route.route_id,
                        "name": scored.route.name,
                        "score": scored.score,
                        "terms": scored.terms,
                        "diversity": scored.diversity,
                        "shared_failed_dependencies": scored.shared_failed_dependencies,
                        "reasons": scored.reasons,
                    }
                )
        request = HumanInterventionRequest(
            request_id=new_id("hir"),
            flight_id=flight.flight_id,
            mission_id=flight.mission_id,
            current_route_id=flight.current_route_id,
            waypoint_id=waypoint.waypoint_id if waypoint else None,
            state=flight.state,
            hazards=decision.evidence.get("hazards", []),
            evidence=decision.evidence,
            alternatives=alternatives,
            recent_telemetry=list(self._recent_telemetry.get(flight.flight_id, [])),
            side_effect_state=side_effect_state(flight),
            recommended_action=decision.action,
            allowed_actions=request_allowed_actions(flight),
            timestamp=self.clock.now(),
            reason=decision.reason,
            control_version=flight.control_version,
        )
        self.human_requests[flight.flight_id] = request
        await self.recorder.append(
            flight.flight_id,
            EventType.HUMAN_REQUEST,
            request.model_dump(mode="json"),
            timestamp=request.timestamp,
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
            waypoint_id=request.waypoint_id,
        )

    async def _apply_reroute(self, flight: Flight, new_route_id: str, reason: str) -> None:
        if flight.current_route_id:
            flight.failed_route_ids.append(flight.current_route_id)
        await self._transition(flight, ExecutionState.REROUTING, reason)
        flight.current_route_id = new_route_id
        flight.current_waypoint_index = 0
        flight.route_changes += 1
        flight.consecutive_retries = 0
        self.radar.reset_route_local(flight.flight_id)
        if hasattr(self.detector, "reset_route_local"):
            self.detector.reset_route_local(flight.flight_id)
        if flight.plan is not None:
            flight.plan.selected_route_id = new_route_id
        self._charge_route(flight)
        flight.updated_at = self.clock.now()
        await self._transition(flight, ExecutionState.RUNNING, f"now flying {new_route_id}")

    def _charge_route(self, flight: Flight) -> None:
        try:
            route = flight.current_route()
        except Exception:
            return
        flight.accumulated_cost += route.estimated_cost

    async def _transition(self, flight: Flight, target: ExecutionState, reason: str) -> None:
        record = self.sm.transition(flight, target, reason)
        if record is None:
            return
        await self.recorder.append(
            flight.flight_id,
            EventType.STATE_TRANSITION,
            record.model_dump(mode="json"),
            timestamp=record.timestamp,
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
        )

    @traced("aeris.flight.complete")
    async def _finish(
        self,
        flight: Flight,
        *,
        success: bool,
        summary: str,
        failure_reason: FailureReason | None = None,
    ) -> Flight:
        if flight.result is None:
            if not success:
                flight.failure_reason = failure_reason
            flight.result = FlightResult(
                success=success,
                terminal_state=flight.state,
                summary=summary,
                waypoint_index=flight.current_waypoint_index,
                retries=flight.retry_count,
                route_changes=flight.route_changes,
                human_interventions=flight.human_interventions,
                hazard_count=flight.hazard_count,
                failure_reason=None if success else failure_reason,
                side_effect_incidents=flight.side_effect_incidents,
                compensations=flight.compensation_count,
                compensation_failures=flight.compensation_failures,
                cancel_outcome=flight.cancel_outcome,
                cancel_status=flight.cancel_status,
                input_tokens=flight.input_tokens,
                output_tokens=flight.output_tokens,
                tool_calls=flight.tool_calls,
                model_cost=flight.model_cost,
                tool_cost=flight.tool_cost,
                execution_time_ms=flight.execution_time_ms,
            )
            flight.updated_at = self.clock.now()
            await self.recorder.append(
                flight.flight_id,
                EventType.FLIGHT_COMPLETED,
                flight.result.model_dump(mode="json"),
                timestamp=flight.updated_at,
                mission_id=flight.mission_id,
                route_id=flight.current_route_id,
            )
            checkpoint = getattr(self.recorder, "checkpoint", None)
            if checkpoint is not None:
                await checkpoint(flight.flight_id, timestamp=flight.updated_at)
        return flight


def _legacy_fault_type(kind: str | None) -> FaultType:
    mapping = {
        "latency": FaultType.LATENCY,
        "timeout": FaultType.TIMEOUT,
        "tool_failure": FaultType.PERSISTENT_FAILURE,
        "low_confidence": FaultType.FALSE_CONFIDENCE,
        "stale_data": FaultType.STALE_RESPONSE,
        "repeated_action": FaultType.REPEATED_ACTION,
        "no_progress": FaultType.NO_PROGRESS,
        "budget": FaultType.BUDGET_OVERRUN,
        "response_lost": FaultType.LOST_RESPONSE_AFTER_COMMIT,
    }
    return mapping.get(kind or "", FaultType.TRANSIENT_FAILURE)


def _failure_from_observation(observation: StepObservation) -> FailureReason:
    if observation.timeout:
        return FailureReason.TIMEOUT
    if observation.tool_error:
        return FailureReason.TOOL_FAILURE
    if observation.error and observation.error.startswith("invalid_runtime_output"):
        return FailureReason.INVALID_RUNTIME_OUTPUT
    return FailureReason.TOOL_FAILURE


def _failure_from_decision(decision: ControlDecision, observation: StepObservation) -> FailureReason:
    blocked = decision.evidence.get("failure_reason") or decision.evidence.get("side_effect_risk", {})
    if isinstance(blocked, dict):
        reason = blocked.get("failure_reason")
    else:
        reason = blocked
    if reason == FailureReason.BUDGET_EXHAUSTION.value or "budget" in decision.reason:
        return FailureReason.BUDGET_EXHAUSTION
    if reason == FailureReason.UNSAFE_RETRY_BLOCKED.value:
        return FailureReason.UNSAFE_RETRY_BLOCKED
    if reason == FailureReason.UNSAFE_REROUTE_BLOCKED.value:
        return FailureReason.UNSAFE_REROUTE_BLOCKED
    if reason == FailureReason.ROUTE_EXHAUSTION.value:
        return FailureReason.ROUTE_EXHAUSTION
    if "no alternative" in decision.reason or "no diversion" in decision.reason:
        return FailureReason.ROUTE_EXHAUSTION
    if observation.timeout:
        return FailureReason.TIMEOUT
    return FailureReason.POLICY_ABORT
