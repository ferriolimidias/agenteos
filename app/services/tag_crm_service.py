import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from db.database import AsyncSessionLocal
from db.models import BusinessMessagingConversionMapping, CRMLead, CRMTagEvent, CRMEtapa, CRMFunil, Empresa, MetaConversionEvent, TagCRM
from app.services.followup_engine import registrar_followup_tag_event


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


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def aplicar_tags_oficiais(
    session: AsyncSession,
    *,
    lead: CRMLead,
    tag_values: list[str],
    source: str,
    actor_user_id: uuid.UUID | None = None,
    source_reference_id: str | None = None,
    replace: bool = False,
    trigger_conversion: bool = True,
) -> dict[str, list[str]]:
    """Aplica tags, registra o evento lógico e mantém a operação idempotente."""
    source_norm = str(source or "unknown").strip().lower()
    allowed_sources = {"manual", "ai", "monitor", "funnel", "automation", "followup", "campaign", "csv_import", "integration", "system", "unknown"}
    if source_norm not in allowed_sources:
        source_norm = "unknown"
    # Existing production CHECK constraint predates the Monitor source. Keep
    # the canonical trigger source in metadata while using the compatible
    # persisted audit value until a planned schema migration is approved.
    persisted_source = "ai" if source_norm == "monitor" else source_norm
    requested_ids: list[uuid.UUID] = []
    for raw in tag_values or []:
        try:
            requested_ids.append(uuid.UUID(str(raw).strip()))
        except (ValueError, TypeError):
            continue
    requested_ids = list(dict.fromkeys(requested_ids))
    result = await session.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id, TagCRM.id.in_(requested_ids))) if requested_ids else None
    valid_ids = {str(tag.id) for tag in result.scalars().all()} if result is not None else set()
    requested_ordered = [str(tag_id) for tag_id in requested_ids if str(tag_id) in valid_ids]
    current_ids = normalizar_tags(lead.tags if isinstance(lead.tags, list) else [])
    current_set = set(current_ids)
    final_ids = requested_ordered if replace else current_ids + [tag_id for tag_id in requested_ordered if tag_id not in current_set]
    final_ids = normalizar_tags(final_ids)
    added_ids = [tag_id for tag_id in final_ids if tag_id not in current_set]
    removed_ids = [tag_id for tag_id in current_ids if tag_id not in set(final_ids)] if replace else []
    if final_ids != current_ids:
        lead.tags = final_ids
        flag_modified(lead, "tags")
    for tag_id in added_ids:
        event_key = f"tag-applied:{lead.empresa_id}:{lead.id}:{tag_id}"
        exists = await session.execute(select(CRMTagEvent.id).where(CRMTagEvent.empresa_id == lead.empresa_id, CRMTagEvent.idempotency_key == event_key))
        if exists.scalar_one_or_none() is None:
            session.add(CRMTagEvent(
                empresa_id=lead.empresa_id, lead_id=lead.id, tag_id=uuid.UUID(tag_id), action="applied",
                source=persisted_source, source_reference_id=source_reference_id, actor_user_id=actor_user_id,
                idempotency_key=event_key, metadata_json={"tag_application_source": source_norm},
            ))
    if removed_ids:
        result_removed = await session.execute(select(TagCRM.id).where(TagCRM.empresa_id == lead.empresa_id, TagCRM.id.in_([uuid.UUID(tag_id) for tag_id in removed_ids if _is_uuid(tag_id)])))
        valid_removed_ids = {str(tag_id) for tag_id in result_removed.scalars().all()}
        for tag_id in valid_removed_ids:
            event_key = f"tag-removed:{lead.empresa_id}:{lead.id}:{tag_id}"
            exists = await session.execute(select(CRMTagEvent.id).where(CRMTagEvent.empresa_id == lead.empresa_id, CRMTagEvent.idempotency_key == event_key))
            if exists.scalar_one_or_none() is None:
                session.add(CRMTagEvent(
                    empresa_id=lead.empresa_id, lead_id=lead.id, tag_id=uuid.UUID(tag_id), action="removed",
                    source=persisted_source, source_reference_id=source_reference_id, actor_user_id=actor_user_id,
                    idempotency_key=event_key, metadata_json={"tag_application_source": source_norm},
                ))
    await sync_tag_etapa_lead(str(lead.id), session=session, preferencia="tag_to_crm")
    await session.flush()
    canonical_events_by_tag: dict[str, uuid.UUID] = {}
    if added_ids:
        from app.services.monitor_funnel import parse_managed_instruction
        from app.services.commercial_events import TAG_EVENT_NAMES, record_managed_tag_commercial_event

        tag_rows = (await session.execute(select(TagCRM).where(
            TagCRM.empresa_id == lead.empresa_id,
            TagCRM.id.in_([uuid.UUID(item) for item in added_ids]),
        ))).scalars().all()
        tag_events = (await session.execute(select(CRMTagEvent).where(
            CRMTagEvent.empresa_id == lead.empresa_id,
            CRMTagEvent.lead_id == lead.id,
            CRMTagEvent.tag_id.in_([uuid.UUID(item) for item in added_ids]),
            CRMTagEvent.action == "applied",
        ))).scalars().all()
        event_by_tag = {str(item.tag_id): item for item in tag_events}
        mappings = (await session.execute(select(BusinessMessagingConversionMapping).where(
            BusinessMessagingConversionMapping.empresa_id == lead.empresa_id,
            BusinessMessagingConversionMapping.trigger_type == "tag_applied",
            BusinessMessagingConversionMapping.trigger_ref.in_(added_ids),
            BusinessMessagingConversionMapping.ativo.is_(True),
        ))).scalars().all()
        event_by_mapping = {str(item.trigger_ref): str(item.event_name or "") for item in mappings}
        for tag in tag_rows:
            tag_event = event_by_tag.get(str(tag.id))
            if tag_event is None:
                continue
            instruction = parse_managed_instruction(tag.instrucao_ia) or {}
            event_name = str(instruction.get("meta_event_name") or event_by_mapping.get(str(tag.id)) or "")
            # Deterministic checkout/order/payment facts are emitted by their
            # verified integration paths, never by a tag or stage change.
            if event_name not in TAG_EVENT_NAMES:
                continue
            canonical_id, _inserted = await record_managed_tag_commercial_event(
                session, tag_event=tag_event, event_name=event_name,
                funnel_stage_id=str(instruction.get("funnel_stage_key") or "") or None,
                metadata_json={
                    "funnel_version": str(instruction.get("funnel_version") or "")[:80] or None,
                    "managed_by_monitor_funnel": bool(instruction.get("managed_by_monitor_funnel")),
                },
            )
            canonical_events_by_tag[str(tag.id)] = canonical_id
    conversion_results = []
    if added_ids and trigger_conversion:
        from app.services.monitor_funnel import parse_managed_instruction
        from app.services.meta.business_messaging_conversion_engine import BusinessMessagingTrigger, process_business_messaging_conversion_trigger
        tag_rows = (await session.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id, TagCRM.id.in_([uuid.UUID(i) for i in added_ids])))).scalars().all()
        for tag in tag_rows:
            meta = parse_managed_instruction(tag.instrucao_ia)
            if not meta or not meta.get("meta_event_name"):
                continue
            try:
                result = await process_business_messaging_conversion_trigger(session, BusinessMessagingTrigger(
                    empresa_id=lead.empresa_id, lead_id=lead.id, trigger_type="tag_applied",
                    trigger_ref=str(tag.id), source_event_id=str(source_reference_id or f"tag:{lead.id}:{tag.id}"),
                    trigger_source=source_norm, event_time=int(__import__("time").time()),
                    canonical_commercial_event_id=canonical_events_by_tag.get(str(tag.id)),
                ))
                legacy_event_id = str(result.get("event_id") or "").strip()
                commercial_event_id = canonical_events_by_tag.get(str(tag.id))
                if legacy_event_id and commercial_event_id:
                    legacy_event = await session.scalar(select(MetaConversionEvent).where(
                        MetaConversionEvent.empresa_id == lead.empresa_id,
                        MetaConversionEvent.event_id == legacy_event_id,
                    ))
                    if legacy_event is not None:
                        from app.services.commercial_events import link_legacy_meta_event_exact

                        await link_legacy_meta_event_exact(
                            session, empresa_id=lead.empresa_id, meta_event_id=legacy_event.id,
                            commercial_event_id=commercial_event_id,
                            match_reason="tag_event_trigger_identity",
                        )
                conversion_results.append({"tag_id": str(tag.id), **result})
            except Exception as exc:
                # Meta is a side effect: a provider error must not roll back CRM tagging.
                conversion_results.append({"tag_id": str(tag.id), "status": "failed", "reason": str(exc)[:160]})
    return {"current_tag_ids": [str(item) for item in (lead.tags or [])], "added_tag_ids": added_ids, "conversion_results": conversion_results}


async def publicar_tags_realtime(empresa_id: str | uuid.UUID, lead: CRMLead) -> None:
    """Publica a mesma mudança para clientes do Inbox, sem acoplar o banco ao WS."""
    from app.services.websocket_manager import manager

    await manager.broadcast_to_empresa(str(empresa_id), {
        "type": "inbox.lead.tags.updated",
        "tipo_evento": "lead_tags_updated",
        "empresa_id": str(empresa_id),
        "lead_id": str(lead.id),
        "tags": [str(item) for item in (lead.tags or [])],
    })


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
    tags_anteriores_ids = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
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

    tags_finais_ids = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
    novas_tags_ids = [tag_id for tag_id in tags_finais_ids if tag_id not in set(tags_anteriores_ids)]
    for tag_id in novas_tags_ids:
        try:
            await registrar_followup_tag_event(
                session,
                empresa_id=lead.empresa_id,
                lead=lead,
                tag_id=uuid.UUID(str(tag_id)),
                action="applied",
                source_event_id="sync_tag_etapa_lead",
            )
        except Exception:
            continue

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
