"""Read-only follow-up operations analytics (A09).

Only persisted execution, provider-status, inbound-message and commercial-event
records are used. A temporal response or stage event is not attributed as being
caused by a follow-up. Legacy logs are deliberately reported separately because
they cannot be safely joined to a modern execution without risking duplicates.
"""
from __future__ import annotations

import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select

from db.models import (
    CanonicalCommercialEvent,
    ConfigFollowUp,
    Empresa,
    FollowupExecution,
    LeadFollowUpLog,
    CRMLead,
    MensagemHistorico,
    MessageStatusEvent,
)

MAX_EXECUTIONS = 20_000
MAX_STAGE_EVENTS = 100_000
MAX_PAGE_SIZE = 100
MODES = {"conversation_resume", "scheduled_message"}
STATUSES = {"scheduled", "retry", "processing", "locked", "sent", "delivered", "read", "cancelled", "stopped", "failed"}
CHANNELS = {"whatsapp", "instagram", "web", "sms", "telegram"}
REASON_CODES = {
    "missing_context", "human_takeover", "opt_out", "inbound_received", "outbound_replaced",
    "outside_business_hours_rescheduled", "outside_business_hours", "delivery_policy_blocked",
    "audio_requires_freeform", "ai_followup_generation_failed", "audio_asset_missing",
    "audio_asset_unavailable", "audio_file_missing", "template_nao_configurado",
    "template_does_not_authorize_audio", "dispatch_failed", "processing_retry",
    "processing_retry_exhausted",
}


