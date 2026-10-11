"""Actual HTTP dependency checks for every A05 tenant-scoped GET."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routers import analytics
from app.api.routers.auth import get_current_user
from db.database import get_db
from db.models import ADMIN_EMPRESA_ROLE,META_APP_REVIEWER_ROLE


@pytest.fixture
def context(monkeypatch):
    app = FastAPI()
    app.include_router(analytics.router)
    user = SimpleNamespace(id=uuid4(),empresa_id=uuid4(),email='synthetic@example.invalid',role=ADMIN_EMPRESA_ROLE)
    async def isolated_db():
        yield object()
    app.dependency_overrides[get_db]=isolated_db
    services = [AsyncMock(return_value={'summary':{'total_leads':0}}),AsyncMock(return_value={'items':[],'total':0}),AsyncMock(return_value='section,metric,value\nsummary,total_leads,0\n')]
    for name,function in zip(('commercial_funnel_dashboard','commercial_funnel_leads','commercial_funnel_export'),services):
        monkeypatch.setattr(analytics,name,function)
    monkeypatch.setenv('COMMERCIAL_FUNNEL_DASHBOARD_ENABLED','true')
    with TestClient(app) as client:
        yield app,client,user,services


def path(tenant,suffix=''):
    return f'/api/empresas/{tenant}/analytics/commercial-funnel{suffix}'


@pytest.mark.parametrize('suffix,index',[('',0),('/leads',1),('/export',2)])
def test_missing_credentials_and_invalid_token_reject_before_read(context,suffix,index):
    app,client,user,services=context
    assert client.get(path(user.empresa_id,suffix)).status_code==401
    assert client.get(path(user.empresa_id,suffix),headers={'Authorization':'Bearer invalid-token'}).status_code==401
    services[index].assert_not_awaited()


@pytest.mark.parametrize('suffix,index',[('',0),('/leads',1),('/export',2)])
def test_cross_tenant_reviewer_and_feature_off_do_not_read(context,monkeypatch,suffix,index):
    app,client,user,services=context
    app.dependency_overrides[get_current_user]=lambda:user
    assert client.get(path(uuid4(),suffix)).status_code==403
    user.role=META_APP_REVIEWER_ROLE
    assert client.get(path(user.empresa_id,suffix)).status_code==403
    user.role=ADMIN_EMPRESA_ROLE
    monkeypatch.setenv('COMMERCIAL_FUNNEL_DASHBOARD_ENABLED','false')
    assert client.get(path(user.empresa_id,suffix)).status_code==404
    services[index].assert_not_awaited()


@pytest.mark.parametrize('suffix,index',[('',0),('/leads',1),('/export',2)])
def test_owned_tenant_passed_as_uuid_and_scope_errors_are_sanitized(context,suffix,index):
    app,client,user,services=context
    app.dependency_overrides[get_current_user]=lambda:user
    assert client.get(path(user.empresa_id,suffix)+'?stage=lead_established&campaign_id=123').status_code==200
    assert services[index].await_args.kwargs['empresa_id']==user.empresa_id
    assert services[index].await_args.kwargs['stage']=='lead_established'
    assert services[index].await_args.kwargs['campaign_id']=='123'
    for exc,code in ((ValueError('PRIVATE DB ERROR'),422),(LookupError('PRIVATE SCOPE'),404)):
        services[index].side_effect=exc
        response=client.get(path(user.empresa_id,suffix))
        assert response.status_code==code
        assert 'PRIVATE' not in response.text


@pytest.mark.parametrize('suffix,query',[('', 'campaign_id=inject'),('', 'origin=invented'),('', 'status=closed'),
 ('','inactivity_hours=0'),('', 'return_window_days=91'),('', 'start=invalid'),('/leads','limit=101'),
 ('/leads','offset=-1'),('/leads','bottleneck=missing'),('/export','ad_id=not-numeric')])
def test_invalid_http_filters_never_reach_service(context,suffix,query):
    app,client,user,services=context
    app.dependency_overrides[get_current_user]=lambda:user
    assert client.get(path(user.empresa_id,suffix)+'?'+query).status_code==422
    for service in services:
        service.assert_not_awaited()


def test_export_is_attachment_no_store_and_no_pii(context):
    app,client,user,services=context
    app.dependency_overrides[get_current_user]=lambda:user
    response=client.get(path(user.empresa_id,'/export'))
    assert response.status_code==200
    assert response.headers['content-type'].startswith('text/csv')
    assert response.headers['cache-control']=='no-store'
    assert 'attachment' in response.headers['content-disposition']
    assert str(user.empresa_id) not in response.text
