"""FastAPI control plane. The API is a window onto in-process ATC, not a cluster."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from aeris import __version__
from aeris.api.schemas import FlightCreate, HumanControlBody, MissionCreate
from aeris.config import Settings
from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.enums import ControlAction, EventType, ExecutionState, InterventionMode
from aeris.core.state_machine import InvalidTransition
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.replay import reconstruct_flight, timeline_view
from aeris.recorder.sqlite import SqliteFlightRecorder
from aeris.routing.planner import RoutePlanner
from aeris.simulation.scenarios import builtin_scenarios, get_scenario
from aeris.telemetry.otel import configure_tracer, traced


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_db_parent()
        self.recorder = SqliteFlightRecorder(settings.db_path)
        self.directors: dict[str, FlightDirector] = {}
        self.missions: dict[str, Any] = {}

    def policy(self, human_on_critical: bool | None = None) -> ThresholdPolicy:
        return ThresholdPolicy(
            human_on_critical=self.settings.human_on_critical
            if human_on_critical is None
            else human_on_critical,
            max_retries=self.settings.max_retries,
            max_route_changes=self.settings.max_route_changes,
            hold_ms=self.settings.hold_ms,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_tracer(enabled=settings.otel_enabled, service_name=settings.otel_service_name)
        app.state.ctx = AppContext(settings)
        yield

    app = FastAPI(
        title="AERIS",
        description="Air Traffic Control for AI Agents",
        version=__version__,
        lifespan=lifespan,
    )

    def ctx() -> AppContext:
        return app.state.ctx

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "aeris", "version": __version__}

    @app.get("/scenarios")
    async def scenarios() -> dict[str, Any]:
        return {
            key: {"objective": sc.objective, "notes": sc.notes, "injects_failure": sc.injects_failure}
            for key, sc in builtin_scenarios().items()
        }

    @app.post("/missions", status_code=201)
    @traced("aeris.api.mission.create")
    async def create_mission(body: MissionCreate) -> dict[str, Any]:
        director = _ephemeral_director(ctx())
        mission = director.create_mission(body.objective, body.success_criteria)
        await director.record_mission(mission)
        ctx().missions[mission.mission_id] = mission
        return mission.model_dump(mode="json")

    @app.post("/flights", status_code=201)
    @traced("aeris.api.flight.create")
    async def create_flight(body: FlightCreate) -> dict[str, Any]:
        try:
            scenario = get_scenario(body.scenario_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        context = ctx()
        policy = context.policy(body.human_on_critical)
        planner = RoutePlanner()
        director = FlightDirector(
            recorder=context.recorder,
            runtime=scenario.runtime(),
            planner=planner,
            radar=RadarEngine(),
            detector=HazardDetector(policy=policy),
            controller=ATCController(planner=planner, policy=policy, mode=body.mode),
            policy=policy,
        )
        if body.mission_id and body.mission_id in context.missions:
            mission = context.missions[body.mission_id]
        else:
            mission = director.create_mission(scenario.objective, scenario.success_criteria)
            await director.record_mission(mission)
            context.missions[mission.mission_id] = mission
        flight = director.create_flight(mission)
        context.directors[flight.flight_id] = director
        await director.run(flight, scenario.routes)
        return flight.model_dump(mode="json")

    @app.get("/flights")
    async def list_flights() -> list[dict[str, Any]]:
        ids = await ctx().recorder.list_flight_ids()
        flights = []
        for flight_id in ids:
            if flight_id.startswith("msn_"):
                continue
            flights.append(await reconstruct_flight(ctx().recorder, flight_id))
        return flights

    @app.get("/flights/{flight_id}")
    async def get_flight(flight_id: str) -> dict[str, Any]:
        live = ctx().directors.get(flight_id)
        if live and flight_id in live.flights:
            return live.flights[flight_id].model_dump(mode="json")
        snapshot = await reconstruct_flight(ctx().recorder, flight_id)
        if snapshot["event_count"] == 0:
            raise HTTPException(status_code=404, detail="flight not found")
        return snapshot

    @app.get("/flights/{flight_id}/timeline")
    async def get_timeline(flight_id: str) -> dict[str, Any]:
        events = await ctx().recorder.timeline(flight_id)
        if not events:
            raise HTTPException(status_code=404, detail="flight not found")
        return {"flight_id": flight_id, "events": timeline_view(events)}

    @app.get("/flights/{flight_id}/telemetry")
    async def get_telemetry(flight_id: str) -> dict[str, Any]:
        events = await ctx().recorder.timeline(flight_id)
        if not events:
            raise HTTPException(status_code=404, detail="flight not found")
        return {
            "flight_id": flight_id,
            "events": timeline_view([e for e in events if e.event_type == EventType.TELEMETRY]),
        }

    @app.get("/flights/{flight_id}/hazards")
    async def get_hazards(flight_id: str) -> dict[str, Any]:
        events = await ctx().recorder.timeline(flight_id)
        if not events:
            raise HTTPException(status_code=404, detail="flight not found")
        return {
            "flight_id": flight_id,
            "events": timeline_view([e for e in events if e.event_type == EventType.HAZARD]),
        }

    @app.post("/flights/{flight_id}/control/continue")
    async def control_continue(flight_id: str, body: HumanControlBody | None = None) -> dict[str, Any]:
        return await _human(ctx(), flight_id, ControlAction.CONTINUE, body)

    @app.post("/flights/{flight_id}/control/retry")
    async def control_retry(flight_id: str, body: HumanControlBody | None = None) -> dict[str, Any]:
        return await _human(ctx(), flight_id, ControlAction.RETRY, body)

    @app.post("/flights/{flight_id}/control/reroute")
    async def control_reroute(flight_id: str, body: HumanControlBody | None = None) -> dict[str, Any]:
        return await _human(ctx(), flight_id, ControlAction.REROUTE, body)

    @app.post("/flights/{flight_id}/control/abort")
    async def control_abort(flight_id: str, body: HumanControlBody | None = None) -> dict[str, Any]:
        return await _human(ctx(), flight_id, ControlAction.ABORT, body)

    return app


def _ephemeral_director(context: AppContext) -> FlightDirector:
    return FlightDirector(recorder=context.recorder, runtime=get_scenario("happy_path").runtime())


@traced("aeris.human.intervention")
async def _human(
    context: AppContext,
    flight_id: str,
    action: ControlAction,
    body: HumanControlBody | None,
) -> dict[str, Any]:
    director = context.directors.get(flight_id)
    if director is None or flight_id not in director.flights:
        raise HTTPException(status_code=404, detail="live flight not found")
    flight = director.flights[flight_id]
    if action != ControlAction.ABORT and flight.state != ExecutionState.WAITING_HUMAN:
        raise HTTPException(
            status_code=409,
            detail=f"flight is {flight.state.value}, human control requires WAITING_HUMAN",
        )
    payload = body or HumanControlBody()
    try:
        updated = await director.apply_human_action(
            flight_id,
            action,
            reason=payload.reason,
            route_id=payload.route_id,
            operator=payload.operator,
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated.model_dump(mode="json")


def main() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run("aeris.api.app:app", host=settings.host, port=settings.port, reload=False)


app = create_app()
