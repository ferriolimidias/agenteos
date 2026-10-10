"""Idempotent, provenance-preserving backfill into the attribution fact ledger."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.attribution_ledger import (
    campaign_reply_idempotency_key,
    campaign_sent_idempotency_key,
    clean_referral,
    ctwa_idempotency_key,
    deterministic_idempotency_key,
    latest_eligible_touch,
    lead_identity,
    payment_idempotency_key,
    record_touch,
)
from db.database import AsyncSessionLocal
from db.models import (
    AttributionTouch,
    CampaignRecipient,
    ChannelConversation,
    CRMLead,
    LeoFTIntegrationEvent,
    LeoFTPaymentEvent,
    LeoFTSaleFulfillment,
    MensagemHistorico,
)


def _parse_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback or datetime.now(timezone.utc)


def _stable_legacy_key(empresa_id: Any, lead_id: Any, touch: dict[str, str]) -> str:
    identity = touch.get("ctwa_clid") or touch.get("source_id") or touch.get("captured_at") or "unknown"
    return deterministic_idempotency_key("legacy-lead-attribution:v1", empresa_id, lead_id, identity)


async def _collect_candidates(session: AsyncSession) -> tuple[list[dict[str, Any]], int]:
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_lead_ctwa: dict[tuple[str, str], set[str]] = {}
    unresolved = 0

    # Message metadata is the highest-fidelity recoverable source: it retains
    # tenant, connection, conversation, message, provider and the original referral.
    messages = (await session.execute(select(
        MensagemHistorico.id, MensagemHistorico.empresa_id, MensagemHistorico.channel,
        MensagemHistorico.provider, MensagemHistorico.conexao_id, MensagemHistorico.channel_conversation_id,
        MensagemHistorico.lead_id, MensagemHistorico.external_message_id, MensagemHistorico.criado_em,
        MensagemHistorico.metadata_json,
    ).where(
        MensagemHistorico.direction == "inbound",
        MensagemHistorico.metadata_json.is_not(None),
    ))).all()
    recipients = (await session.execute(select(
        CampaignRecipient.id, CampaignRecipient.empresa_id, CampaignRecipient.provider,
        CampaignRecipient.channel, CampaignRecipient.sent_at, CampaignRecipient.external_message_id,
        CampaignRecipient.contact_id, CampaignRecipient.conversation_id, CampaignRecipient.lead_id,
        CampaignRecipient.conexao_id, CampaignRecipient.campanha_id,
    ).where(
        CampaignRecipient.status.in_(("sent", "delivered", "read")),
        CampaignRecipient.sent_at.is_not(None),
        CampaignRecipient.external_message_id.is_not(None),
    ))).all()
    messages = [SimpleNamespace(**dict(row._mapping)) for row in messages]
    recipients = [SimpleNamespace(**dict(row._mapping)) for row in recipients]
    recipient_by_external: dict[tuple[str, str, str], CampaignRecipient] = {}
    conversation_contacts = {
        str(conversation_id): contact_id
        for conversation_id, contact_id in (await session.execute(
            select(ChannelConversation.id, ChannelConversation.contact_id)
        )).all()
    }
    for recipient in recipients:
        recipient_by_external[(str(recipient.empresa_id), str(recipient.provider), str(recipient.external_message_id))] = recipient

    for message in messages:
        message_meta = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        referral = clean_referral(message_meta.get("referral"))
        external_id = str(message.external_message_id or message.id)
        if referral:
            key = ctwa_idempotency_key(empresa_id=message.empresa_id, provider=message.provider or "meta_whatsapp", external_message_id=external_id)
            candidate = {
                "empresa_id": message.empresa_id, "provider": message.provider or "meta_whatsapp",
                "channel": message.channel or "whatsapp", "connection_id": message.conexao_id,
                "contact_id": conversation_contacts.get(str(message.channel_conversation_id)) if message.channel_conversation_id else None,
                "conversation_id": message.channel_conversation_id,
                "lead_id": message.lead_id, "message_id": message.id,
                "external_event_id": external_id, "event_kind": "ctwa_referral", "touch_type": "paid_social",
                "touch_source": "backfill_message_metadata", "referral": referral,
                "occurred_at": message.criado_em or datetime.now(timezone.utc), "idempotency_key": key,
                "metadata_json": {"provenance": "mensagens_historico.metadata_json.referral"},
            }
            candidates.append(candidate)
            seen_keys.add(key)
            if referral.get("ctwa_clid") and message.lead_id:
                seen_lead_ctwa.setdefault((str(message.empresa_id), str(message.lead_id)), set()).add(referral["ctwa_clid"])
            if not referral.get("source_id") or referral.get("source_type", "").lower() != "ad":
                unresolved += 1

        # Backfill campaign replies only when the provider's quoted-message
        # context identifies an actual campaign message.
        context = message_meta.get("context") if isinstance(message_meta.get("context"), dict) else {}
        quoted_id = str(context.get("id") or "").strip()
        recipient = recipient_by_external.get((str(message.empresa_id), str(message.provider or ""), quoted_id)) if quoted_id else None
        if recipient is not None:
            reply_key = campaign_reply_idempotency_key(
                empresa_id=message.empresa_id, recipient_id=recipient.id,
                inbound_message_id=external_id,
            )
            if reply_key not in seen_keys:
                candidates.append({
                    "empresa_id": message.empresa_id, "provider": message.provider or "unknown",
                    "channel": message.channel or "whatsapp", "connection_id": message.conexao_id,
            "contact_id": recipient.contact_id or (conversation_contacts.get(str(message.channel_conversation_id)) if message.channel_conversation_id else None), "conversation_id": message.channel_conversation_id,
                    "lead_id": message.lead_id, "message_id": message.id,
                    "external_event_id": external_id, "event_kind": "campaign_reply", "touch_type": "campaign",
                    "touch_source": "backfill_campaign_reply_context", "occurred_at": message.criado_em or datetime.now(timezone.utc),
                    "idempotency_key": reply_key, "outbound_campaign_id": recipient.campanha_id,
                    "campaign_recipient_id": recipient.id,
                    "metadata_json": {"provenance": "provider_context_message_id"},
                })
                seen_keys.add(reply_key)

    # Legacy lead JSON holds first/last projections but not every historical
    # message. Preserve distinct CTWA IDs and never rewrite the original JSON.
    leads = (await session.execute(select(CRMLead.id, CRMLead.empresa_id, CRMLead.dados_adicionais))).all()
    for lead_id, empresa_id, extra in leads:
        extra = extra if isinstance(extra, dict) else {}
        attribution = extra.get("meta_attribution") if isinstance(extra.get("meta_attribution"), dict) else {}
        for slot in ("first_touch", "last_touch"):
            raw = attribution.get(slot)
            referral = clean_referral(raw)
            if not referral:
                continue
            if referral.get("ctwa_clid") in seen_lead_ctwa.get((str(empresa_id), str(lead_id)), set()):
                continue
            key = _stable_legacy_key(empresa_id, lead_id, referral)
            if key in seen_keys:
                continue
            occurred_at = _parse_datetime((raw or {}).get("captured_at") or (raw or {}).get("last_seen_at"))
            candidates.append({
                "empresa_id": empresa_id, "provider": referral.get("provider") or "meta_whatsapp",
                "channel": referral.get("channel") or "whatsapp", "lead_id": lead_id,
                "external_event_id": None, "event_kind": "ctwa_referral", "touch_type": "paid_social",
                "touch_source": "backfill_crm_lead_json", "referral": referral,
                "occurred_at": occurred_at, "idempotency_key": key,
                "metadata_json": {"provenance": f"crm_leads.dados_adicionais.meta_attribution.{slot}"},
            })
            seen_keys.add(key)
            if not referral.get("source_id") or referral.get("source_type", "").lower() != "ad":
                unresolved += 1

    # Existing funnel events are persisted after provider checkout/payment
    # confirmation. Only provider-confirmed approvals become revenue facts.
    funnel_events = (await session.execute(select(
        LeoFTIntegrationEvent.id, LeoFTIntegrationEvent.request_payload_sanitized,
        LeoFTIntegrationEvent.integration, LeoFTIntegrationEvent.event_type, LeoFTIntegrationEvent.processed_at,
        LeoFTIntegrationEvent.created_at,
    ).where(
        LeoFTIntegrationEvent.integration.in_(("leoft_funnel", "mercado_pago_leoft_payment")),
    ))).all()
    funnel_events = [SimpleNamespace(**dict(row._mapping)) for row in funnel_events]
    for event in funnel_events:
        payload = event.request_payload_sanitized if isinstance(event.request_payload_sanitized, dict) else {}
        empresa_id = payload.get("empresa_id")
        lead_id = payload.get("lead_id")
        event_type = str(payload.get("event_type") or event.event_type or "")
        integration = str(event.integration or "")
        source = str(payload.get("source") or "historical_funnel")
        reference = str(payload.get("checkout_reference") or "") or None
        payment_id = str(payload.get("payment_id") or "") or None
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        occurred_at = event.processed_at or event.created_at or datetime.now(timezone.utc)
        if not empresa_id or not lead_id:
            continue
        if event_type == "checkout_created" and reference:
            provider = str(meta.get("provider") or "mercado_pago")
            key = deterministic_idempotency_key("checkout:v1", empresa_id, provider, reference, "started")
            candidates.append({
                "empresa_id": empresa_id, "lead_id": lead_id, "provider": provider, "channel": "whatsapp",
                "event_kind": "checkout_started", "touch_type": "conversion", "touch_source": f"backfill_{source}",
                "occurred_at": occurred_at, "external_event_id": reference, "checkout_id": reference,
                "idempotency_key": key, "metadata_json": {"provenance": "leoft_funnel.checkout_created"},
            })
            seen_keys.add(key)
        elif (
            (event_type == "payment_confirmed" and str(meta.get("status") or "approved").lower() == "approved")
            or (integration == "mercado_pago_leoft_payment" and event_type == "leoft.payment_approved" and str(payload.get("payment_status") or "").lower() == "approved")
        ) and payment_id:
            provider = str(meta.get("provider") or "mercado_pago")
            key = payment_idempotency_key(empresa_id=empresa_id, provider=provider, payment_id=payment_id, status="approved")
            occurred_at = _parse_datetime(payload.get("approved_at"), occurred_at)
            candidates.append({
                "empresa_id": empresa_id, "lead_id": lead_id, "provider": provider, "channel": "whatsapp",
                "event_kind": "payment_approved", "touch_type": "conversion", "touch_source": f"backfill_{source}",
                "occurred_at": occurred_at, "external_event_id": payment_id, "checkout_id": reference,
                "payment_id": payment_id, "amount": meta.get("amount", payload.get("amount")), "currency": meta.get("currency", payload.get("currency")),
                "idempotency_key": key, "metadata_json": {"provenance": "leoft_funnel.provider_payment_confirmed"},
            })
        elif (event_type == "payment_refunded" or (
            integration == "mercado_pago_leoft_payment" and event_type in {"leoft.payment_status", "leoft.payment_refunded"} and str(payload.get("payment_status") or "").lower() == "refunded"
        )) and payment_id:
            provider = str(meta.get("provider") or "mercado_pago")
            key = payment_idempotency_key(empresa_id=empresa_id, provider=provider, payment_id=payment_id, status="refunded")
            refund_amount = meta.get("amount", payload.get("amount"))
            try:
                refund_amount = -abs(float(refund_amount)) if refund_amount is not None else None
            except (TypeError, ValueError):
                refund_amount = None
            candidates.append({
                "empresa_id": empresa_id, "lead_id": lead_id, "provider": provider, "channel": "whatsapp",
                "event_kind": "refund", "touch_type": "conversion", "touch_source": f"backfill_{source}",
                "occurred_at": occurred_at, "external_event_id": payment_id, "checkout_id": reference,
                "payment_id": payment_id, "amount": refund_amount, "currency": meta.get("currency"),
                "idempotency_key": key, "metadata_json": {"provenance": "leoft_funnel.provider_payment_refund"},
            })

    # Successful recipient rows are the durable source for historical campaign_sent.
    for recipient in recipients:
        external_id = str(recipient.external_message_id or "")
        key = campaign_sent_idempotency_key(empresa_id=recipient.empresa_id, recipient_id=recipient.id, external_message_id=external_id)
        candidates.append({
            "empresa_id": recipient.empresa_id, "provider": recipient.provider, "channel": recipient.channel,
            "connection_id": recipient.conexao_id, "contact_id": recipient.contact_id,
            "conversation_id": recipient.conversation_id, "lead_id": recipient.lead_id,
            "event_kind": "campaign_sent", "touch_type": "campaign", "touch_source": "backfill_campaign_recipient",
            "occurred_at": recipient.sent_at, "external_event_id": external_id,
            "outbound_campaign_id": recipient.campanha_id, "campaign_recipient_id": recipient.id,
            "idempotency_key": key, "metadata_json": {"provenance": "campaign_recipients.sent_at"},
        })
        seen_keys.add(key)

    # Sale fulfillment is a provider-approved local payment record. It fills
    # any missing funnel event but converges on the same payment idempotency key.
    sales = (await session.execute(select(
        LeoFTSaleFulfillment.company_id, LeoFTSaleFulfillment.payment_provider,
        LeoFTSaleFulfillment.payment_id, LeoFTSaleFulfillment.payment_status,
        LeoFTSaleFulfillment.lead_id, LeoFTSaleFulfillment.contact_id,
        LeoFTSaleFulfillment.order_id, LeoFTSaleFulfillment.paid_at,
        LeoFTSaleFulfillment.occurred_at, LeoFTSaleFulfillment.amount,
        LeoFTSaleFulfillment.currency,
    ).where(
        LeoFTSaleFulfillment.payment_status == "confirmed",
        LeoFTSaleFulfillment.payment_id.is_not(None),
    ))).all()
    sales = [SimpleNamespace(**dict(row._mapping)) for row in sales]

    confirmed_events = (await session.execute(select(
        LeoFTPaymentEvent.company_id, LeoFTPaymentEvent.student_id,
        LeoFTPaymentEvent.contact_id, LeoFTPaymentEvent.payment_id,
        LeoFTPaymentEvent.provider, LeoFTPaymentEvent.payment_status,
        LeoFTPaymentEvent.environment, LeoFTPaymentEvent.amount,
        LeoFTPaymentEvent.currency, LeoFTPaymentEvent.order_id,
        LeoFTPaymentEvent.occurred_at,
    ).where(
        LeoFTPaymentEvent.payment_status == "confirmed",
        LeoFTPaymentEvent.environment == "production",
    ))).all()
    confirmed_events = [SimpleNamespace(**dict(row._mapping)) for row in confirmed_events]
    for payment in confirmed_events:
        lead_id = payment.student_id or payment.contact_id
        provider = str(payment.provider or "mercado_pago")
        key = payment_idempotency_key(
            empresa_id=payment.company_id, provider=provider,
            payment_id=payment.payment_id, status="approved",
        )
        candidates.append({
            "empresa_id": payment.company_id, "lead_id": lead_id,
            "provider": provider, "channel": "whatsapp", "event_kind": "payment_approved",
            "touch_type": "conversion", "touch_source": "backfill_canonical_payment_event",
            "occurred_at": payment.occurred_at, "checkout_id": payment.order_id,
            "payment_id": payment.payment_id, "external_event_id": payment.payment_id,
            "amount": payment.amount, "currency": payment.currency,
            "idempotency_key": key,
            "metadata_json": {"provenance": "leoft_payment_events.confirmed.production"},
        })
    for sale in sales:
        key = payment_idempotency_key(
            empresa_id=sale.company_id, provider=sale.payment_provider,
            payment_id=sale.payment_id, status="approved",
        )
        candidates.append({
            "empresa_id": sale.company_id, "lead_id": sale.lead_id, "contact_id": sale.contact_id,
            "provider": sale.payment_provider, "channel": "whatsapp", "event_kind": "payment_approved",
            "touch_type": "conversion", "touch_source": "backfill_sale_fulfillment",
            "occurred_at": sale.paid_at or sale.occurred_at, "checkout_id": sale.order_id,
            "payment_id": sale.payment_id, "amount": sale.amount, "currency": sale.currency,
            "idempotency_key": key, "metadata_json": {"provenance": "leoft_sale_fulfillments.confirmed"},
        })

    # One idempotency key may have multiple safe sources (funnel + sale).
    unique: list[dict[str, Any]] = []
    unique_keys: set[str] = set()
    for item in candidates:
        key = item["idempotency_key"]
        if key in unique_keys:
            continue
        unique_keys.add(key)
        unique.append(item)
    unique.sort(key=lambda item: _parse_datetime(item.get("occurred_at")))
    return unique, unresolved


async def backfill_attribution(session: AsyncSession, *, dry_run: bool = True) -> dict[str, int | bool]:
    candidates, unresolved = await _collect_candidates(session)
    existing = set((await session.execute(select(AttributionTouch.idempotency_key).distinct())).scalars().all())
    would_insert = sum(1 for item in candidates if item["idempotency_key"] not in existing)
    skipped = len(candidates) - would_insert
    if dry_run:
        return {"dry_run": True, "rows": len(candidates), "would_insert": would_insert, "inserted": 0, "skipped_duplicate": skipped, "unresolved": unresolved}

    inserted = 0
    executed_skips = 0
    for index, item in enumerate(candidates, start=1):
        lead_id = item.get("lead_id")
        if lead_id and item["event_kind"] == "refund" and item.get("payment_id"):
            approved_payment = await session.scalar(select(AttributionTouch).where(
                AttributionTouch.empresa_id == item["empresa_id"],
                AttributionTouch.provider == item.get("provider"),
                AttributionTouch.payment_id == item["payment_id"],
                AttributionTouch.event_kind == "payment_approved",
            ).order_by(AttributionTouch.occurred_at.asc()).limit(1))
            item["attribution_touch_id"] = approved_payment.attribution_touch_id if approved_payment else None
        elif lead_id and item["event_kind"] in {"checkout_started", "payment_approved"}:
            contact_id = item.get("contact_id")
            conversation_id = item.get("conversation_id")
            if not item.get("connection_id") or (not contact_id and not conversation_id):
                resolved_contact, resolved_conversation, resolved_connection = await lead_identity(session, empresa_id=item["empresa_id"], lead_id=lead_id)
                item["contact_id"] = contact_id or resolved_contact
                item["conversation_id"] = conversation_id or resolved_conversation
                item["connection_id"] = item.get("connection_id") or resolved_connection
            active = await latest_eligible_touch(
                session, empresa_id=item["empresa_id"], lead_id=lead_id,
                contact_id=contact_id, conversation_id=conversation_id,
                at=item.get("occurred_at"),
            )
            if active:
                item["attribution_touch_id"] = active.id
        _, was_inserted = await record_touch(session, **item)
        inserted += int(was_inserted)
        executed_skips += int(not was_inserted)
        if index % 200 == 0:
            await session.commit()
    await session.commit()
    return {"dry_run": False, "rows": len(candidates), "would_insert": would_insert, "inserted": inserted, "skipped_duplicate": executed_skips, "unresolved": unresolved}


async def reconcile_legacy_attribution(session: AsyncSession) -> dict[str, int]:
    leads = (await session.execute(select(CRMLead.id, CRMLead.empresa_id, CRMLead.dados_adicionais))).all()
    facts = (await session.execute(select(
        AttributionTouch.empresa_id, AttributionTouch.lead_id,
        AttributionTouch.ctwa_clid, AttributionTouch.source_id, AttributionTouch.source_type,
    ).where(AttributionTouch.event_kind == "ctwa_referral"))).all()
    ledger: dict[tuple[str, str], list[tuple[str | None, str | None, str | None]]] = {}
    for empresa_id, lead_id, ctwa, source_id, source_type in facts:
        if lead_id:
            ledger.setdefault((str(empresa_id), str(lead_id)), []).append((ctwa, source_id, source_type))
    counts = {"match": 0, "partial": 0, "conflict": 0, "missing": 0}
    for lead_id, empresa_id, extra in leads:
        extra = extra if isinstance(extra, dict) else {}
        attribution = extra.get("meta_attribution") if isinstance(extra.get("meta_attribution"), dict) else {}
        expected = []
        for slot in ("first_touch", "last_touch"):
            clean = clean_referral(attribution.get(slot))
            if clean and clean not in expected:
                expected.append(clean)
        if not expected:
            continue
        actual = ledger.get((str(empresa_id), str(lead_id)), [])
        matched = []
        conflict = False
        for legacy in expected:
            candidates = [row for row in actual if (
                legacy.get("ctwa_clid") and row[0] == legacy.get("ctwa_clid")
            ) or (
                not legacy.get("ctwa_clid") and legacy.get("source_id") and row[1] == legacy.get("source_id")
            )]
            if not candidates:
                continue
            matched.append(legacy)
            for row in candidates:
                if legacy.get("source_id") and row[1] and legacy["source_id"] != row[1]:
                    conflict = True
                if legacy.get("source_type") and row[2] and legacy["source_type"] != row[2]:
                    conflict = True
        if conflict:
            counts["conflict"] += 1
        elif not matched:
            counts["missing"] += 1
        elif len(matched) < len(expected):
            counts["partial"] += 1
        else:
            counts["match"] += 1
    return counts


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Attribution ledger safe backfill")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="SELECT-only count and duplicate preview")
    mode.add_argument("--apply", action="store_true", help="Insert idempotent attribution facts")
    mode.add_argument("--reconcile", action="store_true", help="SELECT-only compare legacy JSON to ledger")
    args = parser.parse_args()
    async with AsyncSessionLocal() as session:
        if args.reconcile:
            result = {"legacy_reconciliation": await reconcile_legacy_attribution(session)}
        else:
            result = {"backfill": await backfill_attribution(session, dry_run=not args.apply)}
            if args.apply:
                result["legacy_reconciliation"] = await reconcile_legacy_attribution(session)
    print("ATTRIBUTION_BACKFILL", result)


if __name__ == "__main__":
    asyncio.run(_main())
