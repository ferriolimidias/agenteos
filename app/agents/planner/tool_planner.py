from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TypedDict

from sqlalchemy import select

from app.agents.llm import AgentLLM, MockAgentLLM, OpenAIAgentLLM
from app.agents.schemas import ConversationState
from db.database import AsyncSessionLocal
from db.models import CRMEtapa, CRMFunil, TagCRM


class ToolPlan(TypedDict):
    selected_tools: list[str]
    tool_args: dict[str, dict]
    expected_missing_fields: list[str]
    intent_summary: str
    commercial_state: dict[str, Any]


class AgentToolPlanner(ABC):
    @abstractmethod
    async def plan(
        self,
        *,
        state: ConversationState,
        tenant_config: dict[str, Any],
        memory: dict[str, Any],
        history: list[dict[str, Any]],
        available_tools: list[str],
    ) -> ToolPlan:
        raise NotImplementedError


class LLMToolPlanner(AgentToolPlanner):
    def __init__(self, llm: AgentLLM | None = None) -> None:
        self.llm = llm

    async def plan(
        self,
        *,
        state: ConversationState,
        tenant_config: dict[str, Any],
        memory: dict[str, Any],
        history: list[dict[str, Any]],
        available_tools: list[str],
    ) -> ToolPlan:
        llm = self.llm or self._resolve_llm(tenant_config)
        allowlist = _dedupe([tool for tool in available_tools if tool])
        # `allowlist` is the tool allowlist (e.g. ``["context", "tags"]``),
        # not the tenant's tag UUID allowlist.  Passing it to the tag loader
        # made every UUID parse fail and silently removed all tag context from
        # the planner, even when the panel configuration was valid.
        tag_context, stage_context = await _load_commercial_context(tenant_config)
        system_prompt = (
            "Voce e a inteligencia comercial semantica do tenant atual. "
            "Retorne somente JSON valido, sem markdown e sem raciocinio. "
            "Interprete a conversa completa, o historico, a memoria e o estado CRM. "
            "Nao use palavras-chave, regex, frases obrigatorias ou CTA literal como classificacao. "
            "Os exemplos nas instrucoes sao ilustrativos e nao sao gatilhos tecnicos. "
            "Escolha apenas tools existentes na allowlist. "
            "Somente tags com ai_can_apply=true podem ser propostas. "
            "Use as regras_negocio como contexto, sem substituir a evidencia da conversa. "
            "Nunca escreva resposta ao cliente. "
            "Nunca reaplique tag ja presente. "
            "Nao mova o estagio por causa de tag auxiliar de objecao ou follow-up. "
            "Emita no maximo um sinal comercial, correspondente ao marco mais avancado comprovado. "
            "Nao empilhe LeadSubmitted, QualifiedLead e InitiateCheckout na mesma decisao. "
            "LeadSubmitted exige interesse comercial demonstrado ou pedido de informacao sobre a oferta; "
            "uma declaracao isolada sobre rotina, treino ou curiosidade sem objetivo comercial nao basta. "
            "QualifiedLead exige fit evidenciado por objetivo, necessidade e compatibilidade com a oferta; "
            "experiencia previa isolada ou pergunta de preco nao qualificam. "
            "Intencao de compra e um estado interno e nao deve ser convertido em QualifiedLead por conveniencia; "
            "quando houver apenas aceite da proposta, use a etapa de intencao se disponivel e nenhum sinal Meta novo. "
            "Objeções e opt-out devem ser registrados como tags permitidas ou reason_codes sem promover venda. "
            "Aplique somente tags que representem o estado atual ou uma informacao auxiliar comprovada; nao replique todos os marcos anteriores. "
            "Intencao verbal, pedido de proximo passo ou aceite da oferta nao sao checkout: InitiateCheckout exige evidencia de entrada operacional real no checkout. "
            "Purchase intent pode mover o estagio interno de intencao e registrar tag de intencao, mas nao emite InitiateCheckout sem sinal operacional. "
            "Use dados operacionais estruturados do contexto, como checkout_started, form_completed ou order_confirmed, quando presentes; nunca deduza esses fatos da fala. "
            "Duvida de compatibilidade, inseguranca ou necessidade de esclarecimento nao e opt-out nem sem-fit confirmado. "
            "Sem-fit exige incompatibilidade objetiva com requisito essencial; opt-out exige pedido explicito de cessar contato. "
            "Purchase exige confirmacao operacional e nunca deve ser inferido apenas por alegacao textual do cliente. "
            "Se nao houver evidencia suficiente, mantenha tags e estagio sem alteracao."
        )
        user_payload = {
            "allowlist": allowlist,
            "tool_schemas": _planner_tool_schemas(allowlist),
            "mensagem_atual": state.mensagem_atual,
            "estado_conversa": {
                "empresa_id": state.empresa_id,
                "lead_id": state.lead_id,
                "status_atendimento": state.status_atendimento,
                "ia_bloqueada": bool(state.ia_bloqueada),
                "active_flow": state.active_flow,
                "active_intent": state.active_intent,
                "pending_slot": state.pending_slot,
                "handoff_requested": bool(state.handoff_requested),
                "scheduling_requested": bool(state.scheduling_requested),
                "tags_aplicadas": [str(tag).strip() for tag in (state.tags or []) if str(tag).strip()],
            },
            "tags_permitidas_ia": tag_context,
            "etapas_disponiveis": stage_context,
            "regras_negocio": str(
                tenant_config.get("regras_negocio")
                or tenant_config.get("ia_regras_negocio")
                or ""
            ).strip(),
            "memoria": _safe_payload(memory),
            "evidencia_operacional": _safe_payload({
                key: memory.get(key)
                for key in ("checkout_started", "form_completed", "anamnese_completed", "order_created", "payment_confirmed")
                if isinstance(memory, dict) and memory.get(key) is not None
            }),
            "historico": history[-8:],
            "tenant": {
                "empresa_id": tenant_config.get("empresa_id"),
                "nome_empresa": tenant_config.get("nome_empresa"),
                "tools_configuradas": _configured_tool_names(tenant_config),
            },
            "schema": {
                "selected_tools": [],
                "tool_args": {"<tool_da_allowlist>": {}},
                "expected_missing_fields": [],
                "intent_summary": "resumo curto sem chain-of-thought",
                "commercial_state": {
                    "tags_to_apply": ["UUID de tag permitida"],
                    "tags_to_remove": ["UUID de tag removivel"],
                    "target_stage_id": "UUID de etapa ou null",
                    "signals": ["LeadSubmitted|QualifiedLead|InitiateCheckout|OrderCreated|Purchase"],
                    "reason_codes": ["evidencias observadas"],
                    "confidence": 0.0,
                    "operational_evidence": {},
                },
            },
        }
        raw = await llm.generate(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
            tools_context=None,
        )
        parsed = _parse_json_object(raw)
        return _normalize_plan(parsed, allowlist, ensure_context=True)

    def _resolve_llm(self, tenant_config: dict[str, Any]) -> AgentLLM:
        if tenant_config.get("has_ai"):
            return OpenAIAgentLLM.from_config(tenant_config)
        return MockAgentLLM()