def enabled() -> bool:
    return os.getenv("FOLLOWUP_ANALYTICS_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}


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


def filters(*, empresa_id, start=None, end=None, observed_at=None, response_window_days=7,
            reactivation_days=7, mode=None, channel=None, status=None, stop_reason=None, now=None):
    tenant = uuid.UUID(str(empresa_id))
    now = _utc(now or datetime.now(timezone.utc))
    observed = _utc(observed_at, fallback=now)
    end = _utc(end, fallback=observed)
    start = _utc(start, fallback=end - timedelta(days=29))
    if start >= end or end > now + timedelta(days=1) or observed > now or start > observed or (end - start).days > 366:
        raise ValueError("invalid follow-up analytics window")
    for value, low, high in ((response_window_days, 1, 90), (reactivation_days, 1, 90)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError("invalid follow-up analytics window")
    if mode is not None and mode not in MODES:
        raise ValueError("invalid follow-up mode")
    if channel is not None and channel not in CHANNELS:
        raise ValueError("invalid follow-up channel")
    if status is not None and status not in STATUSES:
        raise ValueError("invalid follow-up status")
    if stop_reason is not None and stop_reason not in REASON_CODES:
        raise ValueError("invalid follow-up stop reason")
    return {
        "tenant": tenant,
        "start": start,
        "end": end,
        "observed": observed,
        "response_window_days": response_window_days,
        "reactivation_days": reactivation_days,
        "mode": mode,
        "channel": channel,
        "status": status,
        "stop_reason": stop_reason,
    }


def _naive_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _rate(numerator, denominator, *, state=None, coverage=None, definition=""):
    if state is None:
        state = "observed" if denominator else "unknown"
    if state not in {"observed", "partial", "unknown", "not_applicable"}:
        state = "unknown"
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if numerator is not None and denominator else None,
        "coverage": coverage,
        "state": state,
        "definition": definition,
    }


def _snapshot_value(snapshot, *path):
    value = snapshot if isinstance(snapshot, dict) else {}
    for part in path:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _row_mode(row):
    value = _snapshot_value(row.policy_snapshot_json, "mode") or _snapshot_value(row.policy_snapshot_json, "followup_delivery_decision", "mode")
    if value in MODES:
        return value
    anchor = _snapshot_value(row.policy_snapshot_json, "anchor_type") or row.trigger
    return "conversation_resume" if anchor == "last_agent_message" else "scheduled_message"


def _eligibility_decision(snapshot):
    value = _snapshot_value(snapshot, "followup_delivery_decision", "eligible")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _safe_reason(value):
    if not value:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in REASON_CODES else "other_recorded_reason"


def _inbound_predicate(model):
    return or_(
        model.direction == "inbound",
        and_(or_(model.direction.is_(None), model.direction.notin_(("inbound", "outbound"))), model.from_me.is_(False)),
    )


def _base_query(p):
    execution = FollowupExecution
    inbound = MensagemHistorico
    inbound_kind = and_(
        _inbound_predicate(inbound),
        func.lower(func.coalesce(inbound.tipo_mensagem, "")).notin_(("status", "reaction")),
        func.lower(func.coalesce(inbound.metadata_json["event_kind"].astext, "")).notin_(("status", "reaction")),
    )
    conversation_activity = and_(
        or_(
            inbound.direction.in_(("inbound", "outbound")),
            inbound.from_me.is_not(None),
        ),
        func.lower(func.coalesce(inbound.tipo_mensagem, "")).notin_(("status", "reaction")),
        func.lower(func.coalesce(inbound.metadata_json["event_kind"].astext, "")).notin_(("status", "reaction")),
    )
    response_at = (
        select(func.min(inbound.criado_em))
        .where(
            inbound.empresa_id == execution.empresa_id,
            inbound.lead_id == execution.lead_id,
            inbound.channel_conversation_id == execution.conversation_id,
            inbound.criado_em > execution.sent_at,
            inbound.criado_em <= execution.sent_at + timedelta(days=p["response_window_days"]),
            inbound.criado_em <= _naive_utc(p["observed"]),
            inbound_kind,
        )
        .correlate(execution)
        .scalar_subquery()
    )
    previous_activity = (
        select(func.max(inbound.criado_em))
        .where(
            inbound.empresa_id == execution.empresa_id,
            inbound.lead_id == execution.lead_id,
            inbound.channel_conversation_id == execution.conversation_id,
            inbound.criado_em < execution.sent_at,
            conversation_activity,
        )
        .correlate(execution)
        .scalar_subquery()
    )
    delivered = exists(
        select(MessageStatusEvent.id).where(
            MessageStatusEvent.empresa_id == execution.empresa_id,
            MessageStatusEvent.conexao_id == execution.conexao_id,
            MessageStatusEvent.provider == execution.provider,
            MessageStatusEvent.channel == execution.channel,
            execution.external_message_id.is_not(None),
            MessageStatusEvent.external_message_id == execution.external_message_id,
            MessageStatusEvent.status.in_(("delivered", "read")),
            or_(
                MessageStatusEvent.occurred_at <= _naive_utc(p["observed"]),
                and_(MessageStatusEvent.occurred_at.is_(None), MessageStatusEvent.created_at <= _naive_utc(p["observed"])),
            ),
        )
    )
    conversion_id = (
        select(CanonicalCommercialEvent.id).where(
            CanonicalCommercialEvent.empresa_id == execution.empresa_id,
            CanonicalCommercialEvent.lead_id == execution.lead_id,
            CanonicalCommercialEvent.conversation_id == execution.conversation_id,
            CanonicalCommercialEvent.event_name == "Purchase",
            CanonicalCommercialEvent.revenue_verified.is_(True),
            CanonicalCommercialEvent.provider.in_(("mercado_pago", "stripe", "paypal", "asaas", "pagarme", "pagseguro", "efi", "adyen")),
            CanonicalCommercialEvent.payment_id.is_not(None),
            func.length(func.trim(CanonicalCommercialEvent.payment_id)) > 0,
            CanonicalCommercialEvent.currency.op("~")("^[A-Z]{3}$"),
            CanonicalCommercialEvent.value >= 0,
            CanonicalCommercialEvent.value < 1_000_000_000_000_000,
            or_(
                CanonicalCommercialEvent.evidence_json["provenance"].astext == "provider_verified_payment_status",
                CanonicalCommercialEvent.metadata_json["provenance"].astext == "provider_verified_payment_status",
                CanonicalCommercialEvent.evidence_json["provider_status_verified"].astext == "true",
                CanonicalCommercialEvent.metadata_json["provider_status_verified"].astext == "true",
            ),
            func.coalesce(CanonicalCommercialEvent.evidence_json["environment"].astext, "production") == "production",
            func.coalesce(CanonicalCommercialEvent.metadata_json["environment"].astext, "production") == "production",
            func.lower(func.coalesce(CanonicalCommercialEvent.evidence_json["simulated"].astext, "false")).notin_(("true", "1", "yes", "on")),
            func.lower(func.coalesce(CanonicalCommercialEvent.metadata_json["simulated"].astext, "false")).notin_(("true", "1", "yes", "on")),
            func.lower(func.coalesce(CanonicalCommercialEvent.evidence_json["is_simulated"].astext, "false")).notin_(("true", "1", "yes", "on")),
            func.lower(func.coalesce(CanonicalCommercialEvent.metadata_json["is_simulated"].astext, "false")).notin_(("true", "1", "yes", "on")),
            func.timezone("UTC", CanonicalCommercialEvent.occurred_at) > execution.sent_at,
            func.timezone("UTC", CanonicalCommercialEvent.occurred_at) <= execution.sent_at + timedelta(days=p["response_window_days"]),
            func.timezone("UTC", CanonicalCommercialEvent.occurred_at) <= _naive_utc(p["observed"]),
        ).order_by(CanonicalCommercialEvent.occurred_at, CanonicalCommercialEvent.id).limit(1)
        .correlate(execution)
        .scalar_subquery()
    )
    mode_snapshot = execution.policy_snapshot_json["mode"].astext
    nested_mode_snapshot = execution.policy_snapshot_json["followup_delivery_decision"]["mode"].astext
    mode_expr = case(
        (mode_snapshot.in_(tuple(sorted(MODES))), mode_snapshot),
        (nested_mode_snapshot.in_(tuple(sorted(MODES))), nested_mode_snapshot),
        (execution.trigger == "last_agent_message", "conversation_resume"),
        else_="scheduled_message",
    ).label("analytics_mode")
    stmt = (
        select(
            execution.id, execution.empresa_id, execution.lead_id, execution.conversation_id,
            execution.followup_id, execution.channel,
            execution.trigger, execution.policy_snapshot_json, execution.status, execution.stop_reason,
            execution.scheduled_at, execution.created_at, execution.processing_started_at,
            execution.sent_at, execution.completed_at, execution.cancelled_at, execution.failed_at,
            execution.external_message_id, execution.provider, mode_expr,
            response_at.label("response_at"), previous_activity.label("previous_activity_at"),
            delivered.label("provider_delivery_confirmed"), conversion_id.label("verified_conversion_id"),
        )
        .join(ConfigFollowUp, and_(ConfigFollowUp.id == execution.followup_id, ConfigFollowUp.empresa_id == execution.empresa_id))
        .where(
            execution.empresa_id == p["tenant"],
            execution.created_at >= _naive_utc(p["start"]),
            execution.created_at < _naive_utc(p["end"]),
        )
    )
    if p["channel"]:
        stmt = stmt.where(execution.channel == p["channel"])
    if p["status"]:
        stmt = stmt.where(execution.status == p["status"])
    if p["stop_reason"]:
        stmt = stmt.where(execution.stop_reason == p["stop_reason"])
    # Mode is resolved from versioned execution snapshot; legacy rows without it
    # are classified by the persisted anchor, matching followup_rules defaults.
    if p["mode"]:
        stmt = stmt.where(mode_expr == p["mode"])
    return stmt.order_by(execution.created_at, execution.id).limit(MAX_EXECUTIONS + 1)


def _stage_order_map(spec):
    stages = spec.get("stages") if isinstance(spec, dict) else None
    result = {}
    if not isinstance(stages, list):
        return result
    for index, item in enumerate(stages[:40]):
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        order = item.get("order", index)
        if isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) and isinstance(order, int) and not isinstance(order, bool):
            result[key] = order
    return result


