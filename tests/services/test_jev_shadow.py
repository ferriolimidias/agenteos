import copy
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.services import jev_shadow as shadow
from app.services.jev_ledger import LedgerUnavailable
from app.services.jev_gateway import GatewayError
from app.services.jev_classification import empty_classification


def config(**values):
    from pydantic import SecretStr
    default = dict(provider='typesafe', model='jev-1.13.0', enabled=True,api_key=SecretStr('test-key'),daily_budget_usd=Decimal('.1'),timeout_seconds=2,version=1)
    default.update(values)
    return SimpleNamespace(**default)

class Ledger:
    def __init__(self,decision='reserved'):
        self.decision=decision; self.saved=[]; self.reservations=0; self.releases=0
    async def reserve(self,*args,**kwargs): self.reservations+=1; return self.decision,'previous'
    async def get(self,id): return copy.deepcopy(self.saved[0]) if self.saved else None
    async def save(self,run): self.saved.append(copy.deepcopy(run))
    async def release(self,id): self.releases+=1
    async def runs(self): return self.saved
    async def reserved(self): return .002688

@pytest.fixture
def setup(monkeypatch):
    tenant=uuid4(); lead=uuid4(); calls=[]
    async def configuration(db,t): assert t==tenant; return config()
    async def context(db,t,l): assert t==tenant and l==lead; return [{'id':str(uuid4()),'role':'customer','text':'PRIVATE NAME email@example.test Quanto custa?'}]
    async def tags(db,t): assert t==tenant; return [],{}
    async def provider(config,messages,ledger):
        calls.append(messages)
        return empty_classification('uncertain'),{'cost_usd':.0001,'input_tokens':900,'latency_ms':8}
    monkeypatch.setattr(shadow,'configuration',configuration);monkeypatch.setattr(shadow,'read_context',context)
    monkeypatch.setattr(shadow,'read_tags',tags);monkeypatch.setattr(shadow,'evaluate',provider)
    return tenant,lead,calls

@pytest.mark.asyncio
async def test_default_demo_cannot_be_promoted_by_arbitrary_text(setup):
    tenant,lead,calls=setup;ledger=Ledger()
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',ledger=ledger)
    assert result['source']=='synthetic' and result['status']=='demo' and result['engine']=='local_demo'
    assert not calls and ledger.reservations==0
    assert result['metrics']['cost_usd'] is None

@pytest.mark.asyncio
async def test_explicit_synthetic_provider_is_authorized_without_customer_egress(setup):
    tenant,lead,calls=setup;ledger=Ledger()
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=ledger)
    assert result['status']=='completed' and result['source']=='synthetic'
    assert len(calls)==1 and ledger.reservations==1 and ledger.releases==1

@pytest.mark.asyncio
async def test_real_privacy_denied_even_with_provider_configured(setup):
    tenant,lead,calls=setup;ledger=Ledger()
    result=await shadow.analyze(None,tenant,lead_id=lead,ledger=ledger)
    assert result['status']=='privacy_blocked' and not calls and ledger.reservations==0
    assert 'PRIVATE' not in json.dumps(ledger.saved)
    assert 'email@example' not in json.dumps(ledger.saved)

@pytest.mark.asyncio
async def test_config_or_budget_off_fail_closed(setup,monkeypatch):
    tenant,lead,calls=setup
    async def configuration(*args): return config(daily_budget_usd=Decimal('0'))
    monkeypatch.setattr(shadow,'configuration',configuration)
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=Ledger())
    assert result['status']=='not_configured' and not calls

@pytest.mark.asyncio
@pytest.mark.parametrize('decision',['budget_exceeded','concurrency_limited'])
async def test_atomic_reservation_denial_never_calls(setup,decision):
    tenant,lead,calls=setup
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=Ledger(decision))
    assert result['status']==decision and not calls

@pytest.mark.asyncio
async def test_idempotency_returns_existing_and_never_recharges(setup):
    tenant,lead,calls=setup; ledger=Ledger('duplicate')
    ledger.saved=[{'id':'previous','status':'completed','source':'synthetic'}]
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=ledger)
    assert result['id']=='previous' and result['deduplicated'] and not calls

@pytest.mark.asyncio
async def test_redis_outage_prevents_provider_but_local_demo_survives(setup):
    tenant,lead,calls=setup
    class Down(Ledger):
        async def reserve(self,*args,**kw): raise LedgerUnavailable()
        async def save(self,*args,**kw): raise LedgerUnavailable()
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=Down())
    assert result['status']=='ledger_unavailable' and not calls
    demo=await shadow.analyze(None,tenant,example_id='multi_price_trust',ledger=Down())
    assert demo['status']=='demo' and demo['ledger_persisted'] is False and demo['ledger_error']=='redis_unavailable'

