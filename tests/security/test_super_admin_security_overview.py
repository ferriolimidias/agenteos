from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.main import app
from app.api.http_route_policy import policy_for_route
from app.api.routers.empresas import obter_visao_seguranca_admin
from app.api.routers.auth import require_super_admin
from fastapi import HTTPException


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ReadOnlyDb:
    def __init__(self, results):
        self._results = iter(results)

    async def execute(self, _statement):
        return _Rows(next(self._results))


@pytest.mark.asyncio
async def test_security_overview_returns_only_company_access_and_prompt_audit_metadata():
    company_id = uuid4()
    user_id = uuid4()
    audit_id = uuid4()
    db = _ReadOnlyDb([
        [(company_id, "Empresa A")],
        [
            (user_id, company_id, "Admin Cliente", "admin@example.test", "admin_empresa", True),
            (uuid4(), company_id, "Super Admin", "operator@example.test", "super_admin", True),
        ],
        [(company_id, "Empresa A", "operator-id", "version_approved", "prompt_version", audit_id, datetime(2026, 10, 17, 10, 0))],
    ])

    result = await obter_visao_seguranca_admin(db)

    assert result["companies"] == [{"id": str(company_id), "name": "Empresa A", "user_count": 1}]
    assert result["users"] == [{
        "id": str(user_id),
        "company_id": str(company_id),
        "company_name": "Empresa A",
        "name": "Admin Cliente",
        "email": "admin@example.test",
        "role": "admin_empresa",
        "active": True,
    }]
    assert result["prompt_audit"]["items"] == [{
        "company_id": str(company_id),
        "company_name": "Empresa A",
        "actor_id": "operator-id",
        "action": "version_approved",
        "object_type": "prompt_version",
        "object_id": str(audit_id),
        "created_at": "2026-10-17T10:00:00",
    }]
    assert result["general_admin_audit_available"] is False
    assert "details_json" not in result["prompt_audit"]["items"][0]
    assert "password" not in result["users"][0]


def test_security_overview_is_registered_as_super_admin_only():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/admin/security/overview")
    assert policy_for_route(route, "GET") == "SUPER_ADMIN_ONLY"


def test_security_overview_rejects_tenant_and_meta_reviewer_roles():
    async def check():
        allowed = SimpleNamespace(role="super_admin", email="operator@example.test")
        assert await require_super_admin(allowed) is allowed
        for role in ("admin_empresa", "meta_app_reviewer"):
            with pytest.raises(HTTPException) as denied:
                await require_super_admin(SimpleNamespace(role=role, email="tenant@example.test"))
            assert denied.value.status_code == 403

    import asyncio
    asyncio.run(check())
