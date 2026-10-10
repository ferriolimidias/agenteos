"""Tenant-scoped read model over P1C.4 evidence. Never claims, sends or reconciles."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select

from app.services.meta.conversion_outbox import CANONICAL_LIVE_ENABLED
from app.services.meta.legacy_delivery_evidence import TELEMETRY_EVENT_NAMES, TRANSPORT_PATHS
from db.models import (
    AttributionTouch, AttributionTouchResolution, CanonicalCommercialEvent,
    CanonicalConversionOutbox, CanonicalMetaConversionProjection, CommercialMetaEventLink, ConversionDeliveryIdentity,
    LegacyConversionDeliveryEvidence, MetaConversionEvent, TrafficMetaConfig,
)

OUTCOMES = {
    'PROVIDER_ACCEPTED': 'accepted', 'PROVIDER_REJECTED': 'rejected',
    'DELIVERY_UNKNOWN': 'unknown', 'REQUEST_ATTEMPTED': 'unknown',
    'NOT_SENT': 'not_sent', 'LOCAL_PREPARED': 'prepared', 'BLOCKED': 'blocked',
}
TRANSPORTS = dict(zip(TRANSPORT_PATHS, ('business_messaging', 'capi')))
LINKS = ('LINKED_EXACT', 'LINKED_PROBABLE', 'AMBIGUOUS', 'UNLINKED')
OUTBOX_STATES = ('SHADOW', 'BLOCKED', 'HISTORICAL_BLOCKED', 'READY', 'CLAIMED', 'RETRY', 'DELIVERED', 'FAILED_PERMANENT', 'UNKNOWN_DELIVERY')
ATTRIBUTION_STATES = ('resolved', 'partial', 'unresolved', 'not_applicable', 'not_found', 'forbidden', 'error_retryable', 'unknown', 'attributed', 'unattributed')


def enabled():
    return os.getenv('META_SENDER_OPERATIONS_ENABLED', 'true').strip().lower() not in {'false', '0', 'off'}


def _uuid(value):
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _hash(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{64}', value) else None


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def safe_provider_response(value):
    """Revalidate flattened telemetry, even when old persisted rows are polluted."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('events_received', 'code', 'error_subcode'):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[key] = item
    for key in ('has_error', 'is_transient'):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    if _hash(value.get('trace_hash')):
        result['trace_hash'] = _hash(value['trace_hash'])
    return result


def serialize_attempt(row):
    outcome = row.request_outcome if row.request_outcome in OUTCOMES else 'DELIVERY_UNKNOWN'
    return {
        'id': _uuid(row.id),
        'event_name': row.event_name if row.event_name in TELEMETRY_EVENT_NAMES else 'UNKNOWN',
        'outcome': outcome, 'delivery_status': OUTCOMES[outcome],
        'transport': TRANSPORTS.get(row.transport_path, 'unknown'),
        'transport_path': row.transport_path if row.transport_path in TRANSPORTS else None,
        'started_at': _iso(row.attempt_started_at), 'finished_at': _iso(row.attempt_finished_at),
        'http_status': row.http_status if isinstance(row.http_status, int) and not isinstance(row.http_status, bool) and 100 <= row.http_status <= 599 else None,
        'delivery_uncertainty': bool(row.delivery_uncertainty) or OUTCOMES[outcome] == 'unknown',
        'identity': {
            'external_event_id_hash': _hash(row.external_event_id_hash),
            'external_event_id_safe': _hash(row.external_event_id_safe) or _uuid(row.external_event_id_safe),
            'dataset_id_hash': _hash(row.dataset_id_hash), 'waba_id_hash': _hash(row.waba_id_hash),
            'source_id_hash': _hash(row.source_id_hash), 'request_correlation_id': _uuid(row.request_correlation_id),
        },
        'identity_classification': row.identity_classification if row.identity_classification in ('EXACT_LINKED', 'UNLINKED') else 'UNLINKED',
        'provider_response': safe_provider_response(row.provider_response),
    }


async def _count(session, model, tenant, *conditions):
    return int(await session.scalar(select(func.count()).select_from(model).where(model.empresa_id == tenant, *conditions)) or 0)


