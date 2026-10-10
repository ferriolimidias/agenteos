from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.llm.base import AgentLLM


class MockAgentLLM(AgentLLM):
    def __init__(
        self,
        responses: list[str] | None = None,
        response_factory: Callable[[str, list[dict], dict[str, Any] | None], str] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.response_factory = response_factory
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        tools_context: dict | None = None,
    ) -> str:
        call = {
            "system_prompt": system_prompt,
            "messages": [dict(item) for item in messages],
            "tools_context": dict(tools_context or {}),
        }
        self.calls.append(call)

        if self.responses:
            return str(self.responses.pop(0))
        if self.response_factory is not None:
            return str(self.response_factory(system_prompt, messages, tools_context))
        return self._fallback_response(tools_context, messages)

    def _fallback_response(self, tools_context: dict | None, messages: list[dict]) -> str:
        context = dict(tools_context or {})
        if context.get("handoff"):
            details = context.get("handoff", {})
            summary = str(details.get("context_summary") or "").strip()
            if summary:
                return f"Perfeito. Vou encaminhar seu atendimento para uma pessoa da equipe. {summary}"
            return "Perfeito. Vou encaminhar seu atendimento para uma pessoa da equipe."

        agenda = context.get("agenda", {})
        missing_fields = [str(item) for item in agenda.get("missing_fields") or [] if str(item or "").strip()]
        if missing_fields:
            formatted = ", ".join(missing_fields)
            return f"Para continuar com o agendamento, me envie: {formatted}."

        budget = context.get("orcamento", {})
        estimate = budget.get("estimate") if isinstance(budget, dict) else {}
        if isinstance(estimate, dict) and estimate.get("amount"):
            amount = estimate.get("amount")
            currency = estimate.get("currency") or "BRL"
            if currency == "BRL":
                currency_text = "R$"
            else:
                currency_text = str(currency)
            if budget.get("missing_fields"):
                missing = ", ".join(str(item) for item in budget.get("missing_fields") or [] if str(item or "").strip())
                if missing:
                    return f"Para fechar a estimativa, ainda preciso de: {missing}."
            next_action = "Quer que eu te ajude a agendar?" if estimate.get("next_action") == "offer_scheduling" else ""
            return f"Perfeito. O orçamento prévio fica em {currency_text} {amount}. {next_action}".strip()

        current = ""
        if messages:
            current = str(messages[-1].get("content") or "").strip()
        if current:
            lower = current.lower()
            if any(term in lower for term in ("agendar", "marcar", "horário", "horario")):
                return "Perfeito. Para continuar com o agendamento, me envie nome, endereço ou bairro, melhor dia e melhor turno."
            if any(term in lower for term in ("preço", "preco", "valor", "orçamento", "orcamento", "custa")):
                return "Posso te ajudar com uma estimativa. Me diga qual serviço você precisa e os detalhes principais."
        return "Posso te ajudar com isso. Me mande mais alguns detalhes para eu seguir."
