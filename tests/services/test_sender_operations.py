"""Response boundaries are allowlists, including already-persisted telemetry."""
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.services.meta.sender_operations import OUTCOMES, enabled, safe_provider_response, serialize_attempt


def attempt(**changes):
    values = dict(id=uuid.uuid4(), event_name='LeadSubmitted', request_outcome='PROVIDER_ACCEPTED',
                  transport_path='ads_integration_service.enviar_payload_meta_business_messaging',
                  attempt_started_at=datetime.now(timezone.utc), attempt_finished_at=None, http_status=200,
                  delivery_uncertainty=False, external_event_id_hash='a'*64, external_event_id_safe='a'*64,
                  dataset_id_hash='b'*64, waba_id_hash='c'*64, source_id_hash='d'*64,
                  request_correlation_id=uuid.uuid4(), identity_classification='UNLINKED', provider_response={})
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('outcome,status', OUTCOMES.items())
def test_persisted_outcomes_are_distinct_and_crash_after_request_remains_unknown(outcome, status):
    item = serialize_attempt(attempt(request_outcome=outcome))
    assert item['delivery_status'] == status
    if status == 'unknown':
        assert item['delivery_uncertainty'] is True


def test_no_provider_payload_or_free_form_storage_can_escape_response():
    secret = 'private-client-token@example.invalid'
    item = serialize_attempt(attempt(event_name=secret, transport_path=secret, request_outcome=secret,
        external_event_id_hash=secret, external_event_id_safe=secret, dataset_id_hash=secret,
        waba_id_hash=secret, source_id_hash=secret, identity_classification=secret,
        provider_response={'access_token':secret, 'message':secret, 'events_received':secret,
                           'trace_hash':secret, 'has_error':secret, 'code':secret}))
    assert secret not in str(item)
    assert item['provider_response'] == {}
    assert item['event_name'] == 'UNKNOWN'
    assert item['delivery_status'] == 'unknown'
    assert item['identity_classification'] == 'UNLINKED'
    assert item['transport_path'] is None


def test_flattened_provider_response_allowlist_keeps_useful_numeric_evidence():
    value = safe_provider_response({'events_received':1,'code':100,'error_subcode':33,'has_error':True,
                                    'is_transient':False,'trace_hash':'a'*64,'error':{'message':'private'}})
    assert value == {'events_received':1,'code':100,'error_subcode':33,'has_error':True,'is_transient':False,'trace_hash':'a'*64}
    assert safe_provider_response({'events_received':True,'code':-1,'error_subcode':False}) == {}
    assert safe_provider_response(['private']) == {}


def test_external_identity_accepts_only_hash_or_uuid_and_http_bool_is_not_status():
    identifier = uuid.uuid4()
    item = serialize_attempt(attempt(external_event_id_safe=str(identifier), http_status=True))
    assert item['identity']['external_event_id_safe'] == str(identifier)
    assert item['http_status'] is None


@pytest.mark.parametrize('value,expected', [('false',False),('0',False),(' OFF ',False),('true',True),('',True)])
def test_read_view_flag_does_not_enable_any_sender(monkeypatch,value,expected):
    monkeypatch.setenv('META_SENDER_OPERATIONS_ENABLED', value)
    assert enabled() is expected
