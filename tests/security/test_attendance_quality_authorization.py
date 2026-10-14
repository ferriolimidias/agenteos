from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import analytics
from app.api.routers.auth import get_current_user
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE


@pytest.fixture
def context(monkeypatch):
    app = FastAPI()
    app.include_router(analytics.router)
    tenant = uuid4()
    user = SimpleNamespace(id=uuid4(), empresa_id=tenant, email="synthetic@example.invalid", role=ADMIN_EMPRESA_ROLE)
    service = AsyncMock(return_value={"metrics": {}, "coverage": {}})

    async def isolated_db():
        yield object()

    app.dependency_overrides[get_db] = isolated_db
    monkeypatch.setattr(analytics, "attendance_quality_dashboard", service)
    evidence_service = AsyncMock(return_value={"items": [], "total": 0})
    monkeypatch.setattr(analytics, "attendance_quality_drilldown", evidence_service)
    with TestClient(app) as client:
        yield app, client, user, service, evidence_service


def path(tenant):
    return f"/api/empresas/{tenant}/analytics/attendance-quality"


def test_quality_requires_authentication_and_never_reads_without_it(context):
    _app, client, user, service, evidence_service = context
    assert client.get(path(user.empresa_id)).status_code == 401
    assert client.get(path(user.empresa_id), headers={"Authorization": "Bearer invalid-token"}).status_code == 401
    service.assert_not_awaited()
    assert client.get(path(user.empresa_id) + "/evidence?category=question_without_observed_response").status_code == 401
    evidence_service.assert_not_awaited()


def test_quality_scopes_tenant_and_sanitizes_service_errors(context):
    app, client, user, service, evidence_service = context
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(path(uuid4())).status_code == 403
    assert client.get(path(user.empresa_id)).status_code == 200
    assert service.await_args.kwargs["empresa_id"] == user.empresa_id
    service.side_effect = ValueError("private database detail")
    response = client.get(path(user.empresa_id))
    assert response.status_code == 422
    assert "private" not in response.text.lower()
    service.side_effect = None
    assert client.get(path(uuid4()) + "/evidence?category=question_without_observed_response").status_code == 403
    response = client.get(path(user.empresa_id) + "/evidence?category=question_without_observed_response&limit=10&offset=20")
    assert response.status_code == 200
    assert evidence_service.await_args.kwargs["empresa_id"] == user.empresa_id
    assert evidence_service.await_args.kwargs["limit"] == 10
    assert evidence_service.await_args.kwargs["offset"] == 20


def test_quality_filters_are_allowlisted_before_service(context):
    app, client, user, service, evidence_service = context
    app.dependency_overrides[get_current_user] = lambda: user
    response = client.get(path(user.empresa_id) + "?ad_id=1%20OR%201%3D1")
    assert response.status_code == 422
    service.assert_not_awaited()
