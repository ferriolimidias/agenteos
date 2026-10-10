from __future__ import annotations

from abc import ABC, abstractmethod


class AgentLLM(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        tools_context: dict | None = None,
    ) -> str:
        raise NotImplementedError

