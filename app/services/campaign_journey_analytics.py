"""Read-only, tenant-scoped WhatsApp campaign journey analytics (A10).

Delivery metrics come from Campaign Central's existing analytics. This module
adds only persisted origin, response/reactivation, configured stage-transition,
consent and verified-purchase evidence. Post-send outcomes are temporal
associations; none is represented as caused by a campaign.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.campaign_central_dispatch_service import campaign_results_analytics
from app.services.commercial_funnel_dashboard import _configuration as load_funnel_configuration
from db.models import CampaignRecipient, CampanhaDisparo

MAX_CAMPAIGNS = 5
MAX_RECIPIENTS_PER_CAMPAIGN = 10_000
MAX_DRILLDOWN_PAGE = 100
MAX_DRILLDOWN_OFFSET = MAX_RECIPIENTS_PER_CAMPAIGN - 1
ORIGIN_SEGMENTS = {"ctwa_referral", "tracked_link_click", "manual_touch", "unknown_source", "unknown"}


def enabled() -> bool:
    return os.getenv("CAMPAIGN_JOURNEY_ANALYTICS_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}


def _utc(value: datetime | date | str | None, *, fallback: datetime | None = None) -> datetime:
    if value is None:
        if fallback is None:
            raise ValueError("timestamp required")
        value = fallback
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("invalid timestamp")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def filters(*, empresa_id: Any, start=None, end=None, observed_at=None,
            response_window_days: int = 7, reactivation_days: int = 7,
            stage_window_days: int = 30, purchase_window_days: int = 30,
            campaign_ids: list[str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    current = _utc(now or datetime.now(timezone.utc))
    observed = _utc(observed_at, fallback=current)
    end_value = _utc(end, fallback=observed)
    start_value = _utc(start, fallback=end_value - timedelta(days=30))
    if start_value >= end_value or end_value > observed or observed > current or start_value > observed or (end_value - start_value).days > 366:
        raise ValueError("invalid campaign analytics window")
    for value, low, high in ((response_window_days, 1, 30), (reactivation_days, 1, 90),
                             (stage_window_days, 1, 90), (purchase_window_days, 1, 90)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError("invalid campaign analytics window")
    parsed_ids: list[uuid.UUID] = []
    for value in campaign_ids or []:
        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid campaign id") from exc
        if parsed not in parsed_ids:
            parsed_ids.append(parsed)
    if len(parsed_ids) > MAX_CAMPAIGNS:
        raise ValueError("at most five campaigns can be compared")
    return {
        "tenant": uuid.UUID(str(empresa_id)), "start": start_value, "end": end_value,
        "observed": observed, "response_window_days": response_window_days,
        "reactivation_days": reactivation_days, "stage_window_days": stage_window_days,
        "purchase_window_days": purchase_window_days, "campaign_ids": parsed_ids,
    }


def _metric(numerator: int | None, denominator: int | None, *, state: str, definition: str,
            coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    if state not in {"observed", "partial", "unknown", "not_applicable"}:
        state = "unknown"
    return {
        "numerator": numerator, "denominator": denominator,
        "value": numerator / denominator if numerator is not None and denominator else None,
        "state": state, "coverage": coverage or {}, "definition": definition,
    }


def _metric_state(*, sampled: int, capped: bool, denominator: int, source_available: bool = True,
                  pending_window: int = 0, incomplete: int = 0,
                  known_inapplicable: bool = False) -> str:
    if not source_available:
        return "unknown"
    if denominator == 0:
        if sampled == 0 or known_inapplicable:
            return "not_applicable"
        return "partial" if capped or pending_window else "unknown"
    return "partial" if capped or pending_window or incomplete else "observed"


def _delivery_rates(delivery: dict[str, Any], *, sampled: int, capped: bool,
                    provider_delivery_observed: int, provider_read_observed: int) -> dict[str, dict[str, Any]]:
    accepted = int(delivery.get("meta_accepted") or 0)
    delivered = int(delivery.get("delivered") or 0)
    read = int(delivery.get("read") or 0)
    delivery_coverage = min(max(provider_delivery_observed, 0), accepted)
    read_coverage = min(max(provider_read_observed, 0), delivered)
    if accepted == 0:
        delivery_state = "not_applicable"
    elif delivery_coverage == 0 and delivered == 0:
        delivery_state = "unknown"
    elif capped or delivery_coverage < accepted:
        delivery_state = "partial"
    else:
        delivery_state = "observed"
    if delivered == 0:
        read_state = "not_applicable"
    elif read_coverage == 0 and read == 0:
        read_state = "unknown"
    elif capped or read_coverage < delivered:
        read_state = "partial"
    else:
        read_state = "observed"
    return {
        "accepted": _metric(accepted, sampled, state="partial" if capped else ("observed" if sampled else "not_applicable"),
                            definition="Destinatários com aceite persistido dividido pelos destinatários amostrados."),
        "delivered": _metric(delivered, accepted, state=delivery_state,
                             definition="Entregas confirmadas pelo provider divididas pelos aceites persistidos.",
                             coverage={"recipients_with_delivery_confirmation": delivery_coverage, "accepted_recipients": accepted}),
        "read": _metric(read, delivered, state=("not_applicable" if delivered == 0 else read_state),
                        definition="Leituras confirmadas divididas pelas entregas confirmadas.",
                        coverage={"recipients_with_read_confirmation": read_coverage, "delivered_recipients": delivered}),
    }


def _safe_stage_spec(stages: list[dict[str, Any]]) -> str:
    safe: list[dict[str, Any]] = []
    for stage in stages:
        key = stage.get("key")
        order = stage.get("order")
        if isinstance(key, str) and re.fullmatch(r"[a-zA-Z0-9_:-]{1,100}", key) and isinstance(order, int) and not isinstance(order, bool):
            safe.append({"key": key, "order": order})
    return json.dumps(safe, separators=(",", ":"))


async def _selected_campaigns(session: AsyncSession, p: dict[str, Any]) -> list[tuple[uuid.UUID, str]]:
    tenant = p["tenant"]
    if p["campaign_ids"]:
        rows = (await session.execute(
            select(CampanhaDisparo.id, CampanhaDisparo.nome)
            .where(CampanhaDisparo.empresa_id == tenant, CampanhaDisparo.id.in_(p["campaign_ids"]))
            .order_by(CampanhaDisparo.criado_em.desc(), CampanhaDisparo.id.desc())
        )).all()
        if len(rows) != len(p["campaign_ids"]):
            raise LookupError("campaign unavailable")
        by_id = {row[0]: (row[0], str(row[1] or "Campanha")) for row in rows}
        return [by_id[campaign_id] for campaign_id in p["campaign_ids"]]

    rows = (await session.execute(
        select(CampanhaDisparo.id, CampanhaDisparo.nome)
        .join(CampaignRecipient, (CampaignRecipient.empresa_id == CampanhaDisparo.empresa_id) &
              (CampaignRecipient.campanha_id == CampanhaDisparo.id))
        .where(
            CampanhaDisparo.empresa_id == tenant,
            CampaignRecipient.empresa_id == tenant,
            func.coalesce(CampaignRecipient.sent_at, CampaignRecipient.created_at) >= _naive_utc(p["start"]),
            func.coalesce(CampaignRecipient.sent_at, CampaignRecipient.created_at) < _naive_utc(p["end"]),
        )
        .group_by(CampanhaDisparo.id, CampanhaDisparo.nome, CampanhaDisparo.criado_em)
        .order_by(CampanhaDisparo.criado_em.desc(), CampanhaDisparo.id.desc())
        .limit(MAX_CAMPAIGNS)
    )).all()
    return [(row[0], str(row[1] or "Campanha")) for row in rows]


_OUTCOME_SQL = text(r"""
WITH selected_campaigns AS (
    SELECT unnest(CAST(:campaign_ids AS uuid[])) AS campaign_id
), recipients AS MATERIALIZED (
    SELECT picked.* FROM selected_campaigns c
    CROSS JOIN LATERAL (
        SELECT r.* FROM campaign_recipients r
        WHERE r.empresa_id = :tenant AND r.campanha_id = c.campaign_id
          AND coalesce(r.sent_at, r.created_at) >= :period_start
          AND coalesce(r.sent_at, r.created_at) < :period_end
          AND coalesce(r.sent_at, r.created_at) <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
        ORDER BY coalesce(r.sent_at, r.created_at), r.id
        LIMIT :recipient_limit
    ) picked
), status_rollup AS MATERIALIZED (
    SELECT r.id AS recipient_id,
           bool_or(ev.status IN ('sent', 'delivered', 'read')) AS status_accepted,
           bool_or(ev.status = 'delivered') AS provider_delivered,
           bool_or(ev.status = 'read') AS provider_read,
           bool_or(ev.status IN ('sent', 'delivered', 'read', 'failed')) AS has_provider_status,
           (array_agg(ev.status ORDER BY coalesce(ev.occurred_at, ev.created_at) DESC,
                      CASE ev.status WHEN 'read' THEN 4 WHEN 'delivered' THEN 3 WHEN 'sent' THEN 2 ELSE 1 END DESC)
             FILTER (WHERE ev.status IS NOT NULL))[1] AS last_event_status,
           min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'sent') AS event_sent_at,
           min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'delivered') AS event_delivered_at,
           min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'read') AS event_read_at
    FROM recipients r
    LEFT JOIN message_status_events ev
      ON ev.empresa_id = r.empresa_id AND ev.conexao_id = r.conexao_id
     AND ev.provider = r.provider AND ev.channel = r.channel
     AND ev.external_message_id = r.external_message_id
     AND coalesce(ev.occurred_at, ev.created_at) <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
    GROUP BY r.id
), attempt_rollup AS MATERIALIZED (
    SELECT r.id AS recipient_id,
           bool_or(ma.result_status IN ('accepted', 'sent', 'completed') AND ma.external_message_id IS NOT NULL) AS attempt_accepted,
           min(ma.finished_at) FILTER (WHERE ma.result_status IN ('accepted', 'sent', 'completed') AND ma.external_message_id IS NOT NULL) AS attempt_accepted_at
    FROM recipients r
    LEFT JOIN message_outbox mo
      ON mo.empresa_id = r.empresa_id AND mo.campaign_recipient_id = r.id
     AND mo.provider = r.provider AND mo.conexao_id = r.conexao_id AND mo.channel = r.channel
    LEFT JOIN message_attempts ma
      ON ma.empresa_id = mo.empresa_id AND ma.outbox_id = mo.id
     AND ma.provider = r.provider AND ma.conexao_id = r.conexao_id
     AND ma.finished_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
    GROUP BY r.id
), recipient_state AS MATERIALIZED (
    SELECT r.*,
           CASE WHEN r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.status
                ELSE coalesce((
                    SELECT state.status FROM (VALUES
                        (r.sent_at, 'sent', 1), (r.delivered_at, 'delivered', 2),
                        (r.read_at, 'read', 3), (r.failed_at, 'failed', 4),
                        (r.cancelled_at, 'cancelled', 5)
                    ) AS state(status_at, status, priority)
                    WHERE state.status_at IS NOT NULL
                      AND state.status_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                    ORDER BY state.status_at DESC, state.priority DESC LIMIT 1
                ), sr.last_event_status, 'unknown')
           END AS effective_status,
           CASE WHEN r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                THEN r.error_code END AS effective_error_code
    FROM recipients r
    LEFT JOIN status_rollup sr ON sr.recipient_id = r.id
), accepted_recipients AS MATERIALIZED (
    SELECT r.*,
           coalesce(
               CASE WHEN r.sent_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.sent_at END,
               sr.event_sent_at, sr.event_delivered_at, sr.event_read_at,
               CASE WHEN r.delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.delivered_at END,
               CASE WHEN r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.read_at END,
               ar.attempt_accepted_at
           ) AS effective_sent_at,
           coalesce(sr.status_accepted, false) AS status_accepted,
           coalesce(sr.provider_delivered, false) AS provider_delivered,
           coalesce(sr.provider_read, false) AS provider_read,
           coalesce(sr.has_provider_status, false) AS has_provider_status,
           coalesce(ar.attempt_accepted, false) AS attempt_accepted,
           (
               r.external_message_id IS NOT NULL AND r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.effective_status IN ('sent', 'delivered', 'read')
               OR r.sent_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR coalesce(sr.status_accepted, false) OR coalesce(ar.attempt_accepted, false)
           ) AS is_accepted,
           (
               r.delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.effective_status IN ('delivered', 'read')
               OR coalesce(sr.provider_delivered, false) OR coalesce(sr.provider_read, false)
           ) AS delivery_confirmation_observed,
           (
               r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
               OR r.effective_status = 'read'
               OR coalesce(sr.provider_read, false)
           ) AS read_confirmation_observed
    FROM recipient_state r
    LEFT JOIN status_rollup sr ON sr.recipient_id = r.id
    LEFT JOIN attempt_rollup ar ON ar.recipient_id = r.id
), stage_catalog AS (
    SELECT * FROM jsonb_to_recordset(CAST(:stages_json AS jsonb)) AS s(key text, "order" integer)
), facts AS (
    SELECT r.id AS recipient_id, r.campanha_id AS campaign_id, r.empresa_id,
           r.lead_id, r.contact_id, r.conversation_id, r.conexao_id,
           r.effective_status AS recipient_status, r.effective_error_code AS error_code,
           r.sent_at, r.created_at,
           r.effective_sent_at, r.is_accepted, r.has_provider_status,
           r.provider_delivered, r.provider_read,
           r.delivery_confirmation_observed, r.read_confirmation_observed,
           CASE WHEN r.is_accepted AND r.effective_sent_at IS NULL THEN 'unknown'
                ELSE coalesce(origin.event_kind, 'unknown') END AS origin_segment,
           EXISTS (
               SELECT 1 FROM message_outbox mo
               LEFT JOIN message_attempts ma ON ma.empresa_id = mo.empresa_id AND ma.outbox_id = mo.id
               WHERE mo.empresa_id = r.empresa_id AND mo.campaign_recipient_id = r.id
                 AND mo.provider = r.provider AND mo.conexao_id = r.conexao_id AND mo.channel = r.channel
                 AND (r.is_accepted
                      OR (mo.attempts > 0 AND mo.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC')
                      OR (ma.id IS NOT NULL AND ma.provider = r.provider AND ma.conexao_id = r.conexao_id
                          AND ma.started_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'))
           ) AS attempted,
           EXISTS (
               SELECT 1 FROM mensagens_historico mh
               WHERE r.conversation_id IS NOT NULL AND mh.empresa_id = r.empresa_id
                 AND mh.channel_conversation_id = r.conversation_id
                 AND (mh.direction = 'inbound' OR (mh.direction IS NULL AND mh.from_me = false))
                 AND lower(coalesce(mh.tipo_mensagem, '')) NOT IN ('status', 'reaction')
                 AND lower(coalesce(mh.metadata_json->>'event_kind', '')) NOT IN ('status', 'reaction')
                 AND r.effective_sent_at IS NOT NULL
                 AND mh.criado_em > r.effective_sent_at
                 AND mh.criado_em <= r.effective_sent_at + (:response_window_days * interval '1 day')
                 AND mh.criado_em <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
           ) AS replied,
           EXISTS (
               SELECT 1 FROM mensagens_historico resumed
               WHERE r.conversation_id IS NOT NULL
                 AND r.effective_sent_at IS NOT NULL
                 AND prior_inbound.last_inbound_at <= r.effective_sent_at - (:reactivation_days * interval '1 day')
                 AND resumed.empresa_id = r.empresa_id
                 AND resumed.channel_conversation_id = r.conversation_id
                 AND (resumed.direction = 'inbound' OR (resumed.direction IS NULL AND resumed.from_me = false))
                 AND lower(coalesce(resumed.tipo_mensagem, '')) NOT IN ('status', 'reaction')
                 AND lower(coalesce(resumed.metadata_json->>'event_kind', '')) NOT IN ('status', 'reaction')
                 AND resumed.criado_em > r.effective_sent_at
                 AND resumed.criado_em <= r.effective_sent_at + (:response_window_days * interval '1 day')
                 AND resumed.criado_em <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
           ) AS reactivated,
           (prior_inbound.last_inbound_at IS NOT NULL) AS reactivation_history_comparable,
           (r.effective_sent_at IS NOT NULL AND prior_inbound.last_inbound_at <= r.effective_sent_at - (:reactivation_days * interval '1 day')) AS reactivation_comparable,
           (r.conversation_id IS NOT NULL) AS response_comparable,
           (r.lead_id IS NOT NULL) AS purchase_comparable,
           EXISTS (
               SELECT 1 FROM attribution_touches t
               WHERE t.empresa_id = r.empresa_id AND t.campaign_recipient_id = r.id
                 AND t.outbound_campaign_id = r.campanha_id AND t.event_kind = 'campaign_reply'
                 AND t.occurred_at <= CAST(:observed_at AS timestamptz)
           ) AS campaign_reply_touch,
           EXISTS (
               SELECT 1 FROM attribution_touches t
               WHERE t.empresa_id = r.empresa_id AND t.campaign_recipient_id = r.id
                 AND t.outbound_campaign_id = r.campanha_id AND t.event_kind = 'campaign_sent'
                 AND t.occurred_at <= CAST(:observed_at AS timestamptz)
           ) AS campaign_sent_touch,
           EXISTS (
               SELECT 1 FROM commercial_events e
               WHERE r.effective_sent_at IS NOT NULL AND e.empresa_id = r.empresa_id AND e.lead_id = r.lead_id
                 AND e.event_name = 'Purchase' AND e.revenue_verified = true
                 AND e.occurred_at > r.effective_sent_at AT TIME ZONE 'UTC'
                 AND e.occurred_at <= (r.effective_sent_at AT TIME ZONE 'UTC') + (:purchase_window_days * interval '1 day')
                 AND e.occurred_at <= CAST(:observed_at AS timestamptz)
           ) AS verified_purchase,
           (prior_stage.stage_order IS NOT NULL) AS stage_comparable,
           EXISTS (
               SELECT 1
               FROM commercial_events after_stage
               JOIN stage_catalog after_order ON after_order.key = after_stage.funnel_stage_id
               WHERE r.conversation_id IS NOT NULL AND after_stage.empresa_id = r.empresa_id
                 AND after_stage.lead_id = r.lead_id AND after_stage.conversation_id = r.conversation_id
                 AND after_stage.occurred_at > r.effective_sent_at AT TIME ZONE 'UTC'
                 AND r.effective_sent_at IS NOT NULL
                 AND after_stage.occurred_at <= (r.effective_sent_at AT TIME ZONE 'UTC') + (:stage_window_days * interval '1 day')
                 AND after_stage.occurred_at <= CAST(:observed_at AS timestamptz)
                 AND prior_stage.stage_order IS NOT NULL
                 AND after_order."order" > prior_stage.stage_order
           ) AS stage_advanced,
           EXISTS (
               SELECT 1 FROM communication_consents cc
               WHERE cc.empresa_id = r.empresa_id AND cc.contact_id = r.contact_id
                 AND cc.conexao_id = r.conexao_id AND cc.provider = r.provider
                 AND cc.channel = 'whatsapp' AND lower(cc.status) IN ('opt_out', 'opted_out', 'revoked')
           ) AS current_opt_out,
           (r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.effective_sent_at + (:response_window_days * interval '1 day') <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC') AS response_matured
           , (r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.effective_sent_at + (:stage_window_days * interval '1 day') <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC') AS stage_matured
           , (r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.effective_sent_at + (:purchase_window_days * interval '1 day') <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC') AS purchase_matured
    FROM accepted_recipients r
    LEFT JOIN LATERAL (
        SELECT max(mh.criado_em) AS last_inbound_at
        FROM mensagens_historico mh
        WHERE r.conversation_id IS NOT NULL AND mh.empresa_id = r.empresa_id
          AND mh.channel_conversation_id = r.conversation_id
          AND (mh.direction = 'inbound' OR (mh.direction IS NULL AND mh.from_me = false))
          AND lower(coalesce(mh.tipo_mensagem, '')) NOT IN ('status', 'reaction')
          AND lower(coalesce(mh.metadata_json->>'event_kind', '')) NOT IN ('status', 'reaction')
          AND r.effective_sent_at IS NOT NULL AND mh.criado_em < r.effective_sent_at
    ) prior_inbound ON true
    LEFT JOIN LATERAL (
        SELECT stage."order" AS stage_order
        FROM commercial_events event
        JOIN stage_catalog stage ON stage.key = event.funnel_stage_id
        WHERE r.conversation_id IS NOT NULL AND event.empresa_id = r.empresa_id
          AND event.lead_id = r.lead_id AND event.conversation_id = r.conversation_id
          AND event.occurred_at < coalesce(r.effective_sent_at, r.created_at) AT TIME ZONE 'UTC'
        ORDER BY event.occurred_at DESC, event.id DESC
        LIMIT 1
    ) prior_stage ON true
    LEFT JOIN LATERAL (
        SELECT t.event_kind
        FROM attribution_touches t
        WHERE t.empresa_id = r.empresa_id
          AND t.event_kind IN ('ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source')
          AND t.occurred_at <= coalesce(r.effective_sent_at, r.created_at) AT TIME ZONE 'UTC'
          AND ((r.lead_id IS NOT NULL AND t.lead_id = r.lead_id)
            OR (r.lead_id IS NULL AND r.contact_id IS NOT NULL AND t.contact_id = r.contact_id)
            OR (r.lead_id IS NULL AND r.contact_id IS NULL AND r.conversation_id IS NOT NULL AND t.conversation_id = r.conversation_id))
        ORDER BY t.occurred_at ASC, t.id ASC
        LIMIT 1
    ) origin ON true
)
SELECT f.campaign_id, f.origin_segment,
       count(*) AS sampled_recipients,
       count(DISTINCT f.lead_id) AS sampled_leads,
       count(*) FILTER (WHERE f.attempted) AS attempted,
       count(*) FILTER (WHERE f.replied) AS replied,
       count(*) FILTER (WHERE f.reactivated) AS reactivated,
       count(*) FILTER (WHERE f.campaign_reply_touch) AS campaign_reply_touches,
       count(*) FILTER (WHERE f.campaign_sent_touch) AS campaign_sent_touches,
       count(*) FILTER (WHERE f.stage_comparable) AS stage_comparable,
       count(*) FILTER (WHERE f.stage_advanced) AS stage_advanced,
       count(*) FILTER (WHERE f.verified_purchase) AS verified_purchase,
       count(*) FILTER (WHERE f.current_opt_out) AS current_opt_out,
       count(*) FILTER (WHERE f.has_provider_status) AS provider_status_observed,
       count(*) FILTER (WHERE f.delivery_confirmation_observed) AS provider_delivery_observed,
       count(*) FILTER (WHERE f.read_confirmation_observed) AS provider_read_observed,
       count(*) FILTER (WHERE f.is_accepted AND f.effective_sent_at IS NULL) AS accepted_without_timestamp,
       count(*) FILTER (WHERE f.response_matured) AS response_matured,
       count(*) FILTER (WHERE f.stage_matured) AS stage_matured,
       count(*) FILTER (WHERE f.purchase_matured) AS purchase_matured,
       count(*) FILTER (WHERE (f.response_matured AND f.response_comparable) OR f.replied) AS response_denominator,
       count(*) FILTER (WHERE f.response_matured AND f.reactivation_comparable OR f.reactivated) AS reactivation_denominator,
       count(*) FILTER (WHERE f.reactivation_comparable) AS reactivation_comparable,
       count(*) FILTER (WHERE f.reactivation_history_comparable) AS reactivation_history_comparable,
       count(*) FILTER (WHERE (f.stage_matured AND f.stage_comparable) OR f.stage_advanced) AS stage_denominator,
       count(*) FILTER (WHERE f.stage_matured AND f.stage_comparable) AS stage_comparable_denominator,
       count(*) FILTER (WHERE f.response_matured = false AND f.response_comparable AND NOT f.replied AND f.is_accepted AND f.effective_sent_at IS NOT NULL) AS response_pending,
       count(*) FILTER (WHERE f.is_accepted AND f.effective_sent_at IS NOT NULL AND NOT f.response_comparable) AS response_unlinked,
       count(*) FILTER (WHERE f.is_accepted AND f.effective_sent_at IS NOT NULL AND NOT f.reactivation_history_comparable) AS reactivation_unknown_history,
       count(*) FILTER (WHERE f.response_matured = false AND f.reactivation_comparable AND NOT f.reactivated AND f.is_accepted AND f.effective_sent_at IS NOT NULL) AS reactivation_pending,
       count(*) FILTER (WHERE f.stage_matured = false AND f.stage_comparable AND NOT f.stage_advanced AND f.is_accepted AND f.effective_sent_at IS NOT NULL) AS stage_pending,
       count(*) FILTER (WHERE f.is_accepted AND f.effective_sent_at IS NOT NULL AND NOT f.stage_comparable) AS stage_unknown_history,
       count(*) FILTER (WHERE (f.purchase_matured AND f.purchase_comparable) OR f.verified_purchase) AS purchase_denominator,
       count(*) FILTER (WHERE f.purchase_matured = false AND f.purchase_comparable AND NOT f.verified_purchase AND f.is_accepted AND f.effective_sent_at IS NOT NULL) AS purchase_pending,
       count(*) FILTER (WHERE f.is_accepted AND f.effective_sent_at IS NOT NULL AND NOT f.purchase_comparable) AS purchase_unlinked,
       count(*) FILTER (WHERE f.is_accepted) AS sent_recipients,
       count(*) FILTER (WHERE f.recipient_status = 'failed') AS failed,
       count(*) FILTER (WHERE f.recipient_status = 'cancelled') AS cancelled,
       count(*) FILTER (WHERE f.recipient_status = 'stopped') AS stopped,
       count(*) FILTER (WHERE f.recipient_status = 'blocked') AS blocked,
       count(*) FILTER (WHERE f.recipient_status IN ('queued', 'retry', 'locked', 'processing')) AS pending,
       count(*) FILTER (WHERE f.recipient_status = 'unknown') AS unknown_status,
       count(*) FILTER (WHERE f.error_code = 'communication_consent_block') AS consent_blocked,
       count(*) FILTER (WHERE f.error_code = 'campaign_allowlist_block') AS allowlist_blocked,
       count(*) FILTER (WHERE f.error_code = 'TEMPLATE_VARIABLE_MISSING') AS template_blocked,
       count(*) FILTER (WHERE f.recipient_status IN ('failed','blocked') AND coalesce(f.error_code,'') NOT IN ('communication_consent_block','campaign_allowlist_block','TEMPLATE_VARIABLE_MISSING')) AS other_recorded_failure,
       bool_or(f.is_accepted AND f.effective_sent_at IS NOT NULL) AS has_sent_timestamp
FROM facts f
GROUP BY f.campaign_id, f.origin_segment
ORDER BY f.campaign_id, f.origin_segment
""")


async def dashboard(session: AsyncSession, *, empresa_id: Any, **kwargs) -> dict[str, Any]:
    p = filters(empresa_id=empresa_id, **kwargs)
    campaigns = await _selected_campaigns(session, p)
    if not campaigns:
        return {
            "window": _window(p), "campaigns": [], "segments": [],
            "coverage": {"state": "unknown", "sample_limit_per_campaign": MAX_RECIPIENTS_PER_CAMPAIGN,
                         "explanation": "Nenhum destinatário persistido no período selecionado."},
            "definitions": _definitions(),
        }

    stage_params = {"tenant": str(p["tenant"]), "stage": None, "bottleneck": None}
    stage_rows = await load_funnel_configuration(session, stage_params)
    stage_spec = _safe_stage_spec(stage_rows)
    campaign_ids = [campaign_id for campaign_id, _ in campaigns]
    summaries: list[dict[str, Any]] = []
    for campaign_id, label in campaigns:
        results = await campaign_results_analytics(
            session, empresa_id=p["tenant"], campaign_id=campaign_id,
            period_start=_naive_utc(p["start"]), period_end=_naive_utc(p["end"]),
            include_timeline=False, include_segments=True,
            recipient_limit=MAX_RECIPIENTS_PER_CAMPAIGN,
            observed_at=_naive_utc(p["observed"]),
        )
        summary_metrics = results.get("metrics") or {}
        summaries.append({
            "campaign_id": str(campaign_id), "name": label,
            "delivery": summary_metrics, "segments": results.get("segments") or [],
            "coverage": results.get("coverage") or {"state": "unknown"},
        })

    outcome_params = {
        "tenant": str(p["tenant"]), "campaign_ids": campaign_ids,
        "period_start": _naive_utc(p["start"]), "period_end": _naive_utc(p["end"]),
        "observed_at": p["observed"], "response_window_days": p["response_window_days"],
        "reactivation_days": p["reactivation_days"], "stage_window_days": p["stage_window_days"],
        "purchase_window_days": p["purchase_window_days"], "recipient_limit": MAX_RECIPIENTS_PER_CAMPAIGN,
        "stages_json": stage_spec,
    }
    rows = (await session.execute(_OUTCOME_SQL, outcome_params)).mappings().all()
    outcome_by_campaign: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        outcome_by_campaign.setdefault(str(row["campaign_id"]), []).append(dict(row))

    campaign_segments: list[dict[str, Any]] = []
    for summary in summaries:
        campaign_id = summary["campaign_id"]
        delivery_by_segment = {str(item.get("segment") or "unknown"): item for item in summary["segments"]}
        outcomes = outcome_by_campaign.get(campaign_id, [])
        current_delivery = summary["delivery"]
        current_coverage = summary["coverage"]
        sampled = int(current_delivery.get("audience_total") or 0)
        capped = bool(current_coverage.get("capped"))
        total_attempted = total_replied = total_reactivated = total_stage = total_purchase = 0
        sampled_sent = response_unlinked_total = reactivation_unknown_history_total = 0
        reactivation_pending_total = stage_unknown_history_total = purchase_unlinked_total = 0
        sampled_matured_response = sampled_matured_stage = sampled_matured_purchase = 0
        accepted_without_timestamp_total = 0
        sampled_reactivation_denominator = 0
        response_pending_total = stage_pending_total = purchase_pending_total = 0
        campaign_sent_touches = campaign_reply_touches = 0
        for outcome in outcomes:
            segment_key = str(outcome.get("origin_segment") or "unknown")
            delivery_segment = delivery_by_segment.get(segment_key, {})
            segment_total = int(delivery_segment.get("audience_total") or outcome.get("sampled_recipients") or 0)
            attempted = int(outcome.get("attempted") or 0)
            replied = int(outcome.get("replied") or 0)
            reactivated = int(outcome.get("reactivated") or 0)
            stage_advanced = int(outcome.get("stage_advanced") or 0)
            purchase = int(outcome.get("verified_purchase") or 0)
            response_denominator = int(outcome.get("response_denominator") or 0)
            reactivation_denominator = int(outcome.get("reactivation_denominator") or 0)
            stage_denominator = int(outcome.get("stage_denominator") or 0)
            purchase_denominator = int(outcome.get("purchase_denominator") or 0)
            response_pending = int(outcome.get("response_pending") or 0)
            response_unlinked = int(outcome.get("response_unlinked") or 0)
            reactivation_pending = int(outcome.get("reactivation_pending") or 0)
            reactivation_unknown_history = int(outcome.get("reactivation_unknown_history") or 0)
            stage_pending = int(outcome.get("stage_pending") or 0)
            stage_unknown_history = int(outcome.get("stage_unknown_history") or 0)
            purchase_pending = int(outcome.get("purchase_pending") or 0)
            purchase_unlinked = int(outcome.get("purchase_unlinked") or 0)
            sent_recipients = int(outcome.get("sent_recipients") or 0)
            accepted_without_timestamp = int(outcome.get("accepted_without_timestamp") or 0)
            sampled_matured_response += response_denominator
            sampled_matured_stage += stage_denominator
            sampled_matured_purchase += purchase_denominator
            sampled_reactivation_denominator += reactivation_denominator
            response_pending_total += response_pending
            response_unlinked_total += response_unlinked
            reactivation_pending_total += reactivation_pending
            reactivation_unknown_history_total += reactivation_unknown_history
            stage_pending_total += stage_pending
            stage_unknown_history_total += stage_unknown_history
            purchase_pending_total += purchase_pending
            purchase_unlinked_total += purchase_unlinked
            sampled_sent += sent_recipients
            accepted_without_timestamp_total += accepted_without_timestamp
            total_attempted += attempted
            total_replied += replied
            total_reactivated += reactivated
            total_stage += stage_advanced
            total_purchase += purchase
            campaign_sent_touches += int(outcome.get("campaign_sent_touches") or 0)
            campaign_reply_touches += int(outcome.get("campaign_reply_touches") or 0)
            state_response = _metric_state(sampled=sent_recipients, capped=capped, denominator=response_denominator,
                                           pending_window=response_pending, incomplete=response_unlinked + accepted_without_timestamp)
            reactivation_history_known = int(outcome.get("reactivation_history_comparable") or 0)
            state_reactivation = _metric_state(
                sampled=sent_recipients, capped=capped, denominator=reactivation_denominator,
                pending_window=reactivation_pending, incomplete=reactivation_unknown_history + accepted_without_timestamp,
                known_inapplicable=sent_recipients > 0 and reactivation_history_known == sent_recipients
                and reactivation_denominator == 0 and reactivation_pending == 0,
            )
            state_stage = _metric_state(sampled=sent_recipients, capped=capped, denominator=stage_denominator,
                                        source_available=bool(stage_spec.strip() not in {"[]", ""}), pending_window=stage_pending,
                                        incomplete=stage_unknown_history + accepted_without_timestamp)
            state_purchase = _metric_state(sampled=sent_recipients, capped=capped, denominator=purchase_denominator,
                                           pending_window=purchase_pending, incomplete=purchase_unlinked + accepted_without_timestamp)
            delivery_rates = _delivery_rates(
                delivery_segment, sampled=segment_total, capped=capped,
                provider_delivery_observed=int(outcome.get("provider_delivery_observed") or 0),
                provider_read_observed=int(outcome.get("provider_read_observed") or 0),
            )
            segment = {
                "campaign_id": campaign_id, "origin": segment_key,
                "recipient_denominator": segment_total,
                "attempted": int(outcome.get("attempted") or 0),
                "delivery": {key: int(delivery_segment.get(key) or 0) for key in ("meta_accepted", "delivered", "read", "failed", "pending", "replies")},
                "delivery_rates": delivery_rates,
                "reply_after_send": _metric(replied, response_denominator, state=state_response,
                    definition=f"Inbound na conversa vinculada até {p['response_window_days']} dias após o envio; correlação temporal.",
                    coverage={"window_matured_recipients": response_denominator, "pending_window_recipients": response_pending}),
                "reactivation_after_send": _metric(reactivated, reactivation_denominator, state=state_reactivation,
                    definition=f"Resposta observada após {p['reactivation_days']} dias sem inbound anterior; correlação temporal.",
                    coverage={"eligible_dormant_recipients": int(outcome.get("reactivation_comparable") or 0), "pending_window_recipients": response_pending}),
                "stage_advance_after_send": _metric(stage_advanced, stage_denominator, state=state_stage,
                    definition=f"Evento de etapa persistido com ordem maior que a última etapa configurada antes do envio, na mesma conversa e até {p['stage_window_days']} dias; correlação temporal.",
                    coverage={"comparable_recipients": int(outcome.get("stage_comparable") or 0), "pending_window_recipients": stage_pending, "funnel_stages_configured": bool(stage_spec.strip() not in {"[]", ""})}),
                "verified_purchase_after_send": _metric(purchase, purchase_denominator, state=state_purchase,
                    definition=f"Commercial Event Purchase com revenue_verified=true no mesmo lead e até {p['purchase_window_days']} dias após o envio; não comprova causalidade.",
                    coverage={"window_matured_recipients": purchase_denominator, "pending_window_recipients": purchase_pending}),
                "campaign_reply_touch_count": int(outcome.get("campaign_reply_touches") or 0),
                "campaign_sent_touch_count": int(outcome.get("campaign_sent_touches") or 0),
                "provider_status_coverage": {"observed": int(outcome.get("provider_status_observed") or 0), "denominator": segment_total,
                                             "delivery_confirmed": int(outcome.get("provider_delivery_observed") or 0),
                                             "read_confirmed": int(outcome.get("provider_read_observed") or 0),
                                             "accepted_without_timestamp": accepted_without_timestamp,
                                             "state": "partial" if capped or int(outcome.get("provider_status_observed") or 0) < segment_total else "observed"},
                "response_coverage": {"comparable": response_denominator, "unlinked_conversation": response_unlinked,
                                      "pending_window": response_pending, "state": state_response},
                "cancelled": int(outcome.get("cancelled") or 0), "stopped": int(outcome.get("stopped") or 0),
                "failed": int(outcome.get("failed") or 0), "blocked": int(outcome.get("blocked") or 0),
                "consent_blocked": int(outcome.get("consent_blocked") or 0),
                "allowlist_blocked": int(outcome.get("allowlist_blocked") or 0),
                "template_blocked": int(outcome.get("template_blocked") or 0),
                "other_recorded_failure": int(outcome.get("other_recorded_failure") or 0),
                "current_opt_out": int(outcome.get("current_opt_out") or 0),
                "recipient_status_coverage": {
                    "unknown": int(outcome.get("unknown_status") or 0),
                    "denominator": segment_total,
                    "state": "partial" if capped or int(outcome.get("unknown_status") or 0) else ("observed" if segment_total else "unknown"),
                },
                "state": "partial" if capped or accepted_without_timestamp > 0
                or int(outcome.get("unknown_status") or 0) > 0
                or delivery_rates["delivered"]["state"] in {"partial", "unknown"}
                or delivery_rates["read"]["state"] in {"partial", "unknown"}
                or state_response in {"partial", "unknown"} or state_reactivation in {"partial", "unknown"}
                or state_stage in {"partial", "unknown"} or state_purchase in {"partial", "unknown"}
                else ("observed" if segment_total else "unknown"),
            }
            campaign_segments.append(segment)
        snapshot_data = (await session.execute(text("""
            SELECT CASE WHEN jsonb_typeof(s.inclusions_json) = 'array'
                        THEN jsonb_array_length(s.inclusions_json) ELSE NULL END AS selected_snapshot,
                   CASE WHEN s.filters_json->'audience_resolution'->'counts'->>'optout_removed' ~ '^[0-9]{1,9}$'
                        THEN (s.filters_json->'audience_resolution'->'counts'->>'optout_removed')::integer ELSE NULL END AS snapshot_opt_out,
                   CASE WHEN s.filters_json->'audience_resolution'->'counts'->>'blocked_removed' ~ '^[0-9]{1,9}$'
                        THEN (s.filters_json->'audience_resolution'->'counts'->>'blocked_removed')::integer ELSE NULL END AS snapshot_blocked,
                   CASE WHEN s.filters_json->'audience_resolution'->'counts'->>'duplicates_removed' ~ '^[0-9]{1,9}$'
                        THEN (s.filters_json->'audience_resolution'->'counts'->>'duplicates_removed')::integer ELSE NULL END AS snapshot_duplicates
            FROM campaign_preparation_snapshots s
            WHERE s.empresa_id = :tenant AND s.campaign_id = :campaign_id
              AND s.created_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
            ORDER BY s.created_at DESC LIMIT 1
        """), {"tenant": p["tenant"], "campaign_id": uuid.UUID(campaign_id), "observed_at": p["observed"]})).mappings().first()
        snapshot_data = dict(snapshot_data) if snapshot_data else None
        summary.update({
            "eligible_snapshot": {"value": int(snapshot_data["selected_snapshot"]) if snapshot_data and snapshot_data.get("selected_snapshot") is not None else None,
                                  "state": "observed" if snapshot_data and snapshot_data.get("selected_snapshot") is not None else "unknown",
                                  "definition": "Contagem integral de destinatários incluídos no snapshot congelado antes da materialização; pode não corresponder ao período filtrado."},
            "attempted": {"value": total_attempted, "denominator": sampled,
                          "state": "partial" if capped else ("observed" if sampled else "unknown"),
                          "definition": "Destinatários com tentativa/outbox persistida; não equivale a envio aceito pelo provider."},
            "response_after_send": _metric(total_replied, sampled_matured_response,
                state=_metric_state(sampled=sampled_sent, capped=capped, denominator=sampled_matured_response,
                                    pending_window=response_pending_total, incomplete=response_unlinked_total + accepted_without_timestamp_total),
                definition=f"Inbound posterior na mesma conversa até {p['response_window_days']} dias; correlação temporal."),
            "reactivation_after_send": _metric(total_reactivated, sampled_reactivation_denominator,
                state=_metric_state(sampled=sampled_sent, capped=capped, denominator=sampled_reactivation_denominator,
                                    pending_window=reactivation_pending_total,
                                    incomplete=reactivation_unknown_history_total + accepted_without_timestamp_total,
                                    known_inapplicable=sampled_sent > 0 and reactivation_unknown_history_total == 0
                                    and sampled_reactivation_denominator == 0 and reactivation_pending_total == 0),
                definition=f"Inbound após intervalo sem atividade de {p['reactivation_days']} dias e depois do envio; correlação temporal."),
            "stage_advance_after_send": _metric(total_stage, sampled_matured_stage,
                state=_metric_state(sampled=sampled_sent, capped=capped, denominator=sampled_matured_stage,
                                    source_available=bool(stage_spec.strip() not in {"[]", ""}), pending_window=stage_pending_total,
                                    incomplete=stage_unknown_history_total + accepted_without_timestamp_total),
                definition=f"Etapa configurada avançada na mesma conversa até {p['stage_window_days']} dias; correlação temporal, não causalidade."),
            "verified_purchase_after_send": _metric(total_purchase, sampled_matured_purchase,
                state=_metric_state(sampled=sampled_sent, capped=capped, denominator=sampled_matured_purchase,
                                    pending_window=purchase_pending_total, incomplete=purchase_unlinked_total + accepted_without_timestamp_total),
                definition=f"Purchase persistida com revenue_verified=true no mesmo lead até {p['purchase_window_days']} dias; não atribuída causalmente."),
            "opt_out": {"snapshot_exclusions": int(snapshot_data["snapshot_opt_out"]) if snapshot_data and snapshot_data.get("snapshot_opt_out") is not None else None,
                        "current_recipients": sum(int(row.get("current_opt_out") or 0) for row in outcomes),
                        "state": "observed" if snapshot_data and snapshot_data.get("snapshot_opt_out") is not None else "partial",
                        "definition": "Exclusão por opt-out no snapshot e consentimento restritivo atual, fontes/tempos distintos."},
            "campaign_assistance": {"sent_touches": campaign_sent_touches, "reply_touches": campaign_reply_touches,
                                    "state": "partial" if capped or campaign_sent_touches < sampled else ("observed" if sampled else "unknown"),
                                    "definition": "Attribution touches campaign_sent/campaign_reply ligados explicitamente ao destinatário; assistência não é origem de aquisição."},
            "eligibility_coverage": {"blocked_snapshot": int(snapshot_data["snapshot_blocked"]) if snapshot_data and snapshot_data.get("snapshot_blocked") is not None else None,
                                     "duplicates_snapshot": int(snapshot_data["snapshot_duplicates"]) if snapshot_data and snapshot_data.get("snapshot_duplicates") is not None else None,
                                     "state": "partial" if capped or not snapshot_data or snapshot_data.get("snapshot_blocked") is None or snapshot_data.get("snapshot_duplicates") is None else "observed"},
            "failed_cancelled": {"failed": sum(int(row.get("failed") or 0) for row in outcomes),
                                 "cancelled": sum(int(row.get("cancelled") or 0) for row in outcomes),
                                 "stopped": sum(int(row.get("stopped") or 0) for row in outcomes),
                                 "blocked": sum(int(row.get("blocked") or 0) for row in outcomes),
                                 "reasons": {
                                     "consent_block": sum(int(row.get("consent_blocked") or 0) for row in outcomes),
                                     "allowlist_block": sum(int(row.get("allowlist_blocked") or 0) for row in outcomes),
                                     "template_missing": sum(int(row.get("template_blocked") or 0) for row in outcomes),
                                     "other_recorded_failure": sum(int(row.get("other_recorded_failure") or 0) for row in outcomes),
                                 },
                                 "pending": sum(int(row.get("pending") or 0) for row in outcomes),
                                 "unknown_status": sum(int(row.get("unknown_status") or 0) for row in outcomes),
                                 "denominator": sampled,
                                 "state": "partial" if capped or any(int(row.get("unknown_status") or 0) for row in outcomes)
                                 else ("observed" if sampled else "unknown")},
        })
        provider_status_observed = sum(int(row.get("provider_status_observed") or 0) for row in outcomes)
        provider_delivery_observed = sum(int(row.get("provider_delivery_observed") or 0) for row in outcomes)
        provider_read_observed = sum(int(row.get("provider_read_observed") or 0) for row in outcomes)
        summary["delivery_rates"] = _delivery_rates(
            current_delivery, sampled=sampled, capped=capped,
            provider_delivery_observed=provider_delivery_observed,
            provider_read_observed=provider_read_observed,
        )
        summary["response_after_send"]["coverage"] = {
            "matured_comparable_recipients": sampled_matured_response,
            "pending_window_recipients": response_pending_total,
            "unlinked_conversation_recipients": response_unlinked_total,
            "accepted_without_timestamp": accepted_without_timestamp_total,
        }
        coverage_states = [
            summary["eligible_snapshot"]["state"], summary["attempted"]["state"],
            summary["delivery_rates"]["accepted"]["state"], summary["delivery_rates"]["delivered"]["state"],
            summary["delivery_rates"]["read"]["state"], summary["response_after_send"]["state"],
            summary["reactivation_after_send"]["state"], summary["stage_advance_after_send"]["state"],
            summary["verified_purchase_after_send"]["state"], summary["opt_out"]["state"],
            summary["campaign_assistance"]["state"], summary["eligibility_coverage"]["state"],
            summary["failed_cancelled"]["state"],
        ]
        summary["coverage"] = {
            **current_coverage,
            "state": "partial" if capped or any(value in {"partial", "unknown"} for value in coverage_states) else "observed",
            "provider_status_recipients": provider_status_observed,
            "provider_delivery_confirmed_recipients": provider_delivery_observed,
            "provider_read_confirmed_recipients": provider_read_observed,
            "accepted_without_timestamp": accepted_without_timestamp_total,
            "provider_accepted_recipients": int(current_delivery.get("meta_accepted") or 0),
            "sampled_recipients": sampled,
        }
        summary["funnel"] = _funnel(summary)

    return {
        "window": _window(p), "campaigns": summaries, "segments": campaign_segments,
        "coverage": {
            "state": "partial" if any(item["coverage"].get("state") != "observed" for item in summaries) else "observed",
            "sample_limit_per_campaign": MAX_RECIPIENTS_PER_CAMPAIGN,
            "campaigns_compared": len(summaries), "campaign_comparison_limit": MAX_CAMPAIGNS,
            "origin_segments": "Earliest persisted attribution touch before campaign dispatch; absence is unknown, not a new source.",
            "outcomes": "Reply/reactivation/stage/purchase use persisted evidence after dispatch; temporal correlation only.",
            "notes": ["Campanhas sem snapshot têm elegibilidade unknown.",
                      "Segmentos de origem desconhecidos permanecem explícitos.",
                      "Registros além do limite por campanha não entram no denominador e deixam o estado partial."],
        },
        "definitions": _definitions(),
    }


def _funnel(summary: dict[str, Any]) -> list[dict[str, Any]]:
    delivery = summary.get("delivery") or {}
    entries = [
        ("eligible_snapshot", "Elegíveis no snapshot", summary.get("eligible_snapshot", {}).get("value"), None, summary.get("eligible_snapshot", {}).get("state")),
        ("attempted", "Tentativa persistida", summary.get("attempted", {}).get("value"), summary.get("attempted", {}).get("denominator"), summary.get("attempted", {}).get("state")),
        ("sent", "Aceitos pelo provider", delivery.get("meta_accepted"), summary.get("delivery_rates", {}).get("accepted", {}).get("denominator"), summary.get("delivery_rates", {}).get("accepted", {}).get("state")),
        ("delivered", "Entregues", delivery.get("delivered"), summary.get("delivery_rates", {}).get("delivered", {}).get("denominator"), summary.get("delivery_rates", {}).get("delivered", {}).get("state")),
        ("read", "Lidos", delivery.get("read"), summary.get("delivery_rates", {}).get("read", {}).get("denominator"), summary.get("delivery_rates", {}).get("read", {}).get("state")),
        ("reply", "Respostas posteriores", summary.get("response_after_send", {}).get("numerator"), summary.get("response_after_send", {}).get("denominator"), summary.get("response_after_send", {}).get("state")),
        ("reactivation", "Reativações observadas", summary.get("reactivation_after_send", {}).get("numerator"), summary.get("reactivation_after_send", {}).get("denominator"), summary.get("reactivation_after_send", {}).get("state")),
        ("stage_advance", "Avanços comerciais observados", summary.get("stage_advance_after_send", {}).get("numerator"), summary.get("stage_advance_after_send", {}).get("denominator"), summary.get("stage_advance_after_send", {}).get("state")),
        ("purchase", "Compras verificadas após envio", summary.get("verified_purchase_after_send", {}).get("numerator"), summary.get("verified_purchase_after_send", {}).get("denominator"), summary.get("verified_purchase_after_send", {}).get("state")),
    ]
    return [{"key": key, "label": label, "value": value, "denominator": denominator, "state": state or "unknown"}
            for key, label, value, denominator, state in entries]


def _window(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": p["start"].isoformat(), "end": p["end"].isoformat(),
        "observed_at": p["observed"].isoformat(),
        "response_window_days": p["response_window_days"],
        "reactivation_days": p["reactivation_days"],
        "stage_window_days": p["stage_window_days"],
        "purchase_window_days": p["purchase_window_days"],
        "timezone": "UTC", "unit": "campaign_recipient; downstream outcomes deduplicated by recipient within each campaign",
    }


def _definitions() -> dict[str, str]:
    return {
        "eligible_snapshot": "Contagem integral das inclusions do snapshot congelado; pode não corresponder ao período filtrado; unknown sem snapshot persistido.",
        "attempted": "Destinatário com tentativa/outbox persistida; não é confirmação de envio.",
        "sent": "Aceite/envio conforme o contrato existente de Campaign Central (WAMID, tentativa aceita ou status sent/delivered/read).",
        "delivered": "Status delivered/read persistido pelo provider ou marco equivalente do destinatário.",
        "read": "Status read persistido pelo provider.",
        "reply": "Mensagem inbound posterior na mesma conversa, deduplicada por destinatário.",
        "reactivation": "Resposta depois do envio com inbound prévio na conversa há pelo menos o intervalo selecionado.",
        "stage_advance": "Commercial Event posterior com etapa de ordem maior que a etapa configurada anterior ao envio, na mesma conversa.",
        "purchase": "Commercial Event Purchase com revenue_verified=true; valor posterior não prova causa da campanha.",
        "origin": "Primeiro touch persistido de aquisição anterior ao envio; assistência da campanha fica em dimensão separada.",
        "opt_out": "Exclusão explícita no snapshot e consentimento atual são evidências distintas; bloqueio não é contado automaticamente como opt-out.",
        "state": "observed = cobertura presente (inclusive zero); partial = amostra/associação incompleta; unknown = fonte ausente; not_applicable = denominador zero confirmado.",
    }


async def recipients(session: AsyncSession, *, empresa_id: Any, campaign_id: str,
                     start=None, end=None, observed_at=None, segment: str | None = None,
                     response_window_days: int = 7, reactivation_days: int = 7,
                     stage_window_days: int = 30, purchase_window_days: int = 30,
                     limit: int = 25, offset: int = 0, now: datetime | None = None) -> dict[str, Any]:
    p = filters(empresa_id=empresa_id, start=start, end=end, observed_at=observed_at,
                campaign_ids=[campaign_id], response_window_days=response_window_days,
                reactivation_days=reactivation_days, stage_window_days=stage_window_days,
                purchase_window_days=purchase_window_days, now=now)
    if isinstance(limit, bool) or not 1 <= limit <= MAX_DRILLDOWN_PAGE or isinstance(offset, bool) or not 0 <= offset <= MAX_DRILLDOWN_OFFSET:
        raise ValueError("invalid pagination")
    if segment is not None and segment not in ORIGIN_SEGMENTS:
        raise ValueError("invalid origin segment")
    selected = await _selected_campaigns(session, p)
    if not selected:
        raise LookupError("campaign unavailable")
    stage_rows = await load_funnel_configuration(session, {"tenant": str(p["tenant"]), "stage": None, "bottleneck": None})
    statement = text(r"""
        WITH candidates AS MATERIALIZED (
            SELECT r.id, r.empresa_id, r.campanha_id, r.lead_id, r.contact_id, r.conversation_id,
                   r.conexao_id, r.provider, r.channel, r.external_message_id, r.updated_at,
                   r.status, r.error_code, r.sent_at, r.created_at, r.delivered_at, r.read_at,
                   r.failed_at, r.cancelled_at
            FROM campaign_recipients r
            WHERE r.empresa_id = :tenant AND r.campanha_id = :campaign_id
              AND coalesce(r.sent_at,r.created_at) >= :period_start
              AND coalesce(r.sent_at,r.created_at) < :period_end
              AND coalesce(r.sent_at,r.created_at) <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
            ORDER BY coalesce(r.sent_at,r.created_at), r.id
            LIMIT :sample_limit
        ), candidate_state AS MATERIALIZED (
            SELECT r.*,
                   CASE WHEN r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.status
                        ELSE coalesce((
                            SELECT state.status FROM (VALUES
                                (r.sent_at, 'sent', 1), (r.delivered_at, 'delivered', 2),
                                (r.read_at, 'read', 3), (r.failed_at, 'failed', 4),
                                (r.cancelled_at, 'cancelled', 5)
                            ) AS state(status_at, status, priority)
                            WHERE state.status_at IS NOT NULL
                              AND state.status_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                            ORDER BY state.status_at DESC, state.priority DESC LIMIT 1
                        ), status_event.last_status, 'unknown')
                   END AS effective_status,
                   CASE WHEN r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                        THEN r.error_code END AS effective_error_code,
                   status_event.status_accepted, status_event.event_sent_at,
                   status_event.event_delivered_at, status_event.event_read_at
            FROM candidates r
            LEFT JOIN LATERAL (
                SELECT bool_or(ev.status IN ('sent', 'delivered', 'read')) AS status_accepted,
                       (array_agg(ev.status ORDER BY coalesce(ev.occurred_at, ev.created_at) DESC,
                                  CASE ev.status WHEN 'read' THEN 4 WHEN 'delivered' THEN 3 WHEN 'sent' THEN 2 ELSE 1 END DESC)
                         FILTER (WHERE ev.status IS NOT NULL))[1] AS last_status,
                       min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'sent') AS event_sent_at,
                       min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'delivered') AS event_delivered_at,
                       min(coalesce(ev.occurred_at, ev.created_at)) FILTER (WHERE ev.status = 'read') AS event_read_at
                FROM message_status_events ev
                WHERE ev.empresa_id = r.empresa_id AND ev.conexao_id = r.conexao_id
                  AND ev.provider = r.provider AND ev.channel = r.channel
                  AND ev.external_message_id = r.external_message_id
                  AND coalesce(ev.occurred_at, ev.created_at) <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
            ) status_event ON true
        ), candidate_evidence AS MATERIALIZED (
            SELECT r.*,
                   coalesce(
                       CASE WHEN r.sent_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.sent_at END,
                       r.event_sent_at, r.event_delivered_at, r.event_read_at,
                       CASE WHEN r.delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.delivered_at END,
                       CASE WHEN r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN r.read_at END,
                       attempt.accepted_at
                   ) AS effective_sent_at,
                   (
                       r.external_message_id IS NOT NULL AND r.updated_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                       OR r.effective_status IN ('sent', 'delivered', 'read')
                       OR r.sent_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                       OR r.delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                       OR r.read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                       OR r.status_accepted OR attempt.is_accepted
                   ) AS is_accepted
            FROM candidate_state r
            LEFT JOIN LATERAL (
                SELECT bool_or(ma.result_status IN ('accepted', 'sent', 'completed') AND ma.external_message_id IS NOT NULL) AS is_accepted,
                       min(ma.finished_at) FILTER (WHERE ma.result_status IN ('accepted', 'sent', 'completed') AND ma.external_message_id IS NOT NULL) AS accepted_at
                FROM message_outbox mo
                JOIN message_attempts ma ON ma.empresa_id = mo.empresa_id AND ma.outbox_id = mo.id
                WHERE mo.empresa_id = r.empresa_id AND mo.campaign_recipient_id = r.id
                  AND mo.provider = r.provider AND mo.conexao_id = r.conexao_id AND mo.channel = r.channel
                  AND ma.provider = r.provider AND ma.conexao_id = r.conexao_id
                  AND ma.finished_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
            ) attempt ON true
        ), stage_catalog AS (
            SELECT * FROM jsonb_to_recordset(CAST(:stages_json AS jsonb)) AS s(key text, "order" integer)
        ), scoped AS MATERIALIZED (
            SELECT r.*,
                   CASE WHEN r.is_accepted AND r.effective_sent_at IS NULL THEN 'unknown'
                        ELSE coalesce(origin.event_kind, 'unknown') END AS origin_segment,
                   EXISTS (
                     SELECT 1 FROM mensagens_historico mh
                     WHERE r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.conversation_id IS NOT NULL
                       AND mh.empresa_id = r.empresa_id AND mh.channel_conversation_id = r.conversation_id
                       AND (mh.direction = 'inbound' OR (mh.direction IS NULL AND mh.from_me = false))
                       AND lower(coalesce(mh.tipo_mensagem,'')) NOT IN ('status','reaction')
                       AND lower(coalesce(mh.metadata_json->>'event_kind','')) NOT IN ('status','reaction')
                       AND mh.criado_em > r.effective_sent_at
                       AND mh.criado_em <= r.effective_sent_at + (:response_window_days * interval '1 day')
                       AND mh.criado_em <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                   ) AS replied,
                   (r.is_accepted AND r.effective_sent_at IS NOT NULL
                    AND prior_inbound.last_inbound_at <= r.effective_sent_at - (:reactivation_days * interval '1 day')
                    AND EXISTS (
                      SELECT 1 FROM mensagens_historico resumed
                      WHERE r.conversation_id IS NOT NULL
                        AND resumed.empresa_id = r.empresa_id AND resumed.channel_conversation_id = r.conversation_id
                        AND (resumed.direction = 'inbound' OR (resumed.direction IS NULL AND resumed.from_me = false))
                        AND lower(coalesce(resumed.tipo_mensagem,'')) NOT IN ('status','reaction')
                        AND lower(coalesce(resumed.metadata_json->>'event_kind','')) NOT IN ('status','reaction')
                        AND resumed.criado_em > r.effective_sent_at
                        AND resumed.criado_em <= r.effective_sent_at + (:response_window_days * interval '1 day')
                        AND resumed.criado_em <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'
                    )) AS reactivated,
                   EXISTS (
                     SELECT 1 FROM commercial_events after_stage
                     JOIN stage_catalog after_order ON after_order.key = after_stage.funnel_stage_id
                     WHERE r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.conversation_id IS NOT NULL
                       AND after_stage.empresa_id = r.empresa_id AND after_stage.lead_id = r.lead_id
                       AND after_stage.conversation_id = r.conversation_id
                       AND after_stage.occurred_at > r.effective_sent_at AT TIME ZONE 'UTC'
                       AND after_stage.occurred_at <= (r.effective_sent_at AT TIME ZONE 'UTC') + (:stage_window_days * interval '1 day')
                       AND after_stage.occurred_at <= CAST(:observed_at AS timestamptz)
                       AND prior_stage.stage_order IS NOT NULL
                       AND after_order."order" > prior_stage.stage_order
                   ) AS stage_advanced,
                   EXISTS (
                     SELECT 1 FROM commercial_events e
                     WHERE r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.lead_id IS NOT NULL
                       AND e.empresa_id = r.empresa_id AND e.lead_id = r.lead_id
                       AND e.event_name = 'Purchase' AND e.revenue_verified = true
                       AND e.occurred_at > r.effective_sent_at AT TIME ZONE 'UTC'
                       AND e.occurred_at <= (r.effective_sent_at AT TIME ZONE 'UTC') + (:purchase_window_days * interval '1 day')
                       AND e.occurred_at <= CAST(:observed_at AS timestamptz)
                   ) AS verified_purchase,
                   EXISTS (
                     SELECT 1 FROM attribution_touches t WHERE t.empresa_id = r.empresa_id
                       AND t.campaign_recipient_id = r.id AND t.outbound_campaign_id = r.campanha_id AND t.event_kind = 'campaign_sent'
                       AND t.occurred_at <= CAST(:observed_at AS timestamptz)
                   ) AS campaign_sent_touch,
                   EXISTS (
                     SELECT 1 FROM attribution_touches t WHERE t.empresa_id = r.empresa_id
                       AND t.campaign_recipient_id = r.id AND t.outbound_campaign_id = r.campanha_id AND t.event_kind = 'campaign_reply'
                       AND t.occurred_at <= CAST(:observed_at AS timestamptz)
                   ) AS campaign_reply_touch
            FROM candidate_evidence r
            LEFT JOIN LATERAL (
                SELECT t.event_kind FROM attribution_touches t
                WHERE t.empresa_id = r.empresa_id
                  AND t.event_kind IN ('ctwa_referral','tracked_link_click','manual_touch','unknown_source')
                  AND t.occurred_at <= coalesce(r.effective_sent_at,r.created_at) AT TIME ZONE 'UTC'
                  AND ((r.lead_id IS NOT NULL AND t.lead_id = r.lead_id)
                    OR (r.lead_id IS NULL AND r.contact_id IS NOT NULL AND t.contact_id = r.contact_id)
                    OR (r.lead_id IS NULL AND r.contact_id IS NULL AND r.conversation_id IS NOT NULL AND t.conversation_id = r.conversation_id))
                ORDER BY t.occurred_at,t.id LIMIT 1
            ) origin ON true
            LEFT JOIN LATERAL (
                SELECT max(mh.criado_em) AS last_inbound_at
                FROM mensagens_historico mh
                WHERE r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.conversation_id IS NOT NULL
                  AND mh.empresa_id = r.empresa_id AND mh.channel_conversation_id = r.conversation_id
                  AND (mh.direction = 'inbound' OR (mh.direction IS NULL AND mh.from_me = false))
                  AND lower(coalesce(mh.tipo_mensagem,'')) NOT IN ('status','reaction')
                  AND lower(coalesce(mh.metadata_json->>'event_kind','')) NOT IN ('status','reaction')
                  AND mh.criado_em < r.effective_sent_at
            ) prior_inbound ON true
            LEFT JOIN LATERAL (
                SELECT s."order" AS stage_order
                FROM commercial_events e JOIN stage_catalog s ON s.key = e.funnel_stage_id
                WHERE r.is_accepted AND r.effective_sent_at IS NOT NULL AND r.conversation_id IS NOT NULL
                  AND e.empresa_id = r.empresa_id AND e.lead_id = r.lead_id AND e.conversation_id = r.conversation_id
                  AND e.occurred_at < r.effective_sent_at AT TIME ZONE 'UTC'
                ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1
            ) prior_stage ON true
        )
        SELECT id AS recipient_id, lead_id, conversation_id, effective_status AS status,
               effective_sent_at AS sent_at,
               CASE WHEN delivered_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN delivered_at END AS delivered_at,
               CASE WHEN read_at <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC' THEN read_at END AS read_at,
               coalesce(is_accepted, false) AS is_accepted,
               (coalesce(is_accepted, false) AND effective_sent_at IS NULL) AS acceptance_time_unknown,
               origin_segment, replied, reactivated, stage_advanced, verified_purchase,
               campaign_sent_touch, campaign_reply_touch
        FROM scoped
        WHERE (CAST(:segment AS text) IS NULL OR origin_segment = CAST(:segment AS text))
        ORDER BY coalesce(sent_at, :period_start), recipient_id
        LIMIT :limit OFFSET :offset
    """)
    result = (await session.execute(statement, {
        "tenant": p["tenant"], "campaign_id": uuid.UUID(campaign_id),
        "period_start": _naive_utc(p["start"]), "period_end": _naive_utc(p["end"]),
        "segment": segment, "limit": limit, "offset": offset,
        "observed_at": p["observed"], "response_window_days": response_window_days,
        "reactivation_days": reactivation_days, "stage_window_days": stage_window_days,
        "purchase_window_days": purchase_window_days,
        "sample_limit": MAX_RECIPIENTS_PER_CAMPAIGN,
        "stages_json": _safe_stage_spec(stage_rows),
    })).mappings().all()
    return {
        "campaign_id": campaign_id, "items": [dict(row) for row in result],
        "pagination": {"limit": limit, "offset": offset, "returned": len(result),
                       "has_more": len(result) == limit and offset + len(result) < MAX_RECIPIENTS_PER_CAMPAIGN,
                       "sample_limit": MAX_RECIPIENTS_PER_CAMPAIGN},
        "privacy": "Somente IDs operacionais e marcos; sem telefone, nome ou conteúdo de mensagem.",
        "definitions": _definitions(),
    }
