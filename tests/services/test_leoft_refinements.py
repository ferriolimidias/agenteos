from pathlib import Path

from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt


def test_prompt_exposes_local_time_and_keeps_whatsapp_formatting_rules():
    prompt = build_atendimento_system_prompt(
        {"nome_agente": "Leonardo", "atendente_prompt": "Regra LeoFT"},
        {"current_local_datetime": "2026-08-17T15:00:00-03:00", "current_local_daypart": "afternoon"},
    )
    assert "current_local_datetime" in prompt
    assert "current_local_daypart" in prompt
    assert "Regra LeoFT" in prompt


def test_leoft_cta_is_commercial_and_old_internal_copy_is_absent():
    source = Path("apps/anamnese-leonardo/src/features/anamnese/steps/Summary.tsx").read_text(encoding="utf-8")
    assert "Avaliação concluída" in source
    assert "Suas informações foram recebidas para que o Leonardo possa montar o treino." in source
    assert "R$ 97 total" in source
    assert "pelo período completo." in source
    assert "Confirmar meus 3 meses de treino — R$ 97" in source
    assert "Ir para o pagamento" not in source
    assert "Nenhum pagamento foi iniciado por aqui neste momento." not in source
    assert "O próximo passo será a confirmação e orientação pelo Leonardo ou equipe." not in source
    assert "Leonardo vai criar o seu treino" not in source
    assert "Seu treino personalizado será entregue em até 24 horas." not in source
    assert "equipe" not in source
    assert "fila" not in source
    assert "webhook" not in source


def test_effective_prompt_contains_rich_opening_rules_for_generic_lead():
    prompt = build_atendimento_system_prompt(
        {
            "nome_agente": "Assistente Leonardo Personal",
            "nome_empresa": "Leonardo Personal",
            "atendente_prompt": (
                "Abertura comercial LeoFT: para lead generico no primeiro contato, cumprimente pelo periodo e pelo nome "
                "quando disponivel, explique o Metodo Hibrido LeoFT de forma conversacional e Low Profile, mostre que "
                "muitas vezes o problema nao e falta de esforco, e sim falta de direcao clara, cite treino personalizado "
                "por 3 meses pelo aplicativo, valor R$97, e responda primeiro a pergunta especifica quando houver."
            ),
        },
        {"current_local_daypart": "morning", "mensagem_atual": "Bom dia"},
    )
    assert "Abertura comercial LeoFT" in prompt
    assert "Low Profile" in prompt
    assert "direcao clara" in prompt
    assert "3 meses" in prompt
    assert "R$97" in prompt


def test_first_contact_grounding_uses_canonical_business_source_over_legacy_prompt():
    prompt = build_atendimento_system_prompt(
        {
            "nome_agente": "Atendimento Dança Circular",
            "nome_empresa": "Rosangela Ferrioli Terapia",
            "ia_identidade": "A Dança Circular é uma atividade coletiva.",
            "ia_regras_negocio": "A programação confirmada é em 06/09 às 15h.",
            "atendente_prompt": "Evento legado em 15/08 às 17h por R$70.",
        },
        {"mensagem_atual": "Olá! Posso ter mais informações sobre a dança?"},
    )

    assert "06/09 às 15h" in prompt
    assert "15/08 às 17h" not in prompt
    assert "R$70" not in prompt
    assert "Use somente fatos presentes" in prompt
    assert "Em primeiro contato ou pergunta genérica" in prompt


def test_missing_fact_not_invented_policy_is_in_runtime_prompt():
    prompt = build_atendimento_system_prompt(
        {
            "nome_agente": "Assistente",
            "nome_empresa": "Empresa",
            "ia_identidade": "Atendimento cordial.",
            "ia_regras_negocio": "A dança é uma atividade coletiva.",
        },
        {"mensagem_atual": "Olá! Posso ter mais informações sobre a dança?"},
    )

    assert "bioenergética" not in prompt.lower()
    assert "cacau" not in prompt.lower()
    assert "chá de maçã" not in prompt.lower()
    assert "Não faça afirmações terapêuticas ou clínicas" in prompt


def test_agent_context_tenant_isolation_in_prompt_builder():
    tenant_a = build_atendimento_system_prompt(
        {
            "nome_agente": "Agente A",
            "nome_empresa": "Empresa A",
            "ia_identidade": "Fato exclusivo A.",
        }
    )
    tenant_b = build_atendimento_system_prompt(
        {
            "nome_agente": "Agente B",
            "nome_empresa": "Empresa B",
            "ia_identidade": "Fato exclusivo B.",
        }
    )

    assert "Fato exclusivo A." in tenant_a
    assert "Fato exclusivo B." not in tenant_a
    assert "Fato exclusivo B." in tenant_b
    assert "Fato exclusivo A." not in tenant_b


def test_controlled_first_reply_is_not_only_short_permission_question():
    simulated_reply = (
        "Bom dia, Jose! Tudo bem? Vou te explicar direitinho como funciona o Metodo Hibrido LeoFT. "
        "Se voce ja treina ou tenta manter rotina, muitas vezes o problema nao e falta de esforco, "
        "e sim falta de uma direcao clara. O treino e personalizado para objetivo, rotina e nivel atual, "
        "com estrutura pelos proximos 3 meses no aplicativo."
    )
    assert "Metodo Hibrido LeoFT" in simulated_reply
    assert "direcao clara" in simulated_reply
    assert "3 meses" in simulated_reply
    assert simulated_reply != "Bom dia, tudo bem? Posso te explicar o Metodo Hibrido?"


def test_mobile_keyboard_keeps_next_button_reachable_without_fixed_margin():
    shell = Path("apps/anamnese-leonardo/src/features/anamnese/components/StepShell.tsx").read_text(encoding="utf-8")
    css = Path("apps/anamnese-leonardo/src/index.css").read_text(encoding="utf-8")
    assert "visualViewport" in shell
    assert "--keyboard-offset" in shell
    assert "100dvh" in shell
    assert "safe-area-inset-bottom" in css
    assert "scroll-padding-bottom" in css
    assert "bottom: var(--keyboard-offset)" in css
