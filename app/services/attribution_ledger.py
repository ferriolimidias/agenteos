"""Canonical, tenant-scoped attribution facts and read projections.

Facts in ``attribution_touches`` are append-only. Meta/object resolution lives in
``attribution_touch_resolutions`` so future Graph reads never rewrite a raw fact.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AttributionTouch,
    AttributionTouchResolution,
    ChannelConversation,
    ChannelContact,
    CRMLead,
    TrafficMetaConfig,
)

logger = logging.getLogger(__name__)

ENTRY_TOUCH_EVENTS = frozenset({"ctwa_referral", "tracked_link_click", "manual_touch", "unknown_source"})
LAST_TOUCH_EVENTS = frozenset({"ctwa_referral", "tracked_link_click", "campaign_sent", "campaign_reply", "manual_touch"})
CONVERSION_EVENTS = frozenset({"lead_qualified", "checkout_started", "order_created", "payment_approved", "payment_failed", "refund"})
RESOLUTION_STATUSES = frozenset({"resolved", "partial", "unresolved", "not_applicable", "not_found", "forbidden", "error_retryable"})
_SENSITIVE_QUERY_KEYS = frozenset({"access_token", "token", "auth", "authorization", "key", "secret", "signature", "password"})


def deterministic_idempotency_key(*parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def ctwa_idempotency_key(*, empresa_id: Any, provider: str, external_message_id: str) -> str:
    return deterministic_idempotency_key("ctwa:v1", empresa_id, provider, external_message_id)


def payment_idempotency_key(*, empresa_id: Any, provider: str, payment_id: str, status: str) -> str:
    return deterministic_idempotency_key("payment:v1", empresa_id, provider, payment_id, status)


def campaign_sent_idempotency_key(*, empresa_id: Any, recipient_id: Any, external_message_id: str) -> str:
    return deterministic_idempotency_key("campaign-sent:v1", empresa_id, recipient_id, external_message_id)


def campaign_reply_idempotency_key(*, empresa_id: Any, recipient_id: Any, inbound_message_id: str) -> str:
    return deterministic_idempotency_key("campaign-reply:v1", empresa_id, recipient_id, inbound_message_id)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _clean_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = "".join(char for char in value.strip() if char >= " " or char in "\t\n\r")
    clean = re.sub(r"[\t\r\n ]+", " ", clean).strip()
    return clean[:limit] or None


def _safe_source_url(value: Any) -> str | None:
    raw = _clean_text(value, 4096)
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        safe_query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in _SENSITIVE_QUERY_KEYS and not any(secret in key.lower() for secret in ("token", "secret", "auth", "password"))]
        host = parts.hostname.lower()
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path[:1024], urlencode(safe_query)[:1024], ""))[:2048]
    except (TypeError, ValueError):
        return None


def clean_referral(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, str] = {}
    for field, limit in (
        ("ctwa_clid", 512), ("source_id", 160), ("source_type", 40),
        ("headline", 240), ("body", 600), ("media_type", 40),
    ):
        value_clean = _clean_text(value.get(field), limit)
        if value_clean:
            clean[field] = value_clean
    source_url = _safe_source_url(value.get("source_url"))
    if source_url:
        clean["source_url"] = source_url
    return clean


def _resolution_for_referral(referral: Mapping[str, str]) -> tuple[str, str | None]:
    source_id = referral.get("source_id")
    source_type = str(referral.get("source_type") or "").strip().lower()
    if not source_id:
        return ("unresolved", None) if referral.get("ctwa_clid") else ("not_applicable", None)
    if source_type == "ad":
        # This is a direct Meta referral claim, not an enriched adset/campaign join.
        return "partial", source_id
    return "unresolved", None


async def record_touch(
    session: AsyncSession,
    *,
    empresa_id: Any,
    event_kind: str,
    touch_type: str,
    touch_source: str,
    idempotency_key: str,
    occurred_at: datetime | None = None,
    provider: str = "unknown",
    channel: str = "unknown",
    connection_id: Any | None = None,
    contact_id: Any | None = None,
    conversation_id: Any | None = None,
    lead_id: Any | None = None,
    message_id: Any | None = None,
    external_event_id: str | None = None,
    referral: Mapping[str, Any] | None = None,
    outbound_campaign_id: Any | None = None,
    campaign_recipient_id: Any | None = None,
    checkout_id: str | None = None,
    payment_id: str | None = None,
    attribution_touch_id: Any | None = None,
    amount: Decimal | int | float | str | None = None,
    currency: str | None = None,
    metadata_json: Mapping[str, Any] | None = None,
) -> tuple[uuid.UUID, bool]:
    """Append a fact once; caller owns the transaction/commit boundary."""
    clean = clean_referral(referral)
    safe_metadata = {}
    if isinstance(metadata_json, Mapping):
        provenance = _clean_text(metadata_json.get("provenance"), 120)
        if provenance:
            safe_metadata["provenance"] = provenance
    resolution_status, ad_id = _resolution_for_referral(clean) if referral else ("not_applicable", None)
    amount_decimal = Decimal(str(amount)) if amount is not None else None
    normalized_currency = (str(currency or "").strip().upper()[:3] or None) if amount_decimal is not None else None
    values = {
        "id": uuid.uuid4(),
        "empresa_id": uuid.UUID(str(empresa_id)),
        "provider": _clean_text(provider, 80) or "unknown",
        "channel": _clean_text(channel, 40) or "unknown",
        "connection_id": uuid.UUID(str(connection_id)) if connection_id else None,
        "touch_type": _clean_text(touch_type, 60) or "unknown",
        "touch_source": _clean_text(touch_source, 100) or "unknown",
        "event_kind": _clean_text(event_kind, 60) or "unknown_source",
        "occurred_at": _utc(occurred_at),
        "contact_id": uuid.UUID(str(contact_id)) if contact_id else None,
        "conversation_id": uuid.UUID(str(conversation_id)) if conversation_id else None,
        "lead_id": uuid.UUID(str(lead_id)) if lead_id else None,
        "message_id": uuid.UUID(str(message_id)) if message_id else None,
        "external_event_id": _clean_text(external_event_id, 240),
        "ctwa_clid": clean.get("ctwa_clid"),
        "source_id": clean.get("source_id"),
        "source_url": clean.get("source_url"),
        "source_type": clean.get("source_type"),
        "headline": clean.get("headline"),
        "body": clean.get("body"),
        "media_type": clean.get("media_type"),
        "outbound_campaign_id": uuid.UUID(str(outbound_campaign_id)) if outbound_campaign_id else None,
        "campaign_recipient_id": uuid.UUID(str(campaign_recipient_id)) if campaign_recipient_id else None,
        "checkout_id": _clean_text(checkout_id, 240),
        "payment_id": _clean_text(payment_id, 240),
        "attribution_touch_id": uuid.UUID(str(attribution_touch_id)) if attribution_touch_id else None,
        "amount": amount_decimal,
        "currency": normalized_currency,
        "metadata_json": safe_metadata,
        "idempotency_key": str(idempotency_key)[:128],
    }
    insert = pg_insert(AttributionTouch).values(**values).on_conflict_do_nothing(
        constraint="uq_attribution_touches_idempotency"
    ).returning(AttributionTouch.id)
    inserted_id = await session.scalar(insert)
    if inserted_id is None:
        existing_id = await session.scalar(
            select(AttributionTouch.id).where(
                AttributionTouch.empresa_id == values["empresa_id"],
                AttributionTouch.idempotency_key == values["idempotency_key"],
            )
        )
        if existing_id is None:
            raise RuntimeError("attribution_touch_conflict_without_existing_row")
        return existing_id, False

    session.add(AttributionTouchResolution(
        id=uuid.uuid4(), empresa_id=values["empresa_id"], touch_id=inserted_id,
        resolution_status=resolution_status, ad_id=ad_id,
    ))
    return inserted_id, True


async def resolve_meta_ad_source(session: AsyncSession, *, empresa_id: Any, touch_id: Any) -> str:
    """Best-effort account-scoped Marketing Graph GET; raw attribution remains unchanged."""
    touch = await session.scalar(select(AttributionTouch).where(
        AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)),
        AttributionTouch.id == uuid.UUID(str(touch_id)),
    ))
    if touch is None or str(touch.source_type or "").lower() != "ad" or not touch.source_id:
        return "not_applicable"
    resolution = await session.scalar(select(AttributionTouchResolution).where(
        AttributionTouchResolution.empresa_id == touch.empresa_id,
        AttributionTouchResolution.touch_id == touch.id,
    ))
    if resolution is None:
        return "unresolved"
    config = await session.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == touch.empresa_id))
    if config is None or not config.ad_account_id:
        resolution.last_attempt_at = datetime.now(timezone.utc)
        resolution.error_code = "tenant_ad_account_unconfigured"
        await session.commit()
        return resolution.resolution_status
    try:
        from app.services.meta.marketing_credentials import MetaMarketingCapability, MetaMarketingCredentialResolver
        from app.services.meta.marketing_api import MetaMarketingApiClient, normalize_ad_account_id
        from app.services.meta.conversion_credentials import MetaCredentialResolutionError
        from app.services.meta.settings import meta_settings

        attempted_at = datetime.now(timezone.utc)
        resolution.last_attempt_at = attempted_at
        resolution.api_version = meta_settings().marketing_graph_api_version
        try:
            credential = await MetaMarketingCredentialResolver().resolve(
                session, touch.empresa_id, MetaMarketingCapability.ADS_READ,
                ad_account_id=config.ad_account_id, allow_admin_fallback=True,
            )
        except MetaCredentialResolutionError as exc:
            resolution.resolution_status = "forbidden" if "PERMISSION" in str(exc).upper() else "unresolved"
            resolution.error_code = str(exc)[:80]
            resolution.updated_at = attempted_at
            await session.commit()
            return resolution.resolution_status
        await session.commit()  # Release the read transaction before making network requests.
        client = MetaMarketingApiClient(
            credential.access_token, graph_version=credential.graph_version,
            timeout_seconds=4.0, max_attempts=1,
            read_only=bool(getattr(credential, "read_only", True)),
        )
        expected_account = normalize_ad_account_id(config.ad_account_id)
        ad = await client.get_scoped_ad(touch.source_id, expected_account)
        adset_id = _clean_text(ad.get("adset_id"), 160)
        campaign_id = _clean_text(ad.get("campaign_id"), 160)
        adset_name = campaign_name = None
        if adset_id:
            adset = await client.get_scoped_adset(adset_id, expected_account)
            adset_name = _clean_text(adset.get("name"), 240)
            campaign_id = _clean_text(adset.get("campaign_id"), 160) or campaign_id
        if campaign_id:
            campaign = await client.get_scoped_campaign(campaign_id, expected_account)
            campaign_name = _clean_text(campaign.get("name"), 240)
        creative = ad.get("creative") if isinstance(ad.get("creative"), Mapping) else {}
        creative_id = _clean_text(creative.get("id"), 160)
        if creative_id:
            try:
                scoped_creative = await client.get_scoped_creative(creative_id, expected_account)
                creative_id = _clean_text(scoped_creative.get("id"), 160)
            except Exception:
                # Creative is enrichment; the verified ad/adset/campaign facts
                # remain usable when this optional Graph edge is unavailable.
                creative_id = None
        resolution.ad_id = str(touch.source_id)
        resolution.adset_id = adset_id
        resolution.campaign_id = campaign_id
        resolution.creative_id = creative_id
        resolution.ad_name_snapshot = _clean_text(ad.get("name"), 240)
        resolution.adset_name_snapshot = adset_name
        resolution.campaign_name_snapshot = campaign_name
        resolution.resolution_status = "resolved" if adset_id and campaign_id else "partial"
        resolution.resolved_at = datetime.now(timezone.utc)
        resolution.error_code = None
        resolution.api_version = credential.graph_version
        resolution.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return resolution.resolution_status
    except Exception as exc:
        await session.rollback()
        resolution = await session.scalar(select(AttributionTouchResolution).where(
            AttributionTouchResolution.empresa_id == uuid.UUID(str(empresa_id)),
            AttributionTouchResolution.touch_id == uuid.UUID(str(touch_id)),
        ))
        if resolution is not None:
            code = str(getattr(exc, "code", "") or type(exc).__name__).lower()
            status_code = getattr(exc, "status_code", None)
            if status_code == 403 or code in {"missing_permission", "invalid_auth", "token_expired", "marketing_asset_out_of_scope"}:
                resolution.resolution_status = "forbidden"
            elif code in {"not_found", "unsupported_field"}:
                resolution.resolution_status = "not_found"
            else:
                resolution.resolution_status = "error_retryable"
            resolution.last_attempt_at = datetime.now(timezone.utc)
            resolution.error_code = re.sub(r"[^a-z0-9_.-]", "_", code)[:80]
            resolution.updated_at = datetime.now(timezone.utc)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
        logger.info("event=ATTRIBUTION_GRAPH_GET_SKIPPED tenant_scope=tenant exception_type=%s", type(exc).__name__)
        return resolution.resolution_status if resolution is not None else "error_retryable"


async def latest_eligible_touch(
    session: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any | None = None,
    contact_id: Any | None = None,
    conversation_id: Any | None = None,
    at: datetime | None = None,
) -> AttributionTouch | None:
    scopes = []
    if lead_id:
        scopes.append(AttributionTouch.lead_id == uuid.UUID(str(lead_id)))
    if contact_id:
        scopes.append(AttributionTouch.contact_id == uuid.UUID(str(contact_id)))
    if conversation_id:
        scopes.append(AttributionTouch.conversation_id == uuid.UUID(str(conversation_id)))
    if not scopes:
        return None
    statement = select(AttributionTouch).where(
        AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)),
        AttributionTouch.event_kind.in_(LAST_TOUCH_EVENTS),
        or_(*scopes),
    )
    if at:
        statement = statement.where(AttributionTouch.occurred_at <= _utc(at))
    return (await session.execute(statement.order_by(AttributionTouch.occurred_at.desc(), AttributionTouch.created_at.desc()).limit(1))).scalars().first()


async def lead_identity(session: AsyncSession, *, empresa_id: Any, lead_id: Any) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    row = (await session.execute(
        select(ChannelConversation.contact_id, ChannelConversation.id, ChannelConversation.conexao_id)
        .where(ChannelConversation.empresa_id == uuid.UUID(str(empresa_id)), ChannelConversation.lead_id == uuid.UUID(str(lead_id)))
        .order_by(ChannelConversation.last_message_at.desc().nullslast(), ChannelConversation.criado_em.desc())
        .limit(1)
    )).first()
    return (row[0], row[1], row[2]) if row else (None, None, None)


async def record_campaign_touch(
    session: AsyncSession,
    *,
    event_kind: str,
    empresa_id: Any,
    recipient_id: Any,
    campaign_id: Any,
    contact_id: Any | None,
    conversation_id: Any | None,
    lead_id: Any | None,
    connection_id: Any | None,
    provider: str,
    channel: str,
    external_message_id: str,
    occurred_at: datetime,
    message_id: Any | None = None,
) -> tuple[uuid.UUID, bool]:
    if event_kind == "campaign_sent":
        key = campaign_sent_idempotency_key(empresa_id=empresa_id, recipient_id=recipient_id, external_message_id=external_message_id)
    else:
        key = campaign_reply_idempotency_key(empresa_id=empresa_id, recipient_id=recipient_id, inbound_message_id=external_message_id)
    return await record_touch(
        session, empresa_id=empresa_id, event_kind=event_kind, touch_type="campaign", touch_source="campaign_central",
        idempotency_key=key, occurred_at=occurred_at, provider=provider, channel=channel,
        connection_id=connection_id, contact_id=contact_id, conversation_id=conversation_id, lead_id=lead_id,
        message_id=message_id, external_event_id=external_message_id, outbound_campaign_id=campaign_id,
        campaign_recipient_id=recipient_id,
        metadata_json={"provenance": "campaign_recipient_provider_event"},
    )


async def record_checkout_touch(
    session: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any,
    checkout_reference: str,
    provider: str,
    occurred_at: datetime,
    checkout_id: str | None = None,
) -> tuple[uuid.UUID, bool]:
    contact_id, conversation_id, connection_id = await lead_identity(session, empresa_id=empresa_id, lead_id=lead_id)
    active = await latest_eligible_touch(
        session, empresa_id=empresa_id, lead_id=lead_id, contact_id=contact_id,
        conversation_id=conversation_id, at=occurred_at,
    )
    key = deterministic_idempotency_key("checkout:v1", empresa_id, provider, checkout_reference, "started")
    touch_id, inserted = await record_touch(
        session, empresa_id=empresa_id, event_kind="checkout_started", touch_type="conversion",
        touch_source="checkout_service", idempotency_key=key, occurred_at=occurred_at,
        provider=provider, channel="whatsapp", connection_id=connection_id, contact_id=contact_id, conversation_id=conversation_id,
        lead_id=lead_id, external_event_id=checkout_reference, checkout_id=checkout_id or checkout_reference,
        attribution_touch_id=active.id if active else None,
        metadata_json={"provenance": "provider_checkout_reference"},
    )
    touch = await session.scalar(select(AttributionTouch).where(
        AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)), AttributionTouch.id == touch_id,
    ))
    if touch is not None:
        from app.services.commercial_events import record_attribution_conversion_event

        await record_attribution_conversion_event(session, touch=touch)
    return touch_id, inserted


async def record_payment_touch(
    session: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any,
    checkout_reference: str,
    payment_id: str,
    provider: str,
    status: str,
    occurred_at: datetime,
    amount: Any,
    currency: str,
) -> tuple[uuid.UUID, bool]:
    event_kind = "refund" if str(status).lower() in {"refunded", "charged_back"} else "payment_approved"
    contact_id, conversation_id, connection_id = await lead_identity(session, empresa_id=empresa_id, lead_id=lead_id)
    attribution_touch_id = None
    if event_kind == "refund":
        # Refunds reverse the attribution of the original approved payment;
        # later marketing touches must not move refunded revenue to another ad.
        approved_payment = await session.scalar(select(AttributionTouch).where(
            AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)),
            AttributionTouch.provider == provider,
            AttributionTouch.payment_id == payment_id,
            AttributionTouch.event_kind == "payment_approved",
        ).order_by(AttributionTouch.occurred_at.asc()).limit(1))
        if approved_payment is not None:
            attribution_touch_id = approved_payment.attribution_touch_id
    else:
        active = await latest_eligible_touch(
            session, empresa_id=empresa_id, lead_id=lead_id, contact_id=contact_id,
            conversation_id=conversation_id, at=occurred_at,
        )
        attribution_touch_id = active.id if active else None
    key = payment_idempotency_key(empresa_id=empresa_id, provider=provider, payment_id=payment_id, status=status)
    amount_value = Decimal(str(amount)) if amount is not None else None
    if event_kind == "refund" and amount_value is not None:
        amount_value = -abs(amount_value)
    touch_id, inserted = await record_touch(
        session, empresa_id=empresa_id, event_kind=event_kind, touch_type="conversion",
        touch_source="payment_webhook", idempotency_key=key, occurred_at=occurred_at,
        provider=provider, channel="whatsapp", connection_id=connection_id, contact_id=contact_id, conversation_id=conversation_id,
        lead_id=lead_id, external_event_id=payment_id, checkout_id=checkout_reference,
        payment_id=payment_id, attribution_touch_id=attribution_touch_id,
        amount=amount_value, currency=currency,
        metadata_json={"provenance": "provider_verified_payment_status"},
    )
    touch = await session.scalar(select(AttributionTouch).where(
        AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)), AttributionTouch.id == touch_id,
    ))
    if touch is not None:
        from app.services.commercial_events import record_attribution_conversion_event

        await record_attribution_conversion_event(session, touch=touch)
    return touch_id, inserted


def _metric(value: int | Decimal | None, *, zero_is_known: bool = False) -> dict[str, Any]:
    if value is None or (value == 0 and not zero_is_known):
        return {"value": None, "state": "unknown"}
    return {"value": float(value) if isinstance(value, Decimal) else value, "state": "observed"}


async def _touch_rows(session: AsyncSession, *, empresa_id: Any, lead_id: Any | None = None, conversation_id: Any | None = None) -> list[tuple[AttributionTouch, AttributionTouchResolution | None]]:
    filters = [AttributionTouch.empresa_id == uuid.UUID(str(empresa_id))]
    if lead_id is not None:
        filters.append(AttributionTouch.lead_id == uuid.UUID(str(lead_id)))
    if conversation_id is not None:
        filters.append(AttributionTouch.conversation_id == uuid.UUID(str(conversation_id)))
    rows = await session.execute(
        select(AttributionTouch, AttributionTouchResolution)
        .outerjoin(AttributionTouchResolution, and_(
            AttributionTouchResolution.empresa_id == AttributionTouch.empresa_id,
            AttributionTouchResolution.touch_id == AttributionTouch.id,
        ))
        .where(*filters)
        .order_by(AttributionTouch.occurred_at.asc(), AttributionTouch.created_at.asc())
    )
    return list(rows.all())


def _serialize_touch(touch: AttributionTouch, resolution: AttributionTouchResolution | None) -> dict[str, Any]:
    metadata = dict(touch.metadata_json or {})
    return {
        "id": str(touch.id), "event_kind": touch.event_kind, "touch_type": touch.touch_type,
        "touch_source": touch.touch_source, "provider": touch.provider, "channel": touch.channel,
        "occurred_at": _utc(touch.occurred_at).isoformat(), "received_at": _utc(touch.received_at).isoformat(),
        "contact_id": str(touch.contact_id) if touch.contact_id else None,
        "conversation_id": str(touch.conversation_id) if touch.conversation_id else None,
        "lead_id": str(touch.lead_id) if touch.lead_id else None,
        "message_id": str(touch.message_id) if touch.message_id else None,
        "ctwa_clid": _mask(touch.ctwa_clid), "source_id": touch.source_id,
        "source_url": touch.source_url, "source_type": touch.source_type,
        "headline": touch.headline, "body": touch.body, "media_type": touch.media_type,
        "ad_id": resolution.ad_id if resolution else None,
        "adset_id": resolution.adset_id if resolution else None,
        "campaign_id": resolution.campaign_id if resolution else None,
        "creative_id": resolution.creative_id if resolution else None,
        "resolution_status": resolution.resolution_status if resolution else ("unresolved" if touch.source_id else "not_applicable"),
        "ad_name_snapshot": resolution.ad_name_snapshot if resolution else None,
        "adset_name_snapshot": resolution.adset_name_snapshot if resolution else None,
        "campaign_name_snapshot": resolution.campaign_name_snapshot if resolution else None,
        "outbound_campaign_id": str(touch.outbound_campaign_id) if touch.outbound_campaign_id else None,
        "campaign_recipient_id": str(touch.campaign_recipient_id) if touch.campaign_recipient_id else None,
        "checkout_id": touch.checkout_id, "payment_id": touch.payment_id,
        "amount": float(touch.amount) if touch.amount is not None else None, "currency": touch.currency,
        "metadata": {"provenance": metadata["provenance"]} if "provenance" in metadata else {},
    }


def _mask(value: str | None) -> str | None:
    raw = str(value or "")
    if not raw:
        return None
    return raw if len(raw) <= 8 else f"{raw[:4]}…{raw[-4:]}"


async def get_attribution_for_lead(session: AsyncSession, *, empresa_id: Any, lead_id: Any) -> dict[str, Any] | None:
    lead_uuid = uuid.UUID(str(lead_id))
    exists = await session.scalar(select(CRMLead.id).where(CRMLead.empresa_id == uuid.UUID(str(empresa_id)), CRMLead.id == lead_uuid))
    if exists is None:
        return None
    rows = await _touch_rows(session, empresa_id=empresa_id, lead_id=lead_uuid)
    return _project_attribution_rows(rows)


async def get_first_touch(
    session: AsyncSession, *, empresa_id: Any, lead_id: Any | None = None, conversation_id: Any | None = None,
) -> dict[str, Any] | None:
    """Return the earliest known eligible acquisition fact in the requested scope."""
    rows = await _touch_rows(session, empresa_id=empresa_id, lead_id=lead_id, conversation_id=conversation_id)
    for touch, resolution in rows:
        if touch.event_kind in ENTRY_TOUCH_EVENTS:
            return _serialize_touch(touch, resolution)
    return None


async def get_last_touch(
    session: AsyncSession, *, empresa_id: Any, lead_id: Any | None = None, conversation_id: Any | None = None,
) -> dict[str, Any] | None:
    """Return the latest eligible marketing touch; organic inbound is not a touch."""
    rows = await _touch_rows(session, empresa_id=empresa_id, lead_id=lead_id, conversation_id=conversation_id)
    for touch, resolution in reversed(rows):
        if touch.event_kind in LAST_TOUCH_EVENTS:
            return _serialize_touch(touch, resolution)
    return None


def _project_attribution_rows(rows: list[tuple[AttributionTouch, AttributionTouchResolution | None]]) -> dict[str, Any]:
    if not rows:
        return {"first_touch": None, "last_touch": None, "assisted_touches": [], "conversion": [], "revenue": [], "resolution_status": "unknown"}
    serialized = [_serialize_touch(touch, resolution) for touch, resolution in rows]
    first_candidates = [(touch, resolution, data) for (touch, resolution), data in zip(rows, serialized) if touch.event_kind in ENTRY_TOUCH_EVENTS]
    last_candidates = [(touch, resolution, data) for (touch, resolution), data in zip(rows, serialized) if touch.event_kind in LAST_TOUCH_EVENTS]
    first = first_candidates[0] if first_candidates else None
    last = last_candidates[-1] if last_candidates else None
    conversion = [item for item in serialized if item["event_kind"] in CONVERSION_EVENTS]
    first_time = first[0].occurred_at if first else None
    conversion_times = [touch.occurred_at for touch, _ in rows if touch.event_kind in CONVERSION_EVENTS]
    conversion_end = min(conversion_times) if conversion_times else None
    assisted = [
        data for (touch, _resolution), data in zip(rows, serialized)
        if conversion_end is not None and touch.event_kind in LAST_TOUCH_EVENTS and first_time and touch.id != (first[0].id if first else None)
        and _utc(touch.occurred_at) >= _utc(first_time) and _utc(touch.occurred_at) <= _utc(conversion_end)
    ]
    revenues: dict[str, Decimal] = {}
    for touch, _ in rows:
        if touch.event_kind in {"payment_approved", "refund"} and touch.amount is not None and touch.currency:
            revenues[touch.currency] = revenues.get(touch.currency, Decimal("0")) + Decimal(str(touch.amount))
    resolution_status = "resolved" if last and last[2].get("resolution_status") == "resolved" else (
        last[2].get("resolution_status") if last else "unknown"
    )
    return {
        "first_touch": first[2] if first else None,
        "last_touch": last[2] if last else None,
        "assisted_touches": assisted,
        "conversion": conversion,
        "revenue": [{"currency": currency, "amount": float(total)} for currency, total in sorted(revenues.items())],
        "resolution_status": resolution_status,
    }


async def get_attribution_for_conversation(session: AsyncSession, *, empresa_id: Any, conversation_id: Any) -> dict[str, Any] | None:
    tenant_id = uuid.UUID(str(empresa_id))
    conversation = (await session.execute(select(ChannelConversation.id, ChannelConversation.lead_id).where(
        ChannelConversation.empresa_id == tenant_id, ChannelConversation.id == uuid.UUID(str(conversation_id)),
    ))).first()
    if conversation is None:
        return None
    rows = await _touch_rows(session, empresa_id=tenant_id, conversation_id=conversation.id)
    projected = _project_attribution_rows(rows)
    projected["conversation_id"] = str(conversation.id)
    projected["lead_id"] = str(conversation.lead_id) if conversation.lead_id else None
    projected["conversation_touches"] = [_serialize_touch(touch, resolution) for touch, resolution in rows]
    return projected


async def campaign_attribution_summary(session: AsyncSession, *, empresa_id: Any) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    rows = await _touch_rows(session, empresa_id=tenant_id)
    group_key_type = tuple[str | None, str | None, str | None, str | None, str | None]
    groups: dict[group_key_type, dict[str, Any]] = {}
    touch_by_id: dict[uuid.UUID, group_key_type] = {}

    def ensure_group(key: group_key_type, resolution: AttributionTouchResolution | None = None) -> dict[str, Any]:
        return groups.setdefault(key, {
            "campaign_id": key[0], "adset_id": key[1], "ad_id": key[2],
            "source_id": key[3], "outbound_campaign_id": key[4],
            "resolution_status": resolution.resolution_status if resolution else "unresolved",
            "campaign_name": resolution.campaign_name_snapshot if resolution else None,
            "adset_name": resolution.adset_name_snapshot if resolution else None,
            "ad_name": resolution.ad_name_snapshot if resolution else None,
            "touches": 0, "_conversations": set(), "_leads": set(),
            "_touches_with_conversation": 0, "_touches_with_lead": 0,
            "_checkouts": 0, "_purchases": 0, "_qualified": 0, "_revenue": {},
        })

    for touch, resolution in rows:
        if touch.event_kind not in LAST_TOUCH_EVENTS:
            continue
        group_key = (
            resolution.campaign_id if resolution else None,
            resolution.adset_id if resolution else None,
            resolution.ad_id if resolution else None,
            touch.source_id,
            str(touch.outbound_campaign_id) if touch.outbound_campaign_id else None,
        )
        touch_by_id[touch.id] = group_key
        group = ensure_group(group_key, resolution)
        group["touches"] += 1
        if touch.conversation_id:
            group["_conversations"].add(str(touch.conversation_id))
            group["_touches_with_conversation"] += 1
        if touch.lead_id:
            group["_leads"].add(str(touch.lead_id))
            group["_touches_with_lead"] += 1

    conversions = (await session.execute(select(AttributionTouch).where(
        AttributionTouch.empresa_id == tenant_id,
        AttributionTouch.event_kind.in_(CONVERSION_EVENTS),
    ))).scalars().all()
    for fact in conversions:
        group_key = touch_by_id.get(fact.attribution_touch_id) if fact.attribution_touch_id else None
        if group_key is None:
            group_key = (None, None, None, None, None)
        group = ensure_group(group_key)
        if fact.event_kind == "checkout_started":
            group["_checkouts"] += 1
        elif fact.event_kind == "payment_approved":
            group["_purchases"] += 1
            if fact.amount is not None and fact.currency:
                group["_revenue"][fact.currency] = group["_revenue"].get(fact.currency, Decimal("0")) + Decimal(str(fact.amount))
        elif fact.event_kind == "refund" and fact.amount is not None and fact.currency:
            group["_revenue"][fact.currency] = group["_revenue"].get(fact.currency, Decimal("0")) + Decimal(str(fact.amount))
        elif fact.event_kind == "lead_qualified":
            group["_qualified"] += 1

    for group in groups.values():
        conversations = group.pop("_conversations")
        leads = group.pop("_leads")
        touch_count = group["touches"]
        conversation_coverage = group.pop("_touches_with_conversation")
        lead_coverage = group.pop("_touches_with_lead")
        group["conversations"] = {
            "value": len(conversations) if conversation_coverage else None,
            "state": "unknown" if not conversation_coverage else ("observed" if conversation_coverage == touch_count else "partial"),
        }
        group["leads"] = {
            "value": len(leads) if lead_coverage else None,
            "state": "unknown" if not lead_coverage else ("observed" if lead_coverage == touch_count else "partial"),
        }
        group["qualified"] = _metric(group.pop("_qualified"))
        group["checkouts"] = _metric(group.pop("_checkouts"))
        group["purchases"] = _metric(group.pop("_purchases"))
        revenue = group.pop("_revenue")
        group["revenue"] = [{"currency": code, **_metric(amount, zero_is_known=True)} for code, amount in sorted(revenue.items())] if revenue else [{"currency": None, **_metric(None)}]

    return {"items": list(groups.values()), "measurability": {
        "qualified": "unknown_without_qualification_facts",
        "checkouts": "observed_ledger_facts_only",
        "purchases": "observed_provider_approved_payment_facts_only",
        "revenue": "approved_payment_amounts_minus_recorded_refunds",
    }}
