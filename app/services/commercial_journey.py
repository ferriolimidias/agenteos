"""Read-only commercial journey projected from tenant-scoped persisted facts.

This module cannot send, reconcile, backfill or compile a funnel. Planned times
and current snapshots are deliberately not fabricated historical transitions.
"""
from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB

from db.models import (
    AgendamentoLocal, AttributionTouch, AttributionTouchResolution, CampaignRecipient,
    CanonicalCommercialEvent, ChannelConversation, ConfigFollowUp, CRMEtapa, CRMFunil,
    CRMLead, CRMTagEvent, Empresa, FollowupExecution, LeadFollowUpLog,
    LeoFTPaymentEvent, MensagemHistorico, TagCRM,
)

CATEGORIES = ('acquisition', 'conversation', 'message', 'crm', 'commercial', 'followup', 'campaign', 'checkout', 'payment', 'booking')
RESOLUTIONS = ('resolved', 'partial', 'unresolved', 'not_applicable', 'not_found', 'forbidden', 'error_retryable', 'unknown')
META_EVENTS = ('ViewContent', 'LeadSubmitted', 'QualifiedLead', 'AddToCart', 'InitiateCheckout', 'OrderCreated', 'CartAbandoned', 'Purchase', 'Refund', 'OrderShipped', 'OrderDelivered', 'OrderCanceled', 'OrderReturned', 'RatingProvided', 'ReviewProvided')
SOURCES = ('attribution_touches', 'channel_conversations', 'mensagens_historico', 'crm_tag_events', 'commercial_events', 'followup_executions', 'lead_followup_logs', 'campaign_recipients', 'leoft_payment_events', 'agendamentos_locais')


def enabled():
    return os.getenv('INBOX_COMMERCIAL_JOURNEY_ENABLED', 'true').strip().lower() not in {'false', '0', 'off'}


def _uuid(value):
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _iso(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def _label(value):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split())
    if not value or len(value) > 100 or re.search(r'https?://|@|bearer|access_token|password|secret|[A-Za-z0-9_-]{32,}', value, re.I):
        return None
    return value if not any(char in value for char in '<>{}') else None


def _id(value):
    return _uuid(value) or (str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and re.fullmatch(r'[0-9]{1,40}', str(value)) else None)


def _enum(value, allowed, fallback='unknown'):
    return value if value in allowed else fallback


