from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.channels.contracts import ChannelConnectionContext, ChannelProviderAdapter
from app.services.channels.resolver import ChannelResolutionError
from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider

AdapterFactory = Callable[[ChannelConnectionContext, Any | None], ChannelProviderAdapter]


class ChannelProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[Channel, Provider], AdapterFactory] = {}

    def register(
        self,
        channel: str | Channel,
        provider: str | Provider,
        factory: AdapterFactory,
    ) -> None:
        key = (normalize_channel(channel), normalize_provider(provider))
        self._factories[key] = factory

    def resolve(
        self,
        context: ChannelConnectionContext,
        *,
        connection: Any | None = None,
    ) -> ChannelProviderAdapter:
        key = (context.channel, context.provider)
        factory = self._factories.get(key)
        if factory is None:
            raise ChannelResolutionError(
                f"Provider nao registrado para channel={context.channel.value} "
                f"provider={context.provider.value}."
            )
        return factory(context, connection)

    def registered_keys(self) -> set[tuple[Channel, Provider]]:
        return set(self._factories)


default_registry = ChannelProviderRegistry()
