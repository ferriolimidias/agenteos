from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.api.http_route_policy import PUBLIC_ROUTE_ALLOWLIST, policy_for_route
from app.api.main import app
from app.api.routers import empresas as empresas_router
from app.api.routers import inbox as inbox_router
from app.api.routers import conexoes as conexoes_router
from app.api.routers import configuracoes as configuracoes_router
from app.api.routers import webhook as webhook_router
from app.api.routers.auth import get_current_user, require_super_admin, require_tenant_access
from app.services.simulator_session import simulator_response_key
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE, ROOT_ADMIN_ROLE, Usuario


def _user(*, empresa_id, role=ADMIN_EMPRESA_ROLE, email="tenant@example.invalid") -> Usuario:
    return Usuario(
        id=uuid4(),
        empresa_id=empresa_id,
        nome="Test user",
        email=email,
        senha_hash="not-a-real-password-hash",
        role=role,
        ativo=True,
    )


def _request(path: str = "/api/empresas/{empresa_id}/crm") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
            "root_path": "",
        }
    )


def _provider_request(path: str, *, body: bytes, headers: dict[str, str]) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
            "root_path": "",
        },
        receive,
    )


class _Scalars:
    def first(self):
        return None

    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _ObjectScalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return [self.value] if self.value is not None else []


class _ObjectResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _ObjectScalars(self.value)


class _RowsScalars:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)


class _RowsResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalars(self):
        return _RowsScalars(self.rows)


class _RowsDB:
    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, _statement):
        return _RowsResult(self.rows)


class _RecordingDB:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.added = []

    async def execute(self, statement):
        compiled = statement.compile()
        values = " ".join(str(value) for value in compiled.params.values())
        self.statements.append(f"{compiled.string.lower()} {values.lower()}")
        return _Result()

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _QueuedDB(_RecordingDB):
    def __init__(self, rows):
        super().__init__()
        self.rows = list(rows)

    async def execute(self, statement):
        compiled = statement.compile()
        values = " ".join(str(value) for value in compiled.params.values())
        self.statements.append(f"{compiled.string.lower()} {values.lower()}")
        return _ObjectResult(self.rows.pop(0))

    async def refresh(self, _item):
        return None


@pytest.fixture
def api_client():
    previous = dict(app.dependency_overrides)

    async def no_database_access():
        yield object()

    app.dependency_overrides[get_db] = no_database_access
    try:
        client = TestClient(app)
        yield client
        client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_every_registered_http_route_has_an_explicit_policy():
    registered = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods") and hasattr(route, "path")
        for method in route.methods or set()
    }
    classified = {
        (method, route.path): policy_for_route(route, method)
        for route in app.routes
        if hasattr(route, "methods") and hasattr(route, "path")
        for method in route.methods or set()
    }

    unclassified = sorted(key for key, policy in classified.items() if policy is None)
    stale_allowlist = sorted(set(PUBLIC_ROUTE_ALLOWLIST) - registered)

    assert not unclassified, f"HTTP routes missing explicit policy: {unclassified}"
    assert not stale_allowlist, f"Public allowlist entries no longer registered: {stale_allowlist}"
    assert len(registered) >= 280


def test_plain_jwt_is_not_a_tenant_policy_for_company_scoped_routes():
    async def company_route(empresa_id: str, _user=Depends(get_current_user)):
        return {"empresa_id": empresa_id}

    async def global_route(_user=Depends(get_current_user)):
        return {"ok": True}

    async def connection_resource_route(conexao_id: str, _user=Depends(get_current_user)):
        return {"conexao_id": conexao_id}

    tenant_route = APIRoute(
        path="/api/empresas/{empresa_id}/unsafe",
        endpoint=company_route,
        methods=["GET"],
    )
    authenticated_global_route = APIRoute(
        path="/api/admin/status",
        endpoint=global_route,
        methods=["GET"],
    )
    unscoped_connection_route = APIRoute(
        path="/api/conexoes/{conexao_id}/delete",
        endpoint=connection_resource_route,
        methods=["DELETE"],
    )

    assert policy_for_route(tenant_route, "GET") is None
    assert policy_for_route(unscoped_connection_route, "DELETE") is None
    assert policy_for_route(authenticated_global_route, "GET") == "AUTHENTICATED_GLOBAL"


