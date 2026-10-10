from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.services.meta_attribution import apply_meta_attribution
from app.services.meta.business_messaging_conversion_engine import (
    BusinessMessagingTrigger,
    process_business_messaging_conversion_trigger,
    stable_business_messaging_event_id,
)
from app.services.meta.business_messaging_conversions import (
    _debug_token,
    _scope_set,
    _validate_waba_dataset_edge,
    resolve_meta_conversion_credential,
)
from db.database import AsyncSessionLocal
from db.models import BusinessMessagingConversionMapping, CRMLead, Empresa, MensagemHistorico, MetaConversionEvent


EMPRESA_ID = uuid.UUID("a7384cb4-98b2-4750-94bc-3b329d35156d")
DATE_START = datetime(2026, 8, 19)
DATE_END = datetime(2026, 8, 21)
LEADSUBMITTED_REF = "ctwa_lead_created"
CHECKOUT_REF = "checkout_created"
PURCHASE_REF = "mercado_pago_approved"
TAG_LEADSUBMITTED_REF = "16a2fce2-901a-437a-88bf-7eab5c9b8d0d"
TAG_PURCHASE_REF = "42a59458-9b84-42a7-8f13-a03223f811b5"
STAGE_CHECKOUT_REF = "26195a40-4aa2-4dc1-b4b1-6ad1260aef65"


def mask(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return f"{raw[:4]}...{raw[-4:]}" if len(raw) > 8 else f"{raw[:2]}...{raw[-2:]}"


def epoch(value: datetime | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def candidates(session, *, require_missing_attribution: bool = True) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(MensagemHistorico)
            .where(
                MensagemHistorico.empresa_id == EMPRESA_ID,
                MensagemHistorico.from_me.is_(False),
                MensagemHistorico.provider == "meta_whatsapp",
                MensagemHistorico.criado_em >= DATE_START,
                MensagemHistorico.criado_em < DATE_END,
            )
            .order_by(MensagemHistorico.lead_id.asc(), MensagemHistorico.criado_em.asc())
        )
    ).scalars().all()
    first_by_lead: dict[str, MensagemHistorico] = {}
    for msg in rows:
        referral = (msg.metadata_json or {}).get("referral") if isinstance(msg.metadata_json, dict) else None
        if not isinstance(referral, dict):
            continue
        if referral.get("source_type") != "ad" or not referral.get("ctwa_clid"):
            continue
        first_by_lead.setdefault(str(msg.lead_id), msg)
    output = []
    for msg in sorted(first_by_lead.values(), key=lambda item: item.criado_em):
        lead = await session.get(CRMLead, msg.lead_id)
        if not lead or str(lead.empresa_id) != str(EMPRESA_ID):
            continue
        dados = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
        current = dados.get("meta_attribution") if isinstance(dados.get("meta_attribution"), dict) else None
        if require_missing_attribution and current and ((current.get("last_touch") or {}).get("ctwa_clid") or (current.get("first_touch") or {}).get("ctwa_clid")):
            continue
        referral = dict((msg.metadata_json or {}).get("referral") or {})
        output.append(
            {
                "lead": lead,
                "message": msg,
                "referral": referral,
                "proposed_masked": mask(referral.get("ctwa_clid")),
            }
        )
    return output


async def validate_meta(session) -> dict[str, Any]:
    credential = await resolve_meta_conversion_credential(session, EMPRESA_ID, require_dataset=True)
    debug = await _debug_token(credential.access_token)
    scopes = sorted(_scope_set(debug))
    valid_edge, status, body = await _validate_waba_dataset_edge(credential.access_token, credential.waba_id, credential.dataset_id)
    return {
        "dataset_id_masked": mask(credential.dataset_id),
        "waba_id_masked": mask(credential.waba_id),
        "graph_version": credential.graph_version,
        "token_valid": bool((debug.get("data") or {}).get("is_valid", True)) if debug.get("available") else None,
        "scopes": scopes,
        "manage_events": "whatsapp_business_manage_events" in scopes,
        "dataset_waba_association": valid_edge,
        "dataset_status": status,
        "dataset_found": valid_edge and bool(body.get("data")),
    }


