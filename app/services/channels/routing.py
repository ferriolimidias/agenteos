from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.channels.resolver import ChannelResolutionError, channel_for_provider
from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def infer_provider_for_canal(canal: str | Channel | None) -> Provider:
    raw = str(getattr(canal, "value", canal) or "").strip().lower()
    if raw in {"evolution", "whatsapp", "meta", "meta_whatsapp"}:
        if raw == "meta" or raw == "meta_whatsapp":
            return Provider.META_WHATSAPP
        return Provider.EVOLUTION_LEGACY
    if raw in {"instagram", "meta_instagram"}:
        return Provider.META_INSTAGRAM
    raise ChannelResolutionError(f"Canal sem provider conhecido: {raw or 'vazio'}")


def message_requires_canonical_route(message: Any) -> bool:
    """Return whether an inbound message must be routed fail-closed."""
    if any(
        _clean(getattr(message, field, None))
        for field in ("channel", "provider", "conexao_id", "connection_id")
    ):
        return True

    canal = str(getattr(message, "canal", "") or "").strip().lower()
    return canal in {
        "evolution",
        "whatsapp",
        "meta",
        "meta_whatsapp",
        "instagram",
        "meta_instagram",
    }


@dataclass(frozen=True)
class CanonicalMessageRoute:
    empresa_id: str
    channel: Channel
    provider: Provider
    destination: str
    connection_id: str | None = None
    external_message_id: str | None = None
    external_contact_id: str | None = None
    external_thread_id: str | None = None
    channel_conversation_id: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        if not _clean(self.empresa_id):
            raise ChannelResolutionError("empresa_id obrigatorio na rota canonica.")
        if not _clean(self.destination):
            raise ChannelResolutionError("destination obrigatorio na rota canonica.")
        expected_channel = channel_for_provider(self.provider)
        if self.channel != expected_channel:
            raise ChannelResolutionError(
                f"Provider {self.provider.value} nao atende channel {self.channel.value}."
            )

    def as_state(self) -> dict[str, Any]:
        return {
            "empresa_id": self.empresa_id,
            "channel": self.channel.value,
            "provider": self.provider.value,
            "connection_id": self.connection_id,
            "destination": self.destination,
            "external_message_id": self.external_message_id,
            "external_contact_id": self.external_contact_id,
            "external_thread_id": self.external_thread_id,
            "channel_conversation_id": self.channel_conversation_id,
            "conversation_key": self.conversation_key,
            "metadata": dict(self.metadata or {}),
        }


