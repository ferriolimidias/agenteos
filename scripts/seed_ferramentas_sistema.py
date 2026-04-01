import asyncio
from typing import Any

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import Empresa, FerramentaAPI


FERRAMENTAS_SISTEMA: list[dict[str, Any]] = [
    {
        "nome_exibicao": "Aplicar Tag Dinâmica",
        "nome_ferramenta": "tool_aplicar_tag_dinamica",
        "descricao_ia": "Permite que o agente aplique tags de classificação ao contato de forma autônoma.",
        "schema_parametros": {
            "type": "object",
            "properties": {
                "nome_da_tag": {
                    "type": "string",
                    "description": "Nome exato da tag oficial que deve ser aplicada ao lead atual.",
                }
            },
            "required": ["nome_da_tag"],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Transferir para Humano (Pausar Bot)",
        "nome_ferramenta": "tool_transferir_para_humano",
        "descricao_ia": "Permite que o agente pause o bot por 24 horas e coloque o lead na fila de atendimento humano.",
        "schema_parametros": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "description": "Resumo curto do motivo para pausar o bot e transferir para humano.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]


async def seed_ferramentas_sistema() -> None:
    async with AsyncSessionLocal() as session:
        try:
            empresas_result = await session.execute(select(Empresa))
            empresas = empresas_result.scalars().all()

            if not empresas:
                print("Nenhuma empresa encontrada. Nada para processar.")
                return

            total_criadas = 0
            total_atualizadas = 0

            for empresa in empresas:
                nomes_internos = [f["nome_ferramenta"] for f in FERRAMENTAS_SISTEMA]
                existentes_result = await session.execute(
                    select(FerramentaAPI).where(
                        FerramentaAPI.empresa_id == empresa.id,
                        FerramentaAPI.nome_ferramenta.in_(nomes_internos),
                    )
                )
                existentes = {
                    ferramenta.nome_ferramenta: ferramenta
                    for ferramenta in existentes_result.scalars().all()
                }

                for ferramenta_seed in FERRAMENTAS_SISTEMA:
                    nome_interno = ferramenta_seed["nome_ferramenta"]
                    nome_exibicao = ferramenta_seed["nome_exibicao"]
                    descricao = ferramenta_seed["descricao_ia"]
                    schema = ferramenta_seed["schema_parametros"]

                    ferramenta_db = existentes.get(nome_interno)
                    descricao_com_nome = f"[{nome_exibicao}] {descricao}"

                    if ferramenta_db is None:
                        ferramenta_db = FerramentaAPI(
                            empresa_id=empresa.id,
                            nome_ferramenta=nome_interno,
                            descricao_ia=descricao_com_nome,
                            schema_parametros=schema,
                            url=None,
                            metodo="GET",
                            headers=None,
                            payload=None,
                        )
                        session.add(ferramenta_db)
                        total_criadas += 1
                    else:
                        ferramenta_db.descricao_ia = descricao_com_nome
                        ferramenta_db.schema_parametros = schema
                        ferramenta_db.url = None
                        ferramenta_db.metodo = "GET"
                        ferramenta_db.headers = None
                        ferramenta_db.payload = None
                        total_atualizadas += 1

            await session.commit()
            print(
                "Seed concluído com sucesso. "
                f"Empresas processadas: {len(empresas)} | "
                f"Ferramentas criadas: {total_criadas} | "
                f"Ferramentas atualizadas: {total_atualizadas}"
            )
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(seed_ferramentas_sistema())
