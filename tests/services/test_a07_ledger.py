import json
import os
import uuid
from datetime import datetime, timezone
import pytest
from app.services.a07_ledger import A07Ledger, clean_trace, MAX_RUNS, RETENTION_SECONDS, LedgerUnavailable


def trace():
    return {'run_id':str(uuid.uuid4()),'created_at':datetime.now(timezone.utc).isoformat(),'source':'synthetic','channel':'whatsapp',
            'selected_modules':[{'id':'pricing_conditions','version':'1.0.0','prompt':'PRIVATE'}], 'mode':'MODULAR', 'status':'completed',
            'primary_stage':{'value':str(uuid.uuid4()),'status':'known','source':'crm_snapshot','name':'PRIVATE'},
            'metrics':{'input_tokens':None,'cost_usd':None,'latency_ms':3,'estimated_input_tokens':44,'response':'PRIVATE'},
            'message':'PRIVATE','prompt':'PRIVATE','response_text':'PRIVATE','secrets':{'api_key':'PRIVATE'},
            'classification_source':'local_deterministic','measurement_source':'local_synthetic','guard_allowed':True}


def test_allowlist_removes_all_content_secrets_and_unknown_metadata():
    original=trace();clean=clean_trace(original);encoded=json.dumps(clean)
    assert 'PRIVATE' not in encoded and 'prompt' not in encoded and 'message' not in encoded
    assert clean['selected_modules']==[{'id':'pricing_conditions','version':'1.0.0'}]
    assert clean['metrics']['input_tokens'] is None and clean['metrics']['cost_usd'] is None
    original['fallback_reason']='PRIVATE';original['selected_modules'][0]['version']='PRIVATE'
    assert clean_trace(original)['fallback_reason'] is None and not clean_trace(original)['selected_modules']


@pytest.mark.asyncio
async def test_ledger_failure_explicit_and_tenant_prefix_validates_uuid():
    class Client:
        async def ping(self):raise OSError('SECRET exception must not leak')
    with pytest.raises(LedgerUnavailable,match='redis_unavailable'):
        await A07Ledger(Client(),uuid.uuid4()).available()
    with pytest.raises(ValueError):A07Ledger(Client(),'invalid')


@pytest.mark.asyncio
async def test_real_isolated_redis_tenant_retention_update_and_payload_privacy():
    url=os.getenv('A07_TEST_REDIS_URL')
    if not url:pytest.skip('explicit isolated Redis required')
    import redis.asyncio as redis
    client=redis.from_url(url,decode_responses=True);tenant=uuid.uuid4();ledger=A07Ledger(client,tenant)
    try:
        assert await ledger.available()
        initial=trace();saved=await ledger.save(initial)
        assert (await ledger.get(saved['run_id']))==saved
        assert await A07Ledger(client,uuid.uuid4()).get(saved['run_id']) is None
        assert 0<await client.ttl(ledger.prefix+'run:'+saved['run_id'])<=RETENTION_SECONDS
        await ledger.save(initial)
        assert len(await ledger.runs())==1
        for i in range(MAX_RUNS):await ledger.save(trace())
        assert len(await ledger.runs())==MAX_RUNS and await ledger.get(saved['run_id']) is None
        assert 'PRIVATE' not in json.dumps(await ledger.runs())
    finally:
        keys=[key async for key in client.scan_iter(ledger.prefix+'*')]
        if keys:await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_isolated_postgres_canonical_stage_tenant_join():
    url=os.getenv('A07_TEST_DATABASE_URL')
    if not url:pytest.skip('explicit isolated PostgreSQL required')
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy import text
    from app.services.a07_runtime import read_primary_stage
    schema='a07_test_'+uuid.uuid4().hex;engine=create_async_engine(url)
    tenant,other,lead,foreign_lead,stage,foreign_stage,funil,foreign_funil=[uuid.uuid4() for _ in range(8)]
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA {schema}'))
            await connection.execute(text(f'SET LOCAL search_path TO {schema}'))
            await connection.execute(text('CREATE TABLE crm_funis(id UUID PRIMARY KEY, empresa_id UUID)'))
            await connection.execute(text('CREATE TABLE crm_etapas(id UUID PRIMARY KEY, funil_id UUID)'))
            await connection.execute(text('CREATE TABLE crm_leads(id UUID PRIMARY KEY, empresa_id UUID, etapa_id UUID)'))
            await connection.execute(text('INSERT INTO crm_funis VALUES(:f,:t),(:ff,:o)'),{'f':funil,'t':tenant,'ff':foreign_funil,'o':other})
            await connection.execute(text('INSERT INTO crm_etapas VALUES(:s,:f),(:fs,:ff)'),{'s':stage,'f':funil,'fs':foreign_stage,'ff':foreign_funil})
            await connection.execute(text('INSERT INTO crm_leads VALUES(:l,:t,:s),(:fl,:o,:fs)'),{'l':lead,'t':tenant,'s':stage,'fl':foreign_lead,'o':other,'fs':foreign_stage})
            async with AsyncSession(bind=connection) as db:
                assert (await read_primary_stage(db,tenant,lead))['value']==str(stage)
                assert (await read_primary_stage(db,tenant,foreign_lead))['status']=='unknown'
                assert (await read_primary_stage(db,other,lead))['status']=='unknown'
                # Corrupt cross-tenant stage reference remains unknown, never foreign stage disclosure.
                await db.execute(text('UPDATE crm_leads SET etapa_id=:fs WHERE id=:l'),{'fs':foreign_stage,'l':lead})
                assert (await read_primary_stage(db,tenant,lead))['status']=='unknown'
    finally:
        async with engine.begin() as connection:await connection.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()
