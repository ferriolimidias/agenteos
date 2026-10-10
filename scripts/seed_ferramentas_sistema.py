import asyncio
from typing import Any

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import Empresa, FerramentaAPI


FERRAMENTAS_SISTEMA: list[dict[str, Any]] = [
    {
        "nome_exibicao": "Aplicar Tag Dinâmica",
        "nome_ferramenta": "tool_aplicar_tag_dinamica",
        "descricao_ia": "Recebe o tag_id (UUID) para aplicar uma etiqueta ao lead. Obrigatório consultar o ID antes.",
        "schema_parametros": {
            "type": "object",
            "properties": {
                "tag_id": {
                    "type": "string",
                    "description": "UUID da etiqueta oficial que deve ser aplicada ao lead atual.",
                }
            },
            "required": ["tag_id"],
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
    {
        "nome_exibicao": "Consultar Lista de Tags",
        "nome_ferramenta": "tool_consultar_tags_empresa",
        "descricao_ia": "Retorna a lista oficial de etiquetas com NOME e ID. Use isso antes de aplicar qualquer tag.",
        "schema_parametros": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Listar Etapas do Funil (CRM)",
        "nome_ferramenta": "tool_listar_etapas_funil",
        "descricao_ia": (
            "Lista todas as etapas do CRM desta empresa (nome + UUID). "
            "Use antes de mover o lead; os UUIDs são os únicos identificadores válidos de etapa."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Atualizar Etapa do Lead (CRM)",
        "nome_ferramenta": "tool_atualizar_etapa_lead",
        "descricao_ia": (
            "Move o lead para uma etapa do funil. O parâmetro etapa_id deve ser um UUID obtido de tool_listar_etapas_funil."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {
                "etapa_id": {
                    "type": "string",
                    "description": "UUID da etapa de destino (copiado da listagem oficial do funil desta empresa).",
                }
            },
            "required": ["etapa_id"],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Adicionar Tag ao Lead (CRM)",
        "nome_ferramenta": "tool_adicionar_tag_lead",
        "descricao_ia": (
            "Aplica uma etiqueta oficial ao lead. Informe tag_id (UUID) OU tag_nome (nome exato da tag). "
            "Prefira UUID após tool_consultar_tags_empresa."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {
                "tag_id": {
                    "type": "string",
                    "description": "UUID da etiqueta oficial (opcional se tag_nome for informado).",
                },
                "tag_nome": {
                    "type": "string",
                    "description": "Nome exato ou próximo da etiqueta oficial (opcional se tag_id for informado).",
                },
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
