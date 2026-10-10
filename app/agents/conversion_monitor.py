"""Monitor semântico de fatos comerciais, separado do atendimento.

O Monitor observa a conversa e devolve fatos com evidência. Ele nunca chama a
Meta diretamente; a validação determinística e o mapeamento ficam no core.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.agents.llm import AgentLLM, MockAgentLLM, OpenAIAgentLLM


COMMERCIAL_FACT_TYPES = {
    "content_engaged",
    "lead_established",
    "qualification_confirmed",
    "selection_confirmed",
    "checkout_intent",
    "order_commitment",
    "abandonment",
    "purchase_confirmed",
}


class CommercialFact(BaseModel):
    """Fato observável, sem texto privado ou autoridade de envio."""

    fact_type: str
    occurred: bool = False
    confidence: float = 0.0
    occurred_at: str | None = None
    evidence_message_ids: list[str] = Field(default_factory=list)
    reason: str = ""


def normalize_commercial_facts(
    raw: Any,
    *,
    allowed_message_ids: set[str] | None = None,
    evidence_timestamps: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize facts conservatively before they reach the executor."""

    if not isinstance(raw, list):
        return []
    allowed = {str(item).strip() for item in (allowed_message_ids or set()) if str(item).strip()}
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        fact_type = str(item.get("fact_type") or "").strip().lower()
        if fact_type not in COMMERCIAL_FACT_TYPES or not bool(item.get("occurred")):
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = [
            str(value).strip()
            for value in item.get("evidence_message_ids") or []
            if str(value).strip() and (not allowed or str(value).strip() in allowed)
        ][:5]
        if confidence < 0.7 or not evidence:
            continue
        key = (fact_type, tuple(evidence))
        if key in seen:
            continue
        seen.add(key)
        occurred_at = str(item.get("occurred_at") or "").strip() or None
        if evidence_timestamps and evidence:
            # The model may explain a fact, but its event time must come from
            # a local message supplied to the monitor.
            occurred_at = evidence_timestamps.get(evidence[0]) or occurred_at
        facts.append(
            CommercialFact(
                fact_type=fact_type,
                occurred=True,
                confidence=confidence,
                occurred_at=occurred_at,
                evidence_message_ids=evidence,
                reason=str(item.get("reason") or "").strip()[:300],
            ).model_dump()
        )
    return facts


class ConversionMonitorDecision(BaseModel):
    """Decisão curta e auditável; não armazena raciocínio privado."""

    current_state: str = ""
    recommended_actions: list[str] = Field(default_factory=list)
    tags_to_apply: list[str] = Field(default_factory=list)
    tags_to_remove: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    counter_evidence: list[dict[str, Any]] = Field(default_factory=list)
    commercial_facts: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    no_change: bool = True


async def analyze_conversion_monitor(
    *,
    config: dict[str, Any],
    history: list[dict[str, Any]],
    current_message: dict[str, Any],
    allowed_message_ids: set[str],
    evidence_timestamps: dict[str, str] | None = None,
    llm: AgentLLM | None = None,
) -> ConversionMonitorDecision:
    """Run the tenant-scoped Monitor and return only validated facts."""

    provider = llm
    if provider is None:
        if config.get("has_ai"):
            provider = OpenAIAgentLLM.from_config(config)
        else:
            provider = MockAgentLLM()
    context = {
        "history": history[-8:],
        "current_message": current_message,
        "allowed_evidence_message_ids": sorted(allowed_message_ids),
        "evidence_timestamps": evidence_timestamps or {},
        "schema": {
            "current_state": "",
            "commercial_facts": [
                {
                    "fact_type": "content_engaged|lead_established|qualification_confirmed|selection_confirmed|checkout_intent|order_commitment|abandonment|purchase_confirmed",
                    "occurred": False,
                    "confidence": 0.0,
                    "occurred_at": "ISO timestamp or null",
                    "evidence_message_ids": ["local message id"],
                    "reason": "short evidence summary",
                }
            ],
            "confidence": 0.0,
            "no_change": True,
        },
    }
    prompt = build_conversion_monitor_system_prompt(
        config,
        attendance_context={
            "business_rules": config.get("ia_regras_negocio"),
            "attendance_prompt_available": bool(config.get("atendente_prompt")),
        },
    )
    raw = await provider.generate(
        system_prompt=prompt,
        messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        tools_context=None,
    )
    try:
        parsed = json.loads(str(raw or "").strip())
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    facts = normalize_commercial_facts(
        parsed.get("commercial_facts"),
        allowed_message_ids=allowed_message_ids,
        evidence_timestamps=evidence_timestamps,
    )
    try:
        decision_confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0)))
    except (TypeError, ValueError):
        decision_confidence = 0.0
    return ConversionMonitorDecision(
        current_state=str(parsed.get("current_state") or "").strip()[:120],
        commercial_facts=facts,
        evidence=[item for item in parsed.get("evidence") or [] if isinstance(item, dict)][:10],
        counter_evidence=[item for item in parsed.get("counter_evidence") or [] if isinstance(item, dict)][:10],
        reason=str(parsed.get("reason") or "").strip()[:300],
        confidence=decision_confidence,
        no_change=not bool(facts),
    )


