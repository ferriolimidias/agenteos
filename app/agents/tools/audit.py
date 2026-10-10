from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class ToolAuditEvent:
    tool_name: str
    tenant_id: str | None
    specialist_key: str
    allowed: bool
    reason_codes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryToolAudit:
    def __init__(self) -> None:
        self.events: list[ToolAuditEvent] = []

    def record(self, event: ToolAuditEvent) -> None:
        self.events.append(event)
