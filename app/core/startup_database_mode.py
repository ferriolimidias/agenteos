"""Strict contract for backend startup database actions."""

from __future__ import annotations

import os
from enum import Enum


class StartupDatabaseModeError(RuntimeError):
    """Raised when startup mode is invalid or an action is not permitted."""


class StartupDatabaseMode(str, Enum):
    INIT = "init"
    MIGRATE = "migrate"
    SKIP = "skip"

    @classmethod
    def resolve(cls, raw_value: str | None = None) -> "StartupDatabaseMode":
        if raw_value is None:
            raw_value = os.getenv("STARTUP_DATABASE_MODE")
        if raw_value is None:
            return cls.INIT
        normalized = raw_value.strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise StartupDatabaseModeError(
                "Invalid STARTUP_DATABASE_MODE. Expected one of: init, migrate, skip."
            ) from exc

    @property
    def allows_manual_startup_migrations(self) -> bool:
        return self is self.INIT

    @property
    def allows_runtime_workers(self) -> bool:
        """Legacy compatibility alias; database mode never gates worker runtime.

        Worker execution is controlled only by each worker's ENABLED setting.
        Keep the property temporarily so older callers cannot accidentally
        restore the historical skip-mode coupling.
        """
        return True

    @property
    def allows_database_actions(self) -> bool:
        """Whether lifespan may run its database mutators.

        Keep this explicit alias for callers that need one gate for the
        init startup path. Migration mode performs its migration in the
        entrypoint and must not repeat lifespan mutators. Worker execution is
        intentionally controlled by worker-specific flags.
        """
        return self is self.INIT

    def require_database_actions(self, action: str) -> None:
        if self is self.SKIP:
            raise StartupDatabaseModeError(
                f"Database startup action blocked in skip mode: {action}."
            )
