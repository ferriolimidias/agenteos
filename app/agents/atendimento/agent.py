from __future__ import annotations

import asyncio
import os
import uuid
import logging
import json
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.secret_sanitizer import sanitize_for_log
from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt, build_first_turn_extension_system_prompt
from app.agents.fiscal.response_fiscal import AgenteFiscal, FiscalFinding
from app.agents.guards.response_guard import ResponseGuard
from app.agents.llm import AgentLLM, MockAgentLLM, OpenAIAgentLLM
from app.agents.planner.tool_planner import AgentToolPlanner, LLMToolPlanner, MockToolPlanner, ToolPlan
from app.agents.schemas import (
    AtendimentoAgentInput,
    AtendimentoAgentOutput,
    ConversationState,
    FirstTurnAgentDecision,
    GuardResult,
    IncomingMessage,
    IntentDecision,
)
from app.agents.state.memory import apply_memory_to_state, memory_from_state, normalize_memory
from app.agents.tenant_config import build_tenant_agent_config
from app.agents.tools.registry import AgentToolResult, ToolRegistry
from app.agents.vision.image_analysis import ImageAnalysisRequest, ImageAnalysisService


logger = logging.getLogger(__name__)


class TenantLLMUnavailable(RuntimeError):
    pass


def _explicit_mock_llm_allowed() -> bool:
    mode = os.getenv("AGENTEOS_LLM_MODE", "").strip().lower()
    env = os.getenv("AGENTEOS_ENV", "").strip().lower()
    return (
        mode in {"mock", "test", "safe_mock"}
        or env in {"test", "testing", "ci"}
        or os.getenv("PYTEST_CURRENT_TEST") is not None
    )