def test_private_routes_reject_missing_and_invalid_tokens(api_client):
    tenant_id = uuid4()
    path = f"/api/empresas/{tenant_id}/crm"

    missing = api_client.get(path)
    invalid = api_client.get(path, headers={"Authorization": "Bearer invalid-token"})

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_tenant_a_cannot_read_or_write_tenant_b_or_delete_company(api_client):
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = _user(empresa_id=tenant_a)
    app.dependency_overrides[get_current_user] = lambda: user_a

    cross_tenant_read = api_client.get(f"/api/empresas/{tenant_b}/exportar-leads")
    cross_tenant_inbox_read = api_client.get(f"/api/empresas/{tenant_b}/inbox")
    cross_tenant_send = api_client.post(
        f"/api/empresas/{tenant_b}/inbox/5511999999999/send",
        json={"texto": "must not be sent"},
    )
    company_delete = api_client.delete(f"/api/empresas/{tenant_b}")

    assert cross_tenant_read.status_code == 403
    assert cross_tenant_inbox_read.status_code == 403
    assert cross_tenant_send.status_code == 403
    assert company_delete.status_code == 403


def test_critical_tenant_routes_reject_missing_and_foreign_tenant_access(api_client):
    tenant_a, tenant_b = uuid4(), uuid4()
    resource_id = uuid4()
    user_a = _user(empresa_id=tenant_a)
    path = f"/api/empresas/{tenant_b}"
    requests = [
        ("get", f"{path}/crm", {}),
        ("get", f"{path}/exportar-leads", {}),
        ("get", f"{path}/crm/tags/oficiais", {}),
        ("post", f"{path}/crm/tags/oficiais", {"json": {"nome": "blocked-tag"}}),
        ("delete", f"{path}/crm/leads/{resource_id}", {}),
        ("put", f"{path}/agenda", {"json": {"agenda_config": {}}}),
        ("post", f"{path}/leads/{resource_id}/transferir_manual", {"json": {"destino_id": str(resource_id)}}),
        ("get", f"{path}/followups", {}),
        ("post", f"{path}/followups", {"json": {"nome": "blocked-followup"}}),
        ("get", f"{path}/inbox", {}),
        ("get", f"{path}/attribution/leads/{resource_id}", {}),
        ("get", f"{path}/attribution/summary/campaigns", {}),
        ("post", f"{path}/inbox/5511999999999/send", {"json": {"texto": "must not be sent"}}),
        (
            "post",
            f"{path}/inbox/5511999999999/send_media",
            {"files": {"file": ("blocked.txt", b"test", "text/plain")}},
        ),
        ("post", f"{path}/inbox/5511999999999/pausar_bot", {}),
        ("post", f"{path}/inbox/5511999999999/reativar_bot", {}),
    ]

    # Without a token all of the representative tenant surfaces reject the
    # request before their handlers can read or mutate production data.
    for method, route_path, kwargs in requests:
        response = getattr(api_client, method)(route_path, **kwargs)
        assert response.status_code == 401, f"{method.upper()} {route_path}: {response.status_code}"

    app.dependency_overrides[get_current_user] = lambda: user_a
    try:
        # A valid user from tenant A still cannot address tenant B through a
        # tenant URL, including operations that could send or mutate data.
        for method, route_path, kwargs in requests:
            response = getattr(api_client, method)(route_path, **kwargs)
            assert response.status_code == 403, f"{method.upper()} {route_path}: {response.status_code}"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_tenant_policy_allows_only_its_own_tenant_and_super_admin_is_global():
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a = _user(empresa_id=tenant_a)
    same_tenant = asyncio.run(
        require_tenant_access(
            empresa_id=str(tenant_a),
            request=_request(),
            current_user=user_a,
        )
    )
    assert same_tenant is user_a

    with pytest.raises(HTTPException) as denied:
        asyncio.run(
            require_tenant_access(
                empresa_id=str(tenant_b),
                request=_request(),
                current_user=user_a,
            )
        )
    assert getattr(denied.value, "status_code", None) == 403

    root = _user(empresa_id=None, role=ROOT_ADMIN_ROLE, email="root@example.invalid")
    assert asyncio.run(require_super_admin(current_user=root)) is root


