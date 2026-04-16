import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from db.database import AsyncSessionLocal
from db.models import CRMLead, CRMEtapa, CRMFunil, TagCRM
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


def _normalizar_tipo_tag(value: str | None) -> str:
    tipo = str(value or "").strip().lower()
    if tipo in {"etapa_funil", "comportamento"}:
        return tipo
    return "comportamento"


def _resolver_tags_oficiais_do_lead(
    lead_tags: list[str] | None,
    tags_por_id: dict[str, TagCRM],
    tags_por_nome: dict[str, TagCRM],
) -> list[TagCRM]:
    tags_resolvidas: list[TagCRM] = []
    vistos: set[str] = set()
    for item in lead_tags or []:
        valor = str(item or "").strip()
        if not valor:
            continue
        tag = tags_por_id.get(valor) or tags_por_nome.get(valor.lower())
        if not tag:
            continue
        tag_id = str(tag.id)
        if tag_id in vistos:
            continue
        vistos.add(tag_id)
        tags_resolvidas.append(tag)
    return tags_resolvidas


async def sync_tag_etapa_lead(
    lead_id: str,
    session: AsyncSession | None = None,
    preferencia: str = "auto",
) -> dict[str, str | None]:
    """
    Sincroniza TAG <-> ETAPA para um lead sem quebrar o fluxo legado.

    preferencia:
      - "tag_to_crm": prioriza tag de etapa_funil para atualizar etapa_id
      - "crm_to_tag": prioriza etapa_id para atualizar tag de etapa_funil
      - "auto": tenta tag->crm e, se não houver tag de etapa, tenta crm->tag
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        return {"etapa_funil": None, "etapa_crm": None}

    deve_commit = session is None
    if session is None:
        async with AsyncSessionLocal() as internal_session:
            result = await sync_tag_etapa_lead(
                lead_id=str(lead_uuid),
                session=internal_session,
                preferencia=preferencia,
            )
            if result.get("_changed") == "1":
                await internal_session.commit()
            return {k: v for k, v in result.items() if k != "_changed"}

    result_lead = await session.execute(select(CRMLead).where(CRMLead.id == lead_uuid))
    lead = result_lead.scalars().first()
    if not lead:
        return {"etapa_funil": None, "etapa_crm": None}

    result_tags = await session.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id))
    tags_empresa = result_tags.scalars().all()
    tags_por_id = {str(tag.id): tag for tag in tags_empresa}
    tags_por_nome = {str(tag.nome or "").strip().lower(): tag for tag in tags_empresa if str(tag.nome or "").strip()}

    tags_resolvidas = _resolver_tags_oficiais_do_lead(lead.tags if isinstance(lead.tags, list) else [], tags_por_id, tags_por_nome)
    tags_etapa = [tag for tag in tags_resolvidas if _normalizar_tipo_tag(getattr(tag, "tipo", None)) == "etapa_funil"]

    changed = False
    etapa_funil_atual: str | None = None
    etapa_crm_atual: str | None = None
    preferencia_norm = str(preferencia or "auto").strip().lower()

    if preferencia_norm in {"tag_to_crm", "auto"} and tags_etapa:
        tag_etapa_escolhida = tags_etapa[0]
        etapa_funil_atual = str(tag_etapa_escolhida.nome or "").strip() or None

        tags_finais_ids: list[str] = []
        etapa_mantida = False
        for tag in tags_resolvidas:
            tag_tipo = _normalizar_tipo_tag(getattr(tag, "tipo", None))
            if tag_tipo == "etapa_funil":
                if etapa_mantida or str(tag.id) != str(tag_etapa_escolhida.id):
                    continue
                etapa_mantida = True
            tags_finais_ids.append(str(tag.id))

        tags_atuais_ids = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
        if tags_finais_ids != tags_atuais_ids:
            lead.tags = tags_finais_ids
            flag_modified(lead, "tags")
            changed = True

        result_etapa = await session.execute(
            select(CRMEtapa)
            .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
            .where(
                CRMFunil.empresa_id == lead.empresa_id,
                CRMEtapa.nome.ilike(str(tag_etapa_escolhida.nome or "").strip()),
            )
            .order_by(CRMEtapa.ordem.asc())
        )
        etapa = result_etapa.scalars().first()
        if etapa and lead.etapa_id != etapa.id:
            lead.etapa_id = etapa.id
            changed = True
            etapa_crm_atual = str(etapa.nome or "").strip() or None
        elif etapa:
            etapa_crm_atual = str(etapa.nome or "").strip() or None

    if preferencia_norm in {"crm_to_tag", "auto"} and not tags_etapa and lead.etapa_id:
        result_etapa_atual = await session.execute(
            select(CRMEtapa)
            .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
            .where(
                CRMEtapa.id == lead.etapa_id,
                CRMFunil.empresa_id == lead.empresa_id,
            )
        )
        etapa_atual = result_etapa_atual.scalars().first()
        if etapa_atual:
            etapa_crm_atual = str(etapa_atual.nome or "").strip() or None
            result_tag_etapa = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == lead.empresa_id,
                    TagCRM.tipo == "etapa_funil",
                    TagCRM.nome.ilike(str(etapa_atual.nome or "").strip()),
                )
            )
            tag_etapa = result_tag_etapa.scalars().first()
            if tag_etapa:
                tags_ids_finais = []
                for tag in tags_resolvidas:
                    if _normalizar_tipo_tag(getattr(tag, "tipo", None)) == "etapa_funil":
                        continue
                    tags_ids_finais.append(str(tag.id))
                tags_ids_finais.append(str(tag_etapa.id))

                tags_atuais_ids = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
                if tags_ids_finais != tags_atuais_ids:
                    lead.tags = tags_ids_finais
                    flag_modified(lead, "tags")
                    changed = True
                etapa_funil_atual = str(tag_etapa.nome or "").strip() or None

    if deve_commit and changed:
        await session.commit()

    if etapa_funil_atual is None:
        tags_resolvidas_atuais = _resolver_tags_oficiais_do_lead(
            lead.tags if isinstance(lead.tags, list) else [],
            tags_por_id,
            tags_por_nome,
        )
        for tag in tags_resolvidas_atuais:
            if _normalizar_tipo_tag(getattr(tag, "tipo", None)) == "etapa_funil":
                etapa_funil_atual = str(tag.nome or "").strip() or None
                break

    if etapa_crm_atual is None and lead.etapa_id:
        result_etapa_nome = await session.execute(
            select(CRMEtapa.nome)
            .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
            .where(CRMEtapa.id == lead.etapa_id, CRMFunil.empresa_id == lead.empresa_id)
        )
        etapa_crm_atual = result_etapa_nome.scalars().first()
        etapa_crm_atual = str(etapa_crm_atual or "").strip() or None

    return {
        "etapa_funil": etapa_funil_atual,
        "etapa_crm": etapa_crm_atual,
        "_changed": "1" if changed else "0",
    }


async def get_etapa_funil_atual(
    lead_id: str,
    empresa_id: str | None = None,
    session: AsyncSession | None = None,
) -> str | None:
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        return None

    if session is None:
        async with AsyncSessionLocal() as internal_session:
            return await get_etapa_funil_atual(
                lead_id=str(lead_uuid),
                empresa_id=empresa_id,
                session=internal_session,
            )

    result_lead = await session.execute(select(CRMLead).where(CRMLead.id == lead_uuid))
    lead = result_lead.scalars().first()
    if not lead:
        return None
    if empresa_id and str(lead.empresa_id) != str(empresa_id):
        return None

    result_tags = await session.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id))
    tags_empresa = result_tags.scalars().all()
    tags_por_id = {str(tag.id): tag for tag in tags_empresa}
    tags_por_nome = {str(tag.nome or "").strip().lower(): tag for tag in tags_empresa if str(tag.nome or "").strip()}
    tags_resolvidas = _resolver_tags_oficiais_do_lead(lead.tags if isinstance(lead.tags, list) else [], tags_por_id, tags_por_nome)

    for tag in tags_resolvidas:
        if _normalizar_tipo_tag(getattr(tag, "tipo", None)) == "etapa_funil":
            return str(tag.nome or "").strip() or None
    return None
