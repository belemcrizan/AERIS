"""HTTP schemas for the control-plane API."""

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
    background: bool = Field(
        default=False,
        description="Return immediately and fly in the background so cancel/console can observe it",
    )


class HumanControlBody(BaseModel):
    """A control call must say who is acting. There is no default role."""

    operator: str = "human"
    role: OperatorRole
    reason: str = "human controller action"
    route_id: str | None = Field(default=None, description="Required for explicit reroute")
    expected_version: int | None = Field(
        default=None,
        description="control_version the caller saw; a stale value is rejected with 409",
    )