async def _stage_events(session, p, rows):
    tenant = p["tenant"]
    spec_row = (await session.execute(
        select(Empresa.credenciais_canais["monitor_funnel_spec"]).where(Empresa.id == tenant)
    )).first()
    spec = spec_row[0] if spec_row and isinstance(spec_row[0], dict) else {}
    orders = _stage_order_map(spec)
    lead_ids = {row.lead_id for row in rows if row.conversation_id and row.sent_at}
    conversation_ids = {row.conversation_id for row in rows if row.conversation_id and row.sent_at}
    if not orders or not lead_ids or not conversation_ids:
        return {}, orders, False
    event = CanonicalCommercialEvent
    stmt = (
        select(event.lead_id, event.conversation_id, event.funnel_stage_id, event.occurred_at)
        .where(
            event.empresa_id == tenant,
            event.lead_id.in_(lead_ids),
            event.conversation_id.in_(conversation_ids),
            event.funnel_stage_id.in_(list(orders)),
            event.occurred_at <= p["observed"],
            event.event_name.notin_(("Purchase", "Refund")),
        )
        .order_by(event.occurred_at, event.id)
        .limit(MAX_STAGE_EVENTS + 1)
    )
    result = list((await session.execute(stmt)).all())
    truncated = len(result) > MAX_STAGE_EVENTS
    grouped = defaultdict(list)
    for lead_id, conversation_id, stage_id, occurred_at in result[:MAX_STAGE_EVENTS]:
        if conversation_id and stage_id in orders:
            grouped[(lead_id, conversation_id)].append((_naive_utc(occurred_at), stage_id, orders[stage_id]))
    return grouped, orders, truncated