async def configure_live(session) -> dict[str, Any]:
    async def upsert(trigger_type: str, trigger_ref: str, event_name: str, allowed_sources: list[str]) -> str:
        row = (
            await session.execute(
                select(BusinessMessagingConversionMapping).where(
                    BusinessMessagingConversionMapping.empresa_id == EMPRESA_ID,
                    BusinessMessagingConversionMapping.trigger_type == trigger_type,
                    BusinessMessagingConversionMapping.trigger_ref == trigger_ref,
                )
            )
        ).scalars().first()
        if not row:
            row = BusinessMessagingConversionMapping(
                empresa_id=EMPRESA_ID,
                trigger_type=trigger_type,
                trigger_ref=trigger_ref,
                event_name=event_name,
            )
            session.add(row)
            await session.flush()
        row.event_name = event_name
        row.ativo = True
        row.send_mode = "live"
        row.metadata_json = {**(row.metadata_json or {}), "allowed_sources": allowed_sources, "canonical": True}
        flag_modified(row, "metadata_json")
        return str(row.id)

    ids = {
        "LeadSubmitted": await upsert("system_event", LEADSUBMITTED_REF, "LeadSubmitted", ["system", "integration"]),
        "QualifiedLead": await upsert("crm_stage_changed", "20d923ae-61f7-46bc-8038-74bd579cc40c", "QualifiedLead", ["ai", "manual", "automation", "system"]),
        "InitiateCheckout": await upsert("system_event", CHECKOUT_REF, "InitiateCheckout", ["system", "integration"]),
        "Purchase": await upsert("payment_confirmed", PURCHASE_REF, "Purchase", ["system", "integration"]),
    }
    disabled = []
    for trigger_type, trigger_ref in [("tag_applied", TAG_LEADSUBMITTED_REF), ("crm_stage_changed", STAGE_CHECKOUT_REF), ("tag_applied", TAG_PURCHASE_REF)]:
        row = (
            await session.execute(
                select(BusinessMessagingConversionMapping).where(
                    BusinessMessagingConversionMapping.empresa_id == EMPRESA_ID,
                    BusinessMessagingConversionMapping.trigger_type == trigger_type,
                    BusinessMessagingConversionMapping.trigger_ref == trigger_ref,
                )
            )
        ).scalars().first()
        if row:
            row.ativo = False
            row.send_mode = "disabled"
            disabled.append(str(row.id))
    empresa = await session.get(Empresa, EMPRESA_ID)
    before = bool(empresa.meta_capi_ativo)
    empresa.meta_capi_ativo = True
    await session.commit()
    return {"meta_capi_active_before": before, "meta_capi_active_after": True, "live_mapping_ids": ids, "disabled_mapping_ids": disabled}


async def apply_backfill(session, *, dry_run: bool) -> dict[str, Any]:
    items = await candidates(session, require_missing_attribution=True)
    details = []
    for item in items:
        lead: CRMLead = item["lead"]
        msg: MensagemHistorico = item["message"]
        proposed = deepcopy(lead.dados_adicionais or {})
        clone = CRMLead(id=lead.id, empresa_id=lead.empresa_id, dados_adicionais=proposed)
        apply_meta_attribution(
            clone,
            item["referral"],
            captured_at=msg.criado_em.isoformat(),
            provider=msg.provider or "meta_whatsapp",
            channel=msg.channel or "whatsapp",
        )
        details.append(
            {
                "lead_id": str(lead.id),
                "conversation_id": str(msg.channel_conversation_id),
                "first_message_id": str(msg.id),
                "received_at": msg.criado_em.isoformat(),
                "source_type": item["referral"].get("source_type"),
                "source_id_present": bool(item["referral"].get("source_id")),
                "ctwa_clid_present": bool(item["referral"].get("ctwa_clid")),
                "ctwa_masked": item["proposed_masked"],
                "current_meta_attribution": bool((lead.dados_adicionais or {}).get("meta_attribution") if isinstance(lead.dados_adicionais, dict) else False),
                "proposed_meta_attribution": True,
            }
        )
        if not dry_run:
            lead.dados_adicionais = clone.dados_adicionais
            flag_modified(lead, "dados_adicionais")
    if not dry_run:
        await session.commit()
    return {"count": len(details), "items": details, "dry_run": dry_run}


