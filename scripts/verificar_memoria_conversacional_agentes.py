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
FORBIDDEN = ("Intent" + "Router", "Specialist" + "Result", "ToolRegistry", "ResponseGuard", "{", "}", "traceback", "sk-", "Bearer")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_safe(response: str) -> None:
    for token in FORBIDDEN:
        assert_true(token.lower() not in response.lower(), f"Resposta vazou termo proibido {token!r}: {response!r}")


async def load_jjclean() -> Empresa:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == EMPRESA_NOME))
        empresa = result.scalars().first()
        if not empresa:
            raise RuntimeError("Empresa JJClean não encontrada. Rode scripts/setup_jjclean_empresa.py antes.")
        config = (empresa.credenciais_canais or {}).get("orcamento") if isinstance(empresa.credenciais_canais, dict) else None
        if not isinstance(config, dict) or not config.get("enabled"):
            raise RuntimeError("Configuração de orçamento da JJClean não está ativa.")
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
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == "Teste Memoria Conversacional"))
        empresa = result.scalars().first()
        if not empresa:
            empresa = Empresa(
                nome_empresa="Teste Memoria Conversacional",
                area_atuacao="Teste automatizado",
                credenciais_canais={"orcamento": (source.credenciais_canais or {}).get("orcamento")},
            )
            session.add(empresa)
            await session.flush()
        else:
            empresa.credenciais_canais = {"orcamento": (source.credenciais_canais or {}).get("orcamento")}
        await session.commit()
        return str(empresa.id)


async def send(empresa_id: str, lead_id: str, phone: str, text: str, history: list[str] | None = None) -> str:
    state = {
        "empresa_id": empresa_id,
        "lead_id": lead_id,
        "telefone": phone,
        "identificador_origem": phone,
        "canal": "simulador",
        "mensagem_atual": text,
        "mensagens": [*(history or []), f"Usuario: {text}"],
        "status_atendimento": "ia_ativa",
    }
    output = await graph.ainvoke(state, config={"configurable": {"thread_id": f"test:{empresa_id}:{phone}"}})
    response = str(output.get("resposta_final") or "")
    assert_true(response.strip(), f"Sem resposta para {text!r}")
    assert_safe(response)
    return response


async def run_sequence(empresa_id: str, phone: str, messages: list[str]) -> list[str]:
    lead_id = await ensure_lead(empresa_id, phone, f"Teste {phone}")
    history: list[str] = []
    responses: list[str] = []
    for text in messages:
        response = await send(empresa_id, lead_id, phone, text, history)
        responses.append(response)
        history.extend([f"Usuario: {text}", f"Assistente: {response}"])
    return responses


async def main() -> int:
    empresa = await load_jjclean()
    empresa_id = str(empresa.id)
    failures: list[str] = []

    async def case(name: str, coro):
        try:
            await coro
            print(f"OK - {name}")
        except Exception as exc:
            failures.append(f"FAIL - {name}: {exc}")
            print(failures[-1])

    async def fluxo_sofa():
        responses = await run_sequence(
            empresa_id,
            "559900000001",
            ["Boa tarde", "Quero limpar um sofá", "1,80 mais ou menos"],
        )
        second = norm(responses[1])
        third = norm(responses[2])
        assert_true("medida" in second or "tamanho" in second or "metro" in second, responses[1])
        assert_true("r$ 180" in third, responses[2])
        assert_true("orcamento previo" in third or "orçamento prévio" in third, responses[2])
        assert_true("agendamento" in third or "agendar" in third, responses[2])
        assert_true("me diga o que você precisa" not in third, responses[2])
        assert_true("precisa limpar" not in third, responses[2])

    async def retomada():
        lead_id = await ensure_lead(empresa_id, "559900000002", "Teste retomada")
        history: list[str] = []
        for text in ["Quero limpar um sofá", "1,80 mais ou menos"]:
            response = await send(empresa_id, lead_id, "559900000002", text, history)
            history.extend([f"Usuario: {text}", f"Assistente: {response}"])
        response = await send(empresa_id, lead_id, "559900000002", "Eu pedi o orçamento", history)
        response_norm = norm(response)
        assert_true("r$ 180" in response_norm, response)
        assert_true("medida" not in response_norm or "1,80" in response_norm, response)
        assert_true("agendamento" in response_norm or "agendar" in response_norm, response)

    async def medida_com_memoria():
        lead_id = await ensure_lead(empresa_id, "559900000003", "Teste medida memoria")
        await send(empresa_id, lead_id, "559900000003", "Quero limpar um sofá")
        response = await send(empresa_id, lead_id, "559900000003", "2 metros")
        assert_true("r$ 200" in norm(response), response)

    async def medida_sem_memoria():
        responses = await run_sequence(empresa_id, "559900000004", ["2 metros"])
        response_norm = norm(responses[-1])
        assert_true("qual item" in response_norm or "qual serviço" in response_norm or "qual servico" in response_norm, responses[-1])
        assert_true("r$" not in response_norm, responses[-1])
        assert_true("sofa" not in response_norm or "colch" in response_norm or "cadeira" in response_norm, responses[-1])

    async def colchao():
        responses = await run_sequence(empresa_id, "559900000005", ["Quero limpar um colchão", "Queen"])
        assert_true("r$ 120" in norm(responses[-1]), responses[-1])

    async def cadeiras():
        responses = await run_sequence(empresa_id, "559900000006", ["Quero limpar cadeiras", "6"])
        assert_true("r$ 90" in norm(responses[-1]), responses[-1])

    async def agendamento():
        lead_id = await ensure_lead(empresa_id, "559900000007", "Teste agendamento")
        history: list[str] = []
        for text in ["Quero limpar um sofá", "1,80 mais ou menos"]:
            response = await send(empresa_id, lead_id, "559900000007", text, history)
            history.extend([f"Usuario: {text}", f"Assistente: {response}"])
        response = await send(empresa_id, lead_id, "559900000007", "Pode agendar", history)
        response_norm = norm(response)
        for expected in ("nome", "endereço", "melhor dia"):
            assert_true(norm(expected) in response_norm, response)
        assert_true("turno" in response_norm or "horário" in response_norm or "horario" in response_norm, response)

    async def multi_tenant():
        second_empresa_id = await ensure_second_empresa(empresa)
        lead_a = await ensure_lead(empresa_id, "559900000008", "Teste isolamento A")
        lead_b = await ensure_lead(empresa_id, "559900000009", "Teste isolamento B")
        lead_c = await ensure_lead(second_empresa_id, "559900000008", "Teste isolamento C")
        await send(empresa_id, lead_a, "559900000008", "Quero limpar um sofá")
        response_b = await send(empresa_id, lead_b, "559900000009", "1,80 mais ou menos")
        response_c = await send(second_empresa_id, lead_c, "559900000008", "1,80 mais ou menos")
        assert_true("r$ 180" not in norm(response_b), response_b)
        assert_true("r$ 180" not in norm(response_c), response_c)
        assert_true("qual" in norm(response_b), response_b)
        assert_true("qual" in norm(response_c), response_c)

    await case("fluxo sofá com memória", fluxo_sofa())
    await case("retomada usa orçamento anterior", retomada())
    await case("medida com memória", medida_com_memoria())
    await case("medida sem memória", medida_sem_memoria())
    await case("colchão queen", colchao())
    await case("cadeiras", cadeiras())
    await case("agendamento depois do orçamento", agendamento())
    await case("multi-tenant sem vazamento", multi_tenant())

    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(failure)
        return 1
    print("\nTodos os testes de memória conversacional passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