class MockToolPlanner(AgentToolPlanner):
    def __init__(
        self,
        *,
        default_plan: ToolPlan | None = None,
        plans: list[ToolPlan] | None = None,
        plan_factory: Callable[..., ToolPlan] | None = None,
    ) -> None:
        self.default_plan = default_plan
        self.plans = list(plans or [])
        self.plan_factory = plan_factory

    async def plan(
        self,
        *,
        state: ConversationState,
        tenant_config: dict[str, Any],
        memory: dict[str, Any],
        history: list[dict[str, Any]],
        available_tools: list[str],
    ) -> ToolPlan:
        allowlist = _dedupe([tool for tool in available_tools if tool])
        if self.plan_factory is not None:
            plan = self.plan_factory(
                state=state,
                tenant_config=tenant_config,
                memory=memory,
                history=history,
                available_tools=available_tools,
            )
            return _normalize_plan(dict(plan or {}), allowlist)
        if self.plans:
            return _normalize_plan(dict(self.plans.pop(0)), allowlist)
        if self.default_plan is not None:
            return _normalize_plan(dict(self.default_plan), allowlist)
        return _normalize_plan(
            {
                "selected_tools": ["context"],
                "tool_args": {},
                "expected_missing_fields": [],
                "intent_summary": "mock_default_context_only",
                "commercial_state": {},
            },
            allowlist,
        )


