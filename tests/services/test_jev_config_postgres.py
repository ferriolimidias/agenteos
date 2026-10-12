"""Disposable PostgreSQL settings migration, encrypted persistence and tenant isolation."""
import json
import os
from pathlib import Path
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import jev_config as j
from db.manual_migrations import _split_sql_statements
from db.models import Empresa

URL = os.getenv('JEV_CONFIG_TEST_DATABASE_URL', '')
pytestmark = [pytest.mark.asyncio,pytest.mark.skipif(not URL,reason='isolated JEV_CONFIG_TEST_DATABASE_URL required')]


async def test_postgres_idempotent_additive_migration_encrypted_roundtrip_tenant_isolation(monkeypatch):
    parsed = make_url(URL)
    if parsed.host != 'a00-pg' or parsed.database != 'agentos_test':
        pytest.fail('refusing JEV config writes outside isolated a00-pg/agentos_test')
    monkeypatch.setenv('META_CREDENTIAL_ENCRYPTION_KEY', 'synthetic-only-crypto-key-for-isolated-tests-12345678')
    monkeypatch.delenv('JEV_CREDENTIAL_ENCRYPTION_KEY',raising=False)
    engine = create_async_engine(URL)
    tenant, other = uuid4(), uuid4()
    preserved = {'synthetic_preserved':True}
    sql = Path('db/migrations/2026_10_12_001_jev_tenant_config.sql').read_text()
    try:
        async with engine.begin() as conn:
            before = (await conn.execute(text('SELECT count(*) FROM empresas'))).scalar_one()
            columns_before = (await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='empresas' ORDER BY column_name"))).scalars().all()
            for _ in range(2):
                for statement in _split_sql_statements(sql): await conn.execute(text(statement))
            assert (await conn.execute(text('SELECT count(*) FROM empresas'))).scalar_one() == before
            assert (await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='empresas' ORDER BY column_name"))).scalars().all() == columns_before
            for identifier in (tenant,other):
                await conn.execute(insert(Empresa.__table__).values(id=identifier,nome_empresa='JEV isolated synthetic tenant',credenciais_canais=preserved))
        async with AsyncSession(engine) as session:
            assert await j.tenant_exists(session,tenant)
            assert not await j.tenant_exists(session,uuid4())
            initial = await j.load_config(session,tenant)
            assert initial.public()['status'] == 'not_configured'
            saved = await j.save_config(session,tenant,j.JevConfigUpdate(api_key='synthetic-roundtrip-secret',enabled=True,daily_budget_usd='0.2'))
            assert saved.has_key and saved.api_key is None
            assert saved.public()['status'] == 'not_evaluated'
            assert 'synthetic-roundtrip-secret' not in json.dumps(saved.public())
            assert (await j.load_config(session,tenant)).api_key.get_secret_value() == 'synthetic-roundtrip-secret'
            other_config = await j.load_config(session,other)
            assert not other_config.has_key and not other_config.enabled
            ciphertext = (await session.execute(text('SELECT api_key_encrypted FROM jev_tenant_config WHERE empresa_id=:id'),{'id':tenant})).scalar_one()
            assert ciphertext != 'synthetic-roundtrip-secret'
            with pytest.raises(j.JevCredentialError): j.decrypt_key(other,ciphertext)
            await j.save_config(session,tenant,j.JevConfigUpdate(enabled=True,daily_budget_usd='0.3'))
            loaded = await j.load_config(session,tenant)
            assert loaded.api_key.get_secret_value() == 'synthetic-roundtrip-secret'
            assert loaded.daily_budget_usd == Decimal('0.3') and loaded.version == 2
            await j.save_config(session,tenant,j.JevConfigUpdate(api_key='synthetic-replaced-key'))
            loaded = await j.load_config(session,tenant)
            assert loaded.api_key.get_secret_value() == 'synthetic-replaced-key' and loaded.tested_at is None
            await j.save_config(session,tenant,j.JevConfigUpdate(remove_api_key=True))
            removed = await j.load_config(session,tenant)
            assert not removed.has_key and removed.public()['status'] == 'not_configured'
            assert (await session.execute(text('SELECT credenciais_canais FROM empresas WHERE id=:id'),{'id':tenant})).scalar_one() == preserved
            assert (await session.execute(text('SELECT count(*) FROM jev_tenant_config WHERE empresa_id=:id'),{'id':other})).scalar_one() == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(text('DELETE FROM empresas WHERE id IN (:a,:b)'),{'a':tenant,'b':other})
        await engine.dispose()
