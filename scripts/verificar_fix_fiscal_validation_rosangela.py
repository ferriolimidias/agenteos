from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.graph import run_new_agent_flow
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import IncomingMessage
from app.api.routers.empresas import TagCRMResponse


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class RecordingFiscal:
    def __init__(self, findings=None):
        self.calls = 0
        self.findings = findings or []

    def validate(self, **kwargs):
        self.calls += 1
        return list(self.findings)


async def verify_agent_paths() -> None:
    fiscal = RecordingFiscal()
    agent = AtendimentoAgent(
        llm=MockAgentLLM(responses=["Olá! Posso explicar como funciona o atendimento e horários disponíveis."]),
        tool_planner=MockToolPlanner(),
        fiscal=fiscal,
    )
    agent._load_tenant_config = AsyncMock(  # type: ignore[attr-defined]
        return_value={
            "empresa_id": "tenant-rosangela",
            "has_ai": True,
            "modelo_ia": "gpt-4o-mini",
            "nome_empresa": "Rosangela Ferrioli Terapia",
        }
    )
    output = await run_new_agent_flow(
        {"empresa_id": "tenant-rosangela", "lead_id": "lead-1", "canal": "whatsapp"},
        IncomingMessage(text="Olá, gostaria de saber como funciona o atendimento da Rosângela.", tenant_id="tenant-rosangela"),
        atendimento_agent=agent,
    )
    assert_true(bool(output.response_text), "Rosângela com LLM fake gera resposta_final")
    assert_true(fiscal.calls == 0, "Rosângela usa validação geral, não fiscal")

    fiscal_block = RecordingFiscal([{"code": "blocked", "severity": "error", "message": "blocked"}])
    fiscal_agent = AtendimentoAgent(
        llm=MockAgentLLM(responses=["Vou verificar e retorno em breve.", "Vou verificar e retorno em breve."]),
        tool_planner=MockToolPlanner(),
        fiscal=fiscal_block,
    )
    fiscal_agent._load_tenant_config = agent._load_tenant_config  # type: ignore[attr-defined]
    fiscal_output = await run_new_agent_flow(
        {"empresa_id": "tenant-rosangela", "lead_id": "lead-1", "canal": "whatsapp", "active_intent": "orcamento"},
        IncomingMessage(text="Qual o valor?", tenant_id="tenant-rosangela"),
        atendimento_agent=fiscal_agent,
    )
    assert_true(fiscal_block.calls >= 1, "fluxo fiscal legítimo executa validator fiscal")
    assert_true(fiscal_output.metadata["blocked_reason"] == "fiscal_validation_failed", "fiscal inválido preserva reason correto")


def verify_source_contracts() -> None:
    agent = read("app/agents/atendimento/agent.py")
    utils = read("app/api/utils.py")
    empresas = read("app/api/routers/empresas.py")
    inbox_api = read("app/api/routers/inbox.py")
    inbox_front = read("frontend/src/pages/tenant/Inbox.jsx")
    monitor = read("scripts/monitorar_teste_rosangela.sh")

    assert_true("def _should_run_fiscal_validation" in agent, "validator fiscal condicionado")
    assert_true("ResponseGuard" in agent, "validação geral preservada")
    assert_true('review_failure_reasons = {"fiscal_validation_failed"}' in utils, "fiscal legítimo mantém revisão")
    assert_true("O atendimento automatico continua configurado." in utils, "erro técnico não parece desligamento manual")
    assert_true('"operacional"]' in empresas, "tipo operacional aceito no contrato backend")
    assert_true("AgenteFiscal" not in inbox_api.split("async def enviar_mensagem(", 1)[1].split("send_media", 1)[0], "manual outbound não usa fiscal validator")
    assert_true("Falha ao gerar resposta" in inbox_front, "frontend traduz falha técnica")
    assert_true("Resposta fiscal precisa de revisão" in inbox_front, "frontend traduz falha fiscal")
    assert_true("Monitoramento ativo. Aguardando nova mensagem da Rosângela..." in monitor, "monitor exibe mensagem inicial")

    tag = TagCRMResponse(id="tag-1", nome="Atendimento humano", tipo="operacional")
    assert_true(tag.tipo == "operacional", "schema serializa tag operacional")


async def main() -> int:
    verify_source_contracts()
    await verify_agent_paths()
    print("verificar_fix_fiscal_validation_rosangela: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