def safe_details(value):
    """Allowlist including bounded enums: arbitrary persisted metadata stays private."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('tag_id', 'campaign_id', 'stage_id', 'ad_id', 'adset_id', 'creative_id', 'post_id', 'source_tag_event_id', 'source_attribution_touch_id'):
        if (identifier := _id(value.get(key))) is not None:
            result[key] = identifier
    for key in ('tag_name', 'stage_name'):
        if (label := _label(value.get(key))) is not None:
            result[key] = label
    enums = {
        'direction': ('inbound', 'outbound'), 'channel': ('whatsapp', 'instagram', 'web', 'sms', 'telegram'),
        'delivery_status': ('queued', 'scheduled', 'processing', 'sending', 'sent', 'accepted', 'delivered', 'read', 'failed', 'cancelled', 'canceled', 'blocked', 'unknown', 'UNKNOWN_DELIVERY', 'retry', 'completed', 'enviado', 'erro'),
        'action': ('applied', 'removed', 'apply', 'remove'),
        'actor_source': ('manual', 'ai', 'monitor', 'automation', 'system', 'integration', 'unknown'),
        'event_name': META_EVENTS, 'external_dispatch_policy': ('historical_no_send', 'disabled', 'not_applicable'),
        'attribution_status': RESOLUTIONS + ('attributed', 'unattributed'), 'resolution_status': RESOLUTIONS,
        'source_projection': ('attribution_ledger', 'commercial_fact', 'provider_payment_verified', 'persisted_tag_event', 'legacy_followup_log', 'provider_recipient_milestone'),
        'stage_key': ('content_engaged', 'lead_established', 'qualification_confirmed', 'selection_confirmed', 'checkout_intent', 'order_commitment', 'abandonment', 'purchase_confirmed'),
        'payment_status': ('confirmed', 'cancelled', 'refunded', 'pending', 'approved', 'failed'),
        'followup_mode': ('conversation_resume', 'scheduled_message', 'unknown'),
    }
    for key, allowed in enums.items():
        if key in value:
            result[key] = _enum(value[key], allowed)
    for key in ('intervention', 'historical_no_send', 'provider_ack_is_not_delivery', 'verified', 'checkout_reference_present', 'payment_reference_present'):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    for key in ('scheduled_at', 'started_at', 'ended_at'):
        if (timestamp := _iso(value.get(key))) is not None:
            result[key] = timestamp
    if isinstance(value.get('attempt_count'), int) and not isinstance(value['attempt_count'], bool) and 0 <= value['attempt_count'] <= 100000:
        result['attempt_count'] = value['attempt_count']
    # Amount is usable only alongside explicit payment verification, not a tag.
    if value.get('verified') is True:
        try:
            amount = Decimal(str(value.get('amount')))
            if amount.is_finite() and abs(amount) < Decimal('1e15'):
                result['amount'] = str(amount)
        except Exception:
            pass
        if isinstance(value.get('currency'), str) and re.fullmatch(r'[A-Z]{3}', value['currency']):
            result['currency'] = value['currency']
    return result


def encode_cursor(*, tenant, lead, category, occurred_at, event_id):
    value = {'v': 1, 't': str(tenant), 'l': str(lead), 'c': category, 'at': _iso(occurred_at), 'id': event_id}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_cursor(value, *, tenant, lead, category):
    if not value:
        return None
    try:
        if len(value) > 1024 or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise ValueError
        decoded = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if not isinstance(decoded, dict) or set(decoded) != {'v', 't', 'l', 'c', 'at', 'id'} or decoded['v'] != 1:
            raise ValueError
        if decoded['t'] != str(tenant) or decoded['l'] != str(lead) or decoded['c'] != category:
            raise ValueError
        identifier = decoded['id']
        if not isinstance(identifier, str) or len(identifier) > 180 or not re.fullmatch(r'[a-z_]+:[0-9a-f-]{36}:[a-z_]+', identifier):
            raise ValueError
        timestamp = datetime.fromisoformat(decoded['at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc), identifier
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid scoped journey cursor') from exc


def _json(**values):
    args = []
    for key, value in values.items():
        args.extend((literal(key), value if hasattr(value, '_compiler_dispatch') else literal(value)))
    return func.jsonb_build_object(*args) if args else cast(literal('{}'), JSONB)


def _time(column):
    return column if getattr(column.type, 'timezone', False) else func.timezone('UTC', column)


def _status(column):
    return case((column.in_(('scheduled', 'queued', 'processing', 'sending', 'retry')), 'pending'),
                (column.in_(('cancelled', 'canceled', 'blocked')), 'not_occurred'), else_='unknown')


def _projection(model, timestamp, *, category, event_type, title, conditions, details=None, conversation=None,
                milestone='recorded', status='observed', from_clause=None, relationship='explicit_lead_id'):
    event_id = literal(model.__tablename__ + ':') + cast(model.id, String) + literal(':' + milestone)
    query = select(event_id.label('id'), _time(timestamp).label('occurred_at'), literal(category).label('category'),
                   literal(event_type).label('type'), (literal(title) if isinstance(title, str) else title).label('title'),
                   (literal(status) if isinstance(status, str) else status).label('status'),
                   cast(conversation if conversation is not None else literal(None), String).label('conversation_id'),
                   literal(model.__tablename__).label('source'), cast(model.id, String).label('record_id'),
                   literal(relationship).label('relationship'), (details if details is not None else _json()).label('details'))
    if from_clause is not None:
        query = query.select_from(from_clause)
    return query.where(*conditions)


def _queries(tenant, lead):
    queries = []
    touch = AttributionTouch
    scoped = [touch.empresa_id == tenant, touch.lead_id == lead]
    resolution = AttributionTouchResolution
    join = touch.__table__.outerjoin(resolution, and_(resolution.empresa_id == tenant, resolution.touch_id == touch.id))
    details = _json(channel=touch.channel, resolution_status=func.coalesce(resolution.resolution_status, 'unknown'),
                    ad_id=resolution.ad_id, adset_id=resolution.adset_id, campaign_id=resolution.campaign_id,
                    creative_id=resolution.creative_id, source_projection='attribution_ledger')
    queries.append(_projection(touch, touch.occurred_at, category='acquisition', event_type='acquisition_touch',
        title='Origem de aquisição registrada', conditions=scoped + [touch.event_kind.in_(('ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source'))],
        details=details, conversation=touch.conversation_id, from_clause=join))
    verified_checkout = and_(touch.touch_source == 'checkout_service', touch.metadata_json['provenance'].astext == 'provider_checkout_reference',
                             touch.checkout_id.is_not(None), func.length(touch.checkout_id) > 0)
    queries.append(_projection(touch, touch.occurred_at, category='checkout', event_type='checkout_started',
        title=case((verified_checkout, 'Checkout iniciado: evidência operacional'), else_='Checkout: registro sem referência operacional comprovada'),
        conditions=scoped + [touch.event_kind == 'checkout_started'],
        status=case((verified_checkout, 'observed'), else_='unknown'),
        details=_json(checkout_reference_present=touch.checkout_id.is_not(None), source_projection='attribution_ledger'), conversation=touch.conversation_id))
    verified_payment = and_(touch.touch_source == 'payment_webhook', touch.metadata_json['provenance'].astext == 'provider_verified_payment_status',
                            touch.payment_id.is_not(None), func.length(touch.payment_id) > 0, touch.amount.is_not(None), touch.currency.op('~')(r'^[A-Z]{3}$'))
    for kind, title in (('payment_approved', 'Pagamento aprovado: evidência verificada'), ('refund', 'Reembolso: evidência verificada')):
        queries.append(_projection(touch, touch.occurred_at, category='payment', event_type=kind,
            title=case((verified_payment, title), else_='Pagamento: registro sem evidência financeira completa'),
            conditions=scoped + [touch.event_kind == kind], conversation=touch.conversation_id,
            status=case((verified_payment, 'observed'), else_='unknown'),
            details=_json(verified=func.coalesce(verified_payment, False), amount=touch.amount, currency=touch.currency,
                          payment_reference_present=touch.payment_id.is_not(None), source_projection='attribution_ledger')))
    queries.append(_projection(touch, touch.occurred_at, category='payment', event_type='payment_failed',
        title='Falha de pagamento registrada', conditions=scoped + [touch.event_kind == 'payment_failed'], status='not_occurred',
        details=_json(verified=False, payment_status='failed', source_projection='attribution_ledger'), conversation=touch.conversation_id))
    # Replies are interventions, never a new acquisition. Sent touches duplicate
    # recipient milestones, so only the distinct persisted reply fact is added.
    queries.append(_projection(touch, touch.occurred_at, category='campaign', event_type='campaign_reply',
        title='Resposta à campanha registrada', conditions=scoped + [touch.event_kind == 'campaign_reply'],
        conversation=touch.conversation_id, details=_json(intervention=True, campaign_id=touch.outbound_campaign_id, source_projection='attribution_ledger')))
    conversation = ChannelConversation
    conv_conditions = [conversation.empresa_id == tenant, conversation.lead_id == lead]
    queries.append(_projection(conversation, conversation.criado_em, category='conversation', event_type='conversation_registered',
        title='Conversa registrada', conditions=conv_conditions, conversation=conversation.id, details=_json(channel=conversation.channel)))
    message = MensagemHistorico
    message_direction = case((message.direction.in_(('inbound', 'outbound')), message.direction), (message.from_me.is_(True), 'outbound'), (message.from_me.is_(False), 'inbound'), else_='unknown')
    queries.append(_projection(message, message.criado_em, category='message', event_type='message_recorded', title='Mensagem registrada',
        conditions=[message.empresa_id == tenant, message.lead_id == lead,
                    func.lower(func.coalesce(message.metadata_json['event_kind'].astext, '')).notin_(('status', 'reaction')),
                    func.lower(message.tipo_mensagem).notin_(('status', 'reaction'))],
        conversation=message.channel_conversation_id,
        status=case((message_direction == 'inbound', 'observed'), else_=_status(message.message_status)),
        details=_json(direction=message_direction, channel=message.channel, delivery_status=func.coalesce(message.message_status, 'unknown'), provider_ack_is_not_delivery=True)))
    tag = CRMTagEvent
    tag_join = tag.__table__.join(TagCRM, and_(TagCRM.empresa_id == tenant, TagCRM.id == tag.tag_id))
    queries.append(_projection(tag, tag.occurred_at, category='crm', event_type='tag_change', title='Alteração de tag registrada',
        conditions=[tag.empresa_id == tenant, tag.lead_id == lead], from_clause=tag_join, conversation=tag.conversation_id,
        details=_json(tag_id=tag.tag_id, tag_name=TagCRM.nome, action=tag.action, actor_source=tag.source, source_projection='persisted_tag_event')))
    event = CanonicalCommercialEvent
    tag_proven = exists(select(tag.id).where(tag.empresa_id == tenant, tag.lead_id == lead, tag.id == event.tag_event_id))
    touch_proven = exists(select(touch.id).where(touch.empresa_id == tenant, touch.lead_id == lead, touch.id == event.attribution_touch_id))
    commercial_verified = and_(event.revenue_verified.is_(True), event.event_name.in_(('Purchase', 'Refund')),
                              event.value.is_not(None), event.currency.op('~')(r'^[A-Z]{3}$'), event.payment_id.is_not(None))
    queries.append(_projection(event, event.occurred_at, category='commercial', event_type='commercial_fact',
        title=case((or_(tag_proven, touch_proven), 'Representação comercial de evidência registrada'), else_='Evento comercial interno registrado'),
        conditions=[event.empresa_id == tenant, event.lead_id == lead], conversation=event.conversation_id,
        details=_json(event_name=event.event_name, historical_no_send=or_(event.is_backfilled.is_(True), event.external_dispatch_policy == 'historical_no_send'),
            external_dispatch_policy=event.external_dispatch_policy, attribution_status=event.attribution_status,
            verified=func.coalesce(commercial_verified, False), amount=event.value, currency=event.currency, source_projection='commercial_fact',
            source_tag_event_id=case((tag_proven, event.tag_event_id), else_=None),
            source_attribution_touch_id=case((touch_proven, event.attribution_touch_id), else_=None))))
    execution = FollowupExecution
    scoped = [execution.empresa_id == tenant, execution.lead_id == lead]
    common = _json(delivery_status=execution.status, scheduled_at=execution.scheduled_at, intervention=True,
                   followup_mode=execution.policy_snapshot_json['mode'].astext, provider_ack_is_not_delivery=True)
    queries.append(_projection(execution, execution.created_at, category='followup', event_type='followup_registered', title='Follow-up registrado',
        conditions=scoped, conversation=execution.conversation_id, details=common, status=_status(execution.status)))
    for milestone, timestamp, title in (
        ('sent', execution.sent_at, 'Envio de follow-up registrado'),
        ('completed', execution.completed_at, 'Follow-up concluído: registro operacional'),
        ('cancelled', execution.cancelled_at, 'Follow-up cancelado'), ('failed', execution.failed_at, 'Falha de follow-up registrada'),
    ):
        queries.append(_projection(execution, timestamp, category='followup', event_type='followup_'+milestone, title=title,
            conditions=scoped + [timestamp.is_not(None)], conversation=execution.conversation_id, details=common, milestone=milestone,
            status='not_occurred' if milestone == 'cancelled' else ('unknown' if milestone == 'failed' else 'observed')))
    log = LeadFollowUpLog
    # Both legacy parents must belong to the tenant, not only the lead.
    log_join = log.__table__.join(CRMLead, and_(CRMLead.id == log.lead_id, CRMLead.empresa_id == tenant)).join(ConfigFollowUp, and_(ConfigFollowUp.id == log.config_followup_id, ConfigFollowUp.empresa_id == tenant))
    queries.append(_projection(log, log.data_envio, category='followup', event_type='legacy_followup_record', title='Registro legado de follow-up',
        conditions=[log.lead_id == lead], from_clause=log_join, relationship='tenant_scoped_lead_and_followup',
        status=case((log.status_envio.in_(('cancelled', 'cancelado', 'blocked')), 'not_occurred'), else_='unknown'),
        details=_json(delivery_status=log.status_envio, intervention=True, provider_ack_is_not_delivery=True, source_projection='legacy_followup_log')))
    recipient = CampaignRecipient
    scoped = [recipient.empresa_id == tenant, recipient.lead_id == lead]
    common = _json(campaign_id=recipient.campanha_id, delivery_status=recipient.status, attempt_count=recipient.attempt_count,
        scheduled_at=recipient.scheduled_at, intervention=True, provider_ack_is_not_delivery=True, source_projection='provider_recipient_milestone')
    queries.append(_projection(recipient, recipient.created_at, category='campaign', event_type='campaign_recipient_registered', title='Intervenção de campanha registrada',
        conditions=scoped, details=common, conversation=recipient.conversation_id, status=_status(recipient.status)))
    for milestone, timestamp, title in (
        ('sent', recipient.sent_at, 'Envio de campanha registrado'), ('delivered', recipient.delivered_at, 'Entrega de campanha confirmada pelo registro'),
        ('read', recipient.read_at, 'Leitura de campanha registrada'), ('failed', recipient.failed_at, 'Falha de campanha registrada'),
        ('cancelled', recipient.cancelled_at, 'Envio de campanha cancelado'),
    ):
        queries.append(_projection(recipient, timestamp, category='campaign', event_type='campaign_'+milestone, title=title,
            conditions=scoped + [timestamp.is_not(None)], details=common, conversation=recipient.conversation_id, milestone=milestone,
            status='not_occurred' if milestone == 'cancelled' else ('unknown' if milestone == 'failed' else 'observed')))
    payment = LeoFTPaymentEvent
    # A submitted payment contract/status or E2E fixture is not financial proof.
    payment_verified = or_(payment.event_metadata['provenance'].astext == 'provider_verified_payment_status',
                          payment.event_metadata['provider_status_verified'] == cast(literal('true'), JSONB))
    queries.append(_projection(payment, payment.occurred_at, category='payment', event_type='provider_payment_fact',
        title=case((payment_verified, 'Pagamento: fato verificado do provedor'), else_='Pagamento: registro sem verificação do provedor'),
        conditions=[payment.company_id == tenant, payment.contact_id == lead, payment.environment == 'production',
                    payment.provider.in_(('mercado_pago', 'stripe', 'pagarme')),
                    func.coalesce(payment.event_metadata['is_simulated'].astext, 'false') != 'true',
                    func.coalesce(payment.event_metadata['simulated'].astext, 'false') != 'true'], relationship='explicit_contact_crm_lead_id',
        status=case((payment_verified, 'observed'), else_='unknown'),
        details=_json(verified=func.coalesce(payment_verified, False), amount=payment.amount, currency=payment.currency,
            payment_status=payment.payment_status, payment_reference_present=True)))
    booking = AgendamentoLocal
    # No observed-at column exists. A scheduled start is not an occurrence time.
    queries.append(_projection(booking, cast(literal(None), booking.data_hora_inicio.type), category='booking', event_type='booking_snapshot', title='Agendamento: horário planejado',
        conditions=[booking.empresa_id == tenant, booking.lead_id == lead], status=_status(booking.status),
        details=_json(scheduled_at=booking.data_hora_inicio, ended_at=booking.data_hora_fim)))
    # Validate conversation references against this exact lead, including legacy
    # tables whose FK guarantees tenant ownership but not matching lead identity.
    projected = []
    linked_conversation = ChannelConversation.__table__.alias('journey_linked_conversation')
    for query in queries:
        event = query.subquery()
        proven = exists(select(linked_conversation.c.id).where(
            linked_conversation.c.empresa_id == tenant, linked_conversation.c.lead_id == lead,
            cast(linked_conversation.c.id, String) == event.c.conversation_id,
        ))
        projected.append(select(*(case((proven, event.c.conversation_id), else_=None).label('conversation_id') if column.name == 'conversation_id' else column for column in event.c)))
    return projected


async def _snapshots(session, tenant, lead, now, last_event):
    stage = (await session.execute(select(CRMEtapa.id, CRMEtapa.nome).join(CRMFunil, and_(CRMFunil.id == CRMEtapa.funil_id, CRMFunil.empresa_id == tenant)).where(CRMEtapa.id == lead.etapa_id))).first() if lead.etapa_id else None
    identifiers = [_uuid(item.get('id') if isinstance(item, dict) else item) for item in (lead.tags if isinstance(lead.tags, list) else [])[:100]]
    identifiers = [uuid.UUID(item) for item in identifiers if item]
    tags = (await session.execute(select(TagCRM.id, TagCRM.nome).where(TagCRM.empresa_id == tenant, TagCRM.id.in_(identifiers)).order_by(TagCRM.id).limit(100))).all() if identifiers else []
    # Only this JSON path is selected; credentials never leave the database.
    spec = await session.scalar(select(Empresa.credenciais_canais['monitor_funnel_spec']).where(Empresa.id == tenant))
    spec = spec if isinstance(spec, dict) else {}
    stages = []
    for item in (spec.get('stages') if isinstance(spec.get('stages'), list) else [])[:40]:
        if not isinstance(item, dict):
            continue
        key = _enum(item.get('key'), ('content_engaged', 'lead_established', 'qualification_confirmed', 'selection_confirmed', 'checkout_intent', 'order_commitment', 'abandonment', 'purchase_confirmed'))
        if key == 'unknown':
            continue
        stages.append({'key': key, 'order': item.get('order') if isinstance(item.get('order'), int) and not isinstance(item['order'], bool) and 0 <= item['order'] <= 100 else None,
                       'display_name': _label(item.get('display_name')), 'meta_event': _enum(item.get('meta_event'), META_EVENTS)})
    data = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
    state = data.get('commercial_state')
    state = state if isinstance(state, dict) else None
    commercial = None
    if state is not None:
        commercial = {'updated_at': _iso(state.get('updated_at')),
                      'signals': [signal for signal in (state.get('signals') if isinstance(state.get('signals'), list) else [])[:20] if signal in META_EVENTS],
                      'snapshot_only': True, 'history_complete': False}
    resolution_counts = dict.fromkeys(RESOLUTIONS, 0)
    touch = AttributionTouch
    resolution = AttributionTouchResolution
    counts = (await session.execute(select(resolution.resolution_status, func.count()).select_from(touch).outerjoin(resolution, and_(resolution.empresa_id == tenant, resolution.touch_id == touch.id)).where(touch.empresa_id == tenant, touch.lead_id == lead.id, touch.event_kind.in_(('ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source'))).group_by(resolution.resolution_status))).all()
    for status, count in counts:
        resolution_counts[_enum(status, RESOLUTIONS)] += count
    message = MensagemHistorico
    direction = case((message.direction.in_(('inbound', 'outbound')), message.direction), (message.from_me.is_(True), 'outbound'), (message.from_me.is_(False), 'inbound'), else_='unknown')
    message_conditions = [message.empresa_id == tenant, message.lead_id == lead.id, message.criado_em <= now.replace(tzinfo=None),
        func.lower(func.coalesce(message.metadata_json['event_kind'].astext, '')).notin_(('status', 'reaction')),
        func.lower(message.tipo_mensagem).notin_(('status', 'reaction'))]
    activity_rows = (await session.execute(select(direction.label('direction'), func.count().label('count'),
        func.min(message.criado_em).label('first'), func.max(message.criado_em).label('last')).where(*message_conditions).group_by(direction))).all()
    activity_by_direction = {row.direction: row for row in activity_rows}
    first_message = min((row.first for row in activity_rows if row.first is not None), default=None)
    last_message = max((row.last for row in activity_rows if row.last is not None), default=None)
    last_conversation_message = await session.scalar(select(func.max(ChannelConversation.last_message_at)).where(
        ChannelConversation.empresa_id == tenant, ChannelConversation.lead_id == lead.id,
        ChannelConversation.last_message_at <= now.replace(tzinfo=None),
    ))
    last_activity = max((item for item in (last_message, last_conversation_message) if item is not None), default=None)
    last_utc = last_activity.replace(tzinfo=timezone.utc) if last_activity and last_activity.tzinfo is None else last_activity
    # Intervals describe adjacent recorded messages in the same proven
    # conversation. They never imply recipient delivery or reply causality.
    conversations = ChannelConversation.__table__.alias('response_conversation')
    proven_conversation = exists(select(conversations.c.id).where(conversations.c.empresa_id == tenant,
        conversations.c.lead_id == lead.id, conversations.c.id == message.channel_conversation_id))
    window = {'partition_by': message.channel_conversation_id, 'order_by': (message.criado_em, message.id)}
    sequence = select(message.criado_em.label('at'), direction.label('direction'),
        func.lag(message.criado_em).over(**window).label('previous_at'),
        func.lag(direction).over(**window).label('previous_direction')).where(*message_conditions, proven_conversation).subquery()
    intervals = (await session.execute(select(func.count().label('count'),
        func.avg(func.extract('epoch', sequence.c.at-sequence.c.previous_at)).label('mean_seconds')).where(
        sequence.c.direction == 'outbound', sequence.c.previous_direction == 'inbound', sequence.c.previous_at.is_not(None)))).one()
    ads_proof = await session.scalar(select(func.count()).select_from(touch).join(resolution, and_(resolution.empresa_id == tenant,
        resolution.touch_id == touch.id)).where(touch.empresa_id == tenant, touch.lead_id == lead.id,
        touch.event_kind.in_(('ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source')),
        resolution.resolution_status == 'resolved', resolution.ad_id.op('~')(r'^[0-9]{1,40}$'),
        resolution.adset_id.op('~')(r'^[0-9]{1,40}$'), resolution.campaign_id.op('~')(r'^[0-9]{1,40}$')))
    bookings = (await session.execute(select(AgendamentoLocal.id, AgendamentoLocal.status,
        AgendamentoLocal.data_hora_inicio, AgendamentoLocal.data_hora_fim).where(
        AgendamentoLocal.empresa_id == tenant, AgendamentoLocal.lead_id == lead.id).order_by(
        AgendamentoLocal.data_hora_inicio.desc(), AgendamentoLocal.id.desc()).limit(21))).all()
    return {
        'crm': {'stage': {'id': str(stage.id), 'name': _label(stage.nome)} if stage else None,
                'tags': [{'id': str(tag.id), 'name': _label(tag.nome)} for tag in tags], 'snapshot_only': True, 'history_complete': False},
        'funnel': {'version': _label(spec.get('version')), 'stages': stages, 'snapshot_only': True, 'history_complete': False},
        'commercial_state': commercial,
        'activity': {'last_event_at': _iso(last_activity), 'first_message_at': _iso(first_message), 'last_message_at': _iso(last_message),
                     'last_inbound_at': _iso(activity_by_direction['inbound'].last) if 'inbound' in activity_by_direction else None,
                     'last_outbound_at': _iso(activity_by_direction['outbound'].last) if 'outbound' in activity_by_direction else None,
                     'inbound_messages': activity_by_direction['inbound']._mapping['count'] if 'inbound' in activity_by_direction else 0,
                     'outbound_messages': activity_by_direction['outbound']._mapping['count'] if 'outbound' in activity_by_direction else 0,
                     'recorded_response_intervals': intervals._mapping['count'],
                     'mean_recorded_response_seconds': float(intervals.mean_seconds) if intervals.mean_seconds is not None else None,
                     'intervals_are_not_delivery_proof': True,
                     'inactive_seconds': max(0, int((now-last_utc).total_seconds())) if last_utc else None,
                     'state': 'observed' if last_activity else 'unknown', 'abandonment_inferred': False},
        'attribution': {'status_counts': resolution_counts, 'ads_identity_verified': bool(ads_proof)},
        'bookings': [{'id': str(row.id), 'status': 'pending' if row.status in ('agendado', 'confirmado', 'scheduled', 'confirmed')
                     else ('not_occurred' if row.status in ('cancelado', 'cancelled', 'canceled') else 'unknown'),
                     'scheduled_at': _iso(row.data_hora_inicio), 'ended_at': _iso(row.data_hora_fim)} for row in bookings[:20]],
        'bookings_snapshot_only': True, 'bookings_truncated': len(bookings) > 20,
    }


async def commercial_journey(session, *, empresa_id, lead_id, category=None, limit=30, cursor=None):
    tenant, identifier = uuid.UUID(str(empresa_id)), uuid.UUID(str(lead_id))
    if category is not None and category not in CATEGORIES or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('invalid journey filter')
    boundary = decode_cursor(cursor, tenant=tenant, lead=identifier, category=category)
    lead = (await session.execute(select(CRMLead.id, CRMLead.etapa_id, CRMLead.tags, CRMLead.dados_adicionais).where(CRMLead.empresa_id == tenant, CRMLead.id == identifier))).first()
    if lead is None:
        return None
    events = union_all(*_queries(tenant, identifier)).subquery('journey_events')
    # Count at source/category level, not by loading the complete event history.
    grouped = (await session.execute(select(events.c.source, events.c.category, func.count().label('records'),
        func.count(events.c.occurred_at).label('timed'), func.count(func.distinct(events.c.record_id)).label('source_records'),
        func.max(events.c.occurred_at).label('latest')).group_by(events.c.source, events.c.category))).all()
    last_event = max((row.latest for row in grouped if row.latest is not None), default=None)
    constraints = [events.c.occurred_at.is_not(None)]
    if category:
        constraints.append(events.c.category == category)
    if boundary:
        timestamp, event_id = boundary
        constraints.append(or_(events.c.occurred_at < timestamp, and_(events.c.occurred_at == timestamp, events.c.id < event_id)))
    rows = (await session.execute(select(events).where(*constraints).order_by(events.c.occurred_at.desc(), events.c.id.desc()).limit(limit+1))).mappings().all()
    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        items.append({'id': row['id'], 'occurred_at': _iso(row['occurred_at']), 'category': row['category'],
            'type': row['type'], 'title': row['title'], 'status': row['status'], 'conversation_id': _uuid(row['conversation_id']),
            'evidence': {'source': row['source'], 'record_id': _uuid(row['record_id']), 'relationship': row['relationship']},
            'details': safe_details(row['details'])})
    coverage_sources = []
    for source in SOURCES:
        selected = [row for row in grouped if row.source == source]
        if not selected:
            source_category = {'attribution_touches':'acquisition','channel_conversations':'conversation','mensagens_historico':'message','crm_tag_events':'crm','commercial_events':'commercial','followup_executions':'followup','lead_followup_logs':'followup','campaign_recipients':'campaign','leoft_payment_events':'payment','agendamentos_locais':'booking'}[source]
            selected = [dict(source=source, category=source_category, records=0, timed=0, source_records=0)]
        for row in selected:
            data = row if isinstance(row, dict) else row._mapping
            coverage_sources.append({'source': source, 'category': data['category'], 'records': data['source_records'],
                'timeline_records': data['timed'], 'missing_timestamp': data['records']-data['timed'],
                'state': 'observed' if data['source_records'] else 'unknown',
                'limitation': 'Sem timestamp de registro; horários planejados não são ocorrências.' if source == 'agendamentos_locais' else ('Não observado nesta fonte.' if not data['source_records'] else 'Somente relações explícitas e fatos persistidos; histórico pode ser parcial.')})
    coverage_categories = [{'category': key, 'records': sum(row['timeline_records'] for row in coverage_sources if row['category']==key),
        'state': 'observed' if any(row['records'] for row in coverage_sources if row['category']==key) else 'unknown'} for key in CATEGORIES]
    now = datetime.now(timezone.utc)
    return {'checked_at': now.isoformat(), 'lead_id': str(identifier), 'items': items, 'limit': limit, 'has_more': has_more,
        'next_cursor': encode_cursor(tenant=tenant, lead=identifier, category=category, occurred_at=items[-1]['occurred_at'], event_id=items[-1]['id']) if has_more and items else None,
        'summary': await _snapshots(session, tenant, lead, now, last_event),
        'coverage': {'sources': coverage_sources, 'categories': coverage_categories, 'filtered': False, 'history_complete': False,
            'timezone': 'UTC', 'limitations': ['CRM/FunnelSpec são snapshots atuais, não transições reconstruídas.',
                'Histórico comercial em JSON é limitado e não constitui cobertura completa.',
                'Aceitação de envio não confirma entrega nem atribuição Ads.',
                'Ausência de registro não comprova que um evento não ocorreu.',
                'Pagamentos de teste/simulados ou sem prova do provedor não são pagamentos verificados.']}}
