"""Actual HTTP auth guards and secret-safe validation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.services.jev_config import JevCredentialError

from app.api.routers import jev_admin
from app.api.routers.auth import get_current_user
from app.services.jev_config import JevTenantConfig
from db.database import get_db


@pytest.fixture
def context(monkeypatch):
    app = FastAPI()
    app.include_router(jev_admin.router)
    tenant = uuid4()
    user = SimpleNamespace(id=uuid4(),empresa_id=tenant,email='synthetic@example.invalid',role='admin_empresa')
    async def db(): yield SimpleNamespace(rollback=AsyncMock())
    app.dependency_overrides[get_db] = db
    exists = AsyncMock(return_value=True)
    load = AsyncMock(return_value=JevTenantConfig(tenant))
    save = AsyncMock(return_value=JevTenantConfig(tenant))
    test = AsyncMock(return_value=JevTenantConfig(tenant).public())
    monkeypatch.setattr(jev_admin.jev_config,'tenant_exists',exists)
    monkeypatch.setattr(jev_admin.jev_config,'load_config',load)
    monkeypatch.setattr(jev_admin.jev_config,'save_config',save)
    monkeypatch.setattr(jev_admin.jev_config,'test_connection',test)
    with TestClient(app) as client:
        yield app,client,user,exists,load,save,test


def path(user): return f'/api/admin/empresas/{user.empresa_id}/integrations/jev'


@pytest.mark.parametrize('verb,suffix',[('get',''),('put',''),('post','/test')])
def test_401_403_before_reads_or_writes(context,verb,suffix):
    app,client,user,exists,load,save,test = context
    request = getattr(client,verb)
    options = {'json':{'api_key':'synthetic-secret'}} if verb == 'put' else {}
    assert request(path(user)+suffix,**options).status_code == 401
    assert request(path(user)+suffix,headers={'Authorization':'Bearer invalid'},**options).status_code == 401
    app.dependency_overrides[get_current_user] = lambda:user
    assert request(path(user)+suffix,**options).status_code == 403
    for service in (exists,load,save,test): service.assert_not_awaited()


def test_super_admin_scoped_key_write_only_and_missing_tenant_404(context):
    app,client,user,exists,load,save,test = context
    user.role = 'super_admin'
    app.dependency_overrides[get_current_user] = lambda:user
    assert client.get(path(user)).status_code == 200
    assert load.await_args.args[1] == user.empresa_id
    result = client.put(path(user),json={'api_key':'synthetic-secret'})
    assert result.status_code == 200
    assert 'synthetic-secret' not in result.text
    assert save.await_args.args[2].api_key.get_secret_value() == 'synthetic-secret'
    exists.return_value = False
    for verb,suffix in [('get',''),('put',''),('post','/test')]:
        options = {'json':{}} if verb == 'put' else {}
        assert getattr(client,verb)(path(user)+suffix,**options).status_code == 404


@pytest.mark.parametrize('body',[{'api_key':{'PRIVATE_SECRET':'value'}},{'api_key':'short'},
    {'api_key':'PRIVATE_SECRET','model':'PRIVATE_MODEL'}, {'api_key':'PRIVATE_SECRET','mode':'live'},
    {'api_key':'PRIVATE_SECRET','unknown':'PRIVATE'}, {'daily_budget_usd':'PRIVATE_SECRET'}])
def test_validation_never_echoes_credential(context,body):
    app,client,user,exists,load,save,test = context
    user.role = 'super_admin'
    app.dependency_overrides[get_current_user] = lambda:user
    result = client.put(path(user),json=body)
    assert result.status_code == 422
    assert 'PRIVATE' not in result.text and 'short' not in result.text
    save.assert_not_awaited()


@pytest.mark.parametrize('failure',[SQLAlchemyError('PRIVATE_SECRET'),JevCredentialError('PRIVATE_SECRET')])
def test_save_failure_returns_sanitized_503(context,failure):
    app,client,user,exists,load,save,test = context
    user.role = 'super_admin'
    app.dependency_overrides[get_current_user] = lambda:user
    save.side_effect = failure
    result = client.put(path(user),json={'api_key':'synthetic-secret'})
    assert result.status_code == 503
    assert 'PRIVATE_SECRET' not in result.text and 'synthetic-secret' not in result.text


def test_tenant_query_failure_returns_sanitized_503(context):
    app,client,user,exists,load,save,test = context
    user.role = 'super_admin'
    app.dependency_overrides[get_current_user] = lambda:user
    exists.side_effect = SQLAlchemyError('PRIVATE_SECRET')
    result = client.get(path(user))
    assert result.status_code == 503 and 'PRIVATE_SECRET' not in result.text
