"""Read-only, evidence-based attendance quality indicators (A08).

The service deliberately reports observations instead of inferring intent.  It
uses persisted message timestamps, explicit CRM objection tags, and the bounded
A07 trace ledger when it is available.  No message text or customer identity is
returned to the browser and this module never writes to a provider or database.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_right
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import and_, exists, func, or_, select

from app.services.a07_ledger import A07Ledger, LedgerUnavailable
from app.services.jev_ledger import new_client
from db.models import AttributionTouch, AttributionTouchResolution, ChannelConversation, CRMEtapa, CRMFunil, CRMLead, CRMTagEvent, MensagemHistorico, TagCRM


MAX_MESSAGE_ROWS = 100_000
QUESTION_WINDOW_HOURS = 24
CONTINUITY_WINDOW_DAYS = 7
ALLOWED_CHANNELS = ("whatsapp", "instagram", "web", "sms", "telegram")
QUALITY_STATES = ("observed", "partial", "unknown", "not_applicable")
PRICE_MARKER = re.compile(r"(?:\br\$\s*\d|\bpre[cç]o\b|\bvalor\b|\binvestimento\b|\boferta\b|\bcondi[cç][aã]o\b|\bpagamento\b|\bparcel(?:a|as|amento)?\b)", re.I)
PRICE_ONLY_MARKER = re.compile(r"(?:\br\$\s*\d|\bpre[cç]o\b|\bvalor\b|\binvestimento\b)", re.I)
OFFER_ONLY_MARKER = re.compile(r"(?:\boferta\b|\bcondi[cç][aã]o\b|\bpagamento\b|\bparcel(?:a|as|amento)?\b)", re.I)


def enabled() -> bool:
    return os.getenv("ATTENDANCE_QUALITY_ANALYTICS_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        raise ValueError("timestamp ausente")
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("timestamp inválido")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def filters(*, empresa_id, start=None, end=None, observed_at=None, response_window_hours=24,
            continuity_window_days=7, channel=None, stage=None, ad_id=None, offer_signal=None,
            composition_version=None, now=None):
    tenant = uuid.UUID(str(empresa_id))
    now = _utc(now or datetime.now(timezone.utc))
    observed = _utc(observed_at) if observed_at else now
    end = _utc(end) if end else observed
    start = _utc(start) if start else end - timedelta(days=30)
    if start >= end or end > now + timedelta(days=1) or observed > now or (end - start) > timedelta(days=366):
        raise ValueError("janela de qualidade inválida")
    if isinstance(response_window_hours, bool) or not isinstance(response_window_hours, int) or not 1 <= response_window_hours <= 168:
        raise ValueError("janela de resposta inválida")
    if isinstance(continuity_window_days, bool) or not isinstance(continuity_window_days, int) or not 1 <= continuity_window_days <= 30:
        raise ValueError("janela de continuidade inválida")
    if channel is not None and channel not in ALLOWED_CHANNELS:
        raise ValueError("canal inválido")
    if stage is not None and (not isinstance(stage, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", stage)):
        raise ValueError("etapa inválida")
    if composition_version is not None and (not isinstance(composition_version, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", composition_version)):
        raise ValueError("versão de composição inválida")
    if ad_id is not None and (not isinstance(ad_id, str) or not re.fullmatch(r"[0-9]{1,40}", ad_id)):
        raise ValueError("anúncio inválido")
    if offer_signal is not None and offer_signal not in {"price", "offer"}:
        raise ValueError("sinal de oferta inválido")
    return {
        "tenant": tenant,
        "start": start,
        "end": end,
        "observed": observed,
        "response_window_hours": response_window_hours,
        "continuity_window_days": continuity_window_days,
        "channel": channel,
        "stage": stage,
        "ad_id": ad_id,
        "offer_signal": offer_signal,
        "composition_version": composition_version,
    }


def _rate(numerator: int | None, denominator: int | None, *, state: str | None = None, definition: str = ""):
    if state is None:
        state = "observed" if numerator is not None and denominator else "unknown"
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if numerator is not None and denominator else None,
        "state": state if state in QUALITY_STATES else "unknown",
        "definition": definition,
    }


def _direction(row):
    direction = row.direction if isinstance(row.direction, str) else None
    if direction in ("inbound", "outbound"):
        return direction
    if row.from_me is True:
        return "outbound"
    if row.from_me is False:
        return "inbound"
    return "unknown"


async def _stage_options(session, tenant):
    rows = (await session.execute(
        select(CRMEtapa.id, CRMEtapa.nome, CRMEtapa.ordem)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMFunil.empresa_id == tenant)
        .order_by(CRMEtapa.ordem, CRMEtapa.nome)
    )).all()
    return [{"id": str(row[0]), "label": str(row[1]), "order": row[2]} for row in rows]


async def _message_rows(session, p):
    upper = min(p["observed"], p["end"] + timedelta(days=max(p["continuity_window_days"], p["response_window_hours"] / 24)))
    conditions = [
        MensagemHistorico.empresa_id == p["tenant"],
        MensagemHistorico.criado_em >= p["start"],
        MensagemHistorico.criado_em < upper,
        func.lower(func.coalesce(MensagemHistorico.tipo_mensagem, "")).notin_(("status", "reaction")),
        func.lower(func.coalesce(MensagemHistorico.metadata_json["event_kind"].astext, "")).notin_(("status", "reaction")),
    ]
    if p["channel"]:
        conditions.append(or_(MensagemHistorico.channel == p["channel"], MensagemHistorico.channel.is_(None)))
    if p["ad_id"]:
        touch = AttributionTouch
        resolution = AttributionTouchResolution
        conditions.append(exists(select(touch.id).select_from(touch).join(
            resolution, and_(resolution.touch_id == touch.id, resolution.empresa_id == touch.empresa_id)
        ).where(touch.empresa_id == p["tenant"], touch.lead_id == MensagemHistorico.lead_id,
                resolution.ad_id == p["ad_id"])))
    if p["stage"]:
        stage_id = None
        try:
            stage_id = uuid.UUID(p["stage"])
        except (ValueError, AttributeError):
            pass
        stage_conditions = [and_(CRMFunil.id.is_not(None), CRMEtapa.nome == p["stage"])]
        if stage_id:
            stage_conditions.append(CRMEtapa.id == stage_id)
        conditions.append(or_(*stage_conditions))
    stmt = (
        select(
            MensagemHistorico.id,
            MensagemHistorico.lead_id,
            MensagemHistorico.channel_conversation_id,
            MensagemHistorico.channel,
            MensagemHistorico.criado_em,
            MensagemHistorico.direction,
            MensagemHistorico.from_me,
            ChannelConversation.id.label("valid_conversation_id"),
            MensagemHistorico.texto.ilike("%?%").label("has_question_marker"),
            MensagemHistorico.texto.op("~*")(_offer_sql_pattern(p.get("offer_signal"))).label("has_offer_marker"),
        )
        .select_from(MensagemHistorico)
        .join(CRMLead, and_(CRMLead.id == MensagemHistorico.lead_id, CRMLead.empresa_id == p["tenant"]))
        .outerjoin(ChannelConversation, and_(ChannelConversation.id == MensagemHistorico.channel_conversation_id, ChannelConversation.empresa_id == p["tenant"], ChannelConversation.lead_id == MensagemHistorico.lead_id))
        .outerjoin(CRMEtapa, CRMEtapa.id == CRMLead.etapa_id)
        .outerjoin(CRMFunil, and_(CRMFunil.id == CRMEtapa.funil_id, CRMFunil.empresa_id == p["tenant"]))
        .where(*conditions)
        .order_by(MensagemHistorico.criado_em, MensagemHistorico.id)
        .limit(MAX_MESSAGE_ROWS + 1)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    truncated = len(rows) > MAX_MESSAGE_ROWS
    return rows[:MAX_MESSAGE_ROWS], truncated


def _observation_state(*, observed_denominator, pending, missing):
    if observed_denominator:
        return "partial" if pending or missing else "observed"
    return "partial" if pending or missing else "unknown"


def _overall_coverage_state(coverage, *, truncated, objection_state):
    incomplete = (
        truncated
        or coverage["missing_conversation_messages"] > 0
        or coverage["unknown_direction_messages"] > 0
        or coverage["unknown_channel_messages"] > 0
        or coverage["response_pending_window"] > 0
        or coverage["price_offer_pending_window"] > 0
        or objection_state == "partial"
    )
    if incomplete:
        return "partial"
    return "observed" if coverage["messages_in_period"] else "unknown"


def _quality_metrics(rows, p):
    grouped = defaultdict(list)
    missing_conversation = 0
    unknown_direction = 0
    unknown_channel = 0
    messages_in_period = 0
    distinct_leads = set()
    for row in rows:
        at = _utc(row.criado_em)
        in_period = p["start"] <= at < p["end"]
        channel = getattr(row, "channel", None)
        if p["channel"]:
            if channel is None:
                unknown_channel += 1
                continue
            if channel != p["channel"]:
                continue
        if in_period:
            messages_in_period += 1
        if in_period and getattr(row, "lead_id", None):
            distinct_leads.add(str(row.lead_id))
        valid_conversation_id = getattr(row, "valid_conversation_id", row.channel_conversation_id)
        key = str(valid_conversation_id) if valid_conversation_id else None
        if in_period and not key:
            missing_conversation += 1
        direction = _direction(row)
        if direction == "unknown" and in_period:
            unknown_direction += 1
        if direction == "unknown":
            continue
        if not key:
            continue
        grouped[key].append((row, direction))
    for messages in grouped.values():
        messages.sort(key=lambda item: (_utc(item[0].criado_em), str(item[0].id)))

    direction_positions = {
        key: {
            "inbound": [index for index, (_, direction) in enumerate(messages) if direction == "inbound"],
            "outbound": [index for index, (_, direction) in enumerate(messages) if direction == "outbound"],
        }
        for key, messages in grouped.items()
    }

    response_window = timedelta(hours=p["response_window_hours"])
    continuity_window = timedelta(days=p["continuity_window_days"])
    questions = offers = inbound = 0
    inbound_leads = set()
    question_leads = set()
    question_observed = question_pending = question_unanswered = 0
    offer_observed = offer_pending = offer_no_continuity = 0
    response_observed = response_pending = response_count = 0
    response_latencies = []
    evidence = []

    for conversation_key, messages in grouped.items():
        positions = direction_positions[conversation_key]
        for index, (row, direction) in enumerate(messages):
            at = _utc(row.criado_em)
            if not p["start"] <= at < p["end"]:
                continue
            if direction == "inbound":
                inbound += 1
                inbound_leads.add(str(row.lead_id))
                question_marker = getattr(row, "has_question_marker", None)
                has_question = bool(question_marker) if question_marker is not None else ("?" in str(getattr(row, "texto", "")))
                answer_index = bisect_right(positions["outbound"], index)
                answer = messages[positions["outbound"][answer_index]][0] if answer_index < len(positions["outbound"]) else None
                if answer is not None and _utc(answer.criado_em) - at > response_window:
                    answer = None
                complete = at + response_window <= p["observed"]
                if complete:
                    response_observed += 1
                    if answer:
                        response_count += 1
                        response_latencies.append(max(0, (_utc(answer.criado_em) - at).total_seconds()))
                    if has_question:
                        questions += 1
                        question_leads.add(str(row.lead_id))
                        question_observed += 1
                        if not answer:
                            question_unanswered += 1
                            evidence.append({"evidence_type": "question_without_observed_response", "message_id": str(row.id), "lead_id": str(row.lead_id), "conversation_id": str(row.channel_conversation_id), "observed_at": at.isoformat(), "state": "observed"})
                else:
                    response_pending += 1
                    if has_question:
                        questions += 1
                        question_leads.add(str(row.lead_id))
                        question_pending += 1
            elif direction == "outbound":
                offer_marker = getattr(row, "has_offer_marker", None)
                if offer_marker is None:
                    offer_marker = _offer_marker(str(getattr(row, "texto", "")), p.get("offer_signal"))
                if not offer_marker:
                    continue
                reply_index = bisect_right(positions["inbound"], index)
                reply = messages[positions["inbound"][reply_index]][0] if reply_index < len(positions["inbound"]) else None
                if reply is not None and _utc(reply.criado_em) - at > continuity_window:
                    reply = None
                complete = at + continuity_window <= p["observed"]
                offers += 1
                if complete:
                    offer_observed += 1
                    if not reply:
                        offer_no_continuity += 1
                        evidence.append({"evidence_type": "price_offer_without_observed_continuity", "message_id": str(row.id), "lead_id": str(row.lead_id), "conversation_id": str(row.channel_conversation_id), "observed_at": at.isoformat(), "state": "observed"})
                else:
                    offer_pending += 1

    incomplete_associations = missing_conversation + unknown_direction + unknown_channel
    question_state = _observation_state(observed_denominator=question_observed, pending=question_pending, missing=incomplete_associations)
    offer_state = _observation_state(observed_denominator=offer_observed, pending=offer_pending, missing=incomplete_associations)
    response_state = _observation_state(observed_denominator=response_observed, pending=response_pending, missing=incomplete_associations)
    return {
        "question_without_observed_response": _rate(question_unanswered, question_observed, state=question_state, definition="Mensagem inbound contendo '?' sem mensagem outbound observada na mesma conversa em até a janela configurada. Não classifica intenção ou resolução."),
        "price_offer_without_observed_continuity": _rate(offer_no_continuity, offer_observed, state=offer_state, definition="Mensagem outbound com marcador textual explícito de preço/oferta sem inbound observado na mesma conversa em até a janela configurada. Não prova abandono."),
        "response_coverage": _rate(response_count, response_observed, state=response_state, definition="Mensagens inbound com outbound observado na mesma conversa dentro da janela de resposta."),
        "median_response_seconds": {"value": median(response_latencies) if response_latencies else None, "state": ("partial" if response_pending or incomplete_associations else "observed") if response_latencies else ("partial" if response_pending or incomplete_associations else "unknown"), "denominator": len(response_latencies), "mature_inbound_denominator": response_observed, "definition": "Mediana do intervalo entre inbound e o primeiro outbound posterior observado na mesma conversa; denominador é o número de pares respondidos usados na mediana."},
        "coverage": {"messages_loaded": len(rows), "messages_in_period": messages_in_period, "distinct_leads_by_crm_id": len(distinct_leads), "distinct_inbound_leads_by_crm_id": len(inbound_leads), "distinct_question_leads_by_crm_id": len(question_leads), "missing_conversation_messages": missing_conversation, "unknown_direction_messages": unknown_direction, "unknown_channel_messages": unknown_channel, "questions_total": questions, "questions_pending_window": question_pending, "price_offer_signals_total": offers, "price_offer_pending_window": offer_pending, "response_pending_window": response_pending},
        "evidence": evidence,
    }


def _offer_marker(text: str, signal: str | None) -> bool:
    marker = PRICE_MARKER if signal is None else (PRICE_ONLY_MARKER if signal == "price" else OFFER_ONLY_MARKER)
    return bool(marker.search(text))


def _offer_sql_pattern(signal: str | None) -> str:
    if signal == "price":
        return r"(\mr\$\s*[0-9]|\mpre[cç]o\M|\mvalor\M|\minvestimento\M)"
    if signal == "offer":
        return r"(\moferta\M|\mcondi[cç][aã]o\M|\mpagamento\M|\mparcel(a|as|amento)?\M)"
    return r"(\mr\$\s*[0-9]|\mpre[cç]o\M|\mvalor\M|\minvestimento\M|\moferta\M|\mcondi[cç][aã]o\M|\mpagamento\M|\mparcel(a|as|amento)?\M)"


async def _objection_metrics(session, p):
    conditions = [TagCRM.empresa_id == p["tenant"], CRMTagEvent.occurred_at >= p["start"], CRMTagEvent.occurred_at < p["end"],
                  or_(func.lower(TagCRM.nome).like("%objeção%"), func.lower(TagCRM.nome).like("%objecao%"))]
    stmt = (
        select(TagCRM.id, TagCRM.nome, CRMTagEvent.lead_id, ChannelConversation.id,
               ChannelConversation.channel, CRMEtapa.id, CRMEtapa.nome, CRMFunil.id,
               CRMTagEvent.occurred_at)
        .join(CRMTagEvent, and_(CRMTagEvent.tag_id == TagCRM.id, CRMTagEvent.empresa_id == p["tenant"]))
        .outerjoin(CRMLead, and_(CRMLead.id == CRMTagEvent.lead_id, CRMLead.empresa_id == p["tenant"]))
        .outerjoin(ChannelConversation, and_(ChannelConversation.id == CRMTagEvent.conversation_id, ChannelConversation.empresa_id == p["tenant"], ChannelConversation.lead_id == CRMTagEvent.lead_id))
        .outerjoin(CRMEtapa, CRMEtapa.id == CRMLead.etapa_id)
        .outerjoin(CRMFunil, and_(CRMFunil.id == CRMEtapa.funil_id, CRMFunil.empresa_id == p["tenant"]))
    )
    if p["ad_id"]:
        stmt = stmt.join(AttributionTouchResolution, and_(AttributionTouchResolution.empresa_id == p["tenant"], AttributionTouchResolution.ad_id == p["ad_id"]))
        stmt = stmt.join(AttributionTouch, and_(AttributionTouch.id == AttributionTouchResolution.touch_id, AttributionTouch.empresa_id == p["tenant"], AttributionTouch.lead_id == CRMTagEvent.lead_id)).distinct()
    rows = (await session.execute(stmt.where(*conditions, CRMTagEvent.action == "applied"))).all()
    grouped = defaultdict(lambda: {"leads": set(), "conversations": set(), "events": 0, "label": ""})
    unknown_channel_association_events = 0
    unknown_stage_association_events = 0
    matched_rows = 0
    stage_id = None
    if p["stage"]:
        try:
            stage_id = uuid.UUID(p["stage"])
        except (ValueError, AttributeError):
            pass
    for tag_id, label, lead_id, conversation_id, conversation_channel, current_stage_id, current_stage_name, funnel_id, _occurred in rows:
        if p["channel"]:
            if not conversation_id or not conversation_channel:
                unknown_channel_association_events += 1
                continue
            if conversation_channel != p["channel"]:
                continue
        if p["stage"]:
            if not current_stage_id or not funnel_id:
                unknown_stage_association_events += 1
                continue
            if current_stage_id != stage_id and current_stage_name != p["stage"]:
                continue
        matched_rows += 1
        entry = grouped[str(tag_id)]
        entry["label"] = str(label)
        entry["events"] += 1
        if lead_id:
            entry["leads"].add(str(lead_id))
        if conversation_id:
            entry["conversations"].add(str(conversation_id))
    partial = bool(unknown_channel_association_events or unknown_stage_association_events)
    items = [{"tag_id": tag_id, "label": _safe_objection_label(item["label"]), "distinct_leads": len(item["leads"]), "distinct_conversations": len(item["conversations"]), "events": item["events"], "state": "partial" if partial else "observed", "source": "persisted_crm_objection_tag"} for tag_id, item in grouped.items()]
    items.sort(key=lambda item: (-item["distinct_leads"], -item["events"], item["label"]))
    return {"state": "partial" if partial else ("observed" if items else "unknown"), "items": items, "denominator": len({lead for item in grouped.values() for lead in item["leads"]}) if items else None, "definition": "Tags CRM persistidas cujo nome contém marcador explícito de objeção; ausência da tag não prova ausência de objeção.", "coverage": {"tag_events": matched_rows, "unknown_channel_association_events": unknown_channel_association_events, "unknown_stage_association_events": unknown_stage_association_events, "source": "crm_tag_events"}}


def _safe_objection_label(value: str) -> str:
    normalized = re.sub(r"[^a-záàâãéêíóôõúç ]", " ", str(value).lower())
    if re.search(r"\d{7,}|@|https?", str(value), re.I):
        return "Outra tag explícita"
    labels = (("preço", "Preço"), ("preco", "Preço"), ("valor", "Preço"), ("prazo", "Prazo"),
              ("concorrente", "Concorrente"), ("confiança", "Confiança"), ("confianca", "Confiança"),
              ("orçamento", "Orçamento"), ("orcamento", "Orçamento"), ("produto", "Produto/serviço"),
              ("garantia", "Garantia"), ("pagamento", "Pagamento"))
    return next((label for key, label in labels if key in normalized), "Outra tag explícita")


async def _prompt_metrics(p):
    client = new_client()
    try:
        ledger = A07Ledger(client, p["tenant"])
        try:
            runs = await asyncio.wait_for(ledger.runs(), timeout=0.8)
        except (LedgerUnavailable, asyncio.TimeoutError, OSError, ConnectionError):
            return {"state": "unknown", "items": [], "exact_prompt_version_state": "unknown", "coverage": {"retained_runs": 0, "source": "a07_ledger", "version_field": "composition_version", "exact_prompt_version_available": False, "limitation": "Rastreamento A07 não disponível no momento; a versão exata do prompt não é registrada."}, "definition": "A07 expõe somente composition_version, sem versão exata de prompt nem vínculo a conversa; comparação descritiva de execução, sem causalidade."}
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    grouped = defaultdict(lambda: {"runs": 0, "completed": 0, "errors": 0, "latencies": [], "provider_usage_runs": 0, "input_tokens": 0, "input_count": 0, "output_tokens": 0, "output_count": 0, "cost": 0.0, "cost_count": 0})
    for run in runs or []:
        created = run.get("created_at") if isinstance(run, dict) else None
        try:
            created_at = _utc(created)
        except (ValueError, TypeError):
            continue
        if not p["start"] <= created_at < p["end"] or created_at > p["observed"]:
            continue
        version = str(run.get("composition_version") or "unknown")
        if p["composition_version"] and version != p["composition_version"]:
            continue
        mode = str(run.get("mode") or "unknown")
        source = str(run.get("source") or "unknown")
        metric_scope = str(run.get("metric_scope") or "unknown")
        measurement_source = str(run.get("measurement_source") or "unknown")
        item = grouped[(version, mode, source, metric_scope, measurement_source)]
        item["runs"] += 1
        if run.get("status") == "completed":
            item["completed"] += 1
        if run.get("status") in {"generation_error", "generation_timeout", "guard_blocked"}:
            item["errors"] += 1
        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        observed_usage = source == "real" and metric_scope == "final_generation_only" and measurement_source == "provider_observed"
        if observed_usage:
            item["provider_usage_runs"] += 1
            for metric_key in ("input_tokens", "output_tokens"):
                value = metrics.get(metric_key)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1_000_000_000:
                    item[metric_key] += value
                    item[f"{metric_key[:-7]}_count"] += 1
        latency = metrics.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and 0 <= latency < 1e12:
            item["latencies"].append(latency)
        cost = metrics.get("cost_usd")
        if observed_usage and isinstance(cost, (int, float)) and not isinstance(cost, bool) and 0 <= cost < 1e12:
            item["cost"] += cost
            item["cost_count"] += 1
    items = []
    for (version, mode, source, metric_scope, measurement_source), item in sorted(grouped.items()):
        provider_runs = item["provider_usage_runs"]
        def token_state_for(samples):
            return "not_applicable" if not provider_runs else ("observed" if samples == provider_runs else ("partial" if samples else "unknown"))
        input_token_state = token_state_for(item["input_count"])
        output_token_state = token_state_for(item["output_count"])
        token_state = "not_applicable" if not provider_runs else ("observed" if input_token_state == output_token_state == "observed" else ("unknown" if input_token_state == output_token_state == "unknown" else "partial"))
        cost_state = "not_applicable" if not provider_runs else ("observed" if item["cost_count"] == provider_runs else ("partial" if item["cost_count"] else "unknown"))
        items.append({"version": version, "mode": mode, "source": source, "metric_scope": metric_scope, "measurement_source": measurement_source, "runs": item["runs"], "completed": item["completed"], "errors": item["errors"], "completion_rate": _rate(item["completed"], item["runs"], definition="Execuções concluídas / execuções retidas; descrição operacional, não avaliação de qualidade."), "provider_usage_runs": provider_runs, "input_tokens_observed": item["input_tokens"] if item["input_count"] else None, "input_token_samples": item["input_count"], "input_token_state": input_token_state, "output_tokens_observed": item["output_tokens"] if item["output_count"] else None, "output_token_samples": item["output_count"], "output_token_state": output_token_state, "token_state": token_state, "median_latency_ms": median(item["latencies"]) if item["latencies"] else None, "observed_cost_usd": item["cost"] if item["cost_count"] else None, "cost_samples": item["cost_count"], "cost_state": cost_state, "source_system": "a07_ledger"})
    return {"state": "observed" if items else "unknown", "exact_prompt_version_state": "unknown", "items": items, "coverage": {"retained_runs": sum(item["runs"] for item in grouped.values()), "source": "a07_ledger", "version_field": "composition_version", "exact_prompt_version_available": False, "retention_days": 7, "limitation": "A07 registra composition_version, não versão exata do prompt. Traces são agregados, retidos por 7 dias e sem vínculo com conversa; não permitem concluir que uma versão causou melhor atendimento."}, "definition": "Composições A07 observadas; a versão exata do prompt não é persistida neste rastreamento. Comparação operacional descritiva."}


def _window(p):
    return {"start": p["start"].isoformat(), "end": p["end"].isoformat(), "observed_at": p["observed"].isoformat(), "response_window_hours": p["response_window_hours"], "continuity_window_days": p["continuity_window_days"], "timezone": "UTC", "unit": "mensagem e conversa persistidas"}


async def dashboard(session, *, empresa_id, **kwargs):
    if not enabled():
        raise LookupError("dashboard indisponível")
    p = filters(empresa_id=empresa_id, **kwargs)
    rows, truncated = await _message_rows(session, p)
    metrics = _quality_metrics(rows, p)
    objections = await _objection_metrics(session, p)
    prompts = await _prompt_metrics(p)
    stages = await _stage_options(session, p["tenant"])
    channels = sorted({str(row.channel) for row in rows if str(row.channel) in ALLOWED_CHANNELS})
    evidence_count = len(metrics["evidence"])
    coverage = metrics.pop("coverage")
    metrics.pop("evidence", None)
    if truncated:
        for metric in (metrics["question_without_observed_response"], metrics["price_offer_without_observed_continuity"], metrics["response_coverage"], metrics["median_response_seconds"]):
            if metric["state"] in {"observed", "unknown"}:
                metric["state"] = "partial"
    coverage.update({"messages_truncated": truncated, "objection_events": objections["coverage"].get("tag_events", 0), "unknown_objection_channel_association_events": objections["coverage"].get("unknown_channel_association_events", 0), "unknown_objection_stage_association_events": objections["coverage"].get("unknown_stage_association_events", 0), "prompt_runs": prompts["coverage"].get("retained_runs", 0), "limitations": ["Ausência de resposta é uma observação temporal e não prova objeção, abandono ou insatisfação.", "Perguntas e sinais de preço/oferta usam marcadores textuais determinísticos; não são classificação por IA.", "Tags de objeção dependem de registro CRM explícito; sem tag o estado permanece desconhecido.", "Contatos distintos são deduplicados por UUID de CRMLead; registros separados da mesma pessoa não são consolidados."]})
    coverage["state"] = _overall_coverage_state(coverage, truncated=truncated, objection_state=objections["state"])
    opportunities = []
    if metrics["question_without_observed_response"]["state"] in {"observed", "partial"} and metrics["question_without_observed_response"]["numerator"]:
        opportunities.append({"key": "review_question_response", "kind": "hypothesis", "title": "Revisar perguntas sem resposta observada", "basis": "Há mensagens com '?' sem outbound observado na mesma conversa dentro da janela; isso não prova intenção nem abandono."})
    if metrics["price_offer_without_observed_continuity"]["state"] in {"observed", "partial"} and metrics["price_offer_without_observed_continuity"]["numerator"]:
        opportunities.append({"key": "review_offer_followup", "kind": "hypothesis", "title": "Revisar continuidade após preço/oferta", "basis": "Há sinais textuais sem inbound posterior observado; a relação é temporal e não causal."})
    if objections["state"] == "observed":
        opportunities.append({"key": "review_objection_tags", "kind": "fact", "title": "Revisar tags de objeção recorrentes", "basis": "A recorrência abaixo vem de tags CRM persistidas e deve ser validada pelo operador."})
    ad_rows = (await session.execute(
        select(AttributionTouchResolution.ad_id)
        .where(AttributionTouchResolution.empresa_id == p["tenant"], AttributionTouchResolution.ad_id.is_not(None))
        .distinct()
    )).all()
    ads = sorted({str(row[0]) for row in ad_rows if re.fullmatch(r"[0-9]{1,40}", str(row[0]))})
    examples = [
        {"kind": "question_signal", "label": "Inbound com marcador '?'", "count": coverage["questions_total"], "privacy": "sem texto"},
        {"kind": "price_offer_signal", "label": "Outbound com marcador de preço/oferta", "count": coverage["price_offer_signals_total"], "privacy": "sem texto"},
        {"kind": "objection_tag", "label": "Tag CRM explícita de objeção", "count": objections["coverage"].get("tag_events", 0), "privacy": "sem nome de contato"},
    ]
    return {"window": _window(p), "filter_scope": {"channel": "Canal persistido da mensagem; mensagens sem canal são contadas como associação desconhecida e excluídas da coorte. Objeções só entram quando há associação de conversa do mesmo tenant.", "stage": "Etapa atual do contato no CRM; registros históricos são filtrados pelo snapshot atual.", "ad": "Coorte do contato: qualquer touch de anúncio resolvido para o mesmo lead, sem vínculo temporal ou causal com cada mensagem/evento.", "composition_version": "Aplica-se somente aos traces A07; não filtra métricas de conversas."}, "metrics": metrics, "objections": objections, "composition_traces": prompts, "coverage": coverage, "opportunities": opportunities, "examples": examples, "options": {"channels": channels, "stages": stages, "ads": ads, "composition_versions": sorted({item["version"] for item in prompts["items"]}), "offer_signals": [{"id": "price", "label": "Preço / valor"}, {"id": "offer", "label": "Oferta / condição"}]}, "evidence_count": evidence_count}


async def drilldown(session, *, empresa_id, category, limit=25, offset=0, **kwargs):
    if not enabled():
        raise LookupError("dashboard indisponível")
    if category not in {"question_without_observed_response", "price_offer_without_observed_continuity"}:
        raise ValueError("categoria inválida")
    if isinstance(limit, bool) or not 1 <= limit <= 100 or isinstance(offset, bool) or not 0 <= offset <= 100000:
        raise ValueError("paginação inválida")
    p = filters(empresa_id=empresa_id, **kwargs)
    rows, _ = await _message_rows(session, p)
    evidence = _quality_metrics(rows, p)["evidence"]
    evidence = [item for item in evidence if item["evidence_type"] == category]
    truncated = len(rows) >= MAX_MESSAGE_ROWS
    return {"items": evidence[offset:offset + limit], "total": len(evidence), "truncated": truncated, "state": "partial" if truncated else ("observed" if evidence else "unknown"), "category": category, "window": _window(p), "privacy": "Somente identificadores autenticados e timestamps; texto e dados de contato não são retornados."}
