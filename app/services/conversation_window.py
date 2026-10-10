from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider


DEFAULT_WINDOW_HOURS = 24


@dataclass(frozen=True)
class ConversationWindowResult:
    state: str
    reason: str
    reference_at: datetime | None
    policy: dict[str, Any]


def _to_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_conversation_window(
    *,
    conversation: Any,
    provider: str | Provider,
    channel: str | Channel,
    now: datetime | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> ConversationWindowResult:
    ch = normalize_channel(channel)
    pr = normalize_provider(provider)
    policy = {
        "kind": "rolling_inbound_window",
        "window_hours": int(window_hours),
        "channel": ch.value,
        "provider": pr.value,
    }

    if ch != Channel.WHATSAPP:
        return ConversationWindowResult("unsupported", "channel_without_window_policy", None, policy)
    if pr not in {Provider.EVOLUTION_LEGACY, Provider.META_WHATSAPP}:
        return ConversationWindowResult("unsupported", "provider_without_window_policy", None, policy)

    reference = _to_aware_utc(getattr(conversation, "last_inbound_at", None))
    if reference is None:
        return ConversationWindowResult("unknown", "missing_reliable_last_inbound_at", None, policy)

    now_utc = _to_aware_utc(now or datetime.utcnow())
    if now_utc is None:
        return ConversationWindowResult("unknown", "invalid_now", reference, policy)
    if reference > now_utc + timedelta(seconds=5):
        return ConversationWindowResult("unknown", "last_inbound_at_in_future", reference, policy)

    boundary = reference + timedelta(hours=max(1, int(window_hours)))
    if now_utc <= boundary:
        return ConversationWindowResult("open", "last_inbound_inside_window", reference, policy)
    return ConversationWindowResult("closed", "last_inbound_outside_window", reference, policy)
