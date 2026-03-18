from abc import ABC, abstractmethod
from typing import Any

from app.services.mensageria.schemas import (
    StandardIncomingMessage,
    StandardOutgoingMessage,
)


class BaseProvider(ABC):
    @abstractmethod
    def parse_webhook(self, payload: dict) -> StandardIncomingMessage:
        raise NotImplementedError

    @abstractmethod
    async def send_text(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def send_media(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def send_audio(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
