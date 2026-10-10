import json

from app.agents.conversion_monitor import (
    ConversionMonitorDecision,
    build_conversion_monitor_system_prompt,
    generate_conversion_monitor_prompt,
    generate_prompt_artifacts,
    normalize_commercial_facts,
)


def _config(prompt=""):
    return {
        "empresa_id": "tenant-a",
        "nome_empresa": "Empresa A",
        "nome_agente": "Agente A",
        "ia_identidade": "Tom consultivo",
        "ia_regras_negocio": "Atender com dados configurados",
        "conversion_monitor_prompt": prompt,
    }


def test_monitor_decision_is_structured_without_private_reasoning():
    decision = ConversionMonitorDecision(
        current_state="interesse",
        tags_to_apply=["tag-a"],
        evidence=[{"ref": "message:1", "kind": "business_action"}],
        reason="Ação comercial registrada",
        confidence=0.8,
        no_change=False,
    )
    payload = json.loads(decision.model_dump_json())
    assert payload["tags_to_apply"] == ["tag-a"]
    assert "chain_of_thought" not in payload


def test_monitor_normalizes_rich_facts_only_with_confidence_and_local_evidence():
    facts = normalize_commercial_facts(
        [
            {
                "fact_type": "content_engaged",
                "occurred": True,
                "confidence": 0.9,
                "evidence_message_ids": ["message-1"],
                "reason": "Pergunta sobre oferta específica",
            },
            {
                "fact_type": "selection_confirmed",
                "occurred": True,
                "confidence": 0.69,
                "evidence_message_ids": ["message-1"],
            },
            {
                "fact_type": "purchase_confirmed",
                "occurred": True,
                "confidence": 1.0,
                "evidence_message_ids": ["foreign-message"],
            },
        ],
        allowed_message_ids={"message-1"},
    )
    assert [item["fact_type"] for item in facts] == ["content_engaged"]
    assert facts[0]["evidence_message_ids"] == ["message-1"]


def test_monitor_prompt_defines_generic_facts_without_niche_hardcode():
    prompt = build_conversion_monitor_system_prompt(_config())
    assert "qualification_confirmed" in prompt
    assert "Não trate silêncio" in prompt
    assert "fitness" not in prompt.lower()


def test_monitor_and_agent_prompts_are_generated_separately_from_shared_config():
    artifacts = generate_prompt_artifacts(_config("Usar evidência comercial configurada."))
    assert artifacts["agent_prompt"]
    assert artifacts["conversion_monitor_prompt"]
    assert artifacts["agent_prompt"] != artifacts["conversion_monitor_prompt"]
    assert "Usar evidência comercial configurada." in artifacts["conversion_monitor_prompt"]


def test_monitor_only_receives_tenant_tags_and_ai_capability():
    prompt = build_conversion_monitor_system_prompt(
        _config(),
        available_tags=[
            {"id": "tag-a", "name": "Qualidade A", "ai_can_apply": True},
            {"id": "tag-b", "name": "Operacional B", "ai_can_apply": False},
        ],
    )
    assert "tag-a" in prompt
    assert "tag-b" in prompt
    assert "não envie Meta CAPI diretamente" in prompt


def test_monitor_runtime_does_not_contain_agent_prompt():
    prompt = build_conversion_monitor_system_prompt(
        _config(),
        available_tags=[],
        conversation_context={"messages": [{"id": "message:1", "text": "oi"}]},
    )
    assert "Tom consultivo" not in prompt
    assert "Atender com dados configurados" not in prompt
    assert "Você é Agente A" not in prompt


def test_agent_persona_greeting_and_response_rules_do_not_leak():
    config = _config("MONITOR_POLICY_ALPHA")
    config.update(
        {
            "ia_identidade": "PERSONA_SECRET_MARKER_123",
            "mensagem_saudacao": "GREETING_MARKER_456",
            "ia_regras_negocio": "WHATSAPP_STYLE_MARKER_789",
        }
    )
    prompt = build_conversion_monitor_system_prompt(config)
    assert "PERSONA_SECRET_MARKER_123" not in prompt
    assert "GREETING_MARKER_456" not in prompt
    assert "WHATSAPP_STYLE_MARKER_789" not in prompt
    assert "MONITOR_POLICY_ALPHA" in prompt


def test_monitor_generator_requires_explicit_steps_and_not_agent_prompt():
    prompt = generate_conversion_monitor_prompt(
        commercial_steps=[{"id": "step-1", "name": "Pedido real de informação"}],
        available_tags=[{"id": "tag-a", "name": "A", "ai_can_apply": True}],
        objective="Acompanhar avanço",
    )
    assert "Pedido real de informação" in prompt
    assert "AGENT_PROMPT" in prompt
    assert "PERSONA_SECRET_MARKER_123" not in prompt


def test_monitor_generation_preserves_agent_prompt_input_byte_for_byte():
    config = _config("MONITOR_POLICY_ALPHA")
    agent_prompt = "AGENT_PROMPT_ORIGINAL_BYTE_FOR_BYTE"
    config["agent_prompt"] = agent_prompt
    before = dict(config)
    generate_prompt_artifacts(config, available_tags=[])
    assert config["agent_prompt"] == agent_prompt
    assert config == before


def test_monitor_prompt_tenant_isolation():
    prompt_a = build_conversion_monitor_system_prompt({"conversion_monitor_prompt": "MONITOR_POLICY_A"})
    prompt_b = build_conversion_monitor_system_prompt({"conversion_monitor_prompt": "MONITOR_POLICY_B"})
    assert "MONITOR_POLICY_A" in prompt_a and "MONITOR_POLICY_B" not in prompt_a
    assert "MONITOR_POLICY_B" in prompt_b and "MONITOR_POLICY_A" not in prompt_b
