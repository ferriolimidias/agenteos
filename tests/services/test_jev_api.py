import copy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routers import jev
from app.api.routers.auth import get_current_user
from db.database import get_db
from app.services.jev_classification import empty_classification
from app.services.jev_ledger import LedgerUnavailable

@pytest.fixture
def client(monkeypatch):
    app=FastAPI();app.include_router(jev.router)
    async def db(): yield object()
    app.dependency_overrides[get_db]=db
    with TestClient(app) as client:
        yield app,client


def authorized(app,tenant):
    async def user():return SimpleNamespace(empresa_id=tenant,role='admin_empresa',email='admin@synthetic.example')
    app.dependency_overrides[get_current_user]=user

@pytest.mark.parametrize('suffix,method,body', [('/examples','get',None),('/status','get',None),('/runs','get',None),('/runs/'+str(uuid4()),'get',None),('/analyze','post',{'example_id':'uncertain'})])
def test_missing_auth_and_wrong_tenant_fail_before_services(client,monkeypatch,suffix,method,body):
    app,http=client;tenant=uuid4()
    url=f'/api/empresas/{tenant}/jev'+suffix
    res=getattr(http,method)(url,**({'json':body} if body else {}))
    assert res.status_code==401
    authorized(app,uuid4())
    res=getattr(http,method)(url,**({'json':body} if body else {}))
    assert res.status_code==403


def test_examples_contract_and_no_arbitrary_synthetic_text(client):
    app,http=client;tenant=uuid4();authorized(app,tenant);base=f'/api/empresas/{tenant}/jev'
    result=http.get(base+'/examples')
    assert result.status_code==200 and result.json()['items'][0]['reference_source']=='authored_synthetic_demo'
    for body in ({'text':'PRIVATE CUSTOMER','source':'synthetic'}, {'example_id':'uncertain','text':'PRIVATE'}, {'example_id':'uncertain','lead_id':str(uuid4())}, {'lead_id':str(uuid4()),'use_provider':True}, {}, {'example_id':'uncertain','use_provider':'true'}):
        assert http.post(base+'/analyze',json=body).status_code==422


def test_analyze_synthetic_provider_and_unknown_parent_404(client,monkeypatch):
    app,http=client;tenant=uuid4();authorized(app,tenant);base=f'/api/empresas/{tenant}/jev';calls=[]
    async def analyze(db,t,**kw):
        calls.append((t,kw))
        if kw['example_id']=='missing':raise jev.jev_shadow.NotFound()
        return {'source':'synthetic','status':'demo'}
    monkeypatch.setattr(jev.jev_shadow,'analyze',analyze)
    assert http.post(base+'/analyze',json={'example_id':'uncertain','use_provider':True}).status_code==200
    assert calls[0][0]==tenant and calls[0][1]['use_provider'] is True
    assert http.post(base+'/analyze',json={'example_id':'missing'}).status_code==404


def ledger(monkeypatch,runs=None,unavailable=False):
    class Client:
        async def aclose(self):pass
    class Ledger:
        def __init__(self,client,tenant):self.tenant=tenant
        async def runs(self):
            if unavailable: raise LedgerUnavailable()
            return runs or []
        async def get(self,id):
            if unavailable:raise LedgerUnavailable()
            return next((r for r in runs or [] if r['id']==id and r['tenant']==str(self.tenant)),None)
    monkeypatch.setattr(jev,'new_client',Client);monkeypatch.setattr(jev,'JevLedger',Ledger)


def test_runs_filters_limits_details_and_cross_tenant_404(client,monkeypatch):
    app,http=client;tenant=uuid4();authorized(app,tenant);base=f'/api/empresas/{tenant}/jev'
    a={'id':str(uuid4()),'tenant':str(tenant),'status':'demo','classification':empty_classification('local_uncertain')}
    b={'id':str(uuid4()),'tenant':str(uuid4()),'status':'not_configured','classification':empty_classification('not_configured')}
    ledger(monkeypatch,[a,b])
    result=http.get(base+'/runs',params={'status':'abstained'})
    assert result.status_code==200 and result.json()['items']==[a]
    assert http.get(base+'/runs',params={'status':'invalid'}).status_code==422
    assert http.get(base+'/runs',params={'intent':'invalid'}).status_code==422
    assert http.get(base+'/runs',params={'limit':101}).status_code==422
    assert http.get(base+'/runs/'+a['id']).status_code==200
    assert http.get(base+'/runs/'+b['id']).status_code==404


def test_ledger_outage_explicit_503(client,monkeypatch):
    app,http=client;tenant=uuid4();authorized(app,tenant);base=f'/api/empresas/{tenant}/jev';ledger(monkeypatch,unavailable=True)
    assert http.get(base+'/runs').status_code==503
    assert http.get(base+'/runs/'+str(uuid4())).status_code==503