def _execution_state(row):
    return str(row.status or "unknown").lower()


def _is_sent(row):
    return row.sent_at is not None


def _is_delivered(row):
    return bool(row.provider_delivery_confirmed) or _execution_state(row) in {"delivered", "read"}


def _has_executed(row):
    return row.processing_started_at is not None or row.sent_at is not None or _execution_state(row) in {"processing", "locked", "sent", "delivered", "read", "failed"}


def _response_is_mature(row, observed, window):
    if row.response_at is not None:
        return True
    if row.sent_at is None:
        return False
    return row.sent_at + window <= _naive_utc(observed)


def _metric_pack(rows, *, p, stage_events, stage_orders, stage_truncated, truncated):
    scheduled = len(rows)
    executed = [row for row in rows if _has_executed(row)]
    sent = [row for row in rows if _is_sent(row)]
    delivered = [row for row in sent if _is_delivered(row)]
    responded = [row for row in sent if row.response_at is not None]
    mature_sent = [row for row in sent if _response_is_mature(row, p["observed"], timedelta(days=p["response_window_days"]))]
    pending = len(sent) - len(mature_sent)
    response_comparable = [row for row in mature_sent if row.conversation_id]
    conversion_comparable = [
        row for row in sent
        if row.conversation_id and (row.verified_conversion_id or _response_is_mature(row, p["observed"], timedelta(days=p["response_window_days"])))
    ]
    conversion_ids = {row.verified_conversion_id for row in conversion_comparable if row.verified_conversion_id}
    explicit_eligibility = [(row, _eligibility_decision(row.policy_snapshot_json)) for row in rows]
    eligible_values = [(row, value) for row, value in explicit_eligibility if value is not None]
    eligible_leads = {row.lead_id for row, value in eligible_values if value}
    evaluated_leads = {row.lead_id for row, _value in eligible_values}
    response_coverage = len([row for row in sent if row.conversation_id])
    reactivated = [
        row for row in responded
        if row.previous_activity_at is not None
        and row.sent_at - row.previous_activity_at >= timedelta(days=p["reactivation_days"])
    ]
    reactivation_known = [row for row in mature_sent if row.previous_activity_at is not None and row.conversation_id]

    stage_comparable = []
    stage_advances = []
    stage_pending = 0
    if stage_orders:
        for row in sent:
            if not row.conversation_id:
                continue
            facts = stage_events.get((row.lead_id, row.conversation_id), [])
            before = [fact for fact in facts if fact[0] <= row.sent_at]
            after = [fact for fact in facts if row.sent_at < fact[0] <= min(_naive_utc(p["observed"]), row.sent_at + timedelta(days=p["response_window_days"]))]
            if not before:
                continue
            prior_rank = before[-1][2]
            advanced = any(fact[2] > prior_rank for fact in after)
            mature = _response_is_mature(row, p["observed"], timedelta(days=p["response_window_days"]))
            if not mature and not advanced:
                stage_pending += 1
                continue
            stage_comparable.append(row)
            if advanced:
                stage_advances.append(row)

    stopped = [row for row in rows if _execution_state(row) in {"cancelled", "stopped"}]
    failed = [row for row in rows if _execution_state(row) == "failed"]
    unknown_eligibility = len(explicit_eligibility) - len(eligible_values)
    overall = "partial" if truncated or stage_truncated or unknown_eligibility or pending or response_coverage < len(sent) else ("observed" if rows else "unknown")
    mode_groups = {}
    for key in sorted(MODES):
        subset = [row for row in rows if _row_mode(row) == key]
        subset_sent = [row for row in subset if _is_sent(row)]
        subset_mature = [row for row in subset_sent if _response_is_mature(row, p["observed"], timedelta(days=p["response_window_days"]))]
        subset_response = [row for row in subset_sent if row.response_at is not None]
        subset_comparable = [row for row in subset_mature if row.conversation_id]
        subset_delivery = [row for row in subset_sent if _is_delivered(row)]
        mode_groups[key] = {
            "scheduled": len(subset),
            "executed": sum(_has_executed(row) for row in subset),
            "sent": len(subset_sent),
            "delivered": len(subset_delivery),
            "responded": len(subset_response),
            "response_rate": _rate(len(subset_response), len(subset_comparable), state="partial" if truncated or len(subset_mature) < len(subset_sent) or len(subset_comparable) < len(subset_mature) else ("observed" if subset_comparable else "unknown"), coverage={"mature_sent": len(subset_mature), "comparable_sent": len(subset_comparable), "pending_window": len(subset_sent) - len(subset_mature), "sent_without_conversation": len(subset_sent) - len([row for row in subset_sent if row.conversation_id])}),
        }

    return {
        "summary": {
            "scheduled_executions": _rate(scheduled, scheduled, state="partial" if truncated else ("observed" if scheduled else "unknown"), coverage={"distinct_contacts": len({row.lead_id for row in rows})}, definition="Ciclos persistidos criados no período; a unidade é uma execução idempotente."),
            "currently_scheduled": _rate(sum(_execution_state(row) in {"scheduled", "retry"} for row in rows), scheduled, state="partial" if truncated else ("observed" if scheduled else "unknown"), definition="Execuções que ainda aparecem como agendadas ou aguardando nova tentativa no snapshot atual."),
            "eligible_contacts": _rate(len(eligible_leads), len(evaluated_leads), state="partial" if unknown_eligibility or truncated else ("observed" if evaluated_leads else "unknown"), coverage={"executions_with_recorded_decision": len(eligible_values), "executions_without_recorded_decision": unknown_eligibility, "contacts_in_scope": len({row.lead_id for row in rows})}, definition="Contatos únicos com decisão de elegibilidade persistida; não estima contatos nunca avaliados."),
            "executions_started": _rate(len(executed), scheduled, state="partial" if truncated else ("observed" if scheduled else "unknown"), definition="Ciclos com início de processamento ou estado de execução persistido."),
            "messages_sent": _rate(len(sent), len(executed), state="partial" if truncated else ("observed" if executed else "unknown"), definition="Execuções com sent_at persistido; não equivale a entrega pelo provider."),
            "deliveries_confirmed": _rate(len(delivered), len(sent), state="partial" if truncated or len(delivered) < len(sent) else ("observed" if sent else "unknown"), coverage={"sent_without_provider_confirmation": len(sent) - len(delivered)}, definition="Envio com status delivered/read confirmado em evento do provider ou status correlacionado da execução."),
            "responses_observed": _rate(len(responded), len(response_comparable), state="partial" if pending or response_coverage < len(sent) or truncated else ("observed" if response_comparable else "unknown"), coverage={"mature_sent": len(mature_sent), "comparable_sent": len(response_comparable), "pending_window": pending, "sent_with_conversation": response_coverage, "sent_without_conversation": len(sent) - response_coverage}, definition=f"Inbound na mesma conversa até {p['response_window_days']} dias após envio. Relação temporal, sem atribuição causal."),
            "reactivations_observed": _rate(len(reactivated), len(reactivation_known), state="partial" if pending or len(reactivation_known) < len(mature_sent) or truncated else ("observed" if reactivation_known else "unknown"), coverage={"sent_with_prior_activity_and_mature_window": len(reactivation_known), "pending_window": pending}, definition=f"Inbound observado após envio, com última atividade da conversa anterior pelo menos {p['reactivation_days']} dias antes; não prova causalidade."),
            "stage_advances_observed": _rate(len(stage_advances), len(stage_comparable), state="unknown" if not stage_orders or not stage_comparable else ("partial" if truncated or stage_truncated or stage_pending or len(stage_comparable) < len(sent) else "observed"), coverage={"sent_with_prior_configured_stage": len(stage_comparable), "sent_total": len(sent), "pending_window": stage_pending, "stage_history_truncated": stage_truncated, "execution_rows_truncated": truncated}, definition=f"Evento comercial persistido na mesma conversa com ordem de etapa maior que o último evento configurado antes do envio, até {p['response_window_days']} dias. Associação temporal apenas."),
            "confirmed_conversions": _rate(len(conversion_ids), len(conversion_comparable), state="partial" if truncated or pending or response_coverage < len(sent) or conversion_ids else "unknown", coverage={"verified_purchase_event_rows": len(conversion_ids), "comparable_sent": len(conversion_comparable), "mature_sent": len(mature_sent), "pending_window": pending, "sent_without_conversation": len(sent) - response_coverage, "execution_rows_truncated": truncated}, definition="Compras distintas com evento comercial verificado por provider na mesma conversa e dentro da janela. Associação temporal, sem atribuição causal."),
            "cancelled_or_stopped": _rate(len(stopped), scheduled, state="partial" if truncated else ("observed" if scheduled else "unknown"), coverage={"failed": len(failed)}, definition="Execuções com estado cancelled/stopped no snapshot atual; motivos são agrupados sem expor texto livre."),
            "failed": _rate(len(failed), scheduled, state="partial" if truncated else ("observed" if scheduled else "unknown"), definition="Execuções com status failed persistido."),
        },
        "by_mode": mode_groups,
        "stop_reasons": _reason_counts(rows),
        "coverage": {
            "state": overall,
            "execution_rows_read": len(rows),
            "execution_limit": MAX_EXECUTIONS,
            "execution_rows_truncated": truncated,
            "stage_events_truncated": stage_truncated,
            "eligibility_decision_coverage": {"recorded": len(eligible_values), "unknown": unknown_eligibility, "denominator": len(rows)},
            "legacy_logs": {"state": "partial", "included_in_modern_execution_counts": False, "reason": "Logs legados não possuem conversa/provider e podem duplicar execuções modernas; serão exibidos separadamente quando houver vínculo seguro."},
            "limitations": [
                "População elegível inclui somente contatos com decisão persistida; contatos nunca avaliados não são inferidos.",
                "Entrega exige confirmação persistida do provider; ausência do status é desconhecida, não falha nem entrega.",
                "Respostas, reativações, etapas e conversões posteriores indicam ordem temporal e não comprovam causalidade.",
                "Snapshots atuais de estado não reconstroem todas as mudanças históricas.",
            ],
        },
    }