def test_foreign_resource_ids_are_scoped_in_database_queries(monkeypatch):
    tenant_a, tenant_b, resource_b = uuid4(), uuid4(), uuid4()
    fake_db = _RecordingDB()

    with pytest.raises(HTTPException) as lead_denied:
        asyncio.run(
            empresas_router.atualizar_lead_crm(
                empresa_id=str(tenant_a),
                lead_id=str(resource_b),
                data=empresas_router.CRMLeadUpdateRequest(),
                db=fake_db,
                background_tasks=None,
            )
        )
    assert lead_denied.value.status_code == 404
    assert f"crm_lead" in fake_db.statements[-1] or "crm_leads" in fake_db.statements[-1]
    assert str(tenant_a) in fake_db.statements[-1]
    assert str(resource_b) in fake_db.statements[-1]

    fake_db = _RecordingDB()
    response = asyncio.run(empresas_router.exportar_leads_csv(empresa_id=str(tenant_a), db=fake_db))
    assert response.status_code == 200
    assert str(tenant_a) in fake_db.statements[-1]
    assert "crm_leads.empresa_id" in fake_db.statements[-1]

    fake_db = _RecordingDB()
    with pytest.raises(HTTPException) as outbound_denied:
        asyncio.run(
            inbox_router.enviar_mensagem(
                empresa_id=str(tenant_a),
                telefone="5511999999999",
                payload=inbox_router.SendMessagePayload(texto="must not be sent"),
                db=fake_db,
            )
        )
    assert outbound_denied.value.status_code == 404
    assert "crm_leads.empresa_id" in fake_db.statements[0]
    assert str(tenant_a) in fake_db.statements[0]

    fake_db = _RecordingDB()
    monkeypatch.setattr(inbox_router, "AsyncSessionLocal", lambda: _AsyncSessionContext(fake_db))
    with pytest.raises(HTTPException) as conversation_denied:
        asyncio.run(
            inbox_router.listar_mensagens_conversa_multicanal(
                empresa_id=str(tenant_a),
                conversation_id=str(resource_b),
            )
        )
    assert conversation_denied.value.status_code == 404
    assert "channel_conversations.empresa_id" in fake_db.statements[0]
    assert str(tenant_a) in fake_db.statements[0]
    assert str(resource_b) in fake_db.statements[0]

    fake_db = _RecordingDB()
    with pytest.raises(HTTPException) as connection_denied:
        asyncio.run(
            conexoes_router.status_conexao(
                conexao_id=str(resource_b),
                db=fake_db,
                current_user=_user(empresa_id=tenant_a),
            )
        )
    assert connection_denied.value.status_code == 404
    assert "conexoes.empresa_id" in fake_db.statements[0]
    assert str(tenant_a) in fake_db.statements[0]


def test_get_crm_with_missing_funnel_does_not_write_defaults():
    tenant_id = uuid4()
    fake_db = _RecordingDB()

    response = asyncio.run(empresas_router.obter_crm(empresa_id=str(tenant_id), db=fake_db))

    assert response == {"funil_id": "", "funil_nome": "", "etapas": [], "colunas_tags": []}
    assert fake_db.commits == 0
    assert fake_db.added == []


def test_get_global_configuration_defaults_do_not_persist():
    fake_db = _RecordingDB()

    response = asyncio.run(configuracoes_router.get_configuracoes_globais(db=fake_db, _=None))

    assert response["nome_sistema"] == "ANTIGRAVITY"
    assert fake_db.commits == 0
    assert fake_db.added == []


