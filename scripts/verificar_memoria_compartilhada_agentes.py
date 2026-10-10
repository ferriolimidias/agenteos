from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.graph import graph
from db.database import AsyncSessionLocal
from db.models import CRMLead, Empresa


EMPRESA_NOME = "JJClean"
FORBIDDEN = (
    "Intent" + "Router",
    "Specialist" + "Result",
    "ToolRegistry",
    "ResponseGuard",
    "ConversationState",
    "JSON",
    "traceback",
    "sk-",
    "Bearer",
)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_safe(response: str) -> None:
    for token in FORBIDDEN:
        assert_true(token.lower() not in response.lower(), f"Resposta vazou termo proibido {token!r}: {response!r}")


async def load_empresa() -> Empresa:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == EMPRESA_NOME))
        empresa = result.scalars().first()
        if not empresa:
            raise RuntimeError("Empresa JJClean não encontrada. Rode scripts/setup_jjclean_empresa.py antes.")
        return empresa


async def ensure_lead(empresa_id: str, phone: str, name: str) -> str:
    async with AsyncSessionLocal() as session:
        empresa_uuid = uuid.UUID(str(empresa_id))
        result = await session.execute(
            select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.telefone_contato == phone)
        )
        lead = result.scalars().first()
        if not lead:
            lead = CRMLead(
                empresa_id=empresa_uuid,
                nome_contato=name,
                telefone_contato=phone,
                dados_adicionais={},
            )
            session.add(lead)
            await session.flush()
        else:
            dados = dict(lead.dados_adicionais or {})
            dados.pop("conversation_state", None)
            lead.dados_adicionais = dados
        await session.commit()
        return str(lead.id)


async def ensure_second_empresa(source: Empresa) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == "Teste Memoria Compartilhada"))
        empresa = result.scalars().first()
        orcamento = (source.credenciais_canais or {}).get("orcamento") if isinstance(source.credenciais_canais, dict) else None
        if not empresa:
            empresa = Empresa(
                nome_empresa="Teste Memoria Compartilhada",
                area_atuacao="Teste automatizado",
                credenciais_canais={"orcamento": orcamento},
            )
            session.add(empresa)
            await session.flush()
        else:
            empresa.credenciais_canais = {"orcamento": orcamento}
        await session.commit()
        return str(empresa.id)


async def send(empresa_id: str, lead_id: str, phone: str, text: str, history: list[str] | None = None) -> dict:
    state = {
        "empresa_id": empresa_id,
        "lead_id": lead_id,
        "telefone": phone,
        "identificador_origem": phone,
        "canal": "evolution",
        "mensagem_atual": text,
        "mensagens": [*(history or []), f"Usuario: {text}"],
        "status_atendimento": "ia_ativa",
    }
    output = await graph.ainvoke(state, config={"configurable": {"thread_id": f"shared:{empresa_id}:{phone}"}})
    response = str(output.get("resposta_final") or "")
    assert_true(response.strip(), f"Sem resposta para {text!r}")
    assert_safe(response)
    output["response_norm"] = norm(response)
    return output


