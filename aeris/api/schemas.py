"""HTTP schemas for the V0 control-plane API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeris.core.enums import InterventionMode, OperatorRole


class MissionCreate(BaseModel):
    objective: str = "Retrieve information and produce an answer."
    success_criteria: str = "An answer is produced without a terminal failure."


class FlightCreate(BaseModel):
    mission_id: str | None = None
    scenario_id: str = "happy_path"
    mode: InterventionMode = InterventionMode.AERIS
    human_on_critical: bool | None = None


class HumanControlBody(BaseModel):
    operator: str = "human"
    role: OperatorRole = OperatorRole.CONTROLLER
    reason: str = "human controller action"
    route_id: str | None = Field(default=None, description="Required for explicit reroute")
