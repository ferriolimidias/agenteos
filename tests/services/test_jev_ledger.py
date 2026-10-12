"""Real isolated Redis semantics; no monkeypatch of atomic budget/concurrency Lua."""
import asyncio
import os
import uuid
import pytest
import redis.asyncio as redis
from app.services.jev_ledger import JevLedger, ATTEMPT_NANO_USD, MAX_RUNS

@pytest.mark.asyncio
async def test_atomic_budget_is_shared_and_tenant_scoped():
    url=os.getenv('JEV_TEST_REDIS_URL')
    if not url: pytest.skip('explicit isolated Redis required')
    client=redis.from_url(url,decode_responses=True)
    tenant=str(uuid.uuid4()); other=str(uuid.uuid4()); ledger=JevLedger(client,tenant)
    try:
        decisions=await asyncio.gather(*[ledger.reserve(str(i),str(uuid.uuid4()),ATTEMPT_NANO_USD) for i in range(10)])
        assert sum(d[0]=='reserved' for d in decisions)==1
        assert await ledger.reserved()==.002688
        assert (await JevLedger(client,other).reserve('a','other-run',ATTEMPT_NANO_USD))[0]=='reserved'
    finally:
        for prefix in [ledger.prefix,JevLedger(client,other).prefix]:
            keys=[k async for k in client.scan_iter(prefix+'*')]
            if keys: await client.delete(*keys)
        await client.aclose()

@pytest.mark.asyncio
async def test_atomic_concurrency_dedupe_retry_budget_and_retention():
    url=os.getenv('JEV_TEST_REDIS_URL')
    if not url: pytest.skip('explicit isolated Redis required')
    client=redis.from_url(url,decode_responses=True);ledger=JevLedger(client,str(uuid.uuid4()))
    try:
        budget=ATTEMPT_NANO_USD*10
        assert (await ledger.reserve('a','r1',budget))[0]=='reserved'
        assert (await ledger.reserve('a','r2',budget))==('duplicate','r1')
        assert (await ledger.reserve('b','r2',budget))[0]=='reserved'
        assert (await ledger.reserve('c','r3',budget))[0]=='concurrency_limited'
        await ledger.release('r1')
        assert (await ledger.reserve('c','r3',budget))[0]=='reserved'
        assert not await ledger.retry_reserve(ATTEMPT_NANO_USD*3)
        assert await ledger.retry_reserve(budget)
        for index in range(MAX_RUNS+1):
            await ledger.save({'id':str(index),'source':'synthetic','status':'demo'})
        runs=await ledger.runs()
        assert len(runs)==MAX_RUNS and await ledger.get('0') is None
        assert await JevLedger(client,str(uuid.uuid4())).get('1') is None
        assert 0<await client.ttl(ledger.key('run:1'))<=7*86400
    finally:
        keys=[k async for k in client.scan_iter(ledger.prefix+'*')]
        if keys: await client.delete(*keys)
        await client.aclose()