def _reason_counts(rows):
    counts = defaultdict(int)
    for row in rows:
        if row.stop_reason:
            counts[_safe_reason(row.stop_reason)] += 1
    return [{"reason": key, "count": counts[key]} for key in sorted(counts)]


def _row_matches(row, kind, p, stage_events, stage_orders):
    sent = _is_sent(row)
    response = row.response_at is not None
    delivered = _is_delivered(row)
    stopped = _execution_state(row) in {"cancelled", "stopped"}
    eligible = _eligibility_decision(row.policy_snapshot_json) is True
    if kind == "all":
        return True
    if kind == "initiated":
        return _has_executed(row)
    if kind == "scheduled":
        return _execution_state(row) in {"scheduled", "retry"}
    if kind == "failed":
        return _execution_state(row) == "failed"
    if kind == "eligible":
        return eligible
    if kind == "sent":
        return sent
    if kind == "delivered":
        return delivered
    if kind == "replied":
        return response
    if kind == "converted":
        return bool(row.verified_conversion_id)
    if kind == "reactivated":
        return response and row.previous_activity_at is not None and row.sent_at - row.previous_activity_at >= timedelta(days=p["reactivation_days"])
    if kind == "stopped":
        return stopped or _execution_state(row) == "failed"
    if kind == "stage_advanced":
        facts = stage_events.get((row.lead_id, row.conversation_id), [])
        prior = [fact for fact in facts if row.sent_at and fact[0] <= row.sent_at]
        after = [fact for fact in facts if row.sent_at and row.sent_at < fact[0] <= min(_naive_utc(p["observed"]), row.sent_at + timedelta(days=p["response_window_days"]))]
        return bool(prior and any(fact[2] > prior[-1][2] for fact in after))
    return False