def build_conversion_monitor_system_prompt(
    config: dict[str, Any],
    *,
    available_tags: list[dict[str, Any]] | None = None,
    current_stage: dict[str, Any] | None = None,
    conversation_context: dict[str, Any] | None = None,
    attendance_context: dict[str, Any] | None = None,
) -> str:
    """Gera somente o system prompt próprio do Monitor de Conversão.

    ``config`` é aceito como um dicionário de configuração do tenant por
    compatibilidade com os chamadores existentes, mas apenas
    ``conversion_monitor_prompt`` é lido. O prompt do agente, persona,
    saudação e regras de resposta nunca entram nesta fronteira.
    """

    custom_prompt = str(config.get("conversion_monitor_prompt") or "").strip()
    funnel_spec = config.get("monitor_funnel_spec") or {}
    tags = [
        {
            "id": item.get("id"),
            "name": item.get("name") or item.get("nome"),
            "description": item.get("description") or item.get("instrucao_ia"),
            "ai_can_apply": bool(item.get("ai_can_apply")),
        }
        for item in (available_tags or [])
        if isinstance(item, dict) and item.get("id")
    ]
    configured = custom_prompt or (
        "Interprete a progressão comercial conforme as etapas e regras definidas neste prompt. "
        "Não trate cordialidade, curiosidade, CTWA, campanha ou resposta da IA como qualificação "
        "por si só. Recomende no máximo mudanças comprovadas pelas tags permitidas e preserve "
        "no_change quando não houver evidência. Nunca envie eventos Meta diretamente."
    )
    return "\n\n".join(
        [
            "Você é o Monitor de Conversão do tenant atual; não conversa com o cliente.",
            "Analise somente a progressão comercial usando o prompt deste monitor e o contexto operacional estruturado.",
            "Não use AGENT_PROMPT, persona, saudação, tom de voz ou regras de resposta do agente.",
            "Não use palavras-chave isoladas como regra; considere contexto, intenção, jornada e evidência.",
            "Só recomende tags existentes, pertencentes ao tenant e com ai_can_apply=true.",
            "Para cada recomendação, informe tag_id, decision, reason resumido, evidence_refs e source=ai quando aplicável ao schema; não produza raciocínio privado.",
            "Retorne somente decisão estruturada; o executor determinístico valida autorização, idempotência, tenant e provenance.",
            "Não aplique tags, não mova estágio e não envie Meta CAPI diretamente.",
            "Use somente fact_type e etapas presentes no FunnelSpec ativo; o executor aplica a tag gerenciada e dispara a conversão.",
            "Identifique somente fatos comerciais ocorridos e sustentados por mensagens do contato.",
            "Use exatamente um ou mais fact_type do schema; occurred=false quando a evidência for insuficiente.",
            "CONTENT_ENGAGED exige interação real com oferta/conteúdo específico, não apenas saudação.",
            "LEAD_ESTABLISHED exige lead comercial identificável; QUALIFICATION_CONFIRMED depende dos critérios deste tenant.",
            "SELECTION_CONFIRMED exige escolha concreta; CHECKOUT_INTENT não é checkout criado.",
            "ORDER_COMMITMENT, ABANDONMENT e PURCHASE_CONFIRMED exigem evidência operacional; nunca deduza pagamento de texto.",
            "Não trate silêncio, preço isolado, mensagem enviada pela empresa ou expectativa como fato.",
            "Cada fato ocorrido exige confidence >= 0.7 e evidence_message_ids do contexto fornecido.",
            f"Instruções comerciais configuráveis:\n{configured}",
            f"Contexto comercial relacionado ao atendimento (sem persona):\n{attendance_context or {}}",
            f"Tags permitidas:\n{tags}",
            f"Estágio atual:\n{current_stage or {}}",
            f"Contexto operacional estruturado:\n{conversation_context or {}}",
            f"FunnelSpec ativo (somente leitura; não invente tags):\n{funnel_spec}",
            'Schema: {"current_state":"", "commercial_facts":[{"fact_type":"content_engaged|lead_established|qualification_confirmed|selection_confirmed|checkout_intent|order_commitment|abandonment|purchase_confirmed","occurred":false,"confidence":0.0,"occurred_at":null,"evidence_message_ids":[],"reason":""}], "recommended_actions":[], "tags_to_apply":[], "tags_to_remove":[], "evidence":[], "counter_evidence":[], "reason":"", "confidence":0.0, "no_change":true}',
        ]
    )


def generate_conversion_monitor_prompt(
    *,
    commercial_steps: list[dict[str, Any]],
    available_tags: list[dict[str, Any]],
    objective: str = "Interpretar avanço comercial e recomendar tags com evidência.",
    expected_next_step: str | None = None,
) -> str:
    """Monta um prompt de monitor a partir de inputs comerciais explícitos.

    Deliberadamente não recebe ``agent_prompt`` nem a configuração textual do
    agente. As etapas devem ser fornecidas pelo usuário/produto, sem extração
    silenciosa de um prompt conversacional.
    """

    steps = [step for step in commercial_steps if isinstance(step, dict)]
    config = {
        "conversion_monitor_prompt": "\n".join(
            [
                f"Objetivo: {objective}",
                f"Etapas comerciais configuradas: {steps}",
                f"Próximo passo esperado: {expected_next_step or 'não definido'}",
            ]
        )
    }
    return build_conversion_monitor_system_prompt(config, available_tags=available_tags)


def generate_prompt_artifacts(
    config: dict[str, Any],
    *,
    available_tags: list[dict[str, Any]] | None = None,
    current_stage: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Retorna artefatos independentes sem alterar configuração ou runtime."""

    # O artefato do agente continua disponível para compatibilidade do
    # gerador legado, mas a dependência fica fora do builder/runtime do
    # Monitor. Cada prompt é construído em sua própria fronteira.
    from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt

    return {
        "agent_prompt": build_atendimento_system_prompt(config),
        "conversion_monitor_prompt": build_conversion_monitor_system_prompt(
            config,
            available_tags=available_tags,
            current_stage=current_stage,
        ),
    }
