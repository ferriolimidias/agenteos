from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

INSTAGRAM_GRAPH_VERSION = "v26.0"


@dataclass(frozen=True)
class InstagramApiSettings:
    app_secret: str | None
    verify_token: str | None
    graph_api_version: str
    updated_at: datetime | None

    @property
    def configured(self) -> bool:
        return bool(self.app_secret and self.verify_token)


def _clean(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned or cleaned.casefold() in {"null", "none"}:
        return None
    return cleaned


async def load_instagram_api_settings(_session: object | None = None) -> InstagramApiSettings:
    """Resolve webhook credentials exclusively from the deployment environment.

    Legacy encrypted columns remain in the database for rollback/history, but are
    intentionally not read or written by the webhook runtime.
    """
    return InstagramApiSettings(
        app_secret=_clean(os.getenv("META_APP_SECRET")),
        verify_token=_clean(os.getenv("META_WEBHOOK_VERIFY_TOKEN")),
        graph_api_version=INSTAGRAM_GRAPH_VERSION,
        updated_at=None,
    )
