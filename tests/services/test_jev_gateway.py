from decimal import Decimal
from types import SimpleNamespace
import httpx
from pydantic import SecretStr
import pytest
from app.services.jev_gateway import evaluate, GatewayError, usage_from_response
from app.services.jev_examples import examples
from test_jev_classification import response


def config(**values):
    defaults = dict(enabled=True, provider='typesafe', model='jev-1.13.0', api_key=SecretStr('synthetic-secret-test-key'), daily_budget_usd=Decimal('.01'),timeout_seconds=2)
    defaults.update(values)
    return SimpleNamespace(**defaults)

class Ledger:
    def __init__(self, allow=True): self.reserves = 0; self.allow = allow
    async def retry_reserve(self, budget): self.reserves += 1; return self.allow


def client_factory(handler):
    return lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)

@pytest.mark.asyncio
async def test_pinned_authenticated_payload_minimized_and_measured():
    messages = examples()[0]['messages']; calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer synthetic-secret-test-key'
        assert b'fixture:' not in request.content
        assert b'empresa_id' not in request.content and b'lead_id' not in request.content
        return httpx.Response(200,json=response(messages,{'price':.95,'evidence_price_0':.95}))
    result, metrics = await evaluate(config(),messages,Ledger(),client_factory=client_factory(handler))
    assert len(calls) == 1 and result['intents'][0]['key'] == 'price'
    assert metrics['input_tokens'] == 900 and metrics['cost_usd'] == pytest.approx(.0000378)
    assert metrics['reserved_usd'] == .002688

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[401,422,500,302])
async def test_nonretriable_errors_sanitized_and_once(status):
    calls = []
    def handler(request): calls.append(request); return httpx.Response(status,json={'secret':'private provider body'})
    with pytest.raises(GatewayError) as exc: await evaluate(config(),examples()[0]['messages'],Ledger(),client_factory=client_factory(handler))
    assert 'private' not in str(exc.value) and len(calls) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[429,529])
async def test_only_explicit_rejections_retry_and_reserve_each(status):
    messages = examples()[0]['messages']; calls=[]; ledger=Ledger()
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={}) if len(calls)==1 else httpx.Response(200,json=response(messages))
    _, metrics = await evaluate(config(),messages,ledger,client_factory=client_factory(handler))
    assert len(calls)==2 and ledger.reserves==1 and metrics['reserved_usd']==.005376
    assert metrics['cost_status']=='partial_usage_after_retry'

@pytest.mark.asyncio
async def test_retry_budget_exhaustion_stops_second_request():
    calls=[]
    def handler(request): calls.append(request); return httpx.Response(429,json={})
    with pytest.raises(GatewayError,match='budget_exceeded'):
        await evaluate(config(),examples()[0]['messages'],Ledger(False),client_factory=client_factory(handler))
    assert len(calls)==1

@pytest.mark.asyncio
async def test_timeout_ambiguous_is_not_retried_or_logs_text():
    calls=[]
    def handler(request): calls.append(request); raise httpx.ReadTimeout('PRIVATE MESSAGE AND KEY',request=request)
    with pytest.raises(GatewayError,match='provider_timeout') as exc:
        await evaluate(config(),examples()[0]['messages'],Ledger(),client_factory=client_factory(handler))
    assert len(calls)==1 and 'PRIVATE' not in str(exc.value)

@pytest.mark.asyncio
async def test_ledger_must_reserve_before_retry():
    from app.services.jev_ledger import LedgerUnavailable
    class Down:
        async def retry_reserve(self,budget): raise LedgerUnavailable()
    calls=[]
    def handler(request): calls.append(request); return httpx.Response(529,json={})
    with pytest.raises(LedgerUnavailable):
        await evaluate(config(),examples()[0]['messages'],Down(),client_factory=client_factory(handler))
    assert len(calls)==1

@pytest.mark.parametrize('usage',[{'input_tokens':float('nan'),'output_tokens':1},{'input_tokens':64001,'output_tokens':0},{'input_tokens':True,'output_tokens':0}])
def test_usage_invalid_or_missing_is_never_invented(usage):
    with pytest.raises(ValueError): usage_from_response({'usage':usage})
    assert usage_from_response({}) is None

@pytest.mark.asyncio
async def test_disabled_gateway_itself_never_calls():
    def handler(request): raise AssertionError('must not call')
    with pytest.raises(GatewayError,match='provider_not_configured'):
        await evaluate(config(enabled=False),examples()[0]['messages'],Ledger(),client_factory=client_factory(handler))

@pytest.mark.asyncio
async def test_maximum_minimized_context_fits_request_and_all_evidence_questions():
    messages=[{'id':str(i),'role':'customer','text':'X'*500} for i in range(8)]
    seen=[]
    def handler(request):
        import json
        body=json.loads(request.content)
        assert len(request.content)<=48000
        assert sum(len(m['text']) for m in body['state']['messages'])==4000
        assert all(f'evidence_price_{i}' in body['questions'] for i in range(8))
        seen.append(request)
        return httpx.Response(200,json=response(messages))
    await evaluate(config(),messages,Ledger(),client_factory=client_factory(handler))
    assert len(seen)==1
