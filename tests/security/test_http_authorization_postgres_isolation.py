"""Optional real-PostgreSQL tenant isolation test using a disposable schema.

Run only with P0_AUTH_TEST_DATABASE_URL explicitly pointed at a test database.
The test creates and drops its own random schema and never touches production.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.main import app
from app.core.security import create_access_token
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE, ROOT_ADMIN_ROLE


def _test_database_url() -> str:
    value = os.getenv("P0_AUTH_TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("P0_AUTH_TEST_DATABASE_URL is only for an isolated disposable test database")
    if not value.startswith("postgresql+asyncpg://"):
        pytest.fail("P0_AUTH_TEST_DATABASE_URL must use postgresql+asyncpg")
    return value


def test_persisted_users_cannot_cross_tenant_http_boundary(monkeypatch):
    database_url = _test_database_url()
    schema = f"p0_auth_{uuid4().hex}"
    monkeypatch.setenv("SECRET_KEY", "p0-auth-isolated-test-signing-key-000000000000000000000000")
    admin_engine = create_async_engine(database_url, poolclass=NullPool)
    test_engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a, user_b, root_user = uuid4(), uuid4(), uuid4()

    async def setup_schema():
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with test_engine.begin() as connection:
            await connection.execute(text("CREATE TABLE empresas (id uuid PRIMARY KEY)"))
            await connection.execute(
                text(
                    "CREATE TABLE usuarios ("
                    "id uuid PRIMARY KEY, empresa_id uuid NULL REFERENCES empresas(id), "
                    "nome varchar NOT NULL, email varchar UNIQUE NOT NULL, senha_hash varchar NOT NULL, "
                    "role varchar NOT NULL, ativo boolean NOT NULL)"
                )
            )
            await connection.execute(
                text("INSERT INTO empresas (id) VALUES (:a), (:b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            await connection.execute(
                text(
                    "INSERT INTO usuarios (id,empresa_id,nome,email,senha_hash,role,ativo) VALUES "
                    "(:user_a,:tenant_a,'Tenant A','tenant-a@example.invalid','test-hash',:admin,true), "
                    "(:user_b,:tenant_b,'Tenant B','tenant-b@example.invalid','test-hash',:admin,true), "
                    "(:root_id,NULL,'Root','p0-root@example.invalid','test-hash',:root_role,true)"
                ),
                {
                    "user_a": user_a,
                    "tenant_a": tenant_a,
                    "user_b": user_b,
                    "tenant_b": tenant_b,
                    "root_id": root_user,
                    "admin": ADMIN_EMPRESA_ROLE,
                    "root_role": ROOT_ADMIN_ROLE,
                },
            )

    async def drop_schema():
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()

    asyncio.run(setup_schema())
    previous_overrides = dict(app.dependency_overrides)

    async def isolated_get_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = isolated_get_db
    tokens = {
        "a": create_access_token(str(user_a)),
        "b": create_access_token(str(user_b)),
        "root": create_access_token(str(root_user)),
    }
    client = TestClient(app)
    try:
        assert client.get(
            f"/api/empresas/{tenant_a}/dashboard",
            headers={"Authorization": f"Bearer {tokens['a']}"},
        ).status_code == 200
        assert client.get(
            f"/api/empresas/{tenant_b}/dashboard",
            headers={"Authorization": f"Bearer {tokens['b']}"},
        ).status_code == 200
        assert client.get(
            f"/api/empresas/{tenant_b}/dashboard",
            headers={"Authorization": f"Bearer {tokens['root']}"},
        ).status_code == 200

        user_a_headers = {"Authorization": f"Bearer {tokens['a']}"}
        foreign_resource = uuid4()
        denied = [
            client.get(f"/api/empresas/{tenant_b}/dashboard", headers=user_a_headers),
            client.get(f"/api/empresas/{tenant_b}/exportar-leads", headers=user_a_headers),
            client.post(
                f"/api/empresas/{tenant_b}/inbox/5511999999999/send",
                headers=user_a_headers,
                json={"texto": "must not be sent"},
            ),
            client.post(
                f"/api/empresas/{tenant_b}/inbox/5511999999999/pausar_bot",
                headers=user_a_headers,
            ),
            client.post(
                f"/api/empresas/{tenant_b}/inbox/5511999999999/reativar_bot",
                headers=user_a_headers,
            ),
            client.put(
                f"/api/empresas/{tenant_b}/crm/leads/{foreign_resource}",
                headers=user_a_headers,
                json={"nome_contato": "cross tenant"},
            ),
            client.delete(
                f"/api/empresas/{tenant_b}/crm/leads/{foreign_resource}",
                headers=user_a_headers,
            ),
            client.delete(f"/api/empresas/{tenant_b}", headers=user_a_headers),
        ]
        assert [response.status_code for response in denied] == [403] * len(denied)
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        asyncio.run(drop_schema())
