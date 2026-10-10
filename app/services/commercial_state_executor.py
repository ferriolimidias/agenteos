from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.agents.schemas import CommercialStateDecision
from app.services.meta.business_messaging_conversion_engine import BusinessMessagingConversionError, stable_business_fact_id
from app.services.tag_crm_service import aplicar_tags_oficiais
from app.services.monitor_funnel import managed_tag_for_fact
from db.database import AsyncSessionLocal
from db.models import CRMFunil, CRMEtapa, CRMLead, Empresa, TagCRM


COMMERCIAL_SIGNALS = {"LeadSubmitted", "QualifiedLead", "InitiateCheckout", "OrderCreated", "Purchase"}

MONITOR_FACT_EVENT_MAP = {
    "content_engaged": "ViewContent",
    "lead_established": "LeadSubmitted",
    "qualification_confirmed": "QualifiedLead",
    "selection_confirmed": "AddToCart",
}


async def execute_conversion_monitor_facts(
    session: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any,
    facts: list[dict[str, Any]],
    source_event_id: str,
) -> dict[str, Any]:
    """Persist validated Monitor facts and dispatch only eligible Meta mappings."""

    tenant_id = uuid.UUID(str(empresa_id))
    current_lead_id = uuid.UUID(str(lead_id))
    lead = (
        await session.execute(
            select(CRMLead).where(CRMLead.id == current_lead_id, CRMLead.empresa_id == tenant_id)
        )
    ).scalars().first()
    if not lead:
        raise CommercialStateExecutionError("tenant_lead_not_found")

    stored = list((lead.dados_adicionais or {}).get("commercial_facts") or [])
    stored_by_id = {
        str(item.get("fact_id")): dict(item)
        for item in stored
        if isinstance(item, dict) and item.get("fact_id")
    }
    results: list[dict[str, Any]] = []
    for fact in facts or []:
        if not isinstance(fact, dict) or not bool(fact.get("occurred")):
            continue
        fact_type = str(fact.get("fact_type") or "").strip().lower()
        evidence = [str(item).strip() for item in fact.get("evidence_message_ids") or [] if str(item).strip()]
        if fact_type not in {
            "content_engaged", "lead_established", "qualification_confirmed", "selection_confirmed",
            "checkout_intent", "order_commitment", "abandonment", "purchase_confirmed",
        } or not evidence:
            continue
        try:
            confidence = max(0.0, min(1.0, float(fact.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.7:
            continue
        anchor = evidence[0]
        fact_id = stable_business_fact_id(
            empresa_id=tenant_id,
            lead_id=current_lead_id,
            fact_type=fact_type,
            anchor=anchor,
        )
        already_stored = fact_id in stored_by_id
        if already_stored:
            record = stored_by_id[fact_id]
        else:
            record = {
                "fact_id": fact_id, "fact_type": fact_type, "occurred": True,
                "confidence": confidence, "occurred_at": str(fact.get("occurred_at") or "").strip() or None,
                "evidence_message_ids": evidence[:5], "reason": str(fact.get("reason") or "").strip()[:300],
                "source": "conversion_monitor",
            }
            stored_by_id[fact_id] = record
        tag = await managed_tag_for_fact(session, tenant_id, fact_type)
        if not tag or fact_type in {"checkout_intent", "order_commitment", "abandonment", "purchase_confirmed"}:
            record["meta_status"] = "internal_only"
            results.append({"fact_type": fact_type, "fact_id": fact_id, "status": "deduped" if already_stored else "internal_only"})
            continue
        occurred_at = datetime.now(timezone.utc)
        raw_at = str(record.get("occurred_at") or "").strip()
        if raw_at:
            try:
                occurred_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
            except ValueError:
                record["occurred_at"] = None
        tag_result = await aplicar_tags_oficiais(
            session, lead=lead, tag_values=[str(tag.id)], source="monitor",
            source_reference_id=str(source_event_id or anchor), replace=False,
            trigger_conversion=not already_stored,
        )
        conversion = (tag_result.get("conversion_results") or [{}])[0]
        record["tag_id"] = str(tag.id)
        record["meta_status"] = "deduped" if already_stored else str(conversion.get("status") or "unknown")
        results.append({"fact_type": fact_type, "fact_id": fact_id, "status": "deduped" if already_stored else "tag_applied", **conversion})

    dados = dict(lead.dados_adicionais or {})
    dados["commercial_facts"] = list(stored_by_id.values())[-100:]
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    await session.commit()
    return {"status": "processed", "facts": results}


async def execute_conversion_monitor_facts_for_lead(
    *,
    empresa_id: Any,
    lead_id: Any,
    facts: list[dict[str, Any]],
    source_event_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await execute_conversion_monitor_facts(
            session,
            empresa_id=empresa_id,
            lead_id=lead_id,
            facts=facts,
            source_event_id=source_event_id,
        )


class CommercialStateExecutionError(ValueError):
    pass


async def execute_commercial_state(
    session: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any,
    decision: CommercialStateDecision,
    source: str = "ai",
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """Validate and persist one tenant-scoped commercial state decision."""
    try:
        tenant_id = uuid.UUID(str(empresa_id))
        current_lead_id = uuid.UUID(str(lead_id))
    except (TypeError, ValueError) as exc:
        raise CommercialStateExecutionError("tenant_or_lead_invalid") from exc

    empresa = (await session.execute(select(Empresa).where(Empresa.id == tenant_id))).scalars().first()
    lead = (
        await session.execute(
            select(CRMLead).where(CRMLead.id == current_lead_id, CRMLead.empresa_id == tenant_id)
        )
    ).scalars().first()
    if not empresa or not lead:
        raise CommercialStateExecutionError("tenant_lead_not_found")
    if str(getattr(lead, "status_atendimento", "") or "").lower() in {"manual", "aguardando_humano", "handoff"}:
        return {"status": "blocked", "reason": "manual_handoff_active"}

    requested_apply = _uuid_list(decision.tags_to_apply)
    requested_remove = _uuid_list(decision.tags_to_remove)
    all_tag_ids = list(dict.fromkeys(requested_apply + requested_remove))
    tags_by_id: dict[str, TagCRM] = {}
    if all_tag_ids:
        result = await session.execute(
            select(TagCRM).where(TagCRM.empresa_id == tenant_id, TagCRM.id.in_(all_tag_ids))
        )
        tags_by_id = {str(tag.id): tag for tag in result.scalars().all()}
    if set(all_tag_ids) != set(tags_by_id):
        raise CommercialStateExecutionError("tag_not_owned_by_tenant")
    if source == "ai" and any(not bool(getattr(tags_by_id[tag_id], "ai_can_apply", False)) for tag_id in requested_apply):
        raise CommercialStateExecutionError("ai_tag_permission_denied")

    current_tags = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
    final_tags = [tag_id for tag_id in current_tags if tag_id not in set(requested_remove)]
    for tag_id in requested_apply:
        if tag_id not in final_tags:
            final_tags.append(tag_id)
    changed_tag_state = final_tags != current_tags
    if changed_tag_state:
        tag_result = await aplicar_tags_oficiais(
            session,
            lead=lead,
            tag_values=final_tags,
            source=source,
            source_reference_id=source_event_id or "commercial_state",
            replace=True,
        )
    else:
        tag_result = {"added_tag_ids": [], "current_tag_ids": current_tags}

    old_stage_id = str(lead.etapa_id) if lead.etapa_id else None
    new_stage_id = _clean_uuid(decision.target_stage_id)
    if new_stage_id:
        stage_row = (
            await session.execute(
                select(CRMEtapa)
                .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
                .where(CRMEtapa.id == uuid.UUID(new_stage_id), CRMFunil.empresa_id == tenant_id)
            )
        ).scalars().first()
        if not stage_row:
            raise CommercialStateExecutionError("stage_not_owned_by_tenant")
        lead.etapa_id = stage_row.id
    stage_changed = bool(new_stage_id and old_stage_id != new_stage_id)

    operational_evidence = dict(decision.operational_evidence or {})
    effective_signals = []
    for signal in decision.signals:
        signal_name = str(signal or "").strip()
        if signal_name == "InitiateCheckout" and not any(
            operational_evidence.get(key) for key in ("checkout_started", "form_completed", "anamnese_completed")
        ):
            continue
        if signal_name == "Purchase" and not operational_evidence.get("payment_confirmed"):
            continue
        effective_signals.append(signal_name)
    state_payload = decision.to_dict()
    state_payload["signals"] = effective_signals
    if "InitiateCheckout" in decision.signals and "InitiateCheckout" not in effective_signals:
        state_payload["reason_codes"] = list(state_payload.get("reason_codes") or []) + ["checkout_operational_evidence_missing"]
    if "Purchase" in decision.signals and "Purchase" not in effective_signals:
        state_payload["reason_codes"] = list(state_payload.get("reason_codes") or []) + ["payment_operational_evidence_missing"]
    state_payload.update(
        {
            "source": source,
            "source_event_id": source_event_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    dados = dict(lead.dados_adicionais or {})
    dados["commercial_state"] = state_payload
    history = list(dados.get("commercial_state_history") or [])
    history.append(state_payload)
    dados["commercial_state_history"] = history[-20:]
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    await session.flush()

    delivery_results: list[dict[str, Any]] = []
    for tag_id in tag_result.get("added_tag_ids", []):
        delivery_results.append(
            await _dispatch_conversion_trigger_safely(
                session,
                BusinessMessagingTrigger(
                    empresa_id=tenant_id,
                    lead_id=current_lead_id,
                    trigger_type="tag_applied",
                    trigger_ref=str(tag_id),
                    source_event_id=source_event_id or f"tag:{current_lead_id}:{tag_id}",
                    trigger_source=source,
                ),
            )
        )
    if stage_changed:
        delivery_results.append(
            await _dispatch_conversion_trigger_safely(
                session,
                BusinessMessagingTrigger(
                    empresa_id=tenant_id,
                    lead_id=current_lead_id,
                    trigger_type="crm_stage_changed",
                    trigger_ref=new_stage_id,
                    source_event_id=source_event_id or f"stage:{current_lead_id}:{new_stage_id}",
                    trigger_source=source,
                ),
            )
        )
    for signal in effective_signals:
        signal_name = str(signal or "").strip()
        if signal_name not in COMMERCIAL_SIGNALS:
            continue
        delivery_results.append(
            await _dispatch_conversion_trigger_safely(
                session,
                BusinessMessagingTrigger(
                    empresa_id=tenant_id,
                    lead_id=current_lead_id,
                    trigger_type="system_event",
                    trigger_ref=signal_name,
                    source_event_id=source_event_id or f"signal:{current_lead_id}:{signal_name}",
                    trigger_source=source,
                    fact_id=(
                        stable_business_fact_id(
                            empresa_id=tenant_id,
                            lead_id=current_lead_id,
                            fact_type={
                                "LeadSubmitted": "lead_established",
                                "QualifiedLead": "qualification_confirmed",
                            }.get(signal_name, signal_name.lower()),
                            anchor=str(source_event_id or f"signal:{current_lead_id}:{signal_name}"),
                        )
                        if signal_name in {"LeadSubmitted", "QualifiedLead"}
                        else None
                    ),
                ),
            )
        )
    await session.commit()
    await session.refresh(lead)
    return {
        "status": "applied" if changed_tag_state or stage_changed or effective_signals else "no_change",
        "added_tag_ids": list(tag_result.get("added_tag_ids", [])),
        "stage_changed": stage_changed,
        "stage_id": new_stage_id,
        "signals": [str(item) for item in effective_signals if str(item) in COMMERCIAL_SIGNALS],
        "meta_delivery": delivery_results,
    }


async def execute_commercial_state_for_lead(
    *,
    empresa_id: Any,
    lead_id: Any,
    decision: CommercialStateDecision,
    source: str = "ai",
    source_event_id: str | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await execute_commercial_state(
            session,
            empresa_id=empresa_id,
            lead_id=lead_id,
            decision=decision,
            source=source,
            source_event_id=source_event_id,
        )


def decision_from_plan(plan: dict[str, Any] | None) -> CommercialStateDecision:
    state = (plan or {}).get("commercial_state") or {}
    return CommercialStateDecision(
        tags_to_apply=_uuid_list(state.get("tags_to_apply")),
        tags_to_remove=_uuid_list(state.get("tags_to_remove")),
        target_stage_id=_clean_uuid(state.get("target_stage_id")),
        signals=[str(item).strip() for item in state.get("signals") or [] if str(item).strip()],
        reason_codes=[str(item).strip() for item in state.get("reason_codes") or [] if str(item).strip()],
        confidence=float(state.get("confidence") or 0.0),
        operational_evidence={
            str(key): bool(value)
            for key, value in (state.get("operational_evidence") or {}).items()
            if str(key) in {"checkout_started", "form_completed", "anamnese_completed", "order_created", "payment_confirmed"}
        },
    )


async def _dispatch_conversion_trigger_safely(
    session: AsyncSession,
    trigger: BusinessMessagingTrigger,
) -> dict[str, Any]:
    """Keep CRM state durable when a tenant mapping needs configuration review."""
    try:
        return await process_business_messaging_conversion_trigger(session, trigger)
    except BusinessMessagingConversionError as exc:
        return {
            "status": "blocked",
            "reason": "conversion_mapping_invalid",
            "detail": str(exc),
            "trigger_type": trigger.trigger_type,
            "trigger_ref": trigger.trigger_ref,
        }


def _clean_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _uuid_list(values: Any) -> list[str]:
    output: list[str] = []
    for value in values or []:
        cleaned = _clean_uuid(value)
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output
