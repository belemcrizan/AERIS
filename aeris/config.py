"""Process settings. Environment-backed, overridable in tests."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


class Settings(BaseModel):
    db_path: Path = Field(default_factory=lambda: Path(os.getenv("AERIS_DB_PATH", "./data/aeris.db")))
    host: str = Field(default_factory=lambda: os.getenv("AERIS_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: _env_int("AERIS_PORT", 8000))
    otel_enabled: bool = Field(default_factory=lambda: _env_bool("AERIS_OTEL_ENABLED", False))
    otel_service_name: str = Field(
        default_factory=lambda: os.getenv("AERIS_OTEL_SERVICE_NAME", "aeris")
    )
    human_on_critical: bool = Field(
        default_factory=lambda: _env_bool("AERIS_HUMAN_ON_CRITICAL", True)
    )
    max_retries: int = Field(default_factory=lambda: _env_int("AERIS_MAX_RETRIES", 2))
    max_route_changes: int = Field(default_factory=lambda: _env_int("AERIS_MAX_ROUTE_CHANGES", 3))
    hold_ms: int = Field(default_factory=lambda: _env_int("AERIS_HOLD_MS", 50))

    def ensure_db_parent(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
