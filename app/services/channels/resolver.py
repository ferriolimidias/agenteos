from __future__ import annotations

from typing import Any

from app.services.channels.contracts import ChannelConnectionContext, ChannelOutboundMessage
from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider


ACTIVE_CONNECTION_STATUSES = {"ativo", "connected", "conectado", "open", "online"}


class ChannelResolutionError(ValueError):
    pass


def provider_for_connection_type(tipo: Any) -> Provider:
    raw = str(getattr(tipo, "value", tipo) or "").strip().lower()
    if raw == "evolution":
        return Provider.EVOLUTION_LEGACY
    if raw == "meta":
        return Provider.META_WHATSAPP
    if raw == "instagram":
        return Provider.META_INSTAGRAM
    raise ChannelResolutionError(f"Tipo de conexao sem provider conhecido: {raw or 'vazio'}")


def channel_for_provider(provider: Provider) -> Channel:
    provider = normalize_provider(provider)
    if provider in {Provider.EVOLUTION_LEGACY, Provider.META_WHATSAPP}:
        return Channel.WHATSAPP
    if provider == Provider.META_INSTAGRAM:
        return Channel.INSTAGRAM
    raise ChannelResolutionError(f"Provider sem channel conhecido: {provider}")


def context_from_connection(
    conexao: Any,
    *,
    channel: str | Channel | None = None,
    provider: str | Provider | None = None,
) -> ChannelConnectionContext:
    if conexao is None:
        raise ChannelResolutionError("Conexao ausente para resolver provider.")

    connection_provider = getattr(conexao, "provider", None)
    resolved_provider = normalize_provider(provider or connection_provider) if (provider or connection_provider) else provider_for_connection_type(
        getattr(conexao, "tipo", None)
    )
    resolved_channel = normalize_channel(channel) if channel else channel_for_provider(resolved_provider)
    expected_channel = channel_for_provider(resolved_provider)
    if resolved_channel != expected_channel:
        raise ChannelResolutionError(
            f"Provider {resolved_provider.value} nao atende channel {resolved_channel.value}."
        )

    status = str(getattr(conexao, "status", "") or "").strip()
    if status and status.lower() not in ACTIVE_CONNECTION_STATUSES:
        raise ChannelResolutionError(f"Conexao {getattr(conexao, 'id', '')} inativa: {status}.")

    credenciais = dict(getattr(conexao, "credenciais", None) or {})
    external_account_id = (
        getattr(conexao, "phone_number_id", None)
        or credenciais.get("evolution_instance")
        or credenciais.get("resource_id")
        or getattr(conexao, "nome_instancia", None)
    )
    return ChannelConnectionContext(
        empresa_id=str(getattr(conexao, "empresa_id", "") or ""),
        connection_id=str(getattr(conexao, "id", "") or ""),
        channel=resolved_channel,
        provider=resolved_provider,
        status=status or "ativo",
        external_account_id=str(external_account_id) if external_account_id else None,
        metadata={"nome_instancia": getattr(conexao, "nome_instancia", None)},
    )


def assert_context_matches_request(
    context: ChannelConnectionContext,
    *,
    connection_id: str | None = None,
    channel: str | Channel | None = None,
    provider: str | Provider | None = None,
) -> None:
    if connection_id and str(context.connection_id) != str(connection_id):
        raise ChannelResolutionError("connection_id resolvido nao corresponde ao solicitado.")
    if channel and context.channel != normalize_channel(channel):
        raise ChannelResolutionError("channel resolvido nao corresponde ao solicitado.")
    if provider and context.provider != normalize_provider(provider):
        raise ChannelResolutionError("provider resolvido nao corresponde ao solicitado.")


def assert_message_matches_context(
    message: ChannelOutboundMessage,
    context: ChannelConnectionContext,
) -> None:
    if str(message.empresa_id) != str(context.empresa_id):
        raise ChannelResolutionError("Mensagem outbound pertence a outro tenant.")
    if str(message.connection_id) != str(context.connection_id):
        raise ChannelResolutionError("Mensagem outbound pertence a outra conexao.")
    if message.channel != context.channel:
        raise ChannelResolutionError("Mensagem outbound pertence a outro channel.")
    if message.provider != context.provider:
        raise ChannelResolutionError("Mensagem outbound pertence a outro provider.")