def checkout_events_for_lead(lead: CRMLead) -> list[dict[str, Any]]:
    dados = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
    anamneses = dados.get("anamneses") if isinstance(dados.get("anamneses"), dict) else {}
    events = []
    for record in anamneses.values():
        if not isinstance(record, dict):
            continue
        checkout = record.get("checkout") if isinstance(record.get("checkout"), dict) else {}
        reference = str(checkout.get("reference") or "").strip()
        if not reference or not checkout.get("url"):
            continue
        ts = record.get("submitted_at") or ((record.get("metadata") or {}).get("submitted_at_server") if isinstance(record.get("metadata"), dict) else None)
        events.append({"reference": reference, "event_time": epoch(ts), "has_url": True})
    return events


async def send_historical(session, *, dry_run: bool = False) -> dict[str, Any]:
    items = await candidates(session, require_missing_attribution=False)
    sent = []
    skipped = []
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    for item in items:
        lead: CRMLead = item["lead"]
        msg: MensagemHistorico = item["message"]
        lead_event_time = epoch(msg.criado_em)
        if not lead_event_time or now_epoch - lead_event_time > 7 * 24 * 3600:
            skipped.append({"lead_id": str(lead.id), "event_name": "LeadSubmitted", "reason": "event_time_too_old"})
        else:
            result = {"status": "dry_run"}
            if not dry_run:
                result = await process_business_messaging_conversion_trigger(
                    session,
                    BusinessMessagingTrigger(
                        empresa_id=EMPRESA_ID,
                        lead_id=lead.id,
                        trigger_type="system_event",
                        trigger_ref=LEADSUBMITTED_REF,
                        source_event_id=f"ctwa_lead_created:{msg.id}",
                        trigger_source="system",
                        event_time=lead_event_time,
                    ),
                )
            sent.append({"lead_id": str(lead.id), "event_name": "LeadSubmitted", "event_time": lead_event_time, "result": result})

        for checkout in checkout_events_for_lead(lead):
            event_time = checkout.get("event_time")
            if not event_time or now_epoch - event_time > 7 * 24 * 3600:
                skipped.append({"lead_id": str(lead.id), "event_name": "InitiateCheckout", "reason": "event_time_missing_or_too_old"})
                continue
            result = {"status": "dry_run"}
            if not dry_run:
                result = await process_business_messaging_conversion_trigger(
                    session,
                    BusinessMessagingTrigger(
                        empresa_id=EMPRESA_ID,
                        lead_id=lead.id,
                        trigger_type="system_event",
                        trigger_ref=CHECKOUT_REF,
                        source_event_id=f"checkout_created:{checkout['reference']}",
                        trigger_source="system",
                        event_time=event_time,
                    ),
                )
            sent.append({"lead_id": str(lead.id), "event_name": "InitiateCheckout", "event_time": event_time, "result": result})
    return {"attempted": sent, "skipped": skipped, "dry_run": dry_run}


async def already_sent_count(session) -> int:
    rows = (
        await session.execute(
            select(MetaConversionEvent).where(
                MetaConversionEvent.empresa_id == EMPRESA_ID,
                MetaConversionEvent.status == "sent",
                MetaConversionEvent.event_name.in_(["LeadSubmitted", "QualifiedLead", "InitiateCheckout", "Purchase"]),
            )
        )
    ).scalars().all()
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["dry-run", "apply-backfill", "activate-live", "send-historical-dry-run", "send-historical-live", "status"])
    args = parser.parse_args()
    async with AsyncSessionLocal() as session:
        out: dict[str, Any] = {"mode": args.mode, "empresa_id": str(EMPRESA_ID)}
        out["meta"] = await validate_meta(session)
        if args.mode == "dry-run":
            out["backfill"] = await apply_backfill(session, dry_run=True)
            out["historical"] = await send_historical(session, dry_run=True)
        elif args.mode == "apply-backfill":
            out["backfill"] = await apply_backfill(session, dry_run=False)
        elif args.mode == "activate-live":
            out["activation"] = await configure_live(session)
        elif args.mode == "send-historical-dry-run":
            out["historical"] = await send_historical(session, dry_run=True)
        elif args.mode == "send-historical-live":
            out["already_sent_before"] = await already_sent_count(session)
            out["historical"] = await send_historical(session, dry_run=False)
            out["already_sent_after"] = await already_sent_count(session)
        elif args.mode == "status":
            out["backfill"] = await apply_backfill(session, dry_run=True)
            out["already_sent"] = await already_sent_count(session)
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