def route_from_standard_message(message: Any) -> CanonicalMessageRoute:
    provider_raw = _clean(getattr(message, "provider", None))
    provider = normalize_provider(provider_raw) if provider_raw else infer_provider_for_canal(getattr(message, "canal", None))
    channel_raw = _clean(getattr(message, "channel", None)) or getattr(message, "canal", None)
    channel = normalize_channel(channel_raw)
    expected_channel = channel_for_provider(provider)
    if channel != expected_channel:
        raise ChannelResolutionError(
            f"Mensagem inbound tem channel/provider inconsistentes: {channel.value}/{provider.value}."
        )

    destination = _clean(getattr(message, "identificador_origem", None))
    external_contact_id = _clean(getattr(message, "external_contact_id", None)) or destination
    metadata = getattr(message, "metadata", None) or {}
    return CanonicalMessageRoute(
        empresa_id=str(getattr(message, "empresa_id", "") or ""),
        channel=channel,
        provider=provider,
        connection_id=_clean(getattr(message, "conexao_id", None) or getattr(message, "connection_id", None)),
        destination=destination or "",
        external_message_id=_clean(getattr(message, "external_message_id", None)),
        external_contact_id=external_contact_id,
        external_thread_id=_clean(getattr(message, "external_thread_id", None)),
        channel_conversation_id=_clean(getattr(message, "channel_conversation_id", None)),
        conversation_key=_clean(getattr(message, "conversation_key", None)),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def resolve_outbound_route(
    inbound_route: CanonicalMessageRoute,
    requested_state: dict[str, Any] | None = None,
) -> CanonicalMessageRoute:
    state = requested_state or {}

    requested_channel = _clean(state.get("channel") or state.get("canal"))
    if requested_channel and normalize_channel(requested_channel) != inbound_route.channel:
        raise ChannelResolutionError("Resposta tentou alterar channel da conversa original.")

    requested_provider = _clean(state.get("provider"))
    if requested_provider and normalize_provider(requested_provider) != inbound_route.provider:
        raise ChannelResolutionError("Resposta tentou alterar provider da conversa original.")

    requested_empresa_id = _clean(state.get("empresa_id"))
    if requested_empresa_id and requested_empresa_id != inbound_route.empresa_id:
        raise ChannelResolutionError("Resposta tentou alterar empresa_id da conversa original.")

    requested_connection_id = _clean(state.get("connection_id") or state.get("conexao_id"))
    if inbound_route.connection_id and requested_connection_id and requested_connection_id != inbound_route.connection_id:
        raise ChannelResolutionError("Resposta tentou alterar connection_id da conversa original.")

    if not inbound_route.connection_id and requested_connection_id:
        return CanonicalMessageRoute(
            empresa_id=inbound_route.empresa_id,
            channel=inbound_route.channel,
            provider=inbound_route.provider,
            connection_id=requested_connection_id,
            destination=inbound_route.destination,
            external_message_id=inbound_route.external_message_id,
            external_contact_id=inbound_route.external_contact_id,
            external_thread_id=inbound_route.external_thread_id,
            channel_conversation_id=inbound_route.channel_conversation_id,
            conversation_key=inbound_route.conversation_key,
            metadata=dict(inbound_route.metadata or {}),
        )

    return inbound_route


def dispatch_canal_for_route(route: CanonicalMessageRoute) -> str:
    if route.provider == Provider.EVOLUTION_LEGACY:
        return "evolution"
    if route.provider == Provider.META_WHATSAPP:
        return "meta"
    return route.channel.value


def conversation_runtime_key(route: CanonicalMessageRoute, *, lead_id: str | None = None) -> str:
    connection = route.connection_id or "no-connection"
    conversation = (
        (f"conversation:{route.channel_conversation_id}" if route.channel_conversation_id else None)
        or route.conversation_key
        or (f"lead:{lead_id}" if _clean(lead_id) else None)
        or route.external_thread_id
        or route.external_contact_id
        or route.destination
    )
    parts = [
        route.empresa_id,
        route.channel.value,
        route.provider.value,
        connection,
        str(conversation or "").strip(),
    ]
    return ":".join(part.replace(":", "_") for part in parts if part)


def runtime_key_from_standard_message(message: Any) -> str:
    try:
        return conversation_runtime_key(
            route_from_standard_message(message),
            lead_id=_clean(getattr(message, "lead_id", None)),
        )
    except Exception:
        empresa_id = _clean(getattr(message, "empresa_id", None)) or "no-tenant"
        canal = _clean(getattr(message, "channel", None) or getattr(message, "canal", None)) or "no-channel"
        provider = _clean(getattr(message, "provider", None)) or "no-provider"
        connection = _clean(
            getattr(message, "conexao_id", None) or getattr(message, "connection_id", None)
        ) or "no-connection"
        canonical_conversation_id = _clean(getattr(message, "channel_conversation_id", None))
        canonical_lead_id = _clean(getattr(message, "lead_id", None))
        conversation = (
            (f"conversation:{canonical_conversation_id}" if canonical_conversation_id else None)
            or _clean(getattr(message, "conversation_key", None))
            or (f"lead:{canonical_lead_id}" if canonical_lead_id else None)
            or _clean(getattr(message, "external_thread_id", None))
            or _clean(getattr(message, "external_contact_id", None))
            or _clean(getattr(message, "identificador_origem", None))
            or "no-origin"
        )
        return ":".join(
            part.replace(":", "_")
            for part in (empresa_id, canal, provider, connection, conversation)
        )