async def _counts(session, model, column, tenant, allowed):
    rows = (await session.execute(select(column, func.count()).where(model.empresa_id == tenant).group_by(column))).all()
    result = {key: 0 for key in allowed}
    for key, count in rows:
        # No arbitrary enum from storage may become a response key.
        safe_key = key if key in allowed else 'UNKNOWN'
        result[safe_key] = result.get(safe_key, 0) + count
    return result


async def _summary(session, tenant):
    evidence = LegacyConversionDeliveryEvidence
    rows = (await session.execute(select(evidence.transport_path, evidence.request_outcome, func.count(), func.max(evidence.attempt_started_at)).where(evidence.empresa_id == tenant).group_by(evidence.transport_path, evidence.request_outcome))).all()
    counts = dict.fromkeys(dict.fromkeys(OUTCOMES.values()), 0)
    paths = []
    last = None
    for path, transport in TRANSPORTS.items():
        selected = [row for row in rows if row[0] == path]
        total = sum(row[2] for row in selected)
        dates = [row[3] for row in selected if row[3] is not None]
        paths.append({'transport': transport, 'transport_path': path, 'total_attempts': total,
                      'last_attempt_at': _iso(max(dates)) if dates else None,
                      'observation_status': 'EVENT_OBSERVED' if total else 'NO_EVENT_OBSERVED'})
    for _, outcome, count, latest in rows:
        counts[OUTCOMES.get(outcome, 'unknown')] += count
        if latest is not None and (last is None or latest > last):
            last = latest
    config = (await session.execute(select(TrafficMetaConfig.dataset_id, TrafficMetaConfig.waba_id, TrafficMetaConfig.whatsapp_connection_id).where(TrafficMetaConfig.empresa_id == tenant))).first()
    config_present = config is not None
    configuration = {
        'configuration_present': config_present,
        'dataset_present': bool(config and config.dataset_id), 'waba_present': bool(config and config.waba_id),
        'connection_bound': bool(config and config.whatsapp_connection_id),
        'status': 'NOT_CONFIGURED' if not config else ('CONFIGURED_NOT_VALIDATED' if all(config) else 'INCOMPLETE'),
        'operational_readiness': 'NÃO_VALIDADO',
        'scope': 'LOCAL_BUSINESS_MESSAGING_CONFIGURATION_ONLY',
        'capi_configuration_validation': 'NÃO_VALIDADO',
    }
    facts = CanonicalCommercialEvent
    historical_condition = or_(facts.is_backfilled.is_(True), facts.external_dispatch_policy == 'historical_no_send')
    historical = await _count(session, facts, tenant, historical_condition)
    protected = await _count(session, facts, tenant, historical_condition, facts.external_dispatch_policy == 'historical_no_send')
    outbox_counts = await _counts(session, CanonicalConversionOutbox, CanonicalConversionOutbox.status, tenant, OUTBOX_STATES)
    historical_outbox = await _count(session, CanonicalConversionOutbox, tenant, CanonicalConversionOutbox.historical_no_send.is_(True))
    historical_blocked = await _count(session, CanonicalConversionOutbox, tenant, CanonicalConversionOutbox.historical_no_send.is_(True), CanonicalConversionOutbox.status == 'HISTORICAL_BLOCKED')
    reservation_counts = await _counts(session, ConversionDeliveryIdentity, ConversionDeliveryIdentity.delivery_state, tenant, ('RESERVED', 'PROVIDER_ACCEPTED', 'DELIVERY_UNKNOWN'))
    total = sum(counts.values())
    legacy_link_exists = exists(select(CommercialMetaEventLink.id).where(
        CommercialMetaEventLink.empresa_id == tenant,
        CommercialMetaEventLink.meta_event_id == MetaConversionEvent.id,
    ))
    return {
        'observation_status': 'EVENT_OBSERVED' if total else 'NO_EVENT_OBSERVED',
        'delivery_validation': 'NÃO_VALIDADO', 'total_attempts': total, 'last_attempt_at': _iso(last),
        'outcome_counts': counts,
        'coverage': {'paths_discovered': len(TRANSPORTS), 'paths_instrumented': len(TRANSPORTS), 'paths': paths},
        'configuration': configuration,
        'canonical_live_enabled': CANONICAL_LIVE_ENABLED, 'ownership_enforced': False,
        'ownership_mode': 'SHADOW_VALIDATION_ONLY',
        'historical': {'commercial_events': historical, 'protected_commercial_events': protected,
                       'outbox': historical_outbox, 'historical_outbox_blocked': historical_blocked},
        'outbox': {'total': sum(outbox_counts.values()), 'status_counts': outbox_counts},
        'persisted_links': await _counts(session, CommercialMetaEventLink, CommercialMetaEventLink.classification, tenant, LINKS),
        'correspondence': {
            'legacy_events': await _count(session, MetaConversionEvent, tenant),
            'legacy_without_persisted_link': await _count(session, MetaConversionEvent, tenant, ~legacy_link_exists),
            'commercial_events': await _count(session, facts, tenant),
            'projections': await _count(session, CanonicalMetaConversionProjection, tenant),
            'outbox_events': sum(outbox_counts.values()),
            'inferred_exact_links_created': 0,
        },
        'attribution': {
            'commercial_event_status_counts': await _counts(session, facts, facts.attribution_status, tenant, ATTRIBUTION_STATES),
            'touches': await _count(session, AttributionTouch, tenant),
            'resolutions': await _count(session, AttributionTouchResolution, tenant),
        },
        'shadow_diagnostics': {'reservations': sum(reservation_counts.values()), 'state_counts': reservation_counts, 'external_call_permitted': False},
    }


