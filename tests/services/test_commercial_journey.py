"""Pure journey contracts: scoped cursors and a restrictive presentation schema."""
import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.services.commercial_journey import (
    commercial_journey, decode_cursor, encode_cursor, enabled, safe_details,
)


def test_safe_details_excludes_payloads_and_financial_values_without_proof():
    raw = {'payload': {'access_token': 'PRIVATE_PAYLOAD'}, 'metadata': {'phone': 'PRIVATE_PHONE'},
           'error_detail': 'PRIVATE_ERROR', 'tag_name': 'https://private.example/token',
           'stage_name': 'Bearer PRIVATE_TOKEN', 'event_name': 'PRIVATE_EVENT',
           'actor_source': 'PRIVATE_SOURCE', 'amount': '99.00', 'currency': 'BRL',
           'scheduled_at': 'not-a-date', 'attempt_count': True}
    result = safe_details(raw)
    assert result == {'event_name': 'unknown', 'actor_source': 'unknown'}
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('value', [None, [], 'private', 1])
def test_non_object_details_are_empty(value):
    assert safe_details(value) == {}


def test_verified_finance_and_bounded_identifiers_survive_allowlist():
    identifier = str(uuid4())
    result = safe_details({'verified': True, 'amount': '12.50', 'currency': 'BRL',
        'tag_id': identifier, 'ad_id': '123456', 'campaign_id': 'PRIVATE_CAMPAIGN',
        'tag_name': '  Compra   confirmada ', 'direction': 'outbound',
        'scheduled_at': '2026-01-01T03:00:00+03:00', 'intervention': True,
        'attempt_count': 3, 'delivery_status': 'UNKNOWN_DELIVERY'})
    assert result['amount'] == '12.50'
    assert result['tag_id'] == identifier
    assert result['ad_id'] == '123456'
    assert 'campaign_id' not in result
    assert result['tag_name'] == 'Compra confirmada'
    assert result['scheduled_at'] == '2026-01-01T00:00:00+00:00'
    assert result['delivery_status'] == 'UNKNOWN_DELIVERY'


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '-Infinity', '1e15', '-1e15', None, 'invalid'])
def test_financial_non_finite_and_unbounded_values_are_not_serialized(amount):
    assert 'amount' not in safe_details({'verified': True, 'amount': amount})


@pytest.mark.parametrize('name', ['a@private.test', '<script>', '{token}', 'access_token hidden', 'x'*101, 'x'*40])
def test_unsafe_display_labels_are_not_serialized(name):
    assert 'tag_name' not in safe_details({'tag_name': name})


def test_cursor_roundtrip_is_utc_and_bound_to_tenant_lead_and_filter():
    tenant, lead = uuid4(), uuid4()
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    event_id = f'mensagens_historico:{uuid4()}:recorded'
    cursor = encode_cursor(tenant=tenant, lead=lead, category='message', occurred_at=at, event_id=event_id)
    assert decode_cursor(cursor, tenant=tenant, lead=lead, category='message') == (at, event_id)
    for scope in ({'tenant': uuid4(), 'lead': lead, 'category': 'message'},
                  {'tenant': tenant, 'lead': uuid4(), 'category': 'message'},
                  {'tenant': tenant, 'lead': lead, 'category': None}):
        with pytest.raises(ValueError):
            decode_cursor(cursor, **scope)


@pytest.mark.parametrize('cursor', ['*', 'a'*1025, 'not_base64', 'e30', 'W10', 'bnVsbA'])
def test_cursor_rejects_malformed_and_oversized_data(cursor):
    with pytest.raises(ValueError):
        decode_cursor(cursor, tenant=uuid4(), lead=uuid4(), category=None)


def test_cursor_rejects_naive_times_and_unexpected_fields():
    tenant, lead = uuid4(), uuid4()
    data = {'v': 1, 't': str(tenant), 'l': str(lead), 'c': None,
            'at': '2026-01-01T00:00:00', 'id': f'commercial_events:{uuid4()}:recorded'}
    for payload in (data, data | {'at': '2026-01-01T00:00:00Z', 'private': 'value'}):
        cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        with pytest.raises(ValueError):
            decode_cursor(cursor, tenant=tenant, lead=lead, category=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('filters', [{'limit': 0}, {'limit': 101}, {'limit': True}, {'limit': 2.5}, {'category': 'invalid'}])
async def test_invalid_filter_cannot_reach_database(filters):
    class NoQueries:
        async def execute(self, query):
            raise AssertionError('invalid request reached database')
    with pytest.raises(ValueError):
        await commercial_journey(NoQueries(), empresa_id=uuid4(), lead_id=uuid4(), **filters)


@pytest.mark.parametrize('value,expected', [('false',False), ('OFF',False), ('0',False), ('true',True), ('1',True)])
def test_readonly_feature_flag(monkeypatch, value, expected):
    monkeypatch.setenv('INBOX_COMMERCIAL_JOURNEY_ENABLED', value)
    assert enabled() is expected
