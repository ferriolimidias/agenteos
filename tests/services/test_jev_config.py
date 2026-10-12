"""Deterministic credential/config contracts, no real provider traffic."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.services import jev_config as j


@pytest.fixture
def encryption(monkeypatch):
    monkeypatch.setenv('META_CREDENTIAL_ENCRYPTION_KEY', 'synthetic-crypto-key-only-for-tests-123456789')
    monkeypatch.delenv('JEV_CREDENTIAL_ENCRYPTION_KEY', raising=False)


def test_encryption_randomized_tenant_bound_and_repr_safe(encryption):
    tenant, other = uuid4(), uuid4()
    key = SecretStr('synthetic-provider-secret')
    one, two = j.encrypt_key(tenant, key), j.encrypt_key(tenant, key)
    assert one != two
    assert key.get_secret_value() not in one
    assert j.decrypt_key(tenant, one) == key
    with pytest.raises(j.JevCredentialError):
        j.decrypt_key(other, one)
    config = j.JevTenantConfig(tenant, has_key=True, api_key=key)
    assert key.get_secret_value() not in repr(config)
    assert key.get_secret_value() not in json.dumps(config.public())
    assert not any('encrypted' in field for field in config.public())


def test_crypto_fail_closed_and_dedicated_key_precedence(monkeypatch, encryption):
    monkeypatch.setenv('JEV_CREDENTIAL_ENCRYPTION_KEY', 'short')
    assert not j.credential_storage_available()
    with pytest.raises(j.JevCredentialError):
        j.encrypt_key(uuid4(), SecretStr('synthetic-api-key'))
    monkeypatch.delenv('JEV_CREDENTIAL_ENCRYPTION_KEY')
    monkeypatch.delenv('META_CREDENTIAL_ENCRYPTION_KEY')
    assert not j.credential_storage_available()


@pytest.mark.parametrize('values', [
    {'mode':'live'}, {'provider':'http://127.0.0.1'}, {'model':'arbitrary'},
    {'daily_budget_usd': -1}, {'daily_budget_usd': 'NaN'}, {'daily_budget_usd': 'Infinity'},
    {'daily_budget_usd':1001}, {'timeout_seconds':0}, {'timeout_seconds':16},
    {'api_key':'short'}, {'api_key':'abc\nsecrets'}, {'api_key':'synthetic-secret', 'remove_api_key':True},
    {'real_customer_egress_enabled':True},
])
def test_invalid_configuration_rejected(values):
    with pytest.raises(ValidationError):
        j.JevConfigUpdate(**values)


def test_default_not_configured_no_connected_claim_and_fixed_contract():
    config = j.JevTenantConfig(uuid4())
    assert config.public()['status'] == 'not_configured'
    assert not config.public()['enabled']
    assert config.public()['real_conversation_egress_enabled'] is False
    assert config.daily_budget_usd == Decimal('0')
    assert j.JevConfigUpdate(api_key='synthetic-secret').api_key.get_secret_value() == 'synthetic-secret'


@pytest.mark.asyncio
async def test_missing_configuration_is_tenant_scoped():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        mappings=lambda: SimpleNamespace(first=lambda: None))))
    tenant = uuid4()
    config = await j.load_config(db, tenant)
    assert config.empresa_id == tenant and not config.has_key
    assert db.execute.await_args.args[1] == {'id':tenant}


@pytest.mark.asyncio
async def test_saving_without_key_preserves_credential_atomically(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    tenant = uuid4()
    monkeypatch.setattr(j, 'load_config', AsyncMock(return_value=j.JevTenantConfig(tenant)))
    await j.save_config(db, tenant, j.JevConfigUpdate(enabled=True, daily_budget_usd='0.2'))
    sql, params = db.execute.await_args.args
    assert 'ELSE jev_tenant_config.api_key_encrypted' in str(sql)
    assert params['replace'] is False and params['remove'] is False
    assert params['id'] == tenant
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_key_connection_test_has_no_egress(monkeypatch):
    tenant = uuid4()
    monkeypatch.setattr(j, 'load_config', AsyncMock(return_value=j.JevTenantConfig(tenant)))
    network = AsyncMock()
    monkeypatch.setattr(j.httpx, 'AsyncClient', network)
    assert (await j.test_connection(object(),tenant))['status'] == 'not_configured'
    network.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,payload,expected', [
    (200, {'models':[{'name':'jev-latest'}]},'connected'),
    (200, {'models':[]},'invalid_response'), (200, ['PRIVATE'],'invalid_response'),
    (401, {'detail':'PRIVATE'},'invalid_credentials'),
    (403, {'detail':'PRIVATE'},'invalid_credentials'),
    (429, {'detail':'PRIVATE'},'rate_limited'), (529, {'detail':'PRIVATE'},'unavailable'),
    (302, {'detail':'PRIVATE'},'unavailable'), (500, {'detail':'PRIVATE'},'unavailable'),
])
async def test_official_models_connection_contract(monkeypatch,code,payload,expected):
    tenant = uuid4()
    config = j.JevTenantConfig(tenant,has_key=True,api_key=SecretStr('synthetic-secret'),version=7)
    monkeypatch.setattr(j, 'load_config', AsyncMock(return_value=config))
    observed = {}
    class Client:
        def __init__(self,**kwargs): observed.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,headers):
            assert url == 'https://api.typesafe.ai/v1/models'
            assert headers == {'Authorization':'Bearer synthetic-secret'}
            return httpx.Response(code,json=payload)
    monkeypatch.setattr(j.httpx,'AsyncClient',Client)
    db = SimpleNamespace(execute=AsyncMock(),commit=AsyncMock())
    await j.test_connection(db,tenant)
    assert db.execute.await_args.args[1] == {'status':expected,'id':tenant,'version':7}
    assert observed == {'timeout':8,'follow_redirects':False,'trust_env':False}


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status',[(httpx.ReadTimeout('PRIVATE'),'timeout'),(httpx.ConnectError('PRIVATE'),'unavailable')])
async def test_connection_exceptions_are_sanitized(monkeypatch,error,status):
    tenant = uuid4()
    config = j.JevTenantConfig(tenant,has_key=True,api_key=SecretStr('synthetic-secret'),version=3)
    monkeypatch.setattr(j,'load_config',AsyncMock(return_value=config))
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,*args,**kwargs): raise error
    monkeypatch.setattr(j.httpx,'AsyncClient',Client)
    db = SimpleNamespace(execute=AsyncMock(),commit=AsyncMock())
    result = await j.test_connection(db,tenant)
    assert 'PRIVATE' not in json.dumps(result)
    assert db.execute.await_args.args[1]['status'] == status
