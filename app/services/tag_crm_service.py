import uuid

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import CRMLead, TagCRM


def normalizar_tags(tags: list[str] | None) -> list[str]:
    output: list[str] = []
    vistos: set[str] = set()

    for tag in tags or []:
        limpa = str(tag or "").strip()
        if not limpa:
            continue
        chave = limpa.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        output.append(limpa)

    return output


async def listar_tags_crm_para_prompt(empresa_id: str | uuid.UUID | None) -> str:
    if not empresa_id:
        return ""

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return ""

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TagCRM)
            .where(
                TagCRM.empresa_id == empresa_uuid,
                TagCRM.instrucao_ia.is_not(None),
            )
            .order_by(TagCRM.nome.asc())
        )
        tags = result.scalars().all()

    linhas = []
    for tag in tags:
        instrucao = (tag.instrucao_ia or "").strip()
        if not instrucao:
            continue
        linhas.append(f"- {tag.nome} - Regra: {instrucao}")

    return "\n".join(linhas)


async def listar_tags_oficiais_ou_existentes(empresa_id: str | uuid.UUID) -> list[str]:
    empresa_uuid = uuid.UUID(str(empresa_id))

    async with AsyncSessionLocal() as session:
        result_tags = await session.execute(
            select(TagCRM.nome)
            .where(TagCRM.empresa_id == empresa_uuid)
            .order_by(TagCRM.nome.asc())
        )
        tags_oficiais = [str(nome).strip() for nome in result_tags.scalars().all() if str(nome).strip()]
        if tags_oficiais:
            return tags_oficiais

        result_leads = await session.execute(
            select(CRMLead.tags).where(CRMLead.empresa_id == empresa_uuid)
        )
        tags_unicas: set[str] = set()
        for tags in result_leads.scalars().all():
            for tag in tags or []:
                limpa = str(tag).strip()
                if limpa:
                    tags_unicas.add(limpa)

        return sorted(tags_unicas, key=lambda item: item.lower())
