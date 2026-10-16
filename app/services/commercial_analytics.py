"""Tenant-scoped, evidence-based commercial analytics projections."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
import uuid

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.commercial_events import meta_signal_status
from db.models import (
    AttributionTouch, AttributionTouchResolution, CanonicalCommercialEvent,
    ChannelConversation, CommercialMetaEventLink, MetaAdsInsightDaily, MetaConversionEvent,
)

FUNNEL_EVENTS = ("ViewContent", "LeadSubmitted", "QualifiedLead", "AddToCart", "InitiateCheckout", "Purchase")
EVENT_LABELS = {
    "ViewContent": "view_content", "LeadSubmitted": "leads", "QualifiedLead": "qualified",
    "AddToCart": "add_to_cart", "InitiateCheckout": "checkouts", "Purchase": "purchases",
}
ZERO = Decimal(0)


def _d(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except Exception:
        return None


def _metric(value: Decimal | int | None, *, state: str | None = None, currency: str | None = None) -> dict[str, Any]:
    if value is None:
        return {"value": None, "state": state or "unknown", **({"currency": currency} if currency else {})}
    return {"value": float(value) if isinstance(value, Decimal) else value, "state": state or "known", **({"currency": currency} if currency else {})}


def _ratio(numerator: Decimal | int | None, denominator: Decimal | int | None, *, multiplier: Decimal = Decimal(1), currency: str | None = None, result_state: str | None = None) -> dict[str, Any]:
    if numerator is None or denominator is None:
        return _metric(None, currency=currency)
    den = Decimal(str(denominator))
    if den == 0:
        return _metric(None, state="not_applicable", currency=currency)
    value = Decimal(str(numerator)) / den * multiplier
    return _metric(value, state=result_state, currency=currency)


def _utc_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _utc_end_exclusive(value: date) -> datetime:
    return datetime.combine(value + timedelta(days=1), time.min, tzinfo=timezone.utc)


def _entity_id(event: CanonicalCommercialEvent, model: str, level: str) -> str | None:
    prefix = "first" if model == "first_touch" else "last"
    value = getattr(event, f"{prefix}_{level}_id", None)
    return str(value) if value else None


def _insight_entity_id(row: MetaAdsInsightDaily, level: str) -> str | None:
    return {
        "campaign": row.campaign_id,
        "adset": row.adset_id,
        "ad": row.ad_id,
    }[level]


def _matches_filters(
    *, campaign_id: str | None, adset_id: str | None, ad_id: str | None,
    actual_campaign: Any, actual_adset: Any, actual_ad: Any,
) -> bool:
    return all(expected is None or str(actual or "") == expected for expected, actual in (
        (campaign_id, actual_campaign), (adset_id, actual_adset), (ad_id, actual_ad),
    ))


def _resolution_state(events: Iterable[CanonicalCommercialEvent], level: str, model: str) -> str:
    materialized = list(events)
    if not materialized:
        return "known"
    resolved = sum(1 for row in materialized if _entity_id(row, model, level))
    if resolved == len(materialized):
        return "resolved"
    if resolved:
        return "partial"
    return "unknown"


def _unique_stage_count(events: Iterable[CanonicalCommercialEvent], event_name: str) -> int:
    keys = set()
    for event in events:
        if event.event_name != event_name or (event_name == "Purchase" and not event.revenue_verified):
            continue
        if event_name in {"InitiateCheckout", "Purchase", "OrderCreated", "Refund"}:
            identity = event.checkout_id if event_name == "InitiateCheckout" else event.payment_id or event.source_id
        else:
            identity = event.lead_id or event.contact_id or event.conversation_id or event.source_id
        keys.add(str(identity or event.id))
    return len(keys)


def _sum_known(values: list[Decimal | None]) -> tuple[Decimal | None, str]:
    if not values:
        return None, "unknown"
    known = [value for value in values if value is not None]
    if not known:
        return None, "unknown"
    return sum(known, ZERO), "known" if len(known) == len(values) else "partial"


def _revenue(events: Iterable[CanonicalCommercialEvent]) -> dict[str, Any]:
    by_currency: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"gross": ZERO, "refunds": ZERO})
    unknown_value = 0
    for event in events:
        if not event.revenue_verified or event.event_name not in {"Purchase", "Refund"}:
            continue
        amount = _d(event.value)
        currency = str(event.currency or "").upper()
        if amount is None or len(currency) != 3:
            unknown_value += 1
            continue
        bucket = by_currency[currency]
        bucket["gross" if event.event_name == "Purchase" else "refunds"] += abs(amount)
    result = {}
    for currency, sums in by_currency.items():
        result[currency] = {
            "gross_revenue": _metric(sums["gross"], currency=currency),
            "refunds": _metric(sums["refunds"], currency=currency),
            "net_revenue": _metric(sums["gross"] - sums["refunds"], currency=currency),
        }
    return {"by_currency": result, "unknown_value_count": unknown_value,
            "state": "unknown" if not by_currency and not unknown_value else ("partial" if unknown_value else "known")}


async def _load_window(
    session: AsyncSession, *, empresa_id: Any, start_date: date, end_date: date,
) -> tuple[list[CanonicalCommercialEvent], list[MetaAdsInsightDaily], list[AttributionTouch], list[ChannelConversation], dict[str, AttributionTouchResolution]]:
    tenant_id = uuid.UUID(str(empresa_id))
    start, stop = _utc_start(start_date), _utc_end_exclusive(end_date)
    events = list((await session.execute(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
        CanonicalCommercialEvent.occurred_at >= start,
        CanonicalCommercialEvent.occurred_at < stop,
    ).order_by(CanonicalCommercialEvent.occurred_at.asc()))).scalars().all())
    insights = list((await session.execute(select(MetaAdsInsightDaily).where(
        MetaAdsInsightDaily.empresa_id == tenant_id,
        MetaAdsInsightDaily.date_start >= start_date,
        MetaAdsInsightDaily.date_stop <= end_date,
    ))).scalars().all())
    conversations = list((await session.execute(select(ChannelConversation.id).where(
        ChannelConversation.empresa_id == tenant_id,
        ChannelConversation.criado_em >= start.replace(tzinfo=None),
        ChannelConversation.criado_em < stop.replace(tzinfo=None),
    ))).scalars().all())
    touches = list((await session.execute(select(AttributionTouch).where(
        AttributionTouch.empresa_id == tenant_id,
        AttributionTouch.occurred_at >= start,
        AttributionTouch.occurred_at < stop,
    ))).scalars().all())
    # The query above selects the primary-key scalar, so each element is the
    # UUID itself rather than a ChannelConversation ORM instance.
    conversation_ids = list(conversations)
    if conversation_ids:
        related_touches = (await session.execute(select(AttributionTouch).where(
            AttributionTouch.empresa_id == tenant_id,
            AttributionTouch.conversation_id.in_(conversation_ids),
        ))).scalars().all()
        known_ids = {row.id for row in touches}
        touches.extend(row for row in related_touches if row.id not in known_ids)
    resolutions = list((await session.execute(select(AttributionTouchResolution).where(
        AttributionTouchResolution.empresa_id == tenant_id,
        AttributionTouchResolution.touch_id.in_([touch.id for touch in touches] or [uuid.UUID(int=0)]),
    ))).scalars().all())
    return events, insights, touches, conversations, {str(row.touch_id): row for row in resolutions}


def _cost_metrics(
    *, spend: Decimal | None, spend_state: str, impressions: int | None, impressions_state: str,
    clicks: int | None, clicks_state: str, conversations: int | None, leads: int | None,
    qualified: int | None, checkouts: int | None, purchases: int | None, currency: str | None,
    count_states: dict[str, str] | None = None,
) -> dict[str, Any]:
    ctr = _ratio(
        Decimal(clicks) if clicks is not None else None,
        Decimal(impressions) if impressions is not None else None,
        multiplier=Decimal(100),
        result_state="partial" if "partial" in {impressions_state, clicks_state} else None,
    )
    cpc = _ratio(spend if spend_state != "unknown" else None, Decimal(clicks) if clicks is not None else None, currency=currency,
                 result_state="partial" if spend_state == "partial" or clicks_state == "partial" else None)
    cpm = _ratio(spend if spend_state != "unknown" else None, Decimal(impressions) if impressions is not None else None, multiplier=Decimal(1000), currency=currency,
                 result_state="partial" if spend_state == "partial" or impressions_state == "partial" else None)
    count_states = count_states or {}
    def cost(count: int | None, key: str):
        count_state = count_states.get(key, "known")
        if spend_state == "unknown" or count is None or count_state == "unknown":
            return _metric(None, currency=currency)
        return _ratio(spend, count, currency=currency, result_state="partial" if spend_state == "partial" or count_state == "partial" else None)
    return {
        "ctr": ctr, "cpc": cpc, "cpm": cpm,
        "cost_per_conversation": cost(conversations, "conversations"), "cpl": cost(leads, "leads"),
        "cpql": cost(qualified, "qualified"), "cost_per_checkout": cost(checkouts, "checkouts"), "cpa": cost(purchases, "purchases"),
    }


def _campaign_name(events: Iterable[CanonicalCommercialEvent], insight_rows: Iterable[MetaAdsInsightDaily], entity_id: str, model: str, level: str) -> str | None:
    name_field = {"campaign": "campaign_name", "adset": "adset_name", "ad": "ad_name"}[level]
    snapshot_field = {"campaign": "campaign_name", "adset": "adset_name", "ad": "ad_name"}[level]
    for row in insight_rows:
        if _insight_entity_id(row, level) == entity_id and getattr(row, name_field):
            return str(getattr(row, name_field))
    for event in events:
        touch = (event.attribution_snapshot or {}).get("first_touch" if model == "first_touch" else "last_touch") or {}
        if str(touch.get(f"{level}_id") or "") == entity_id and touch.get(snapshot_field):
            return str(touch[snapshot_field])
    return None


def _events_received(response_preview: Any) -> int:
    preview = response_preview if isinstance(response_preview, dict) else {}
    body = preview.get("body") if isinstance(preview.get("body"), dict) else preview
    try:
        return max(0, int(body.get("events_received") or 0))
    except (TypeError, ValueError):
        return 0


def _meta_acceptance_for_events(
    events: Iterable[CanonicalCommercialEvent],
    linked_attempts: dict[str, list[tuple[str, str, Any]]],
) -> dict[str, Any]:
    """Summarize exact CRM→provider links without implying ad optimization.

    The denominator is canonical CRM events attributed to this ad. An event
    counts as accepted only when an exact persisted link has status `sent` and
    the Meta response reports events_received > 0. A missing link or pending
    status makes coverage partial; no linked event is never presented as a
    confirmed zero rejection.
    """
    rows = list(events)
    denominator = len(rows)
    if not denominator:
        unknown = {"numerator": 0, "denominator": 0, "coverage": 0.0, "value": None, "state": "unknown"}
        return {"crm_events": 0, "exact_link_coverage": dict(unknown), "meta_endpoint_acceptance": dict(unknown)}

    exact_linked = 0
    accepted = 0
    terminal = 0
    for event in rows:
        attempts = [row for row in linked_attempts.get(str(event.id), []) if str(row[0]).upper() == "LINKED_EXACT"]
        if not attempts:
            continue
        exact_linked += 1
        accepted_attempts = [row for row in attempts if str(row[1] or "").lower() == "sent" and _events_received(row[2]) > 0]
        if accepted_attempts:
            accepted += 1
            terminal += 1
        elif any(str(row[1] or "").lower() in {"sent", "failed", "rejected", "cancelled", "canceled"} for row in attempts):
            terminal += 1

    coverage_state = "observed" if exact_linked == denominator else "partial" if exact_linked else "unknown"
    outcome_state = "observed" if terminal == denominator else "partial" if exact_linked or terminal else "unknown"
    return {
        "crm_events": denominator,
        "exact_link_coverage": {
            "numerator": exact_linked, "denominator": denominator,
            "coverage": exact_linked / denominator, "value": exact_linked, "state": coverage_state,
        },
        "meta_endpoint_acceptance": {
            "numerator": accepted, "denominator": denominator,
            "coverage": terminal / denominator, "value": accepted / denominator,
            "state": outcome_state,
            "interpretation": "Meta endpoint acceptance only; this does not prove ad-level optimization or causality.",
        },
    }


async def _load_exact_meta_acceptance(
    session: AsyncSession, *, empresa_id: Any, events: Iterable[CanonicalCommercialEvent],
) -> dict[str, list[tuple[str, str, Any]]]:
    event_ids = [event.id for event in events]
    if not event_ids:
        return {}
    rows = (await session.execute(
        select(
            CommercialMetaEventLink.commercial_event_id,
            CommercialMetaEventLink.classification,
            MetaConversionEvent.status,
            MetaConversionEvent.response_preview,
        ).join(
            MetaConversionEvent,
            and_(
                MetaConversionEvent.empresa_id == CommercialMetaEventLink.empresa_id,
                MetaConversionEvent.id == CommercialMetaEventLink.meta_event_id,
            ),
        ).where(
            CommercialMetaEventLink.empresa_id == uuid.UUID(str(empresa_id)),
            CommercialMetaEventLink.commercial_event_id.in_(event_ids),
        )
    )).all()
    by_event: dict[str, list[tuple[str, str, Any]]] = defaultdict(list)
    for commercial_event_id, classification, status, response_preview in rows:
        by_event[str(commercial_event_id)].append((str(classification or ""), str(status or ""), response_preview))
    return by_event


async def performance(
    session: AsyncSession, *, empresa_id: Any, start_date: date, end_date: date,
    attribution_model: str = "last_touch", level: str = "campaign",
    campaign_id: str | None = None, adset_id: str | None = None, ad_id: str | None = None,
) -> dict[str, Any]:
    if attribution_model not in {"first_touch", "last_touch"} or level not in {"campaign", "adset", "ad"}:
        raise ValueError("analytics_filter_invalid")
    events, insights, touches, conversations, resolution_by_touch = await _load_window(session, empresa_id=empresa_id, start_date=start_date, end_date=end_date)
    events = [event for event in events if _matches_filters(
        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
        actual_campaign=_entity_id(event, attribution_model, "campaign"),
        actual_adset=_entity_id(event, attribution_model, "adset"),
        actual_ad=_entity_id(event, attribution_model, "ad"),
    )]
    linked_attempts = await _load_exact_meta_acceptance(session, empresa_id=empresa_id, events=events)
    level_insights = [row for row in insights if row.entity_level == level]
    if not level_insights:
        fallback_levels = {"campaign": ("adset", "ad"), "adset": ("ad",), "ad": ()}[level]
        for fallback in fallback_levels:
            level_insights = [row for row in insights if row.entity_level == fallback]
            if level_insights:
                break
    insights = level_insights
    insights = [row for row in insights if _matches_filters(
        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
        actual_campaign=row.campaign_id, actual_adset=row.adset_id, actual_ad=row.ad_id,
    )]
    all_attributed = [row for row in events if _entity_id(row, attribution_model, level)]
    ids = {str(_insight_entity_id(row, level)) for row in insights if _insight_entity_id(row, level)}
    ids.update(str(_entity_id(row, attribution_model, level)) for row in events if _entity_id(row, attribution_model, level))
    rows = []
    for entity_id in sorted(ids):
        group_insights = [row for row in insights if _insight_entity_id(row, level) == entity_id]
        spend, spend_state = _sum_known([_d(row.spend) for row in group_insights])
        impressions_sum = _sum_known([Decimal(row.impressions) if row.impressions is not None else None for row in group_insights])
        clicks_sum = _sum_known([Decimal(row.clicks) if row.clicks is not None else None for row in group_insights])
        group_events = [row for row in all_attributed if _entity_id(row, attribution_model, level) == entity_id]
        counts: dict[str, int] = {}
        for event_name, key in EVENT_LABELS.items():
            matching = [row for row in group_events if row.event_name == event_name and (event_name != "Purchase" or row.revenue_verified)]
            counts[key] = _unique_stage_count(matching, event_name)
        # A conversation is a distinct, existing tenant conversation joined to an eligible touch.
        conv_by_entity = set()
        for touch in touches:
            if not touch.conversation_id or touch.event_kind not in {"ctwa_referral", "campaign_reply", "tracked_link_click"}:
                continue
            resolution = resolution_by_touch.get(str(touch.id))
            resolved_id = getattr(resolution, f"{level}_id", None) if resolution else None
            if str(resolved_id or "") == entity_id:
                conv_by_entity.add(str(touch.conversation_id))
        conversation_ids_in_window = {str(conversation_id) for conversation_id in conversations}
        counts["conversations"] = len(conv_by_entity & conversation_ids_in_window) if conversation_ids_in_window else len(conv_by_entity)
        purchase_events = [row for row in group_events if row.event_name in {"Purchase", "Refund"} and row.revenue_verified]
        revenue = _revenue(purchase_events)
        matching_currency = group_insights[0].account_currency if group_insights else None
        revenue_item = revenue["by_currency"].get(str(matching_currency or "").upper()) if matching_currency else None
        count_states: dict[str, str] = {}
        for event_name, key in EVENT_LABELS.items():
            unresolved_stage = sum(1 for row in events if row.event_name == event_name and (event_name != "Purchase" or row.revenue_verified) and not _entity_id(row, attribution_model, level))
            count_states[key] = "unknown" if unresolved_stage and counts[key] == 0 else ("partial" if unresolved_stage else "known")
        unresolved_conversation_touch = any(
            touch.event_kind in {"ctwa_referral", "campaign_reply", "tracked_link_click"}
            and touch.conversation_id and getattr(resolution_by_touch.get(str(touch.id)), f"{level}_id", None) is None
            for touch in touches
        )
        count_states["conversations"] = "unknown" if unresolved_conversation_touch and counts["conversations"] == 0 else ("partial" if unresolved_conversation_touch else "known")
        roas = _ratio(
            Decimal(str(revenue_item["net_revenue"]["value"])) if revenue_item else None,
            spend if spend_state != "unknown" else None,
            currency=matching_currency,
            result_state="partial" if spend_state == "partial" else None,
        )
        if revenue_item is None and revenue["by_currency"]:
            roas = _metric(None, state="unknown")
        metrics = _cost_metrics(
            spend=spend, spend_state=spend_state,
            impressions=int(impressions_sum[0]) if impressions_sum[0] is not None else None,
            impressions_state=impressions_sum[1],
            clicks=int(clicks_sum[0]) if clicks_sum[0] is not None else None,
            clicks_state=clicks_sum[1], conversations=counts["conversations"], leads=counts["leads"],
            qualified=counts["qualified"], checkouts=counts["checkouts"], purchases=counts["purchases"], currency=matching_currency,
            count_states=count_states,
        )
        metrics["roas"] = roas
        for key, state in count_states.items():
            metrics[key] = _metric(counts[key], state=state)
        crm_event_acceptance = _meta_acceptance_for_events(group_events, linked_attempts)
        rows.append({
            "entity_id": entity_id, "name": _campaign_name(events, group_insights, entity_id, attribution_model, level),
            "level": level, "attribution_model": attribution_model,
            "attribution_confidence": _resolution_state(group_events, level, attribution_model),
            "spend": _metric(spend, state=spend_state, currency=matching_currency),
            "impressions": _metric(int(impressions_sum[0]) if impressions_sum[0] is not None else None, state=impressions_sum[1]),
            "clicks": _metric(int(clicks_sum[0]) if clicks_sum[0] is not None else None, state=clicks_sum[1]),
            **metrics, "revenue": revenue,
            "meta_conversion_evidence": crm_event_acceptance,
        })
    # Orphans with attribution but no entity IDs remain visible as data quality gaps.
    unresolved_event_count = sum(1 for event in events if event.event_name in set(EVENT_LABELS) and not _entity_id(event, attribution_model, level))
    return {
        "tenant_id": str(empresa_id), "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "attribution_model": attribution_model, "group_by": level,
        "filters": {"campaign_id": campaign_id, "adset_id": adset_id, "ad_id": ad_id}, "rows": rows,
        "unattributed_conversion_events": unresolved_event_count,
        "spend_state": "unknown" if not insights else "partial" if any(row.spend is None for row in insights) else "known",
    }


async def funnel(
    session: AsyncSession, *, empresa_id: Any, start_date: date, end_date: date,
    attribution_model: str = "last_touch", campaign_id: str | None = None,
    adset_id: str | None = None, ad_id: str | None = None,
) -> dict[str, Any]:
    if attribution_model not in {"first_touch", "last_touch"}:
        raise ValueError("analytics_attribution_model_invalid")
    events, insights, touches, conversations, _resolution_by_touch = await _load_window(session, empresa_id=empresa_id, start_date=start_date, end_date=end_date)
    events = [event for event in events if _matches_filters(
        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
        actual_campaign=_entity_id(event, attribution_model, "campaign"),
        actual_adset=_entity_id(event, attribution_model, "adset"),
        actual_ad=_entity_id(event, attribution_model, "ad"),
    )]
    insights = [row for row in insights if _matches_filters(
        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
        actual_campaign=row.campaign_id, actual_adset=row.adset_id, actual_ad=row.ad_id,
    )]
    if campaign_id or adset_id or ad_id:
        filtered_touches = []
        for touch in touches:
            resolution = _resolution_by_touch.get(str(touch.id))
            if _matches_filters(
                campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
                actual_campaign=resolution.campaign_id if resolution else None,
                actual_adset=resolution.adset_id if resolution else None,
                actual_ad=resolution.ad_id if resolution else None,
            ):
                filtered_touches.append(touch)
        touches = filtered_touches
    aggregate_level = "campaign" if any(row.entity_level == "campaign" for row in insights) else (
        "adset" if any(row.entity_level == "adset" for row in insights) else "ad"
    )
    insights = [row for row in insights if row.entity_level == aggregate_level]
    impressions, imp_state = _sum_known([Decimal(row.impressions) if row.impressions is not None else None for row in insights])
    clicks, click_state = _sum_known([Decimal(row.clicks) if row.clicks is not None else None for row in insights])
    conv_ids = {str(touch.conversation_id) for touch in touches if touch.conversation_id and touch.event_kind in {"ctwa_referral", "campaign_reply", "tracked_link_click"}}
    conv_ids &= {str(conversation_id) for conversation_id in conversations}
    stage_counts = {
        "ViewContent": _unique_stage_count(events, "ViewContent"),
        "LeadSubmitted": _unique_stage_count(events, "LeadSubmitted"),
        "QualifiedLead": _unique_stage_count(events, "QualifiedLead"),
        "AddToCart": _unique_stage_count(events, "AddToCart"),
        "InitiateCheckout": _unique_stage_count(events, "InitiateCheckout"),
        "Purchase": _unique_stage_count(events, "Purchase"),
    }
    stages = [
        ("impressions", int(impressions) if impressions is not None else None, imp_state),
        ("clicks", int(clicks) if clicks is not None else None, click_state),
        ("conversations", len(conv_ids), "known"),
    ] + [(EVENT_LABELS[name], value, "known") for name, value in stage_counts.items()]
    output = []
    previous = None
    for name, count, state in stages:
        rate = _ratio(Decimal(count) if count is not None else None, Decimal(previous) if previous is not None else None, multiplier=Decimal(100))
        output.append({"stage": name, "count": _metric(count, state=state), "conversion_rate_from_previous_percent": rate})
        if count is not None:
            previous = count
    return {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "attribution_model": attribution_model,
            "filters": {"campaign_id": campaign_id, "adset_id": adset_id, "ad_id": ad_id},
            "stages": output, "unique_leads": _metric(len({str(event.lead_id) for event in events if event.event_name == "LeadSubmitted" and event.lead_id})),
            "unique_conversations": _metric(len(conv_ids)), "revenue": _revenue(events)}


async def attribution_quality(session: AsyncSession, *, empresa_id: Any, start_date: date, end_date: date) -> dict[str, Any]:
    events, _insights, touches, conversations, resolution_by_touch = await _load_window(session, empresa_id=empresa_id, start_date=start_date, end_date=end_date)
    resolutions = list(resolution_by_touch.values())
    marketing_conversations = {str(touch.conversation_id) for touch in touches if touch.conversation_id and touch.event_kind in {"ctwa_referral", "campaign_reply", "tracked_link_click"}}
    window_conversation_ids = {str(conversation_id) for conversation_id in conversations}
    attributed_conversations = marketing_conversations & window_conversation_ids
    with_ad = set()
    with_adset = set()
    with_campaign = set()
    for touch in touches:
        resolution = resolution_by_touch.get(str(touch.id))
        if resolution is None:
            continue
        if resolution.ad_id:
            with_ad.add(str(touch.id))
        if resolution.adset_id:
            with_adset.add(str(touch.id))
        if resolution.campaign_id:
            with_campaign.add(str(touch.id))
    purchases = [event for event in events if event.event_name == "Purchase" and event.revenue_verified]
    resolved_purchases = [event for event in purchases if event.last_campaign_id]
    verified_revenue_events = [event for event in events if event.event_name in {"Purchase", "Refund"} and event.revenue_verified]
    revenue = _revenue(verified_revenue_events)
    revenue_resolved = _revenue([event for event in verified_revenue_events if event.last_campaign_id])
    revenue_unknown_campaign = _revenue([event for event in verified_revenue_events if not event.last_campaign_id])
    legacy_links = list((await session.execute(select(CommercialMetaEventLink).where(
        CommercialMetaEventLink.empresa_id == uuid.UUID(str(empresa_id)),
    ))).scalars().all())
    return {
        "total_conversations": len(conversations), "with_attribution": len(attributed_conversations),
        "without_attribution": max(0, len(conversations) - len(attributed_conversations)),
        "touches": {"total": len(touches), "resolved_to_ad": len(with_ad), "resolved_to_adset": len(with_adset), "resolved_to_campaign": len(with_campaign)},
        "purchases": {"total": len(purchases), "resolved_campaign": len(resolved_purchases), "unknown_campaign": len(purchases) - len(resolved_purchases)},
        "revenue": revenue,
        "revenue_with_resolved_campaign": revenue_resolved,
        "revenue_with_unknown_campaign": revenue_unknown_campaign,
        "legacy_meta_events": dict(Counter(row.classification for row in legacy_links)),
        "resolution_states": dict(Counter(row.resolution_status for row in resolutions)),
    }


async def event_signal_status(session: AsyncSession, *, empresa_id: Any, start_date: date, end_date: date) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    events = (await session.execute(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
        CanonicalCommercialEvent.occurred_at >= _utc_start(start_date),
        CanonicalCommercialEvent.occurred_at < _utc_end_exclusive(end_date),
    ).order_by(CanonicalCommercialEvent.occurred_at.asc()))).scalars().all()
    counts: Counter[str] = Counter()
    for event in events:
        counts[await meta_signal_status(session, event)] += 1
    return {"business_event_count": len(events), "meta_signal_counts": dict(counts), "backfilled_event_can_trigger_meta": False}
