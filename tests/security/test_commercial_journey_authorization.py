"""Real HTTP dependency checks; the read service never receives unauthorized scopes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import inbox
from app.api.routers.auth import get_current_user
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE, META_APP_REVIEWER_ROLE


@pytest.fixture
def context(monkeypatch):
    app = FastAPI()
    app.include_router(inbox.router)
    tenant, lead = uuid4(), uuid4()
    user = SimpleNamespace(id=uuid4(), empresa_id=tenant, email='synthetic@example.invalid', role=ADMIN_EMPRESA_ROLE)
    async def isolated_db():
        yield object()
    app.dependency_overrides[get_db] = isolated_db
    service = AsyncMock(return_value={'lead_id': str(lead), 'items': [], 'summary': {}, 'coverage': {}})
    monkeypatch.setattr(inbox, 'commercial_journey', service)
    monkeypatch.setenv('INBOX_COMMERCIAL_JOURNEY_ENABLED', 'true')
    with TestClient(app) as client:
        yield app, client, user, lead, service


def path(user, lead):
    return f'/api/empresas/{user.empresa_id}/inbox/leads/{lead}/journey'


def test_missing_and_invalid_credentials_reject_before_service(context):
    app, client, user, lead, service = context
    assert client.get(path(user, lead)).status_code == 401
    assert client.get(path(user, lead), headers={'Authorization': 'Bearer invalid-token'}).status_code == 401
    service.assert_not_awaited()


def test_cross_tenant_and_reviewer_reject_before_service(context):
    app, client, user, lead, service = context
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(f'/api/empresas/{uuid4()}/inbox/leads/{lead}/journey').status_code == 403
    user.role = META_APP_REVIEWER_ROLE
    assert client.get(path(user, lead)).status_code == 403
    service.assert_not_awaited()


def test_own_tenant_is_passed_as_uuid_and_unknown_lead_is_404(context):
    app, client, user, lead, service = context
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(path(user, lead)+'?category=message&limit=2').status_code == 200
    assert service.await_args.kwargs['empresa_id'] == user.empresa_id
    assert service.await_args.kwargs['lead_id'] == lead
    assert service.await_args.kwargs['category'] == 'message'
    service.return_value = None
    assert client.get(path(user, uuid4())).status_code == 404


def test_disabled_feature_has_no_reads(context, monkeypatch):
    app, client, user, lead, service = context
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setenv('INBOX_COMMERCIAL_JOURNEY_ENABLED', 'off')
    assert client.get(path(user, lead)).status_code == 404
    service.assert_not_awaited()


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'category=invalid', 'cursor='+'a'*1025])
def test_invalid_http_filters_do_not_read(context, query):
    app, client, user, lead, service = context
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(path(user, lead)+'?'+query).status_code == 422
    service.assert_not_awaited()


def test_invalid_scoped_cursor_is_public_400_without_internal_error(context):
    app, client, user, lead, service = context
    app.dependency_overrides[get_current_user] = lambda: user
    service.side_effect = ValueError('PRIVATE database details')
    response = client.get(path(user, lead)+'?cursor=invalid')
    assert response.status_code == 400
    assert 'PRIVATE' not in response.text
