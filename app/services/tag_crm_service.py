import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import CRMLead, TagCRM
from app.services.ads_integration_service import notificar_conversao_ads


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


async def processar_disparo_conversao_ads_para_tags(
    session: AsyncSession,
    lead: CRMLead,
    tags_aplicadas: list[str],
) -> None:
    if not tags_aplicadas:
        return

    if not (str(getattr(lead, "gclid", "") or "").strip() or str(getattr(lead, "fbclid", "") or "").strip()):
        return

    tags_norm = {str(tag).strip().lower() for tag in tags_aplicadas if str(tag).strip()}
    if not tags_norm:
        return

    result = await session.execute(
        select(TagCRM).where(
            TagCRM.empresa_id == lead.empresa_id,
            TagCRM.disparar_conversao_ads == True,
        )
    )
    tags_disparo = result.scalars().all()

    for tag in tags_disparo:
        nome_tag = str(tag.nome or "").strip().lower()
        if nome_tag in tags_norm:
            await notificar_conversao_ads(str(lead.id), str(tag.nome), session)


def disparar_conversao_ads_background(lead_id: str, tag_nome: str) -> None:
    async def _runner() -> None:
        async with AsyncSessionLocal() as session:
            await notificar_conversao_ads(lead_id, tag_nome, session)

    asyncio.create_task(_runner())
