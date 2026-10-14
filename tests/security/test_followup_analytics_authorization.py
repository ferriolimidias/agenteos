from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import analytics
from app.api.routers.auth import get_current_user
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE


def test_followup_analytics_requires_auth_and_enforces_tenant(monkeypatch):
    app = FastAPI()
    app.include_router(analytics.router)
    tenant = uuid4()
    other_tenant = uuid4()
    current_user = SimpleNamespace(id=uuid4(), empresa_id=tenant, email="synthetic@example.invalid", role=ADMIN_EMPRESA_ROLE)
    dashboard = AsyncMock(return_value={"summary": {}, "coverage": {}})
    evidence = AsyncMock(return_value={"items": [], "total": 0})
    monkeypatch.setattr(analytics, "followup_analytics_dashboard", dashboard)
    monkeypatch.setattr(analytics, "followup_analytics_drilldown", evidence)

    async def isolated_db():
        yield object()

    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    with TestClient(app) as client:
        path = f"/api/empresas/{tenant}/analytics/followup-analytics"
        assert client.get(path).status_code == 200
        assert dashboard.await_args.kwargs["empresa_id"] == tenant
        assert client.get(f"/api/empresas/{other_tenant}/analytics/followup-analytics").status_code == 403
        assert client.get(f"/api/empresas/{tenant}/analytics/followup-analytics?mode=bad").status_code == 422
        assert client.get(f"/api/empresas/{tenant}/analytics/followup-analytics/evidence?kind=bad").status_code == 422
        assert client.get(f"/api/empresas/{tenant}/analytics/followup-analytics/evidence?kind=replied&limit=10&offset=20").status_code == 200
        assert evidence.await_args.kwargs["empresa_id"] == tenant
        assert evidence.await_args.kwargs["kind"] == "replied"
        assert evidence.await_args.kwargs["limit"] == 10
        assert evidence.await_args.kwargs["offset"] == 20


def test_followup_analytics_rejects_anonymous_users(monkeypatch):
    app = FastAPI()
    app.include_router(analytics.router)
    dashboard = AsyncMock(return_value={})
    monkeypatch.setattr(analytics, "followup_analytics_dashboard", dashboard)

    async def isolated_db():
        yield object()

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        response = client.get(f"/api/empresas/{uuid4()}/analytics/followup-analytics")
    assert response.status_code == 401
    dashboard.assert_not_awaited()
