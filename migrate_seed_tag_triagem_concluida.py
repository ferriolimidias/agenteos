"""
Backfill: garante a tag \"[Triagem Concluída]\" em todas as empresas existentes.

Útil para bases antigas criadas antes do seed em inicializar_dados_nova_empresa.

Uso:
  python migrate_seed_tag_triagem_concluida.py
"""

from __future__ import annotations

import asyncio
import uuid

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from db.database import AsyncSessionLocal
from db.models import Empresa, TagCRM, TagGroup

TAG_NOME = "[Triagem Concluída]"


async def main() -> None:
    criadas = 0
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Empresa.id))
        empresas = [row[0] for row in res.all()]
        for emp_id in empresas:
            if not isinstance(emp_id, uuid.UUID):
                continue
            existe = await session.execute(
                select(TagCRM.id).where(TagCRM.empresa_id == emp_id, TagCRM.nome == TAG_NOME)
            )
            if existe.scalar():
                continue
            res_g = await session.execute(
                select(TagGroup).where(TagGroup.empresa_id == emp_id, TagGroup.nome.ilike("Sistema"))
            )
            grupo = res_g.scalars().first()
            if not grupo:
                grupo = TagGroup(empresa_id=emp_id, nome="Sistema", cor="#64748b", ordem=0)
                session.add(grupo)
                await session.flush()
            session.add(
                TagCRM(
                    empresa_id=emp_id,
                    grupo_id=grupo.id,
                    nome=TAG_NOME,
                    cor="#22c55e",
                    tipo="comportamento",
                    ordem=0,
                    ativa_no_funil=False,
                    instrucao_ia=(
                        "Aplicada automaticamente pelo agente de saudação quando o nome do "
                        "contato já foi identificado como pessoa física."
                    ),
                )
            )
            criadas += 1
        await session.commit()
    print(f"Tags criadas: {criadas} (empresas sem a tag). Total empresas: {len(empresas)}.")


if __name__ == "__main__":
    asyncio.run(main())
