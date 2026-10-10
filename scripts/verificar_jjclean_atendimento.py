from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.graph import run_new_agent_flow
from app.agents.schemas import IncomingMessage
from db.database import AsyncSessionLocal
from db.models import Empresa


EMPRESA_NOME = "JJClean"
TERMOS_PROIBIDOS = (
    "Intent" + "Router",
    "Specialist" + "Result",
    "ToolRegistry",
    "ResponseGuard",
    "JSON",
    "traceback",
    "sk-",
    "Bearer",
)


@dataclass
class Case:
    name: str
    text: str
    must_contain: tuple[str, ...]
    must_not_contain: tuple[str, ...] = TERMOS_PROIBIDOS
    metadata: dict | None = None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


async def load_empresa_id() -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == EMPRESA_NOME))
        empresa = result.scalars().first()
        if not empresa:
            raise RuntimeError("Empresa JJClean não encontrada. Rode scripts/setup_jjclean_empresa.py antes.")
        config = (empresa.credenciais_canais or {}).get("orcamento") if isinstance(empresa.credenciais_canais, dict) else None
        if not isinstance(config, dict) or not config.get("enabled"):
            raise RuntimeError("Configuração de atendimento/orçamento da JJClean não está ativa.")
        return str(empresa.id)


async def simulate(empresa_id: str, case: Case) -> str:
    metadata = dict(case.metadata or {})
    message = IncomingMessage(
        text=case.text,
        tenant_id=empresa_id,
        phone="5521999999999",
        channel="simulador",
        metadata=metadata,
        has_image=bool(metadata.get("has_image")),
        media_type=metadata.get("media_type"),
        media_mime_type=metadata.get("media_mime_type"),
        caption=metadata.get("caption"),
        image_analysis=metadata.get("image_analysis"),
    )
    output = await run_new_agent_flow(
        {
            "empresa_id": empresa_id,
            "telefone": "5521999999999",
            "canal": "simulador",
            "status_atendimento": "ia_ativa",
            "mensagem_atual": case.text,
            **metadata,
        },
        message=message,
    )
    return output.response_text or ""


def assert_response(case: Case, response: str) -> None:
    if not response.strip():
        raise AssertionError(f"{case.name}: resposta vazia")
    normalized = normalize(response)
    for expected in case.must_contain:
        if normalize(expected) not in normalized:
            raise AssertionError(f"{case.name}: resposta não contém {expected!r}. Resposta: {response!r}")
    for forbidden in case.must_not_contain:
        if forbidden.lower() in response.lower():
            raise AssertionError(f"{case.name}: termo técnico proibido {forbidden!r}. Resposta: {response!r}")


async def main() -> int:
    empresa_id = await load_empresa_id()
    cases = [
        Case(
            name="saudação natural e apresentação",
            text="Olá",
            must_contain=("Olá! Tudo bem?", "assistente da JJClean", "Me conta o que você precisa limpar"),
        ),
        Case(
            name="sofá sem medida",
            text="Quero limpar meu sofá",
            must_contain=("Claro", "sofá", "tamanho aproximado em metros", "foto"),
        ),
        Case(
            name="sofá 2 metros",
            text="Meu sofá tem 2 metros",
            must_contain=("sofá de aproximadamente 2 metros", "R$ 200", "orçamento prévio e aproximado", "agendamento"),
        ),
        Case(
            name="colchão queen",
            text="tenho um colchão queen",
            must_contain=("colchão queen", "R$ 120", "orçamento prévio"),
        ),
        Case(
            name="6 cadeiras",
            text="quero limpar 6 cadeiras",
            must_contain=("6 cadeiras", "R$ 90", "orçamento prévio"),
        ),
        Case(
            name="combinação sofá e cadeiras",
            text="sofá de 2 metros e 6 cadeiras",
            must_contain=("R$ 200", "R$ 90", "Total prévio: R$ 290", "orçamento prévio"),
        ),
        Case(
            name="imagem sem medida",
            text="[Imagem]",
            metadata={
                "has_image": True,
                "media_type": "image",
                "media_mime_type": "image/jpeg",
                "caption": "foto do sofá",
                "image_analysis_available": False,
            },
            must_contain=("Recebi a foto", "tamanho aproximado do sofá", "medida"),
        ),
        Case(
            name="aceite conduz agendamento",
            text="Pode agendar",
            must_contain=("Perfeito", "nome", "endereço ou bairro", "melhor dia", "melhor turno", "equipe confirma"),
        ),
        Case(
            name="pedido humano cordial",
            text="quero falar com humano",
            must_contain=("Claro", "encaminhar", "pessoa da equipe"),
        ),
    ]

    failures: list[str] = []
    print(f"empresa_id={empresa_id}")
    for case in cases:
        try:
            response = await simulate(empresa_id, case)
            assert_response(case, response)
            print(f"OK - {case.name}: {response}")
        except Exception as exc:
            failures.append(f"FAIL - {case.name}: {exc}")
            print(failures[-1])

    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(failure)
        return 1
    print("\nTodos os cenários de atendimento JJClean passaram sem WhatsApp, Evolution ou OpenAI real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
