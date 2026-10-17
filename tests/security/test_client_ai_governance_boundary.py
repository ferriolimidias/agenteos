"""Client users cannot read or change Super Admin prompt governance."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import atendimento_modular, empresas, jev, prompt_optimizer
from app.api.routers.auth import get_current_user
from db.database import get_db
from app.schemas import IAConfigUpdateRequest

META_TOKEN_FIELD = "meta_access_" + "token"


class _Scalars:
    def __init__(self, first=None, items=None):
        self._first = first
        self._items = list(items or [])

    def first(self):
        return self._first

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, first=None, items=None):
        self._scalars = _Scalars(first, items)

    def scalars(self):
        return self._scalars


class _ReadDB:
    def __init__(self, company):
        self.company = company
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _Result(first=self.company)
        return _Result(items=["22222222-2222-2222-2222-222222222222"])

    async def commit(self):
        return None

    async def refresh(self, _company):
        return None

    async def rollback(self):
        return None


@pytest.fixture
def auth_api(monkeypatch):
    company_id = uuid4()
    company = SimpleNamespace(
        id=company_id,
        nome_empresa="Synthetic Company",
        area_atuacao="Synthetic Services",
        logo_url=None,
        conexao_disparo_id=None,
        ia_personalidade="private synthetic prompt",
        ia_regras_negocio="synthetic business rules",
        nome_agente="Synthetic Agent",
        mensagem_saudacao="Synthetic greeting",
        conversion_monitor_prompt="private conversion prompt",
        modelo_ia="private-model-id",
        modelo_ia_reasoning_effort=None,
        credenciais_canais={"openai_api_" + "key": "SYNTHETIC-CREDENTIAL"},
        openai_api_key=None,
        meta_capi_ativo=True,
        meta_pixel_id="synthetic-pixel-id",
        disparo_delay_min=3,
        disparo_delay_max=7,
        limite_certeza=0.65,
        limite_duvida=0.45,
        max_agentes_desempate=3,
        status_openai="ok",
        telefone_notificacao="+5500000000000",
    )
    setattr(company, "meta_access_" + "token", "SYNTHETIC-META-CREDENTIAL")
    db = _ReadDB(company)
    app = FastAPI()
    app.include_router(empresas.router, prefix="/api")
    app.include_router(atendimento_modular.router)
    app.include_router(prompt_optimizer.router)
    app.include_router(jev.router)
    user = SimpleNamespace(id=uuid4(), empresa_id=company_id, email="synthetic@example.invalid", role="admin_empresa")

    async def db_dependency():
        yield db

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    yield app, client, user, company_id, db
    client.close()
    app.dependency_overrides.clear()


def test_tenant_ia_config_read_returns_operational_summary_without_prompts_or_model(auth_api):
    _, client, user, company_id, db = auth_api
    response = client.get(f"/api/empresas/{company_id}/ia-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "atendimento_automatico_estado": "configurado",
        "tags_ia_habilitadas": ["22222222-2222-2222-2222-222222222222"],
    }
    assert "private-model-id" not in response.text
    assert "SYNTHETIC-CREDENTIAL" not in response.text
    assert "telefone_notificacao" not in payload
    assert "status_openai" not in payload
    assert user.role == "admin_empresa" and db.calls == 2


def test_tenant_company_read_excludes_prompt_and_meta_credential_internals(auth_api):
    _, client, _user, company_id, db = auth_api
    response = client.get(f"/api/empresas/{company_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(company_id),
        "nome_empresa": "Synthetic Company",
        "area_atuacao": "Synthetic Services",
        "logo_url": None,
        "conexao_disparo_id": None,
    }
    assert "private synthetic prompt" not in response.text
    assert "SYNTHETIC-META-CREDENTIAL" not in response.text
    assert "synthetic-pixel-id" not in response.text
    assert db.calls == 1


def test_tenant_cannot_read_another_company_through_legacy_company_route(auth_api):
    _, client, _user, _company_id, db = auth_api
    response = client.get(f"/api/empresas/{uuid4()}")
    assert response.status_code == 403
    assert db.calls == 0


def test_super_admin_keeps_full_company_detail_without_plaintext_meta_credential(auth_api):
    _, client, user, company_id, _db = auth_api
    user.role = "super_admin"
    user.empresa_id = None
    response = client.get(f"/api/empresas/{company_id}")
    assert response.status_code == 200
    assert response.json()["ia_personalidade"] == "private synthetic prompt"
    assert response.json()["meta_access_token_configurado"] is True
    assert "SYNTHETIC-META-CREDENTIAL" not in response.text


def test_tenant_can_update_only_basic_company_and_connection_data(auth_api):
    _, client, _user, company_id, db = auth_api
    response = client.put(f"/api/empresas/{company_id}", json={"nome_empresa": "Nome atualizado"})
    assert response.status_code == 200
    assert response.json() == {
        "id": str(company_id),
        "nome_empresa": "Nome atualizado",
        "area_atuacao": "Synthetic Services",
        "logo_url": None,
        "conexao_disparo_id": None,
    }
    assert db.company.nome_empresa == "Nome atualizado"


def test_super_admin_can_update_company_prompt_and_meta_credential(auth_api):
    _, client, user, company_id, db = auth_api
    user.role = "super_admin"
    user.empresa_id = None
    payload = {
        "ia_personalidade": "new synthetic prompt",
        META_TOKEN_FIELD: "synthetic-replacement-value",
    }
    response = client.put(f"/api/empresas/{company_id}", json=payload)
    assert response.status_code == 200
    assert db.company.ia_personalidade == "new synthetic prompt"
    assert db.company.meta_access_token == "synthetic-replacement-value"
    assert "synthetic-replacement-value" not in response.text


@pytest.mark.parametrize("field,value", [
    ("ia_personalidade", "attacker prompt"),
    ("ia_regras_negocio", "attacker business rules"),
    ("disparo_delay_min", 0),
    ("disparo_delay_max", 0),
    ("limite_certeza", 0.99),
    ("max_agentes_desempate", 9),
    ("meta_capi_ativo", True),
    ("meta_pixel_id", "attacker-pixel"),
    (META_TOKEN_FIELD, "SYNTHETIC-ATTACKER-TOKEN"),
    ("remover_meta_access_token", True),
])
def test_tenant_cannot_change_prompt_ai_engine_or_meta_credentials_via_legacy_company_update(auth_api, field, value):
    _, client, _user, company_id, db = auth_api
    response = client.put(f"/api/empresas/{company_id}", json={field: value})
    assert response.status_code == 403
    assert "SYNTHETIC-ATTACKER-TOKEN" not in response.text
    assert db.calls == 0


def test_super_admin_retains_full_tenant_scoped_ia_configuration_read(auth_api):
    _, client, user, company_id, _db = auth_api
    user.role = "super_admin"
    user.empresa_id = None

    response = client.get(f"/api/empresas/{company_id}/ia-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["modelo_ia"] == "private-model-id"
    assert payload["ia_personalidade"] == "private synthetic prompt"
    assert "SYNTHETIC-CREDENTIAL" not in response.text


@pytest.mark.parametrize("field,value", [
    ("modelo_ia", "another-model"),
    ("modelo_ia_reasoning_effort", "high"),
    ("ia_personalidade", "attacker prompt"),
    ("conversion_monitor_prompt", "attacker conversion prompt"),
    ("ia_regras_negocio", "attacker rules"),
    ("followup_ativo", True),
])
@pytest.mark.parametrize("method", ["put", "post"])
def test_tenant_cannot_mutate_prompt_model_or_agent_settings_even_via_legacy_api(auth_api, field, value, method):
    _, client, _user, company_id, db = auth_api
    response = getattr(client, method)(f"/api/empresas/{company_id}/ia-config", json={field: value})

    assert response.status_code == 403
    assert "Super Admin" not in response.text
    assert db.calls == 0


@pytest.mark.parametrize("role", ["admin_empresa", "meta_app_reviewer", "atendente"])
def test_non_super_admin_cannot_read_or_mutate_prompt_optimizer_or_jev(auth_api, role):
    _, client, user, company_id, _db = auth_api
    user.role = role

    assert client.get(f"/api/empresas/{company_id}/prompt-optimizer").status_code == 403
    assert client.post(f"/api/empresas/{company_id}/prompt-optimizer/bootstrap").status_code == 403
    assert client.get(f"/api/empresas/{company_id}/jev/status").status_code == 403
    assert client.get(f"/api/empresas/{company_id}/jev/examples").status_code == 403
    assert client.post(f"/api/empresas/{company_id}/jev/analyze", json={"example_id": "synthetic_demo"}).status_code == 403


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/monitor/funnel", None),
    ("post", "/monitor/funnel/preview", None),
    ("post", "/monitor/funnel/sync", None),
    ("get", "/atendimento-modular/status", None),
    ("get", "/atendimento-modular/examples", None),
    ("post", "/atendimento-modular/preview", {"message": "pergunta sintética", "use_jev": True}),
    ("get", "/atendimento-modular/runs", None),
])
def test_customer_cannot_access_technical_funnel_or_a07_controls(auth_api, method, path, payload):
    _, client, _user, company_id, db = auth_api
    url = f"/api/empresas/{company_id}{path}"
    response = getattr(client, method)(url, json=payload) if payload is not None else getattr(client, method)(url)
    assert response.status_code == 403
    assert db.calls == 0


def test_tenant_cannot_use_prompt_or_jev_routes_for_another_company(auth_api):
    _, client, user, _company_id, _db = auth_api
    other_company = uuid4()
    assert client.get(f"/api/empresas/{other_company}/prompt-optimizer").status_code == 403
    assert client.get(f"/api/empresas/{other_company}/jev/status").status_code == 403
    assert user.role == "admin_empresa"


def test_customer_can_still_change_only_the_official_tag_allowlist():
    user = SimpleNamespace(role="admin_empresa", email="tenant@example.invalid")
    allowed = IAConfigUpdateRequest(tags_ia_habilitadas=["22222222-2222-2222-2222-222222222222"])
    empresas._require_ia_config_update_permission(allowed, user)


def test_super_admin_can_open_tenant_scoped_optimizer_and_jev_admin_routes(auth_api, monkeypatch):
    _, client, user, company_id, _db = auth_api
    user.role = "super_admin"
    user.empresa_id = None
    dashboard = AsyncMock(return_value={"modules": [], "experiments": []})
    status = AsyncMock(return_value={"state": "unknown", "coverage": "partial"})
    monkeypatch.setattr(prompt_optimizer.service, "dashboard", dashboard)
    monkeypatch.setattr(jev.jev_shadow, "status", status)
    target_company = uuid4()

    optimizer_response = client.get(f"/api/empresas/{target_company}/prompt-optimizer")
    jev_response = client.get(f"/api/empresas/{target_company}/jev/status")

    assert optimizer_response.status_code == 200
    assert jev_response.status_code == 200
    assert dashboard.await_args.args[1] == target_company
    assert status.await_args.args[1] == target_company
