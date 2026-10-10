from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_text: bool
    supports_media: bool = False
    supports_templates: bool = False
    supports_mark_read: bool = False
    supports_freeform: bool = True
    supports_status_webhook: bool = False
    supports_multiple_connections: bool = True


@dataclass(frozen=True)
class ChannelConnectionContext:
    empresa_id: str
    connection_id: str
    channel: Channel
    provider: Provider
    status: str
    external_account_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        if not str(self.empresa_id or "").strip():
            raise ValueError("empresa_id obrigatorio no contexto de conexao.")
        if not str(self.connection_id or "").strip():
            raise ValueError("connection_id obrigatorio no contexto de conexao.")


@dataclass(frozen=True)
class ChannelOutboundMessage:
    empresa_id: str
    connection_id: str
    channel: Channel
    provider: Provider
    destination: str
    message_type: str = "text"
    text: str | None = None
    media: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        if not str(self.empresa_id or "").strip():
            raise ValueError("empresa_id obrigatorio na mensagem outbound.")
        if not str(self.connection_id or "").strip():
            raise ValueError("connection_id obrigatorio na mensagem outbound.")
        if not str(self.destination or "").strip():
            raise ValueError("destination obrigatorio na mensagem outbound.")


@dataclass(frozen=True)
class ChannelInboundMessage:
    empresa_id: str
    connection_id: str
    channel: Channel
    provider: Provider
    external_message_id: str
    external_contact_id: str
    external_thread_id: str | None = None
    message_type: str = "text"
    text: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        if not str(self.connection_id or "").strip():
            raise ValueError("connection_id obrigatorio na mensagem inbound.")
        if not str(self.external_message_id or "").strip():
            raise ValueError("external_message_id obrigatorio na mensagem inbound.")
        if not str(self.external_contact_id or "").strip():
            raise ValueError("external_contact_id obrigatorio na mensagem inbound.")


@runtime_checkable
class ChannelProviderAdapter(Protocol):
    @property
    def provider_name(self) -> Provider:
        ...

    @property
    def channel_name(self) -> Channel:
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        ...

    async def send_text(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        ...

    async def send_audio(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        ...

    async def list_templates(self, *, waba_id: str, after: str | None = None) -> dict[str, Any]:
        ...

    async def validate_template_for_use(self, *, template: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        ...

    async def health_check(self) -> dict[str, Any]:
        ...
