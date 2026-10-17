"""Technical Meta credentials and diagnostics are controlled by Super Admin."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routers import conexoes, instagram_simple, traffic_meta
from app.api.routers.auth import get_current_user
from db.database import get_db

INSTAGRAM_TOKEN_FIELD = "instagram_user_" + "access_token"
ENCRYPTED_TOKEN_FIELD = "encrypted_" + "access_token"
ACCESS_TOKEN_FIELD = "access_" + "token"


class _Scalars:
    def first(self):
        return None

    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _ReadOnlyEmptyDB:
    async def execute(self, _statement):
        return _Result()


@pytest.fixture
def meta_api():
    empresa_id = uuid4()
    user = SimpleNamespace(id=uuid4(), empresa_id=empresa_id, email="cliente@example.invalid", role="admin_empresa")
    app = FastAPI()
    app.include_router(instagram_simple.router)
    app.include_router(traffic_meta.router, prefix="/api")

    async def db_dependency():
        yield _ReadOnlyEmptyDB()

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    yield client, user, empresa_id
    client.close()
    app.dependency_overrides.clear()


def test_tenant_cannot_create_or_replace_instagram_credential_via_api(meta_api):
    client, _user, empresa_id = meta_api
    response = client.post(
        f"/api/empresas/{empresa_id}/instagram/connection",
        json={"instagram_id": "synthetic-account", INSTAGRAM_TOKEN_FIELD: "synthetic-placeholder-value"},
    )
    assert response.status_code == 403


def test_client_meta_config_read_is_limited_to_campaign_context(meta_api):
    client, _user, empresa_id = meta_api
    response = client.get(f"/api/empresas/{empresa_id}/traffic/meta/config")
    assert response.status_code == 200
    payload = response.json()
    assert "marketing_graph_version" not in payload
    assert "admin_fallback" not in payload
    assert "meta_write_enabled" not in payload
    assert payload["config"]["business_id"] is None
    assert payload["config"]["ad_account_id"] is None
    assert payload["config"]["dataset_id"] is None


def test_instagram_connection_list_excludes_admin_credential_metadata(meta_api, monkeypatch):
    client, _user, empresa_id = meta_api
    monkeypatch.setattr(instagram_simple, "serialize_connection", lambda _row: {
        "id": "synthetic-connection", "connection_id": "synthetic-connection", "name": "Conta de teste",
        "status": "connected", "instagram_username": "@synthetic", "instagram_account_id": "private-id",
        "permissions": ["synthetic_permission"], "token_expires_at": "2030-01-01T00:00:00Z",
        ENCRYPTED_TOKEN_FIELD: "must-not-be-returned",
    })

    class _OneRowDB:
        async def execute(self, _statement):
            class _OneScalars:
                def all(self):
                    return [object()]
            class _OneResult:
                def scalars(self):
                    return _OneScalars()
            return _OneResult()

    original = client.app.dependency_overrides[get_db]

    async def one_row_dependency():
        yield _OneRowDB()

    client.app.dependency_overrides[get_db] = one_row_dependency
    try:
        response = client.get(f"/api/empresas/{empresa_id}/instagram/connections")
    finally:
        client.app.dependency_overrides[get_db] = original

    assert response.status_code == 200
    assert response.json()["connections"] == [{
        "id": "synthetic-connection", "connection_id": "synthetic-connection", "name": "Conta de teste",
        "status": "connected", "instagram_username": "@synthetic",
    }]
    assert "must-not-be-returned" not in response.text


@pytest.mark.parametrize("method,path,payload", [
    ("put", "/traffic/meta/config", {"ad_account_id": "synthetic-account"}),
    ("get", "/traffic/meta/credentials/status", None),
    ("get", "/traffic/meta/conversions", None),
    ("get", "/traffic/meta/conversions/history", None),
    ("post", "/traffic/meta/conversions/token", {ACCESS_TOKEN_FIELD: "synthetic-placeholder-value"}),
    ("post", "/traffic/meta/conversions/setup", None),
])
def test_tenant_cannot_read_or_mutate_technical_meta_configuration(meta_api, method, path, payload):
    client, _user, empresa_id = meta_api
    response = getattr(client, method)(f"/api/empresas/{empresa_id}{path}", json=payload) if payload is not None else getattr(client, method)(f"/api/empresas/{empresa_id}{path}")
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["admin_empresa", "atendente", "meta_app_reviewer"])
def test_meta_oauth_administration_is_super_admin_only(role):
    user = SimpleNamespace(email="tenant@example.invalid", role=role, empresa_id=uuid4())
    with pytest.raises(HTTPException) as caught:
        conexoes._ensure_meta_marketing_admin(user, user.empresa_id)
    assert caught.value.status_code == 403


def test_super_admin_can_administer_tenant_meta_credentials():
    user = SimpleNamespace(email="root@example.invalid", role="super_admin", empresa_id=None)
    conexoes._ensure_meta_marketing_admin(user, uuid4())
    traffic_meta._ensure_meta_platform_admin(user, uuid4())


def test_customer_campaign_readiness_projection_omits_internal_meta_probes_and_credentials():
    safe = traffic_meta._customer_campaign_readiness({
        "ready": False,
        "reason": "TENANT_MARKETING_PERMISSION_REQUIRED",
        "credential": {"source": "synthetic-marketing-token", "id": "credential-id"},
        "probe_results": [{"meta_goal": "OUTCOME_LEADS", "error": "provider diagnostic"}],
        "status": {"dataset_id": "private-dataset"},
        "optimization_eligibility": {"state": "partial", "details": "internal"},
    })

    assert safe == {
        "ready": False,
        "message": "A equipe Ferrioli está concluindo a configuração necessária para esta campanha.",
    }
    assert "credential" not in safe and "probe_results" not in safe
    assert "private-dataset" not in str(safe)


def test_customer_meta_assets_projection_keeps_campaign_choices_but_hides_technical_ids_and_credential_metadata():
    safe = traffic_meta._customer_meta_assets({
        "credential": {"source": "synthetic-token-source", "permissions": ["ads_management"]},
        "ad_account": {"id": "act-private", "business_id": "private-business", "name": "Conta comercial"},
        "pages": [{"id": "page-private", "name": "Página da empresa", "selected": True,
                   "instagram_account_id": "instagram-private", "instagram_username": "synthetic"}],
        "selected_page": {"id": "page-private"},
        "instagram": {"account_id": "instagram-private", "account_id_masked": "••••vate", "username": "synthetic", "linked_to_page": True},
        "dataset": {"id": "dataset-private", "source": "internal"},
        "asset_graph_valid": True,
    }, [{"connection_id": "whatsapp-connection", "label": "WhatsApp Oficial", "name": "Principal",
         "status": "connected", "page_compatible": True, "compatibility_source": "credential-check",
         "dataset_id": "dataset-private"}])

    assert safe["pages"] == [{"id": "page-private", "name": "Página da empresa", "selected": True,
                              "instagram_linked": True, "instagram_username": "synthetic"}]
    assert safe["selected_page"]["id"] == "page-private"
    assert safe["whatsapps"] == [{"connection_id": "whatsapp-connection", "label": "WhatsApp Oficial",
                                  "name": "Principal", "status": "connected", "page_compatible": True}]
    serialized = str(safe)
    for internal_value in ("synthetic-token-source", "private-business", "instagram-private", "dataset-private", "credential-check"):
        assert internal_value not in serialized


def test_customer_pages_projection_keeps_choices_and_drops_graph_transport_metadata():
    safe = traffic_meta._customer_meta_pages([{
        "id": "synthetic-page-id",
        "name": "Página de teste",
        "tasks": ["ADVERTISE"],
        "access_" + "token": "never-return-this",
        "instagram_business_account": {"id": "synthetic-instagram-id", "username": "loja_teste"},
        "credential_source": "admin_managed",
    }])

    assert safe == [{
        "id": "synthetic-page-id",
        "name": "Página de teste",
        "instagram_linked": True,
        "instagram_username": "loja_teste",
    }]
    assert "never-return-this" not in str(safe)
    assert "credential_source" not in str(safe)
    assert "synthetic-instagram-id" not in str(safe)


def test_customer_existing_posts_projection_hides_account_and_transport_metadata():
    safe = traffic_meta._customer_existing_posts_response({
        "source_platform": "instagram",
        "account": {
            "ad_account_id": "synthetic-ad-account",
            "page_id": "synthetic-page-id",
            "page_name": "Página de teste",
            "instagram_account_id": "synthetic-instagram-id",
            "instagram_username": "loja_teste",
        },
        "credential": {"source": "private-source", "tenant_id": "private-tenant", "capability": "ADS_READ"},
        "graph_api_version": "v99.0",
        "items": [{
            "source_id": "synthetic-media-id",
            "caption": "Publicação sintética",
            "id_mapping": {
                "source_platform": "instagram",
                "page_id": "synthetic-page-id",
                "instagram_account_id": "synthetic-instagram-id",
                "media_id": "synthetic-media-id",
                "creative_id": None,
                "comment_id": None,
            },
        }],
        "next_cursor": None,
        "has_more": False,
        "query_scope": "current_graph_page",
        "write_performed": False,
    })

    assert safe["account"] == {
        "page_id": "synthetic-page-id",
        "page_name": "Página de teste",
        "instagram_username": "loja_teste",
        "instagram_linked": True,
    }
    assert "credential" not in safe and "graph_api_version" not in safe
    assert "synthetic-instagram-id" not in str(safe)
    assert "synthetic-ad-account" not in str(safe)
    assert safe["items"][0]["id_mapping"]["media_id"] == "synthetic-media-id"


def test_customer_preview_projection_exposes_only_safe_operation_status():
    safe = traffic_meta._customer_existing_post_preview({
        "state": "simulated",
        "graph_api_version": "v99.0",
        "id_mapping": {"page_id": "internal-page-id"},
        "builder_preview": {"optimization_goal": "INTERNAL_VALUE"},
        "credential": {"source": "private-source"},
    })

    assert safe["meta_write_performed"] is False
    assert safe["database_write_performed"] is False
    assert safe["summary"]["destination"] == "WhatsApp"
    assert "graph_api_version" not in safe and "builder_preview" not in safe
    assert "private-source" not in str(safe)
