from __future__ import annotations

from typing import Any


def build_atendimento_system_prompt(config: dict[str, Any], memory: dict | None = None) -> str:
    memory = dict(memory or {})
    sections: list[str] = []

    nome_agente = str(config.get("nome_agente") or "Assistente Virtual").strip()
    nome_empresa = str(config.get("nome_empresa") or "").strip()
    if nome_empresa:
        sections.append(f"Você é {nome_agente} da empresa {nome_empresa}.")
    else:
        sections.append(f"Você é {nome_agente}.")

    identidade = str(config.get("ia_identidade") or "").strip()
    if identidade:
        sections.append(f"Identidade do agente:\n{identidade}")

    regras = str(config.get("ia_regras_negocio") or "").strip()
    if regras:
        sections.append(f"Regras do negócio:\n{regras}")

    atendente_prompt = str(config.get("atendente_prompt") or "").strip()
    # Canonical identity/business rules are the factual source. The legacy
    # attendant prompt may contain stale event details and is only a fallback
    # for tenants that have no canonical configuration yet.
    if atendente_prompt and not (identidade or regras):
        sections.append(f"Prompt do atendente:\n{atendente_prompt}")

    mensagem_saudacao = str(config.get("mensagem_saudacao") or "").strip()
    if mensagem_saudacao:
        sections.append(f"Saudação configurada do tenant:\n{mensagem_saudacao}")

    if memory:
        sections.append(f"Memória de trabalho:\n{_format_memory(memory)}")

    sections.append(
        "\n".join(
            [
                "Política de resposta:",
                "- Gere uma única resposta final ao cliente.",
                "- Use tools e contexto interno quando houver dados estruturados.",
                "- Se faltar dado, faça uma pergunta objetiva e curta.",
                "- Não invente preço, regra, disponibilidade ou prazo.",
                "- Use somente fatos presentes na identidade/regras canônicas ou retornados por uma tool bem-sucedida.",
                "- Não faça afirmações terapêuticas ou clínicas sem autorização explícita nas informações canônicas do negócio.",
                "- Não exponha JSON, nomes internos, ferramentas ou termos técnicos.",
                "- Use formato simples de WhatsApp, com frases curtas e claras.",
                "- Se houver orçamento ou agendamento, preserve os dados calculados pelas tools.",
            ]
        )
    )

    return "\n\n".join(section for section in sections if str(section).strip())


def build_first_turn_extension_system_prompt(config: dict[str, Any], memory: dict | None = None) -> str:
    memory = dict(memory or {})
    greeting = str(config.get("mensagem_saudacao") or "")
    base = build_atendimento_system_prompt({**config, "mensagem_saudacao": ""}, memory={**memory, "first_agent_response": False})
    return "\n\n".join(
        [
            base,
            "Contrato de saída estruturada do primeiro turno:",
            "- Analise a mensagem atual em relação ao greeting canônico delimitado abaixo.",
            "- Retorne somente um objeto JSON com as chaves needs_extension (boolean) e extension (string).",
            "- Não retorne o greeting, não o copie e não o parafraseie na extension.",
            "- Use needs_extension=false quando o greeting já cobrir suficientemente a intenção.",
            "- Use needs_extension=true somente para uma dúvida adicional não coberta e escreva apenas o complemento mínimo grounded.",
            "- Se o fato adicional não estiver disponível, não invente; use needs_extension=false e extension vazia.",
            "<CONFIGURED_GREETING>",
            greeting,
            "</CONFIGURED_GREETING>",
            'Formato obrigatório: {"needs_extension": false, "extension": ""}',
        ]
    )


def _format_memory(memory: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in (
        "empresa_id",
        "lead_id",
        "active_flow",
        "active_intent",
        "pending_slot",
        "last_agent_question",
        "handoff_requested",
        "scheduling_requested",
        "current_local_datetime",
        "current_local_daypart",
    ):
        value = memory.get(key)
        if value not in (None, "", [], {}):
            pieces.append(f"{key}: {value}")

    slots = memory.get("slots") if isinstance(memory.get("slots"), dict) else {}
    if slots:
        pieces.append(f"slots: {slots}")

    budget = memory.get("last_budget_estimate") if isinstance(memory.get("last_budget_estimate"), dict) else {}
    if budget:
        pieces.append(f"last_budget_estimate: {budget}")

    history = memory.get("historico_curto")
    if isinstance(history, list) and history:
        pieces.append(f"historico_curto: {history[-5:]}")

    current = str(memory.get("mensagem_atual") or "").strip()
    if current:
        pieces.append(f"mensagem_atual: {current}")

    return "\n".join(str(item) for item in pieces if str(item).strip())
