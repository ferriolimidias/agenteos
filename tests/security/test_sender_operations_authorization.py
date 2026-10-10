"""Exercise the actual tenant dependency, without DB/network or production sessions."""
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import analytics
from app.api.routers.auth import get_current_user
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE, META_APP_REVIEWER_ROLE


@pytest.fixture
def context(monkeypatch):
    api = FastAPI()
    api.include_router(analytics.router)
    tenant = uuid4()
    user = SimpleNamespace(id=uuid4(), empresa_id=tenant, email='synthetic@example.invalid', role=ADMIN_EMPRESA_ROLE)
    async def isolated_db():
        yield object()
    api.dependency_overrides[get_db] = isolated_db
    listing = AsyncMock(return_value={'checked_at':'2026-10-10T00:00:00Z','summary':{},'items':[],'total':0,'limit':25,'offset':0})
    detail = AsyncMock(return_value={'id':str(uuid4())})
    monkeypatch.setattr(analytics, 'sender_operations', listing)
    monkeypatch.setattr(analytics, 'sender_attempt_detail', detail)
    monkeypatch.setenv('META_SENDER_OPERATIONS_ENABLED', 'true')
    with TestClient(api) as client:
        yield api, client, user, listing, detail


def test_missing_invalid_and_cross_tenant_credentials_are_rejected_before_reads(context):
    api, client, user, listing, detail = context
    for suffix in ('', '/'+str(uuid4())):
        path = f'/api/empresas/{user.empresa_id}/analytics/meta-sender-operations'+suffix
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization':'Bearer invalid-token'}).status_code == 401
    api.dependency_overrides[get_current_user] = lambda:user
    for suffix in ('', '/'+str(uuid4())):
        assert client.get(f'/api/empresas/{uuid4()}/analytics/meta-sender-operations'+suffix).status_code == 403
    listing.assert_not_awaited()
    detail.assert_not_awaited()


def test_same_tenant_reads_detail_unknown_and_feature_disabled(context,monkeypatch):
    api, client, user, listing, detail = context
    api.dependency_overrides[get_current_user] = lambda:user
    path = f'/api/empresas/{user.empresa_id}/analytics/meta-sender-operations'
    assert client.get(path).status_code == 200
    assert client.get(path+'/'+str(uuid4())).status_code == 200
    assert listing.await_args.kwargs['empresa_id'] == user.empresa_id
    detail.return_value = None
    assert client.get(path+'/'+str(uuid4())).status_code == 404
    monkeypatch.setenv('META_SENDER_OPERATIONS_ENABLED','off')
    listing.reset_mock(); detail.reset_mock()
    assert client.get(path).status_code == 404
    assert client.get(path+'/'+str(uuid4())).status_code == 404
    listing.assert_not_awaited(); detail.assert_not_awaited()


def test_restricted_meta_reviewer_has_no_new_operational_data_access(context):
    api, client, user, listing, _ = context
    user.role = META_APP_REVIEWER_ROLE
    api.dependency_overrides[get_current_user] = lambda:user
    assert client.get(f'/api/empresas/{user.empresa_id}/analytics/meta-sender-operations').status_code == 403
    listing.assert_not_awaited()


def test_bad_filters_and_service_range_errors_have_no_internal_details(context):
    api, client, user, listing, _ = context
    api.dependency_overrides[get_current_user] = lambda:user
    path = f'/api/empresas/{user.empresa_id}/analytics/meta-sender-operations'
    for query in ('limit=0','limit=101','offset=-1','offset=10001','outcome=bogus','transport=bogus','start_at=not-date'):
        assert client.get(path+'?'+query).status_code == 422
    listing.assert_not_awaited()
    listing.side_effect = ValueError('private data that must not escape')
    response = client.get(path+'?start_at=2026-01-01T00:00:00&end_at=2026-01-02T00:00:00Z')
    assert response.status_code == 400
    assert 'private' not in response.text
    assert listing.await_args.kwargs['start_at'].tzinfo is not None
