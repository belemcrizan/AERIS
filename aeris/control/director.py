"""FlightDirector: run one flight through plan -> observe -> detect -> decide -> act."""

from __future__ import annotations

from aeris.adapters.runtime import AgentRuntime, ExecutionContext
from aeris.control.controller import ATCController
from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ControlAction, EventType, ExecutionState, HazardSeverity
from aeris.core.ids import new_id
from aeris.core.models import (
    Agent,
    ControlDecision,
    Flight,
    FlightResult,
    Hazard,
    HumanIntervention,
    Mission,
    Route,
    Waypoint,
)
from aeris.core.state_machine import StateMachine
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
        self.flights[flight.flight_id] = flight
        self.missions.setdefault(mission.mission_id, mission)
        return flight

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
        await self._transition(flight, ExecutionState.RUNNING, "cleared for takeoff")
        return await self._loop(flight)

    async def resume(self, flight: Flight, intervention: HumanIntervention) -> Flight:
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
            return await self._finish(flight, success=False, summary=intervention.reason)
        if intervention.action == ControlAction.RETRY:
            flight.consecutive_retries += 1
            flight.retry_count += 1
            await self._transition(flight, ExecutionState.HOLDING, "human ordered retry")
            await self.clock.sleep(self.policy.hold_ms / 1000)
            await self._transition(flight, ExecutionState.RUNNING, "human retry cleared")
            return await self._loop(flight)
        if intervention.action == ControlAction.REROUTE:
            target = intervention.route_id
            if target is None:
                nxt = self.planner.next_route(flight, [])
                target = nxt.route.route_id if nxt else None
            if target is None:
                await self._transition(flight, ExecutionState.FAILED, "human reroute with no airway")
                return await self._finish(flight, success=False, summary="no route available")
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
    ) -> Flight:
        flight = self.flights[flight_id]
        intervention = HumanIntervention(
            intervention_id=new_id("hum"),
            flight_id=flight_id,
            action=action,
            operator=operator,
            reason=reason,
            route_id=route_id,
            timestamp=self.clock.now(),
        )
        return await self.resume(flight, intervention)

    async def _loop(self, flight: Flight) -> Flight:
        while not self.sm.is_terminal(flight.state) and flight.state != ExecutionState.WAITING_HUMAN:
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

            observation = await self._execute_waypoint(flight, waypoint)
            telemetry = self.radar.observe(flight, observation)
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
            for hazard in hazards:
                await self._record_hazard(flight, hazard)

            if hazards and flight.state == ExecutionState.RUNNING:
                worst = max(hazards, key=lambda item: item.severity)
                if worst.severity in {HazardSeverity.CAUTION, HazardSeverity.WARNING, HazardSeverity.CRITICAL}:
                    await self._transition(flight, ExecutionState.DEGRADED, f"radar: {worst.type.value}")

            decision = self.controller.decide(flight, hazards)
            await self._record_decision(flight, decision)
            await self._apply_decision(flight, decision, observation.success and not observation.tool_error and not observation.timeout)

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
            return await self._finish(flight, success=success, summary=flight.state.value)
        return flight

    @traced("aeris.waypoint.execute")
    async def _execute_waypoint(self, flight: Flight, waypoint: Waypoint):
        context = ExecutionContext(flight=flight, waypoint=waypoint, attempt=flight.consecutive_retries)
        return await self.runtime.execute_waypoint(context)

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

    async def _apply_decision(self, flight: Flight, decision: ControlDecision, step_ok: bool) -> None:
        action = decision.action
        if action == ControlAction.CONTINUE:
            if not step_ok:
                await self._transition(flight, ExecutionState.FAILED, "step failed without intervention")
                await self._finish(flight, success=False, summary=decision.reason)
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
            assert decision.new_route is not None
            await self._apply_reroute(flight, decision.new_route, decision.reason)
            return
        if action == ControlAction.ESCALATE_HUMAN:
            await self._transition(flight, ExecutionState.WAITING_HUMAN, decision.reason)
            return
        if action == ControlAction.ABORT:
            await self._transition(flight, ExecutionState.ABORTED, decision.reason)
            await self._finish(flight, success=False, summary=decision.reason)

    async def _apply_reroute(self, flight: Flight, new_route_id: str, reason: str) -> None:
        if flight.current_route_id:
            flight.failed_route_ids.append(flight.current_route_id)
        await self._transition(flight, ExecutionState.REROUTING, reason)
        flight.current_route_id = new_route_id
        flight.current_waypoint_index = 0
        flight.route_changes += 1
        flight.consecutive_retries = 0
        self.radar.reset_route_local(flight.flight_id)
        if flight.plan is not None:
            flight.plan.selected_route_id = new_route_id
        flight.updated_at = self.clock.now()
        await self._transition(flight, ExecutionState.RUNNING, f"now flying {new_route_id}")

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
    async def _finish(self, flight: Flight, *, success: bool, summary: str) -> Flight:
        if flight.result is None:
            flight.result = FlightResult(
                success=success,
                terminal_state=flight.state,
                summary=summary,
                waypoint_index=flight.current_waypoint_index,
                retries=flight.retry_count,
                route_changes=flight.route_changes,
                human_interventions=flight.human_interventions,
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
