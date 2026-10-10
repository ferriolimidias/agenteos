from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.guards.response_guard import ResponseGuard
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage


FORBIDDEN_RUNTIME_PATTERNS = (
    "JJClean",
    "jjclean",
    "Assistente JJClean",
    "97318",
    "jjlimpezaprofissional",
    "Rosangela",
    "Rosângela",
    "9f1b7f7d-7952-448c-b4c8-614b2fc73481",
    "Dança Circular para Mulheres",
    "danca_circular",
    "QUERO MINHA VAGA",
    "R$ 70",
    "Leonardo Personal",
    "a7384cb4-98b2-4750-94bc-3b329d35156d",
    "Método Híbrido LeoFT",
    "QUERO COMEÇAR",
    "quero começar",
    "quero comecar",
    "quero minha vaga",
    "anamnese_leoft",
)


JJCLEAN_CONFIG = {
    "enabled": True,
    "currency": "R$",
    "disclaimer": "Esse é um orçamento prévio e aproximado.",
    "messages": {"budget_next_step": "Quer solicitar o agendamento?"},
    "services": {
        "sofa": {
            "label": "Sofá",
            "aliases": ["sofa", "sofá"],
            "response_label": "um sofá",
            "pricing_type": "tier_by_measure",
            "missing_field": "tamanho do sofá em metros",
            "tiers": [
                {"max": 1.8, "price": 180, "label": "Sofá até 1,80 m"},
                {"max": 2.3, "price": 200, "label": "Sofá até 2,30 m"},
                {"max": 2.8, "price": 250, "label": "Sofá até 2,80 m"},
            ],
        },
        "cadeiras": {
            "label": "Cadeira",
            "aliases": ["cadeira", "cadeiras"],
            "response_label_plural": "cadeiras",
            "pricing_type": "unit",
            "missing_field": "quantidade de cadeiras",
            "unit_price": 15,
        },
    },
}


EMPRESA_TESTE_CONFIG = {
    "enabled": True,
    "currency": "R$",
    "disclaimer": "Estimativa sujeita à validação operacional.",
    "messages": {"budget_next_step": "Deseja enviar os dados para conferência?"},
    "services": {
        "vistoria": {
            "label": "Vistoria técnica",
            "aliases": ["vistoria", "inspecao", "inspeção"],
            "pricing_type": "unit",
            "missing_field": "quantidade de vistorias",
            "unit_price": 75,
        },
        "laudo": {
            "label": "Laudo técnico",
            "aliases": ["laudo"],
            "pricing_type": "variant",
            "missing_field": "tipo do laudo",
            "variants": {
                "simples": {"label": "Laudo simples", "aliases": ["simples"], "price": 310},
                "completo": {"label": "Laudo completo", "aliases": ["completo"], "price": 590},
            },
        },
    },
}