def _normalize_plan(raw: dict[str, Any], allowlist: list[str], *, ensure_context: bool = False) -> ToolPlan:
    selected = raw.get("selected_tools") if isinstance(raw.get("selected_tools"), list) else []
    selected_tools = _dedupe([str(tool) for tool in selected if str(tool) in allowlist])
    if ensure_context and "context" in allowlist and "context" not in selected_tools:
        selected_tools.insert(0, "context")
    tool_args = raw.get("tool_args") if isinstance(raw.get("tool_args"), dict) else {}
    safe_args = {str(key): dict(value) for key, value in tool_args.items() if str(key) in allowlist and isinstance(value, dict)}
    missing = [str(item) for item in raw.get("expected_missing_fields") or [] if str(item or "").strip()]
    normalized_state = _normalize_commercial_state(raw.get("commercial_state"))
    if normalized_state and any(normalized_state.get(key) for key in ("tags_to_apply", "tags_to_remove", "target_stage_id", "signals")):
        selected_tools = [tool for tool in selected_tools if tool != "tags"]
    return {
        "selected_tools": selected_tools,
        "tool_args": safe_args,
        "expected_missing_fields": missing,
        "intent_summary": str(raw.get("intent_summary") or "").strip()[:300],
        "commercial_state": normalized_state,
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _configured_tool_names(tenant_config: dict[str, Any]) -> list[str]:
    credenciais = tenant_config.get("credenciais_canais")
    if not isinstance(credenciais, dict):
        return []
    configured = []
    for key in ("orcamento", "agenda", "handoff", "crm"):
        value = credenciais.get(key)
        if isinstance(value, dict) and value.get("enabled"):
            configured.append(key)
    return configured


def _planner_tool_schemas(allowlist: list[str]) -> list[dict[str, Any]]:
    schemas = {
        "context": {"name": "context", "purpose": "carregar contexto do atendimento", "args": {}},
        "anamnese": {
            "name": "anamnese",
            "purpose": "criar ou recuperar idempotentemente a avaliacao individual e obter sua URL publica",
            "args": {},
            "semantic_precondition": (
                "aceite ou pedido atual da avaliacao comprovado pela pergunta/oferta anterior e pelo estado da conversa"
            ),
        },
        "orcamento": {"name": "orcamento", "purpose": "calcular orcamento estruturado", "args": {}},
        "agenda": {"name": "agenda", "purpose": "coletar dados para agendamento", "args": {}},
        "handoff": {"name": "handoff", "purpose": "transferir para atendimento humano", "args": {}},
        "tags": {"name": "tags", "purpose": "operacao CRM permitida", "args": {"tag_id": "UUID"}},
    }
    return [schemas[name] for name in allowlist if name in schemas]


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload or {})
    for key in ("_openai_api_key", "openai_api_key", "api_key_openai"):
        safe.pop(key, None)
    return safe