def _serialize_row(row):
    return {
        "execution_id": str(row.id),
        "lead_id": str(row.lead_id),
        "conversation_id": str(row.conversation_id) if row.conversation_id else None,
        "followup_id": str(row.followup_id),
        "mode": _row_mode(row),
        "channel": row.channel,
        "status": _execution_state(row),
        "stop_reason": _safe_reason(row.stop_reason),
        "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "processing_started_at": row.processing_started_at.isoformat() if row.processing_started_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "response_at": row.response_at.isoformat() if row.response_at else None,
        "delivered": _is_delivered(row),
        "eligibility": _eligibility_decision(row.policy_snapshot_json),
    }


async def dashboard(session, *, empresa_id, start=None, end=None, observed_at=None,
                    response_window_days=7, reactivation_days=7, mode=None, channel=None,
                    status=None, stop_reason=None, now=None):
    p = filters(empresa_id=empresa_id, start=start, end=end, observed_at=observed_at,
                response_window_days=response_window_days, reactivation_days=reactivation_days,
                mode=mode, channel=channel, status=status, stop_reason=stop_reason, now=now)
    result = list((await session.execute(_base_query(p))).all())
    truncated = len(result) > MAX_EXECUTIONS
    rows = result[:MAX_EXECUTIONS]
    events, stage_orders, stage_truncated = await _stage_events(session, p, rows)
    payload = _metric_pack(rows, p=p, stage_events=events, stage_orders=stage_orders,
                           stage_truncated=stage_truncated, truncated=truncated)
    payload["legacy_send_logs"] = await legacy_sent_summary(session, empresa_id=p["tenant"], start=p["start"], end=p["end"])
    payload["window"] = {
        "start": p["start"].isoformat(), "end": p["end"].isoformat(), "observed_at": p["observed"].isoformat(),
        "response_window_days": p["response_window_days"], "reactivation_days": p["reactivation_days"],
        "interval": "[start,end) UTC; execução contada por created_at",
    }
    payload["filters"] = {"mode": p["mode"], "channel": p["channel"], "status": p["status"], "stop_reason": p["stop_reason"]}
    payload["options"] = {
        "modes": [{"id": key, "label": "Retomada automática" if key == "conversation_resume" else "Mensagem programada"} for key in sorted(MODES)],
        "channels": sorted(CHANNELS), "statuses": sorted(STATUSES), "stop_reasons": REASON_CODES,
    }
    return payload