async def sender_operations(session, *, empresa_id, outcome=None, transport=None, event_name=None, start_at=None, end_at=None, limit=25, offset=0):
    tenant = uuid.UUID(str(empresa_id))
    start_at = start_at.replace(tzinfo=timezone.utc) if start_at and start_at.tzinfo is None else start_at
    end_at = end_at.replace(tzinfo=timezone.utc) if end_at and end_at.tzinfo is None else end_at
    if start_at and end_at and start_at > end_at:
        raise ValueError('invalid date range')
    if start_at and end_at and end_at - start_at > timedelta(days=366):
        raise ValueError('date range exceeds 366 days')
    evidence = LegacyConversionDeliveryEvidence
    conditions = [evidence.empresa_id == tenant]
    if outcome:
        if outcome not in OUTCOMES.values():
            raise ValueError('invalid outcome')
        conditions.append(evidence.request_outcome.in_([key for key, value in OUTCOMES.items() if value == outcome]))
    if transport:
        if transport not in TRANSPORTS.values():
            raise ValueError('invalid transport')
        conditions.append(evidence.transport_path.in_([key for key, value in TRANSPORTS.items() if value == transport]))
    if event_name:
        if event_name not in TELEMETRY_EVENT_NAMES:
            raise ValueError('invalid event')
        conditions.append(evidence.event_name == event_name)
    if start_at:
        conditions.append(evidence.attempt_started_at >= start_at)
    if end_at:
        conditions.append(evidence.attempt_started_at <= end_at)
    total = int(await session.scalar(select(func.count()).select_from(evidence).where(*conditions)) or 0)
    rows = (await session.execute(select(evidence).where(*conditions).order_by(evidence.attempt_started_at.desc(), evidence.id.desc()).limit(min(max(limit, 1), 100)).offset(min(max(offset, 0), 10000)))).scalars().all()
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'summary': await _summary(session, tenant),
            'items': [serialize_attempt(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


async def sender_attempt_detail(session, *, empresa_id, attempt_id):
    tenant = uuid.UUID(str(empresa_id))
    model = LegacyConversionDeliveryEvidence
    row = await session.scalar(select(model).where(model.empresa_id == tenant, model.id == uuid.UUID(str(attempt_id))))
    if row is None:
        return None
    result = serialize_attempt(row)
    legacy = await session.scalar(select(MetaConversionEvent.id).where(MetaConversionEvent.empresa_id == tenant, MetaConversionEvent.id == row.legacy_event_id)) if row.legacy_event_id else None
    fact = (await session.execute(select(CanonicalCommercialEvent.id, CanonicalCommercialEvent.attribution_status, CanonicalCommercialEvent.attribution_touch_id, CanonicalCommercialEvent.attribution_resolution_id).where(CanonicalCommercialEvent.empresa_id == tenant, CanonicalCommercialEvent.id == row.commercial_event_id))).first() if row.commercial_event_id else None
    link = (await session.execute(select(CommercialMetaEventLink.classification, CommercialMetaEventLink.commercial_event_id).where(CommercialMetaEventLink.empresa_id == tenant, CommercialMetaEventLink.meta_event_id == legacy))).first() if legacy else None
    # These are persisted references, never inferred from lead/time similarity.
    outbox = (await session.execute(select(CanonicalConversionOutbox.id, CanonicalConversionOutbox.status, CanonicalConversionOutbox.dispatch_mode, CanonicalConversionOutbox.historical_no_send).where(CanonicalConversionOutbox.empresa_id == tenant, CanonicalConversionOutbox.commercial_event_id == fact.id).order_by(CanonicalConversionOutbox.created_at.desc()).limit(100))).all() if fact else []
    attribution = None
    if fact:
        touch = await session.scalar(select(AttributionTouch.id).where(AttributionTouch.empresa_id == tenant, AttributionTouch.id == fact.attribution_touch_id)) if fact.attribution_touch_id else None
        resolution = await session.scalar(select(AttributionTouchResolution.id).where(AttributionTouchResolution.empresa_id == tenant, AttributionTouchResolution.id == fact.attribution_resolution_id)) if fact.attribution_resolution_id else None
        attribution = {'status': fact.attribution_status if fact.attribution_status in ATTRIBUTION_STATES else 'unknown', 'touch_id': _uuid(touch), 'resolution_id': _uuid(resolution)}
    linked_fact = await session.scalar(select(CanonicalCommercialEvent.id).where(CanonicalCommercialEvent.empresa_id == tenant, CanonicalCommercialEvent.id == link.commercial_event_id)) if link and link.commercial_event_id else None
    result['related'] = {
        'legacy_event_id': _uuid(legacy), 'commercial_event_id': _uuid(fact.id) if fact else None,
        'persisted_link': {'classification': link.classification if link.classification in LINKS else 'UNLINKED', 'commercial_event_id': _uuid(linked_fact)} if link else None,
        'outbox': [{'id': _uuid(box.id), 'status': box.status if box.status in OUTBOX_STATES else 'UNKNOWN', 'dispatch_mode': box.dispatch_mode if box.dispatch_mode in ('shadow', 'disabled', 'live') else 'unknown', 'historical_no_send': bool(box.historical_no_send)} for box in outbox],
        'attribution': attribution,
    }
    identity = result['identity']
    complete = bool(identity['external_event_id_hash'] and identity['dataset_id_hash'] and identity['waba_id_hash'] and row.event_name in TELEMETRY_EVENT_NAMES)
    counts = {}
    if complete:
        counts = dict((await session.execute(select(model.request_outcome, func.count()).where(
            model.empresa_id == tenant, model.external_event_id_hash == row.external_event_id_hash,
            model.dataset_id_hash == row.dataset_id_hash, model.waba_id_hash == row.waba_id_hash,
            model.event_name == row.event_name,
        ).group_by(model.request_outcome))).all())
    reservations = (await session.execute(select(ConversionDeliveryIdentity.id, ConversionDeliveryIdentity.owner, ConversionDeliveryIdentity.delivery_state).where(ConversionDeliveryIdentity.empresa_id == tenant, ConversionDeliveryIdentity.commercial_event_id == fact.id, ConversionDeliveryIdentity.external_event_id_hash == row.external_event_id_hash).limit(100))).all() if fact else []
    result['shadow_diagnostics'] = {
        'mode': 'SHADOW_VALIDATION_ONLY', 'enforced': False, 'identity_complete': complete,
        'same_identity_attempts': sum(counts.values()) if complete else None,
        'accepted_attempts': counts.get('PROVIDER_ACCEPTED', 0) if complete else None,
        'uncertain_attempts': sum(counts.get(key, 0) for key in ('DELIVERY_UNKNOWN', 'REQUEST_ATTEMPTED')) if complete else None,
        'repeated_attempts_are_not_duplicate_proof': True, 'retry_authorized': False,
        'reservation_destination_match_validated': False,
        'reservations': [{'id': _uuid(item.id), 'owner': item.owner if item.owner in ('legacy', 'canonical') else 'unknown', 'delivery_state': item.delivery_state if item.delivery_state in ('RESERVED', 'PROVIDER_ACCEPTED', 'DELIVERY_UNKNOWN') else 'UNKNOWN'} for item in reservations],
    }
    return result