async def _load_tag_context(tenant_config: dict[str, Any], allowlist: list[str]) -> list[dict[str, Any]]:
    tenant_id = str(tenant_config.get("empresa_id") or "").strip()
    if not tenant_id or not allowlist:
        return []

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (TypeError, ValueError):
        return []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TagCRM).where(
                TagCRM.empresa_id == tenant_uuid,
                TagCRM.ai_can_apply.is_(True),
            ).order_by(TagCRM.nome.asc())
        )
        tags = result.scalars().all()

    return [
        {
            "id": str(tag.id),
            "nome": tag.nome,
            "tipo": str(getattr(tag, "tipo", "") or ""),
            "descricao": str(getattr(tag, "instrucao_ia", "") or "").strip(),
            "ai_can_apply": bool(getattr(tag, "ai_can_apply", False)),
            "ativa_no_funil": bool(getattr(tag, "ativa_no_funil", False)),
            "disparar_conversao_ads": bool(getattr(tag, "disparar_conversao_ads", False)),
            "acao_transferir_humano": bool(getattr(tag, "acao_transferir_humano", False)),
            "pausa_permanente": bool(getattr(tag, "pausa_permanente", False)),
        }
        for tag in tags
    ]


async def _load_commercial_context(tenant_config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tenant_id = str(tenant_config.get("empresa_id") or "").strip()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (TypeError, ValueError):
        return [], []
    async with AsyncSessionLocal() as session:
        tags_result = await session.execute(
            select(TagCRM).where(TagCRM.empresa_id == tenant_uuid, TagCRM.ai_can_apply.is_(True)).order_by(TagCRM.nome.asc())
        )
        stages_result = await session.execute(
            select(CRMEtapa, CRMFunil.nome)
            .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
            .where(CRMFunil.empresa_id == tenant_uuid)
            .order_by(CRMEtapa.ordem.asc(), CRMEtapa.nome.asc())
        )
        tags = tags_result.scalars().all()
        stages = stages_result.all()
    tag_context = [
        {
            "id": str(tag.id),
            "nome": tag.nome,
            "tipo": str(getattr(tag, "tipo", "") or ""),
            "instrucao_ia": str(getattr(tag, "instrucao_ia", "") or "").strip(),
            "ai_can_apply": True,
        }
        for tag in tags
    ]
    stage_context = [
        {"id": str(stage.id), "nome": stage.nome, "funil": str(funil_nome or "")}
        for stage, funil_nome in stages
    ]
    return tag_context, stage_context


def _normalize_commercial_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    confidence = raw.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    allowed_signals = {"LeadSubmitted", "QualifiedLead", "InitiateCheckout", "OrderCreated", "Purchase"}
    signals = [
        str(item).strip()
        for item in raw.get("signals") or []
        if str(item).strip() in allowed_signals
    ]
    operational_evidence = {
        str(key): bool(value)
        for key, value in (raw.get("operational_evidence") or {}).items()
        if str(key) in {"checkout_started", "form_completed", "anamnese_completed", "order_created", "payment_confirmed"}
    }
    if not any(operational_evidence.get(key) for key in ("checkout_started", "form_completed", "anamnese_completed")):
        signals = [item for item in signals if item != "InitiateCheckout"]
    if not operational_evidence.get("payment_confirmed"):
        signals = [item for item in signals if item != "Purchase"]
    # A single decision represents one highest-confidence commercial
    # milestone.  This keeps the structured contract from emitting a burst
    # of historical events when the model describes several facts at once.
    signal_priority = {"LeadSubmitted": 1, "QualifiedLead": 2, "InitiateCheckout": 3, "OrderCreated": 4, "Purchase": 5}
    signals = sorted(set(signals), key=lambda item: signal_priority.get(item, 0), reverse=True)[:1]
    return {
        "tags_to_apply": _clean_uuid_list(raw.get("tags_to_apply")),
        "tags_to_remove": _clean_uuid_list(raw.get("tags_to_remove")),
        "target_stage_id": _clean_uuid(raw.get("target_stage_id")),
        "signals": signals,
        "reason_codes": [str(item).strip()[:120] for item in raw.get("reason_codes") or [] if str(item).strip()][:10],
        "confidence": confidence,
        "operational_evidence": operational_evidence,
    }


def _clean_uuid(value: Any) -> str | None:
    if value in (None, "", "null"):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _clean_uuid_list(values: Any) -> list[str]:
    output: list[str] = []
    for value in values or []:
        cleaned = _clean_uuid(value)
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output[:10]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
