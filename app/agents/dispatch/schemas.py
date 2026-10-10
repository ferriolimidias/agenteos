from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DispatchRequest:
    tenant_id: str | None
    lead_id: str | None
    channel: str
    phone: str | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DispatchResult:
    dispatched: bool
    reason: str = "dry_run"
    provider_message_id: str | None = None
