"""Minimal request model for a human ATC action."""

from __future__ import annotations

from pydantic import BaseModel

from aeris.core.enums import ControlAction


class HumanActionRequest(BaseModel):
    operator: str = "human"
    reason: str = "human controller action"
    route_id: str | None = None
    action: ControlAction | None = None