@pytest.mark.asyncio
async def test_provider_failure_sanitized_fallback_without_local_real_inference(setup,monkeypatch):
    tenant,lead,calls=setup;ledger=Ledger()
    async def error(*args): raise GatewayError('provider_timeout')
    monkeypatch.setenv('JEV_REAL_AUTHORIZED_'+str(tenant).replace('-','').upper(),'true')
    monkeypatch.setattr(shadow,'evaluate',error)
    result=await shadow.analyze(None,tenant,lead_id=lead,ledger=ledger)
    assert result['status']=='provider_error' and result['classification']['intents']==[]
    assert ledger.releases==1 and 'PRIVATE' not in json.dumps(ledger.saved)

@pytest.mark.asyncio
async def test_disabled_flag_blocks_all_evaluation(setup,monkeypatch):
    tenant,lead,calls=setup;monkeypatch.setenv('JEV_SHADOW_ENABLED','false')
    result=await shadow.analyze(None,tenant,example_id='multi_price_trust',use_provider=True,ledger=Ledger())
    assert result['status']=='disabled' and not calls

@pytest.mark.asyncio
async def test_tenant_parent_missing_stops_before_message_read():
    class DB:
        async def scalar(self,query):
            params=query.compile().params
            assert tenant in params.values() and lead in params.values()
            return None
        async def execute(self,query): raise AssertionError('must stop at scoped parent')
    tenant=uuid4();lead=uuid4()
    with pytest.raises(shadow.NotFound): await shadow.read_context(DB(),tenant,lead)

@pytest.mark.asyncio
async def test_bounded_context_scoped_customer_agent_and_no_metadata_leak():
    tenant,lead=uuid4(),uuid4()
    rows=[SimpleNamespace(id=uuid4(),texto='email@test.example +55 (11) 99876-5432 https://secret.example/ '+('X'*1000),from_me=index%2==0,direction=None) for index in range(8)]
    class DB:
        async def scalar(self,query): return lead
        async def execute(self,query):
            params=query.compile().params
            assert tenant in params.values() and lead in params.values() and 8 in params.values()
            return SimpleNamespace(all=lambda: rows)
    result=await shadow.read_context(DB(),tenant,lead)
    assert sum(len(m['text']) for m in result)<=4000 and all(len(m['text'])<=800 for m in result)
    assert {m['role'] for m in result}=={'customer','agent'}
    assert 'email@test.example' not in json.dumps(result) and 'https://' not in json.dumps(result)

@pytest.mark.asyncio
async def test_status_coverage_uses_retained_provider_runs_and_unknown_cost(setup):
    tenant,lead,calls=setup;ledger=Ledger()
    await shadow.analyze(None,tenant,example_id='multi_price_trust',ledger=ledger)
    result=await shadow.status(None,tenant,ledger=ledger)
    assert result['coverage']['requested']==1 and result['coverage']['evaluated']==0
    assert result['coverage']['synthetic_demos']==1 and result['coverage']['denominator']=='retained_runs'
    assert result['budget']['measured_usd'] is None

@pytest.mark.asyncio
async def test_unknown_direction_and_missing_text_excluded_and_one_conversation_only():
    tenant,lead,conversation=uuid4(),uuid4(),uuid4()
    rows=[SimpleNamespace(id=uuid4(),texto='preço',from_me=None,direction='unknown'),
          SimpleNamespace(id=uuid4(),texto=None,from_me=False,direction='inbound'),
          SimpleNamespace(id=uuid4(),texto='Quero comprar',from_me=False,direction='inbound')]
    class DB:
        def __init__(self):self.scalars=0
        async def scalar(self,query):
            self.scalars+=1
            if self.scalars==1:return lead
            sql=str(query.compile())
            params=query.compile().params
            assert 'channel_conversations' in sql and tenant in params.values() and lead in params.values()
            return conversation
        async def execute(self,query):
            params=query.compile().params
            assert conversation in params.values() and tenant in params.values() and lead in params.values()
            return SimpleNamespace(all=lambda:rows)
    result=await shadow.read_context(DB(),tenant,lead)
    assert len(result)==1 and result[0]['text']=='Quero comprar' and result[0]['role']=='customer'