async def drilldown(session, *, empresa_id, kind="all", limit=25, offset=0, **kwargs):
    if kind not in {"all", "scheduled", "eligible", "initiated", "sent", "delivered", "replied", "reactivated", "converted", "stopped", "failed", "stage_advanced"}:
        raise ValueError("invalid follow-up evidence category")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("invalid page size")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
        raise ValueError("invalid page offset")
    p = filters(empresa_id=empresa_id, **kwargs)
    result = list((await session.execute(_base_query(p))).all())
    truncated = len(result) > MAX_EXECUTIONS
    rows = result[:MAX_EXECUTIONS]
    events, stage_orders, stage_truncated = await _stage_events(session, p, rows)
    selected = [row for row in rows if _row_matches(row, kind, p, events, stage_orders)]
    selected.sort(key=lambda row: (row.created_at or datetime.min, str(row.id)), reverse=True)
    return {
        "items": [_serialize_row(row) for row in selected[offset:offset + limit]],
        "total": len(selected), "limit": limit, "offset": offset, "kind": kind,
        "coverage": {"state": "partial" if truncated or stage_truncated else ("observed" if selected else "unknown"), "truncated": truncated, "stage_events_truncated": stage_truncated},
    }


async def legacy_sent_summary(session, *, empresa_id, start, end):
    """Count legacy send logs separately, without merging them into execution rows."""
    tenant = uuid.UUID(str(empresa_id))
    stmt = (
        select(func.count(LeadFollowUpLog.id), func.count(func.distinct(LeadFollowUpLog.lead_id)))
        .join(ConfigFollowUp, ConfigFollowUp.id == LeadFollowUpLog.config_followup_id)
        .join(CRMLead, and_(CRMLead.id == LeadFollowUpLog.lead_id, CRMLead.empresa_id == ConfigFollowUp.empresa_id))
        .where(
            ConfigFollowUp.empresa_id == tenant,
            LeadFollowUpLog.status_envio == "enviado",
            LeadFollowUpLog.data_envio >= _naive_utc(_utc(start)),
            LeadFollowUpLog.data_envio < _naive_utc(_utc(end)),
        )
    )
    count, contacts = (await session.execute(stmt)).one()
    return {"send_log_rows": int(count or 0), "distinct_contacts": int(contacts or 0), "delivery_state": "unknown", "response_state": "unknown", "state": "partial" if count else "unknown"}
