import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routers import atendimento_modular as api
from app.api.routers.auth import get_current_user
from db.database import get_db


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(api.router)
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid.uuid4()))
    async def session(): yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app) as http:
        yield app, http, db


def auth(app, tenant, role='admin_empresa'):
    async def user(): return SimpleNamespace(empresa_id=tenant, role=role, email='operator@synthetic.example')
    app.dependency_overrides[get_current_user] = user


@pytest.mark.parametrize('path,method,body', [('/status','get',None), ('/examples','get',None), ('/preview','post',{'message':'teste'}), ('/runs','get',None), ('/runs/'+str(uuid.uuid4()),'get',None)])
def test_auth_scope_reviewer_before_services(client, path, method, body):
    app,http,db=client; tenant=uuid.uuid4(); url=f'/api/empresas/{tenant}/atendimento-modular{path}'
    kwargs={'json':body} if body else {}
    assert getattr(http, method)(url, **kwargs).status_code == 401
    auth(app, uuid.uuid4())
    assert getattr(http, method)(url, **kwargs).status_code == 403
    auth(app, tenant, 'meta_app_reviewer')
    assert getattr(http, method)(url, **kwargs).status_code == 403
    db.scalar.assert_not_called()


def test_examples_missing_tenant_and_invalid_id(client):
    app,http,db=client;tenant=uuid.uuid4();auth(app,tenant);base=f'/api/empresas/{tenant}/atendimento-modular'
    result=http.get(base+'/examples');assert result.status_code==200 and len(result.json()['items'])==12
    db.scalar.return_value=None
    assert http.get(base+'/examples').status_code==404
    assert http.post(base+'/preview',json={'message':'teste'}).status_code==404


@pytest.mark.parametrize('body', [{}, {'example_id':'schedule_multi','message':'PRIVATE'}, {'message':'PRIVATE','use_jev':True}, {'example_id':'multi_price_trust_availability','use_jev':True}, {'message':'   '}, {'message':'x'*2001}, {'message':'teste','lead_id':str(uuid.uuid4())}, {'message':'teste','mode':'MODULAR'}, {'message':'teste','empresa_id':str(uuid.uuid4())}, {'message':'teste','channel':'sms'}, {'message':'teste','use_jev':'true'}, {'message':'teste','context_mode':'actual'}])
def test_strict_source_no_custom_provider_or_configuration(client, monkeypatch, body):
    app,http,_=client;tenant=uuid.uuid4();auth(app,tenant)
    provider=AsyncMock(side_effect=AssertionError('blocked'))
    monkeypatch.setattr(api.a07_preview,'preview',provider)
    assert http.post(f'/api/empresas/{tenant}/atendimento-modular/preview',json=body).status_code==422
    provider.assert_not_called()


def test_preview_contract_no_egress_and_unknown_example(client,monkeypatch):
    app,http,_=client;tenant=uuid.uuid4();auth(app,tenant);base=f'/api/empresas/{tenant}/atendimento-modular'
    monkeypatch.setattr(api.a07_preview,'persist_trace',AsyncMock(return_value=True))
    result=http.post(base+'/preview',json={'example_id':'multi_price_trust_availability','context_mode':'complete'})
    assert result.status_code==200
    payload=result.json();assert payload['mode']=='MODULAR' and payload['live_mode']=='LEGACY'
    assert len(payload['selected_modules'])>=3 and payload['trace']['persisted']
    assert http.post(base+'/preview',json={'example_id':'does_not_exist'}).status_code==404


def test_ledger_errors_and_cross_tenant_missing_runs(client,monkeypatch):
    app,http,_=client;tenant=uuid.uuid4();auth(app,tenant);base=f'/api/empresas/{tenant}/atendimento-modular'
    class Client:
        async def aclose(self): pass
    class Ledger:
        def __init__(self, client, t): assert t==tenant
        async def runs(self): raise api.LedgerUnavailable()
        async def get(self, id): return None
    monkeypatch.setattr(api,'new_client',Client);monkeypatch.setattr(api,'A07Ledger',Ledger)
    assert http.get(base+'/runs').status_code==503
    assert http.get(base+'/runs/'+str(uuid.uuid4())).status_code==404
    assert http.get(base+'/runs?limit=101').status_code==422
