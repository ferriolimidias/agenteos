from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.channels.contracts import (
    ChannelConnectionContext,
    ChannelOutboundMessage,
    ProviderCapabilities,
)
from app.services.channels.resolver import ChannelResolutionError, assert_message_matches_context
from app.services.channels.types import Channel, Provider


async def _dispatch_outbound_message(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from app.services.mensageria.dispatcher import dispatch_outbound_message

    return await dispatch_outbound_message(*args, **kwargs)


def _build_standard_outgoing_message(**kwargs: Any) -> Any:
    try:
        from app.services.mensageria.schemas import StandardOutgoingMessage

        return StandardOutgoingMessage(**kwargs)
    except ModuleNotFoundError:
        return SimpleNamespace(**kwargs)


class EvolutionLegacyProvider:
    def __init__(self, context: ChannelConnectionContext, conexao: Any | None = None) -> None:
        self.context = context
        self.conexao = conexao

    @property
    def provider_name(self) -> Provider:
        return Provider.EVOLUTION_LEGACY

    @property
    def channel_name(self) -> Channel:
        return Channel.WHATSAPP

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_media=True,
            supports_templates=False,
            supports_mark_read=False,
            supports_freeform=True,
            supports_status_webhook=False,
            supports_multiple_connections=True,
        )

    async def send_text(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        assert_message_matches_context(message, self.context)
        if self.conexao is None:
            raise ChannelResolutionError("Conexao Evolution ausente para envio.")
        payload = _build_standard_outgoing_message(
            identificador_contato=str(message.destination or "").strip(),
            canal=message.channel.value,
            texto=message.text or "",
            tipo="text",
            media_url=None,
        )
        return await _dispatch_outbound_message(
            empresa_id=self.context.empresa_id,
            conexao=self.conexao,
            payload=payload,
        )

    async def send_audio(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        assert_message_matches_context(message, self.context)
        if self.conexao is None:
            raise ChannelResolutionError("Conexao Evolution ausente para envio.")
        media = message.media if isinstance(message.media, dict) else {}
        payload = _build_standard_outgoing_message(
            identificador_contato=str(message.destination or "").strip(),
            canal=message.channel.value,
            texto=str(message.text or ""),
            tipo="audio",
            media_url=str(media.get("url") or media.get("path") or ""),
        )
        return await _dispatch_outbound_message(
            empresa_id=self.context.empresa_id,
            conexao=self.conexao,
            payload=payload,
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name.value,
            "channel": self.channel_name.value,
            "connection_id": self.context.connection_id,
            "status": self.context.status,
        }