def test_whatsapp_status_get_does_not_persist_evolution_status(monkeypatch):
    tenant_id = uuid4()
    connection = SimpleNamespace(
        id=uuid4(),
        empresa_id=tenant_id,
        tipo=conexoes_router.TipoConexao.EVOLUTION,
        status="disconnected",
        nome_instancia="test-instance",
    )
    fake_db = _QueuedDB([SimpleNamespace(id=tenant_id), connection])

    async def resolved_status(_db, _empresa_id):
        return {"provider": "evolution", "status": "disconnected", "online": False}

    async def current_status(_connection_id):
        return {"success": True, "status": "open"}

    monkeypatch.setattr(conexoes_router, "resolve_whatsapp_operational_status", resolved_status)
    monkeypatch.setattr(conexoes_router, "consultar_status_conexao", current_status)

    response = asyncio.run(conexoes_router._status_whatsapp_empresa(fake_db, str(tenant_id)))

    assert response["online"] is True
    assert connection.status == "disconnected"
    assert fake_db.commits == 0


def test_legacy_meta_webhook_requires_a_valid_hmac(monkeypatch):
    import app.services.meta.settings as settings_module

    secret = "unit-test-meta-signing-secret"
    body = b'{"object":"whatsapp_business_account"}'
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings_module, "meta_settings", lambda: SimpleNamespace(app_secret=secret))

    asyncio.run(
        webhook_router.require_legacy_meta_signature(
            _provider_request("/api/webhook/00000000-0000-0000-0000-000000000001/meta", body=body, headers={"x-hub-signature-256": signature})
        )
    )

    with pytest.raises(HTTPException) as denied:
        asyncio.run(
            webhook_router.require_legacy_meta_signature(
                _provider_request("/api/webhook/00000000-0000-0000-0000-000000000001/meta", body=body, headers={"x-hub-signature-256": "sha256=invalid"})
            )
        )
    assert denied.value.status_code == 401


def test_evolution_webhook_requires_the_tenant_instance_token(monkeypatch):
    tenant_id = uuid4()
    connection = SimpleNamespace(
        empresa_id=tenant_id,
        tipo=conexoes_router.TipoConexao.EVOLUTION,
        nome_instancia="instance-a",
        credenciais={"evolution_instance": "instance-a", "evolution_apikey": "unit-test-instance-secret"},
    )
    monkeypatch.setattr(webhook_router, "AsyncSessionLocal", lambda: _AsyncSessionContext(_RowsDB([connection])))
    body = json.dumps({"instance": "instance-a", "data": {}}).encode()

    asyncio.run(
        webhook_router.require_evolution_webhook_token(
            str(tenant_id),
            _provider_request("/api/webhook/test/evolution", body=body, headers={"x-agentos-webhook-token": "unit-test-instance-secret"}),
        )
    )

    with pytest.raises(HTTPException) as denied:
        asyncio.run(
            webhook_router.require_evolution_webhook_token(
                str(tenant_id),
                _provider_request("/api/webhook/test/evolution", body=body, headers={"x-agentos-webhook-token": "wrong-token"}),
            )
        )
    assert denied.value.status_code == 401


def test_sensitive_routes_declare_expected_scope_and_super_admin_policy():
    policies = {
        (method, route.path): policy_for_route(route, method)
        for route in app.routes
        if hasattr(route, "methods") and hasattr(route, "path")
        for method in route.methods or set()
    }

    assert policies[("DELETE", "/api/empresas/{empresa_id}")] == "SUPER_ADMIN_ONLY"
    assert policies[("GET", "/api/empresas/{empresa_id}/exportar-leads")] == "TENANT_READ"
    assert policies[("POST", "/api/empresas/{empresa_id}/inbox/{telefone}/send")] == "TENANT_WRITE"
    assert policies[("POST", "/api/empresas/{empresa_id}/inbox/{telefone}/send_media")] == "TENANT_WRITE"
    assert policies[("POST", "/api/empresas/{empresa_id}/inbox/{telefone}/pausar_bot")] == "TENANT_WRITE"
    assert policies[("POST", "/api/empresas/{empresa_id}/inbox/{telefone}/reativar_bot")] == "TENANT_WRITE"


def test_simulator_redis_keys_are_tenant_scoped():
    session_id = str(uuid4())
    tenant_a, tenant_b = str(uuid4()), str(uuid4())

    assert simulator_response_key(tenant_a, session_id) != simulator_response_key(tenant_b, session_id)