class AtendimentoAgent:
    """Camada única autorizada a produzir a resposta final ao cliente no fluxo LLM-first."""

    def __init__(
        self,
        guard: ResponseGuard | None = None,
        image_analysis_service: ImageAnalysisService | None = None,
        llm: AgentLLM | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_planner: AgentToolPlanner | None = None,
        fiscal: AgenteFiscal | None = None,
    ) -> None:
        self.guard = guard or ResponseGuard()
        self.image_analysis_service = image_analysis_service or ImageAnalysisService()
        self.llm = llm
        self.tool_registry = tool_registry or ToolRegistry()
        self.tool_planner = tool_planner
        self.fiscal = fiscal or AgenteFiscal()

    async def run(self, agent_input: AtendimentoAgentInput) -> AtendimentoAgentOutput:
        state = agent_input.state
        message = self._latest_message(agent_input.messages, state)
        if message and message.text:
            state.mensagem_atual = message.text
            self._sync_media_from_message(state, message)

        tenant_config = await self._load_tenant_config(state.empresa_id)
        if not bool(tenant_config.get("has_ai")):
            decision = IntentDecision(
                intent=None,
                specialist_key=None,
                confidence=0.0,
                reason_codes=["tenant_ai_disabled"],
                should_respond=False,
            )
            guard_result = GuardResult(
                allowed=True,
                text="",
                reason_codes=["tenant_ai_disabled"],
                sanitized=False,
            )
            if message and str(message.text or "").strip():
                self._update_state_memory(state, [], message)
            return AtendimentoAgentOutput(
                response_text="",
                decision=decision,
                guard_result=guard_result,
                should_dispatch=False,
                metadata=self._build_metadata(
                    state,
                    tenant_config,
                    decision,
                    [],
                    {},
                    guard_result,
                    plan=self._empty_plan("tenant_ai_disabled"),
                    blocked_reason="tenant_ai_disabled",
                ),
            )

        if state.has_image and not state.image_analysis:
            analysis = await self.image_analysis_service.analyze(
                ImageAnalysisRequest(
                    tenant_id=state.empresa_id,
                    media_url=state.media_url,
                    media_id=state.media_id,
                    media_mime_type=state.media_mime_type,
                    caption=state.caption,
                    metadata=state.metadata,
                )
            )
            state.image_analysis = analysis.to_dict()
            state.image_analysis_status = analysis.status
            state.metadata["image_analysis"] = state.image_analysis
            state.metadata["image_analysis_status"] = analysis.status
            state.metadata["image_analysis_available"] = bool(analysis.available)

        memory = normalize_memory(memory_from_state(state), state)
        should_respond = not (
            state.ia_bloqueada
            or str(state.status_atendimento or "").lower() in {"manual", "aguardando_humano", "handoff"}
        )
        decision = IntentDecision(intent=None, specialist_key=None, confidence=0.0, reason_codes=["llm_first"], should_respond=should_respond)

        tool_results: list[AgentToolResult] = []
        tools_context: dict[str, Any] = {}
        if decision.should_respond:
            try:
                plan = await self._resolve_tool_planner(tenant_config).plan(
                    state=state,
                    tenant_config=tenant_config,
                    memory=memory,
                    history=self._build_llm_messages(state, message),
                    available_tools=self.tool_registry.available_tools(tenant_config),
                )
            except Exception as exc:
                reason_code = "tenant_tool_planner_error"
                operational_error = str(sanitize_for_log(str(exc)))[:300]
                guard_result = GuardResult(
                    allowed=False,
                    text="",
                    reason_codes=[reason_code],
                    sanitized=True,
                )
                self._update_state_memory(state, [], message)
                return AtendimentoAgentOutput(
                    response_text="",
                    decision=decision,
                    guard_result=guard_result,
                    should_dispatch=False,
                    metadata=self._build_metadata(
                        state,
                        tenant_config,
                        decision,
                        [],
                        {},
                        guard_result,
                        plan=self._empty_plan(reason_code),
                        operational_error=operational_error,
                        blocked_reason=reason_code,
                    ),
                )
            logger.warning(
                "ai.agent.decision empresa_id=%s lead_id=%s selected_tools=%s intent_present=%s",
                sanitize_for_log(str(state.empresa_id or "")),
                sanitize_for_log(str(state.lead_id or "")),
                ",".join(str(item) for item in plan.get("selected_tools") or []),
                bool(str(plan.get("intent_summary") or "").strip()),
            )
            self._schedule_conversion_monitor(
                state=state,
                tenant_config=tenant_config,
                message=message,
            )
            commercial_state_result = {"status": "no_change"}
            if state.lead_id and (plan.get("commercial_state") or {}):
                try:
                    from app.services.commercial_state_executor import (
                        decision_from_plan,
                        execute_commercial_state_for_lead,
                    )

                    commercial_state_result = await execute_commercial_state_for_lead(
                        empresa_id=state.empresa_id,
                        lead_id=state.lead_id,
                        decision=decision_from_plan(plan),
                        source="ai",
                        source_event_id=str(getattr(message, "message_id", None) or "") or None,
                    )
                except Exception as exc:
                    logger.warning(
                        "commercial_state.execution_failed empresa_id=%s lead_id=%s reason=%s",
                        sanitize_for_log(str(state.empresa_id or "")),
                        sanitize_for_log(str(state.lead_id or "")),
                        sanitize_for_log(str(exc))[:200],
                    )
                    commercial_state_result = {"status": "blocked", "reason": "commercial_state_execution_failed"}
            tool_results = await self.tool_registry.run_planned_tools(
                state=state,
                plan=plan,
                message=message,
                tenant_config=tenant_config,
            )
            tools_context = self.tool_registry.build_tools_context(tool_results)
            tools_context["commercial_state"] = commercial_state_result
            failed_anamnese = next(
                (result for result in tool_results if result.get("name") == "anamnese" and not result.get("ok")),
                None,
            )
            if failed_anamnese:
                reason_code = "anamnese_tool_error"
                guard_result = GuardResult(allowed=False, text="", reason_codes=[reason_code], sanitized=True)
                self._update_state_memory(state, tool_results, message)
                return AtendimentoAgentOutput(
                    response_text="",
                    decision=decision,
                    guard_result=guard_result,
                    should_dispatch=False,
                    metadata=self._build_metadata(
                        state,
                        tenant_config,
                        decision,
                        tool_results,
                        tools_context,
                        guard_result,
                        plan=plan,
                        operational_error=str(failed_anamnese.get("error") or reason_code),
                        blocked_reason=reason_code,
                    ),
                )
        else:
            plan = self._empty_plan("no_response_expected")

        if not decision.should_respond:
            guard_result = GuardResult(allowed=True, text="", reason_codes=["no_response_expected"], sanitized=False)
            self._update_state_memory(state, tool_results, message)
            return AtendimentoAgentOutput(
                response_text="",
                decision=decision,
                guard_result=guard_result,
                should_dispatch=False,
                metadata=self._build_metadata(state, tenant_config, decision, tool_results, tools_context, guard_result),
            )

        llm_provider = self._resolve_llm_provider(tenant_config)
        prompt_memory = self._build_prompt_memory(state, message, tool_results)
        system_prompt = (
            build_first_turn_extension_system_prompt(tenant_config, prompt_memory)
            if state.first_agent_response
            else build_atendimento_system_prompt(tenant_config, memory=prompt_memory)
        )
        llm_messages = self._build_llm_messages(state, message)
        try:
            draft = await llm_provider.generate(
                system_prompt=system_prompt,
                messages=llm_messages,
                tools_context=tools_context,
            )
        except TenantLLMUnavailable as exc:
            operational_error = str(exc)
            reason_code = "tenant_llm_unavailable"
        except Exception as exc:
            operational_error = str(sanitize_for_log(str(exc)))[:300]
            reason_code = "tenant_llm_provider_error"
        else:
            operational_error = None
            reason_code = None

        if not reason_code and not str(draft or "").strip():
            operational_error = "OpenAI returned empty response"
            reason_code = "tenant_llm_empty_response"

        if reason_code:
            guard_result = GuardResult(
                allowed=False,
                text="",
                reason_codes=[reason_code],
                sanitized=True,
            )
            self._update_state_memory(state, tool_results, message)
            return AtendimentoAgentOutput(
                response_text="",
                decision=decision,
                guard_result=guard_result,
                should_dispatch=False,
                metadata=self._build_metadata(
                    state,
                    tenant_config,
                    decision,
                    tool_results,
                    tools_context,
                    guard_result,
                    plan=plan,
                    operational_error=operational_error,
                    blocked_reason=reason_code,
                ),
            )

        if state.first_agent_response:
            first_turn_decision = self._parse_first_turn_decision(draft, tenant_config)
            if first_turn_decision is None:
                first_turn_decision = FirstTurnAgentDecision(needs_extension=False, extension="")
            extension = first_turn_decision.extension
            if extension:
                from app.agents.atendimento.formatters import format_for_whatsapp

                extension = format_for_whatsapp(extension, state)
                guard_result = self.guard.validate(extension)
                if not guard_result.allowed:
                    extension = ""
                    first_turn_decision = FirstTurnAgentDecision(needs_extension=False, extension="")
                else:
                    extension = guard_result.text
                    first_turn_decision = FirstTurnAgentDecision(needs_extension=True, extension=extension)
            self._update_state_memory(state, tool_results, message)
            return AtendimentoAgentOutput(
                response_text=extension,
                decision=IntentDecision(reason_codes=["first_turn_extension_only"], should_respond=True),
                guard_result=GuardResult(allowed=True, text=extension, sanitized=False),
                should_dispatch=False,
                first_turn_decision=first_turn_decision,
                metadata=self._build_metadata(
                    state,
                    tenant_config,
                    decision=IntentDecision(reason_codes=["first_turn_extension_only"], should_respond=True),
                    tool_results=tool_results,
                    tools_context=tools_context,
                    guard_result=GuardResult(allowed=True, text=extension, sanitized=False),
                    plan=plan,
                ),
            )
        fiscal_validation_enabled = self._should_run_fiscal_validation(
            state=state,
            decision=decision,
            tools_context=tools_context,
            tenant_config=tenant_config,
            plan=plan,
        )
        fiscal_findings: list[FiscalFinding] = []
        if fiscal_validation_enabled:
            fiscal_findings = self.fiscal.validate(
                response=draft,
                tool_results=tools_context,
                memory=memory_from_state(state),
                tenant_config=tenant_config,
            )
        logger.info(
            "ai.validation.completed empresa_id=%s lead_id=%s selected_agent=%s validator=%s reason_code=%s output_type=%s",
            sanitize_for_log(str(state.empresa_id or "")),
            sanitize_for_log(str(state.lead_id or "")),
            "AtendimentoAgent",
            "AgenteFiscal" if fiscal_validation_enabled else "ResponseGuard",
            "ok" if not self._has_fiscal_errors(fiscal_findings) else "fiscal_validation_failed",
            "text",
        )
        repair_attempted = False
        if fiscal_validation_enabled and self._has_fiscal_errors(fiscal_findings):
            repair_attempted = True
            draft = await self._repair_response(
                llm_provider=llm_provider,
                original_draft=draft,
                fiscal_findings=fiscal_findings,
                system_prompt=system_prompt,
                llm_messages=llm_messages,
                tools_context=tools_context,
            )
            fiscal_findings = self.fiscal.validate(
                response=draft,
                tool_results=tools_context,
                memory=memory_from_state(state),
                tenant_config=tenant_config,
            )

        if self._has_fiscal_errors(fiscal_findings):
            guard_result = GuardResult(
                allowed=False,
                text="",
                reason_codes=["fiscal_validation_failed"],
                sanitized=True,
            )
            self._update_state_memory(state, tool_results, message)
            return AtendimentoAgentOutput(
                response_text="",
                decision=decision,
                guard_result=guard_result,
                should_dispatch=False,
                metadata=self._build_metadata(
                    state,
                    tenant_config,
                    decision,
                    tool_results,
                    tools_context,
                    guard_result,
                    plan=plan,
                    fiscal_findings=fiscal_findings,
                    repair_attempted=repair_attempted,
                    operational_error="fiscal_validation_failed",
                    blocked_reason="fiscal_validation_failed",
                ),
            )

        from app.agents.atendimento.formatters import format_for_whatsapp

        draft = self._ensure_anamnese_url(draft, tool_results)
        formatted = format_for_whatsapp(draft, state)
        guard_result = self.guard.validate(formatted)
        response_text = guard_result.text if guard_result.allowed else guard_result.text
        self._update_state_memory(state, tool_results, message)

        return AtendimentoAgentOutput(
            response_text=response_text,
            decision=decision,
            guard_result=guard_result,
            should_dispatch=False,
            metadata=self._build_metadata(
                state,
                tenant_config,
                decision,
                tool_results,
                tools_context,
                guard_result,
                plan=plan,
                fiscal_findings=fiscal_findings,
                repair_attempted=repair_attempted,
            ),
        )

    async def _load_tenant_config(self, empresa_id: str | None) -> dict[str, Any]:
        if not empresa_id:
            return {
                "empresa_id": None,
                "nome_empresa": None,
                "nome_agente": "Assistente Virtual",
                "modelo_ia": "gpt-4o-mini",
                "mensagem_saudacao": None,
                "ia_identidade": None,
                "ia_regras_negocio": None,
                "atendente_prompt": None,
                "credenciais_canais": {},
                "has_ai": False,
                "_openai_api_key": None,
            }

        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
        except (TypeError, ValueError):
            return {
                "empresa_id": str(empresa_id),
                "nome_empresa": None,
                "nome_agente": "Assistente Virtual",
                "modelo_ia": "gpt-4o-mini",
                "mensagem_saudacao": None,
                "ia_identidade": None,
                "ia_regras_negocio": None,
                "atendente_prompt": None,
                "credenciais_canais": {},
                "has_ai": False,
                "_openai_api_key": None,
            }

        try:
            from db.database import AsyncSessionLocal
            from db.models import Empresa

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                empresa = result.scalars().first()
                if not empresa:
                    return {
                        "empresa_id": str(empresa_uuid),
                        "nome_empresa": None,
                        "nome_agente": "Assistente Virtual",
                        "modelo_ia": "gpt-4o-mini",
                        "mensagem_saudacao": None,
                        "ia_identidade": None,
                        "ia_regras_negocio": None,
                        "atendente_prompt": None,
                        "credenciais_canais": {},
                        "has_ai": False,
                        "_openai_api_key": None,
                    }
                config = build_tenant_agent_config(empresa)
                config["empresa_id"] = str(empresa_uuid)
                config["_openai_api_key"] = str(
                    getattr(empresa, "openai_api_key", "")
                    or ((getattr(empresa, "credenciais_canais", {}) or {}).get("openai_api_key"))
                    or ((getattr(empresa, "credenciais_canais", {}) or {}).get("api_key_openai"))
                    or ""
                ).strip() or None
                return config
        except Exception:
            return {
                "empresa_id": str(empresa_uuid),
                "nome_empresa": None,
                "nome_agente": "Assistente Virtual",
                "modelo_ia": "gpt-4o-mini",
                "mensagem_saudacao": None,
                "ia_identidade": None,
                "ia_regras_negocio": None,
                "atendente_prompt": None,
                "credenciais_canais": {},
                "has_ai": False,
                "_openai_api_key": None,
            }

    def _resolve_llm_provider(self, tenant_config: dict[str, Any]) -> AgentLLM:
        if self.llm is not None:
            return self.llm
        if tenant_config.get("has_ai"):
            return OpenAIAgentLLM.from_config(tenant_config)
        if _explicit_mock_llm_allowed():
            return MockAgentLLM()
        raise TenantLLMUnavailable("Tenant sem LLM configurada; resposta automática bloqueada.")

    def _parse_first_turn_decision(self, draft: str, tenant_config: dict[str, Any]) -> FirstTurnAgentDecision | None:
        try:
            raw = str(draft or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()
            decision = FirstTurnAgentDecision.model_validate(json.loads(raw))
            greeting = str(tenant_config.get("mensagem_saudacao") or "")
            if greeting and greeting in decision.extension:
                return FirstTurnAgentDecision(needs_extension=False, extension="")
            return decision
        except Exception:
            return None

    def _resolve_tool_planner(self, tenant_config: dict[str, Any]) -> AgentToolPlanner:
        if self.tool_planner is not None:
            return self.tool_planner
        if self.llm is not None:
            return MockToolPlanner()
        if tenant_config.get("has_ai"):
            return LLMToolPlanner()
        if _explicit_mock_llm_allowed():
            return MockToolPlanner()
        return MockToolPlanner()

    def _schedule_conversion_monitor(
        self,
        *,
        state: ConversationState,
        tenant_config: dict[str, Any],
        message: IncomingMessage | None,
    ) -> None:
        """Observe asynchronously so Meta failures never block the reply."""

        if (
            not message
            or not str(message.message_id or "").strip()
            or not state.lead_id
            or os.getenv("PYTEST_CURRENT_TEST")
        ):
            return
        history = [
            {"role": "user", "content": str(item).strip()}
            for item in list(state.historico_curto or [])[-8:]
            if str(item).strip()
        ]
        current = {
            "message_id": str(message.message_id).strip(),
            "text": str(message.text or "").strip(),
            "received_at": message.received_at.isoformat() if message.received_at else None,
        }
        asyncio.create_task(
            self._run_conversion_monitor(
                state=state,
                tenant_config=dict(tenant_config),
                history=history,
                current=current,
            )
        )

    async def _run_conversion_monitor(
        self,
        *,
        state: ConversationState,
        tenant_config: dict[str, Any],
        history: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> None:
        try:
            from app.agents.conversion_monitor import analyze_conversion_monitor
            from app.services.commercial_state_executor import execute_conversion_monitor_facts_for_lead

            message_id = str(current.get("message_id") or "").strip()
            decision = await analyze_conversion_monitor(
                config=tenant_config,
                history=history,
                current_message=current,
                allowed_message_ids={message_id},
                llm=self.llm,
            )
            logger.info(
                "MONITOR_ANALYSIS_COMPLETE empresa_id=%s lead_id=%s fact_count=%s",
                sanitize_for_log(str(state.empresa_id or "")),
                sanitize_for_log(str(state.lead_id or "")),
                len(decision.commercial_facts),
            )
            if not decision.commercial_facts:
                return
            result = await execute_conversion_monitor_facts_for_lead(
                empresa_id=state.empresa_id,
                lead_id=state.lead_id,
                facts=decision.commercial_facts,
                source_event_id=message_id,
            )
            for item in result.get("facts") or []:
                logger.info(
                    "COMMERCIAL_FACT_%s empresa_id=%s lead_id=%s fact_type=%s fact_id_hash=%s status=%s",
                    "DETECTED" if item.get("status") != "deduped" else "DEDUPED",
                    sanitize_for_log(str(state.empresa_id or "")),
                    sanitize_for_log(str(state.lead_id or "")),
                    sanitize_for_log(str(item.get("fact_type") or "")),
                    sanitize_for_log(str(item.get("fact_id") or ""))[:16],
                    sanitize_for_log(str(item.get("status") or "")),
                )
        except Exception as exc:
            logger.warning(
                "MONITOR_ANALYSIS_FAILED empresa_id=%s lead_id=%s error_class=%s",
                sanitize_for_log(str(state.empresa_id or "")),
                sanitize_for_log(str(state.lead_id or "")),
                exc.__class__.__name__,
            )

    def _build_llm_messages(self, state: ConversationState, message: IncomingMessage | None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        history = list(state.historico_curto or [])
        if history:
            for item in history[-8:]:
                role, content = self._message_role_and_content(item)
                if content:
                    messages.append({"role": role, "content": content})
        current = str((message.text if message else None) or state.mensagem_atual or "").strip()
        if current:
            messages.append({"role": "user", "content": current})
        return messages

    def _message_role_and_content(self, text: str) -> tuple[str, str]:
        content = str(text or "").strip()
        if not content:
            return "user", ""
        lower = content.lower()
        if lower.startswith(("assistente:", "ia:")):
            return "assistant", content.split(":", 1)[1].strip() if ":" in content else content
        if lower.startswith(("usuario:", "usuário:", "cliente:")):
            return "user", content.split(":", 1)[1].strip() if ":" in content else content
        return "user", content

    def _build_prompt_memory(
        self,
        state: ConversationState,
        message: IncomingMessage | None,
        tool_results: list[AgentToolResult],
    ) -> dict[str, Any]:
        memory = memory_from_state(state)
        local_now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        memory["current_local_datetime"] = local_now.isoformat()
        memory["current_local_daypart"] = (
            "morning" if local_now.hour < 12 else "afternoon" if local_now.hour < 18 else "night"
        )
        memory["historico_curto"] = list(state.historico_curto or [])
        memory["first_agent_response"] = bool(state.first_agent_response)
        memory["mensagem_atual"] = str((message.text if message else None) or state.mensagem_atual or "").strip()
        memory["tool_results"] = [
            {
                "name": result["name"],
                "ok": result["ok"],
                "missing_fields": list(result.get("missing_fields") or []),
                "data": {
                    key: value
                    for key, value in dict(result.get("data") or {}).items()
                    if key not in {"memory_update"}
                },
            }
            for result in tool_results
        ]
        return memory

    def _update_state_memory(
        self,
        state: ConversationState,
        tool_results: list[AgentToolResult],
        message: IncomingMessage | None,
    ) -> None:
        updated_memory = normalize_memory(memory_from_state(state), state)
        for result in tool_results:
            patch = result.get("memory_update") or result.get("data", {}).get("memory_update")
            if not isinstance(patch, Mapping):
                continue
            if isinstance(patch.get("slots"), Mapping):
                updated_memory["slots"] = {**dict(updated_memory.get("slots") or {}), **dict(patch.get("slots") or {})}
            for key in (
                "active_flow",
                "active_intent",
                "pending_slot",
                "last_agent_question",
                "last_budget_estimate",
                "handoff_requested",
                "scheduling_requested",
            ):
                if key in patch:
                    updated_memory[key] = patch[key]
        if message and str(message.text or "").strip():
            updated_memory["last_user_message"] = str(message.text).strip()
        apply_memory_to_state(state, updated_memory)

    def _build_metadata(
        self,
        state: ConversationState,
        tenant_config: dict[str, Any],
        decision,
        tool_results: list[AgentToolResult],
        tools_context: dict[str, Any],
        guard_result: GuardResult,
        *,
        plan: ToolPlan | None = None,
        fiscal_findings: list[FiscalFinding] | None = None,
        repair_attempted: bool = False,
        operational_error: str | None = None,
        blocked_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "agent": "AtendimentoAgent",
            "dispatch": "disabled_in_new_architecture",
            "tenant_config": {
                key: value
                for key, value in tenant_config.items()
                if key not in {"credenciais_canais", "_openai_api_key"}
            },
            "tool_results": self._safe_tool_metadata(tool_results),
            "tools_context": self._safe_tools_context(tools_context),
            "selected_tools": [result["name"] for result in tool_results],
            "tool_plan": dict(plan or {}),
            "fiscal_findings": list(fiscal_findings or []),
            "repair_attempted": bool(repair_attempted),
            "operational_error": operational_error,
            "blocked_reason": blocked_reason,
            "should_respond": bool(decision.should_respond),
            "guard_result": {
                "allowed": guard_result.allowed,
                "sanitized": guard_result.sanitized,
                "reason_codes": list(guard_result.reason_codes or []),
            },
            "conversation_state": memory_from_state(state),
            "decision": {
                "intent": decision.intent,
                "specialist_key": decision.specialist_key,
                "confidence": decision.confidence,
                "reason_codes": list(decision.reason_codes or []),
                "should_respond": decision.should_respond,
            },
        }

    def _empty_plan(self, reason: str) -> ToolPlan:
        return {
            "selected_tools": [],
            "tool_args": {},
            "expected_missing_fields": [],
            "intent_summary": reason,
            "commercial_state": {},
        }

    def _ensure_anamnese_url(self, draft: str, tool_results: list[AgentToolResult]) -> str:
        text = str(draft or "").strip()
        for result in tool_results:
            if result.get("name") != "anamnese" or not result.get("ok"):
                continue
            url = str((result.get("data") or {}).get("url") or "").strip()
            if url and url not in text:
                return f"{text}\n\n{url}".strip()
        return text

    def _safe_tool_metadata(self, tool_results: list[AgentToolResult]) -> list[dict[str, Any]]:
        return [
            {
                **dict(result),
                "data": self._safe_tool_data(dict(result.get("data") or {})),
            }
            for result in tool_results
        ]

    def _safe_tools_context(self, tools_context: dict[str, Any]) -> dict[str, Any]:
        return {
            str(name): self._safe_tool_data(dict(data)) if isinstance(data, dict) else data
            for name, data in tools_context.items()
        }

    def _safe_tool_data(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.services.tokenized_links import mask_tokenized_url_for_logs

        safe = dict(data)
        if safe.get("url"):
            safe["url"] = mask_tokenized_url_for_logs(str(safe["url"]))
        return safe

    def _has_fiscal_errors(self, findings: list[FiscalFinding]) -> bool:
        return any(str(item.get("severity")) == "error" for item in findings)

    def _should_run_fiscal_validation(
        self,
        *,
        state: ConversationState,
        decision: IntentDecision,
        tools_context: dict[str, Any],
        tenant_config: dict[str, Any],
        plan: ToolPlan | None,
    ) -> bool:
        explicit_agent_type = str(
            tenant_config.get("agent_type")
            or tenant_config.get("tipo_agente")
            or tenant_config.get("validator")
            or ""
        ).strip().lower()
        if explicit_agent_type in {"fiscal", "orcamento", "orçamento", "budget"}:
            return True

        # A commercial event price question is handled by the tenant prompt. The
        # fiscal validator is reserved for an explicitly configured structured
        # budget flow, not for an incidental "valor" intent or tool selection.
        credentials = tenant_config.get("credenciais_canais")
        budget_config = credentials.get("orcamento") if isinstance(credentials, Mapping) else None
        return isinstance(budget_config, Mapping) and bool(budget_config.get("enabled"))

    async def _repair_response(
        self,
        *,
        llm_provider: AgentLLM,
        original_draft: str,
        fiscal_findings: list[FiscalFinding],
        system_prompt: str,
        llm_messages: list[dict[str, Any]],
        tools_context: dict[str, Any],
    ) -> str:
        repair_context = {
            "tool_results": tools_context,
            "findings": fiscal_findings,
            "draft_reprovado": original_draft,
            "instrucao": (
                "Reescreva somente a resposta final ao cliente. "
                "Use apenas os dados das tools. Nao mencione validacao, JSON ou termos internos."
            ),
        }
        repaired = await llm_provider.generate(
            system_prompt=system_prompt,
            messages=llm_messages
            + [
                {
                    "role": "system",
                    "content": str(repair_context),
                }
            ],
            tools_context=tools_context,
        )
        return str(repaired or "").strip()

    def _sync_media_from_message(self, state: ConversationState, message: IncomingMessage) -> None:
        if message.media_type and not state.media_type:
            state.media_type = message.media_type
        if message.media_url and not state.media_url:
            state.media_url = message.media_url
        if message.media_id and not state.media_id:
            state.media_id = message.media_id
        if message.media_mime_type and not state.media_mime_type:
            state.media_mime_type = message.media_mime_type
        if message.caption and not state.caption:
            state.caption = message.caption
        state.has_image = bool(state.has_image or message.has_image or state.media_type == "image")
        if message.image_analysis and not state.image_analysis:
            state.image_analysis = message.image_analysis
            state.image_analysis_status = "available"

    def _latest_message(self, messages: list[IncomingMessage], state: ConversationState) -> IncomingMessage | None:
        if messages:
            return messages[-1]
        if state.mensagem_atual:
            return IncomingMessage(
                text=state.mensagem_atual,
                tenant_id=state.empresa_id,
                lead_id=state.lead_id,
                phone=state.telefone,
                channel=state.canal,
            )
        return None
