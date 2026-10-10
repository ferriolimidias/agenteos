from __future__ import annotations

from typing import Any, Mapping

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.schemas import AtendimentoAgentInput, AtendimentoAgentOutput, IncomingMessage
from app.agents.state.builder import build_conversation_state
from app.agents.state.memory import memory_from_state
from app.agents.state.repository import ConversationStateRepository


class NewAgentGraph:
    """Interface produtiva compatível com o antigo `graph.ainvoke`, usando apenas `app/agents`."""

    def __init__(
        self,
        atendimento_agent: AtendimentoAgent | None = None,
        state_repository: ConversationStateRepository | None = None,
    ) -> None:
        self.atendimento_agent = atendimento_agent or AtendimentoAgent()
        self.state_repository = state_repository or ConversationStateRepository()

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        final_state = dict(state or {})
        message = self._message_from_state(final_state)
        persisted = await self.state_repository.load(
            empresa_id=str(final_state.get("empresa_id") or "").strip() or None,
            lead_id=str(final_state.get("lead_id") or "").strip() or None,
            canal=str(final_state.get("canal") or "whatsapp").strip() or "whatsapp",
            identificador=str(final_state.get("identificador_origem") or final_state.get("telefone") or "").strip() or None,
        )
        if persisted:
            final_state["conversation_state"] = persisted
        conversation_state = build_conversation_state(final_state, incoming=message)
        output = await self.atendimento_agent.run(
            AtendimentoAgentInput(
                state=conversation_state,
                messages=[message] if message else [],
            )
        )
        await self.state_repository.save(conversation_state)
        self._apply_output(final_state, conversation_state, output)
        return final_state

    def _apply_output(
        self,
        final_state: dict[str, Any],
        conversation_state,
        output: AtendimentoAgentOutput,
    ) -> None:
        final_state["resposta_final"] = output.response_text or None
        final_state["conversation_state"] = self._conversation_state_payload(conversation_state)
        final_state["pendencias"] = self._build_pendencias(output)
        final_state["lead_id"] = final_state.get("lead_id") or conversation_state.lead_id
        final_state["status_atendimento"] = conversation_state.status_atendimento
        final_state["has_image"] = conversation_state.has_image
        final_state["media_type"] = conversation_state.media_type
        final_state["media_mime_type"] = conversation_state.media_mime_type
        final_state["caption"] = conversation_state.caption
        final_state["image_analysis"] = conversation_state.image_analysis
        final_state["image_analysis_status"] = conversation_state.image_analysis_status
        final_state["status_conversa"] = self._status_conversa(final_state, output)
        final_state["intencao"] = [output.decision.intent] if output.decision.intent else []
        final_state["ferramentas_selecionadas"] = self._selected_tools(output)
        final_state["resumos_ferramentas"] = self._tool_summaries(output)
        final_state["acoes_sistema_pendentes"] = self._action_payloads(output)
        final_state["guard_result"] = {
            "allowed": bool(output.guard_result.allowed) if output.guard_result else True,
            "sanitized": bool(output.guard_result.sanitized) if output.guard_result else False,
            "reason_codes": list(output.guard_result.reason_codes) if output.guard_result else [],
        }
        final_state["agent_metadata"] = output.metadata
        if output.first_turn_decision is not None:
            final_state["first_turn_decision"] = output.first_turn_decision.model_dump()
        final_state["agent_architecture"] = "app.agents"

    def _message_from_state(self, state: Mapping[str, Any]) -> IncomingMessage | None:
        text = str(state.get("mensagem_atual") or "").strip()
        if not text:
            text = self._latest_user_text(state.get("mensagens") or state.get("messages") or [])
        if not text and bool(state.get("has_image")):
            text = str(state.get("caption") or "").strip() or "[Imagem]"
        if not text:
            return None
        return IncomingMessage(
            text=text,
            tenant_id=str(state.get("empresa_id") or "").strip() or None,
            lead_id=str(state.get("lead_id") or "").strip() or None,
            phone=str(state.get("identificador_origem") or state.get("telefone") or "").strip() or None,
            channel=str(state.get("canal") or "whatsapp").strip() or "whatsapp",
            media_type=str(state.get("media_type") or "").strip() or None,
            media_url=str(state.get("media_url") or "").strip() or None,
            media_id=str(state.get("media_id") or "").strip() or None,
            media_mime_type=str(state.get("media_mime_type") or "").strip() or None,
            caption=str(state.get("caption") or "").strip() or None,
            has_image=bool(state.get("has_image")),
            image_analysis=state.get("image_analysis") if isinstance(state.get("image_analysis"), dict) else None,
            metadata={
                "conversation_id": str(state.get("conversation_id") or state.get("channel_conversation_id") or "").strip() or None,
                "media_messages": state.get("media_messages") or [],
                "has_image": bool(state.get("has_image")),
                "image_analysis_available": bool(state.get("image_analysis_available")),
                "image_analysis": state.get("image_analysis") if isinstance(state.get("image_analysis"), dict) else None,
                "image_analysis_status": state.get("image_analysis_status"),
            },
        )

    def _latest_user_text(self, messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for item in reversed(messages):
            text = str(item or "").strip()
            if not text:
                continue
            lower = text.lower()
            if lower.startswith(("assistente:", "ia:")):
                continue
            if ":" in text and lower.startswith(("usuario:", "usuário:", "cliente:")):
                return text.split(":", 1)[1].strip()
            return text
        return ""

    def _build_pendencias(self, output: AtendimentoAgentOutput) -> list[str]:
        if not output.decision.should_respond:
            return ["sem_resposta_ia_bloqueada"]
        tool_results = self._tool_results(output)
        missing = []
        for result in tool_results:
            missing.extend([str(item) for item in result.get("missing_fields") or [] if str(item or "").strip()])
        return missing

    def _status_conversa(self, state: Mapping[str, Any], output: AtendimentoAgentOutput) -> str | None:
        if not output.decision.should_respond:
            return str(state.get("status_conversa") or state.get("status_atendimento") or "MANUAL")
        if output.decision.specialist_key == "transferencia" or any(item == "handoff" for item in self._selected_tools(output)):
            return "HANDOFF"
        return str(state.get("status_conversa") or "IA_ATIVA")

    def _tool_results(self, output: AtendimentoAgentOutput) -> list[dict[str, Any]]:
        if not output.metadata:
            return []
        tool_results = output.metadata.get("tool_results")
        return list(tool_results) if isinstance(tool_results, list) else []

    def _selected_tools(self, output: AtendimentoAgentOutput) -> list[str]:
        if not output.metadata:
            return []
        tools = output.metadata.get("selected_tools")
        return [str(item) for item in tools] if isinstance(tools, list) else []

    def _tool_summaries(self, output: AtendimentoAgentOutput) -> list[str]:
        summaries: list[str] = []
        for result in self._tool_results(output):
            data = result.get("data") if isinstance(result, dict) else {}
            if isinstance(data, dict):
                summary = str(data.get("summary") or "").strip()
                if summary:
                    summaries.append(summary)
        return summaries

    def _action_payloads(self, output: AtendimentoAgentOutput) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for result in self._tool_results(output):
            data = result.get("data") if isinstance(result, dict) else {}
            if not isinstance(data, dict):
                continue
            for action in data.get("actions") or []:
                if isinstance(action, dict):
                    payloads.append(
                        {
                            "action_type": action.get("action_type"),
                            "scope": action.get("scope"),
                            "risk": action.get("risk"),
                            "requires_confirmation": bool(action.get("requires_confirmation")),
                        }
                    )
        return payloads

    def _conversation_state_payload(self, state) -> dict[str, Any]:
        return memory_from_state(state)


graph = NewAgentGraph()


async def run_new_agent_flow(
    state: Mapping[str, Any] | None = None,
    message: IncomingMessage | None = None,
    atendimento_agent: AtendimentoAgent | None = None,
) -> AtendimentoAgentOutput:
    conversation_state = build_conversation_state(state, incoming=message)
    if message is None and conversation_state.mensagem_atual:
        message = IncomingMessage(
            text=conversation_state.mensagem_atual,
            tenant_id=conversation_state.empresa_id,
            lead_id=conversation_state.lead_id,
            phone=conversation_state.telefone,
            channel=conversation_state.canal,
            media_type=conversation_state.media_type,
            media_url=conversation_state.media_url,
            media_id=conversation_state.media_id,
            media_mime_type=conversation_state.media_mime_type,
            caption=conversation_state.caption,
            has_image=conversation_state.has_image,
            image_analysis=conversation_state.image_analysis,
            metadata={
                "conversation_id": str((state or {}).get("conversation_id") or (state or {}).get("channel_conversation_id") or "").strip() or None,
                "media_messages": (state or {}).get("media_messages") if isinstance(state, Mapping) else [],
                "has_image": bool((state or {}).get("has_image")) if isinstance(state, Mapping) else False,
                "image_analysis_available": bool((state or {}).get("image_analysis_available")) if isinstance(state, Mapping) else False,
                "image_analysis": (state or {}).get("image_analysis") if isinstance(state, Mapping) else None,
                "image_analysis_status": (state or {}).get("image_analysis_status") if isinstance(state, Mapping) else None,
            },
        )
    agent = atendimento_agent or AtendimentoAgent()
    return await agent.run(AtendimentoAgentInput(state=conversation_state, messages=[message] if message else []))