TENANT_CONFIGS = {
    "jjclean-simulado": JJCLEAN_CONFIG,
    "empresa-teste-servicos": EMPRESA_TESTE_CONFIG,
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _plan(*tools: str) -> dict:
    return {
        "selected_tools": list(tools),
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "test_injected_plan",
    }


def build_tenant_config(tenant_id: str) -> dict[str, Any]:
    orcamento = TENANT_CONFIGS.get(str(tenant_id))
    if not orcamento:
        return {"empresa_id": tenant_id, "nome_empresa": tenant_id, "nome_agente": "Assistente", "credenciais_canais": {}, "has_ai": False}
    return {
        "empresa_id": tenant_id,
        "nome_empresa": tenant_id,
        "nome_agente": "Assistente",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "mensagem_saudacao": None,
        "ia_identidade": None,
        "ia_regras_negocio": None,
        "atendente_prompt": None,
        "condutor_prompt": None,
        "credenciais_canais": {"orcamento": orcamento},
        "has_ai": True,
    }


def check_static_no_company_hardcode() -> None:
    runtime_paths = (
        Path("app"),
        Path("app/agents/atendimento"),
        Path("app/agents/llm"),
        Path("app/agents/tools"),
        Path("app/agents/tenant_config.py"),
        Path("app/api/utils.py"),
    )
    for base in runtime_paths:
        if base.is_file():
            candidates = [base]
        elif base.exists():
            candidates = [path for path in base.rglob("*") if path.is_file() and path.suffix in {".py", ".jsx", ".js", ".ts", ".tsx"}]
        else:
            continue
        for path in candidates:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in FORBIDDEN_RUNTIME_PATTERNS:
                assert_true(pattern not in text, f"Hardcode de tenant/segmento em runtime: {path}: {pattern}")

    tokenized_service = Path("app/services/tokenized_links.py")
    assert_true(tokenized_service.exists(), "Serviço genérico de links tokenizados não encontrado.")
    source = tokenized_service.read_text(encoding="utf-8", errors="ignore")
    assert_true("get_tokenized_link_config" in source and "generate_or_get_tokenized_link" in source, "Serviço de link tokenizado não expõe funções genéricas.")
    for pattern in ("Método Híbrido LeoFT", "QUERO COMEÇAR", "anamnese_leoft", "Leonardo"):
        assert_true(pattern not in source, f"Serviço de link tokenizado contém regra específica: {pattern}")

    sequence_source = Path("app/services/configured_whatsapp_sequences.py").read_text(encoding="utf-8", errors="ignore")
    assert_true("_cta_applies" in sequence_source and "_has_cta_negation" in sequence_source, "Detecção de CTA usa funções genéricas.")
    for pattern in ("QUERO COMEÇAR", "QUERO MINHA VAGA", "quero começar", "quero minha vaga", "anamnese_leoft"):
        assert_true(pattern not in sequence_source, f"Matcher de CTA contém alias específico em runtime: {pattern}")

    public_anamnese = Path("app/api/routers/public_anamnese.py")
    assert_true(public_anamnese.exists(), "Router público de anamnese genérica existe.")
    public_source = public_anamnese.read_text(encoding="utf-8", errors="ignore")
    assert_true('/{slug}/session' in public_source and '/{slug}/respostas' in public_source, "Rota pública de anamnese usa slug genérico.")
    for pattern in ("Método Híbrido LeoFT", "QUERO COMEÇAR", "anamnese_leoft", "Leonardo", "a7384cb4-98b2-4750-94bc-3b329d35156d"):
        assert_true(pattern not in public_source, f"Router público contém regra específica: {pattern}")


async def run_agent(tenant_id: str, text: str):
    agent = AtendimentoAgent(llm=MockAgentLLM(), tool_planner=MockToolPlanner(default_plan=_plan("context", "orcamento")))
    agent._load_tenant_config = AsyncMock(  # type: ignore[attr-defined]
        return_value=build_tenant_config(tenant_id)
    )
    return await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id=tenant_id, mensagem_atual=text),
            messages=[IncomingMessage(text=text, tenant_id=tenant_id)],
        )
    )


async def check_tenant_isolation() -> None:
    jj = await run_agent("jjclean-simulado", "quanto custa um sofá de 2 metros?")
    assert_true("orcamento" in jj.metadata.get("selected_tools", []), "Tenant simulado não planejou orçamento.")
    assert_true("R$ 200" in jj.response_text, "JJClean simulada não usou a tabela do tenant.")

    other = await run_agent("empresa-teste-servicos", "quanto custa 2 vistorias?")
    assert_true("orcamento" in other.metadata.get("selected_tools", []), "EmpresaTesteServicos não planejou orçamento.")
    assert_true("R$ 150" in other.response_text, "EmpresaTesteServicos não usou a própria tabela.")
    assert_true("R$ 200" not in other.response_text and "sofá" not in other.response_text.lower(), "Regra JJClean vazou para outro tenant.")

    unconfigured = await run_agent("tenant-sem-orcamento", "quanto custa o serviço?")
    assert_true(unconfigured.response_text == "", "Tenant sem IA/configuração deveria falhar fechado.")
    assert_true(unconfigured.decision.should_respond is False, "Tenant sem IA deveria marcar should_respond=False.")

    different = await run_agent("empresa-teste-servicos", "laudo completo")
    assert_true("R$ 590" in different.response_text, "Configuração diferente não calculou regra própria.")


def check_response_guard_no_leaks() -> None:
    guard = ResponseGuard()
    samples = (
        ("Intent" + "Router") + " selecionou " + ("Specialist" + "Result") + " com ToolRegistry.",
        '{"internal": true, "token": "abc"}',
        "Traceback (most recent call last): File \"x.py\", line 1",
        "segredo sk-1234567890abcdef",
        "Authorization: Bearer abcdefghijklmnop",
        "ResponseGuard bloqueou a resposta",
    )
    for sample in samples:
        result = guard.validate(sample)
        assert_true(result.sanitized is True, f"ResponseGuard não sanitizou vazamento: {sample}")
        blocked = ("Intent" + "Router", "Specialist" + "Result", "ToolRegistry", "ResponseGuard", "sk-", "Bearer", "Traceback")
        assert_true(not any(token in result.text for token in blocked), "Fallback vazou marcador interno.")


async def main() -> None:
    check_static_no_company_hardcode()
    await check_tenant_isolation()
    check_response_guard_no_leaks()
    print("verificar_multi_tenant_sem_hardcode: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"verificar_multi_tenant_sem_hardcode: FALHOU: {exc}", file=sys.stderr)
        sys.exit(1)