async def main() -> int:
    empresa = await load_empresa()
    empresa_id = str(empresa.id)
    failures: list[str] = []

    async def case(name: str, coro):
        try:
            await coro
            print(f"OK - {name}")
        except Exception as exc:
            failures.append(f"FAIL - {name}: {exc}")
            print(failures[-1])

    async def orcamento_medida():
        lead_id = await ensure_lead(empresa_id, "559910000001", "Teste compartilhada 1")
        history: list[str] = []
        first = await send(empresa_id, lead_id, "559910000001", "Quero limpar um sofá", history)
        history.extend(["Usuario: Quero limpar um sofá", f"Assistente: {first['resposta_final']}"])
        second = await send(empresa_id, lead_id, "559910000001", "1,80 mais ou menos", history)
        memory = second.get("conversation_state") or {}
        budget = memory.get("last_budget_estimate") or {}
        assert_true("*r$ 180*" in second["response_norm"], second["resposta_final"])
        assert_true(float(budget.get("amount") or 0) == 180, str(memory))
        assert_true(memory.get("active_flow") == "orcamento", str(memory))
        assert_true((memory.get("slots") or {}).get("service_type") == "sofa", str(memory))
        assert_true((memory.get("slots") or {}).get("measure") == 1.8, str(memory))
        assert_true("me diga o que você precisa" not in second["response_norm"], second["resposta_final"])

    async def orcamento_agendamento():
        lead_id = await ensure_lead(empresa_id, "559910000002", "Teste compartilhada 2")
        history: list[str] = []
        for text in ["Quero limpar um sofá", "1,80 mais ou menos"]:
            output = await send(empresa_id, lead_id, "559910000002", text, history)
            history.extend([f"Usuario: {text}", f"Assistente: {output['resposta_final']}"])
        output = await send(empresa_id, lead_id, "559910000002", "Pode agendar", history)
        memory = output.get("conversation_state") or {}
        response = output["response_norm"]
        assert_true(memory.get("scheduling_requested") is True, str(memory))
        assert_true(memory.get("active_flow") == "agendamento", str(memory))
        for expected in ("nome", "endereço", "melhor dia", "melhor turno"):
            assert_true(expected in response, output["resposta_final"])
        assert_true("qual serviço" not in response and "qual servico" not in response, output["resposta_final"])

    async def orcamento_transferencia():
        lead_id = await ensure_lead(empresa_id, "559910000003", "Teste compartilhada 3")
        history: list[str] = []
        for text in ["Quero limpar um sofá", "1,80 mais ou menos"]:
            output = await send(empresa_id, lead_id, "559910000003", text, history)
            history.extend([f"Usuario: {text}", f"Assistente: {output['resposta_final']}"])
        output = await send(empresa_id, lead_id, "559910000003", "Quero falar com uma pessoa", history)
        memory = output.get("conversation_state") or {}
        actions = output.get("acoes_sistema_pendentes") or []
        assert_true(memory.get("handoff_requested") is True, str(memory))
        assert_true((memory.get("slots") or {}).get("service_type") == "sofa", str(memory))
        assert_true(float((memory.get("last_budget_estimate") or {}).get("amount") or 0) == 180, str(memory))
        action_types = [item.get("action_type") for item in actions if isinstance(item, dict)]
        assert_true("solicitar_transferencia_humana" in action_types, str(actions))

    async def qualificacao_nao_reseta():
        lead_id = await ensure_lead(empresa_id, "559910000004", "Teste compartilhada 4")
        history: list[str] = []
        first = await send(empresa_id, lead_id, "559910000004", "Quero limpar um sofá", history)
        history.extend(["Usuario: Quero limpar um sofá", f"Assistente: {first['resposta_final']}"])
        output = await send(empresa_id, lead_id, "559910000004", "ok", history)
        response = output["response_norm"]
        memory = output.get("conversation_state") or {}
        assert_true(memory.get("active_flow") == "orcamento", str(memory))
        assert_true("orçamento" in response or "orcamento" in response, output["resposta_final"])
        assert_true("olá" not in response and "sou o assistente" not in response, output["resposta_final"])

    async def numerica_com_memoria():
        lead_id = await ensure_lead(empresa_id, "559910000005", "Teste compartilhada 5")
        await send(empresa_id, lead_id, "559910000005", "Quero limpar um sofá")
        output = await send(empresa_id, lead_id, "559910000005", "2 metros")
        assert_true("*r$ 200*" in output["response_norm"], output["resposta_final"])

    async def numerica_sem_memoria():
        lead_id = await ensure_lead(empresa_id, "559910000006", "Teste compartilhada 6")
        output = await send(empresa_id, lead_id, "559910000006", "2 metros")
        response = output["response_norm"]
        assert_true("qual item" in response or "qual serviço" in response or "qual servico" in response, output["resposta_final"])
        assert_true("r$" not in response, output["resposta_final"])

    async def isolamento():
        second_empresa_id = await ensure_second_empresa(empresa)
        lead_a = await ensure_lead(empresa_id, "559910000007", "Lead A")
        lead_b = await ensure_lead(empresa_id, "559910000008", "Lead B")
        lead_c = await ensure_lead(second_empresa_id, "559910000007", "Lead C")
        await send(empresa_id, lead_a, "559910000007", "Quero limpar um sofá")
        out_b = await send(empresa_id, lead_b, "559910000008", "1,80")
        out_c = await send(second_empresa_id, lead_c, "559910000007", "1,80")
        assert_true("r$ 180" not in out_b["response_norm"], out_b["resposta_final"])
        assert_true("r$ 180" not in out_c["response_norm"], out_c["resposta_final"])
        assert_true("qual" in out_b["response_norm"], out_b["resposta_final"])
        assert_true("qual" in out_c["response_norm"], out_c["resposta_final"])

    await case("orçamento -> medida compartilha memória", orcamento_medida())
    await case("orçamento -> agendamento lê orçamento", orcamento_agendamento())
    await case("orçamento -> transferência preserva contexto", orcamento_transferencia())
    await case("qualificação não reseta fluxo ativo", qualificacao_nao_reseta())
    await case("mensagem numérica com memória", numerica_com_memoria())
    await case("mensagem numérica sem memória", numerica_sem_memoria())
    await case("multi-tenant e multi-lead isolados", isolamento())

    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(failure)
        return 1
    print("\nTodos os testes de memória compartilhada passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
