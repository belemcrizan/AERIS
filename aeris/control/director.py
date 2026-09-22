"""FlightDirector: run one flight through plan -> observe -> detect -> decide -> act.

Compensation is an operation inside that loop, not a new flight state.
The director applies it only after ATC has already decided that a retry or
reroute is otherwise warranted and the side-effect guard requires it.
"""

from __future__ import annotations

from aeris.adapters.runtime import AgentRuntime, CancelToken, ExecutionContext, RuntimeCapabilities
from aeris.control.controller import ATCController
from aeris.control.safety import assess_action
from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ControlAction, EventType, ExecutionState, FailureReason, HazardSeverity
from aeris.core.errors import UnsafeReroute, UnsafeRetry
from aeris.core.ids import new_id
from aeris.core.models import (
    Agent,
    CommittedEffect,
    ControlDecision,
    Flight,
    FlightResult,
    Hazard,
    HumanIntervention,
    Mission,
    Route,
    StepObservation,
    Waypoint,
)
from aeris.core.state_machine import StateMachine
from aeris.human.authorization import authorize
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
        self._recent_telemetry: dict[str, list[dict]] = {}

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
        self._apply_capabilities(flight)
        await self.record_flight_created(flight)
        plan = self.planner.plan(flight.mission_id, routes)
        flight.plan = plan
        flight.current_route_id = plan.selected_route_id
        await self.recorder.append(
            flight.flight_id,
            EventType.PLAN_CREATED,
            plan.model_dump(mode="json"),
            timestamp=self.clock.now(),
            mission_id=flight.mission_id,
            route_id=plan.selected_route_id,
        )
        await self._transition(flight, ExecutionState.PLANNED, "flight plan accepted")
        if flight.cancel_requested:
            await self._transition(flight, ExecutionState.ABORTED, "cancelled before takeoff")
            return await self._finish(
                flight,
                success=False,
                summary="cancelled before execution",
                failure_reason=FailureReason.CANCELLED,
            )
        await self._transition(flight, ExecutionState.RUNNING, "cleared for takeoff")
        self._charge_route(flight)
        return await self._loop(flight)

    async def resume(self, flight: Flight, intervention: HumanIntervention) -> Flight:
        authorize(intervention.role, intervention.action)
        self._reject_unsafe_human_action(flight, intervention.action)
        await self.recorder.append(
            flight.flight_id,
            EventType.HUMAN_INTERVENTION,
            intervention.model_dump(mode="json"),
            timestamp=intervention.timestamp,
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
        )
        flight.human_interventions += 1
        flight.updated_at = self.clock.now()

        if intervention.action == ControlAction.ABORT:
            await self._transition(flight, ExecutionState.ABORTED, intervention.reason)
            return await self._finish(
                flight,
                success=False,
                summary=intervention.reason,
                failure_reason=FailureReason.HUMAN_ABORT,
            )
        if intervention.action == ControlAction.DENY_COMPENSATION:
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
        route_id: str | None = None,
        operator: str = "human",
        role=None,
    ) -> Flight:
        from aeris.core.enums import OperatorRole

        flight = self.flights[flight_id]
        intervention = HumanIntervention(
            intervention_id=new_id("hum"),
            flight_id=flight_id,
            action=action,
            operator=operator,
            role=role or OperatorRole.ADMIN,
            reason=reason,
            route_id=route_id,
            timestamp=self.clock.now(),
        )
        return await self.resume(flight, intervention)

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

    async def _loop(self, flight: Flight) -> Flight:
        while not self.sm.is_terminal(flight.state) and flight.state != ExecutionState.WAITING_HUMAN:
            if flight.cancel_requested:
                await self._transition(flight, ExecutionState.ABORTED, "cancelled before next waypoint")
                return await self._finish(
                    flight,
                    success=False,
                    summary="cancelled before execution",
                    failure_reason=FailureReason.CANCELLED,
                )
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

            started = self.clock.now()
            observation = await self._execute_waypoint(flight, waypoint)
            if observation.fault_injected:
                await self.recorder.append(
                    flight.flight_id,
                    EventType.FAULT_INJECTED,
                    {
                        "fault_kind": observation.fault_kind,
                        "waypoint_id": waypoint.waypoint_id,
                        "route_id": flight.current_route_id,
                    },
                    timestamp=started,
                    mission_id=flight.mission_id,
                    route_id=flight.current_route_id,
                    waypoint_id=waypoint.waypoint_id,
                )
            self.clock.advance(observation.latency_ms)
            flight.execution_time_ms += observation.latency_ms
            self._remember_side_effect(flight, waypoint, observation)
            if observation.side_effect_committed and not observation.duplicate_suppressed and flight.committed_effects:
                await self._record_side_effect(flight, flight.committed_effects[-1])

            if observation.cancelled:
                await self._transition(flight, ExecutionState.ABORTED, observation.error or "cancelled")
                return await self._finish(
                    flight,
                    success=False,
                    summary=observation.error or "cancelled during execution",
                    failure_reason=FailureReason.CANCELLED,
                )

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

            hazards = self.detector.detect(flight, telemetry)
            flight.hazard_count += len(hazards)
            for hazard in hazards:
                await self._record_hazard(flight, hazard)

            if hazards and flight.state == ExecutionState.RUNNING:
                worst = max(hazards, key=lambda item: item.severity)
                if worst.severity in {HazardSeverity.CAUTION, HazardSeverity.WARNING, HazardSeverity.CRITICAL}:
                    await self._transition(flight, ExecutionState.DEGRADED, f"radar: {worst.type.value}")

            decision = self.controller.decide(flight, hazards)
            await self._record_decision(flight, decision)
            step_ok = observation.success and not observation.tool_error and not observation.timeout
            await self._apply_decision(flight, decision, step_ok, observation)

            if observation.success and decision.action == ControlAction.CONTINUE:
                await self.recorder.append(
                    flight.flight_id,
                    EventType.WAYPOINT_COMPLETED,
                    {"waypoint_id": waypoint.waypoint_id, "output": observation.output},
                    timestamp=self.clock.now(),
                    mission_id=flight.mission_id,
                    route_id=flight.current_route_id,
                    waypoint_id=waypoint.waypoint_id,
                )
                flight.current_waypoint_index += 1
                flight.consecutive_retries = 0
                flight.updated_at = self.clock.now()

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
    async def _execute_waypoint(self, flight: Flight, waypoint: Waypoint) -> StepObservation:
        token = CancelToken()
        if flight.cancel_requested:
            token.request("before")
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
        await self.recorder.append(
            flight.flight_id,
            EventType.HAZARD,
            hazard.model_dump(mode="json"),
            timestamp=hazard.timestamp,
            mission_id=flight.mission_id,
            route_id=flight.current_route_id,
        )

    @traced("aeris.control.decision")
    async def _record_decision(self, flight: Flight, decision: ControlDecision) -> None:
        await self.recorder.append(
            flight.flight_id,
            EventType.DECISION,
            decision.model_dump(mode="json"),
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
            if not step_ok:
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
            for scored in self.planner.score_routes(flight.plan.routes, failed_route_ids=flight.failed_route_ids):
                alternatives.append(
                    {
                        "route_id": scored.route.route_id,
                        "name": scored.route.name,
                        "score": scored.score,
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
        return flight


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
    if "no alternative" in decision.reason or "no diversion" in decision.reason:
        return FailureReason.ROUTE_EXHAUSTION
    if observation.timeout:
        return FailureReason.TIMEOUT
    return FailureReason.POLICY_ABORT
