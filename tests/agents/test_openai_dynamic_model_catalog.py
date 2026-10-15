from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import openai_model_catalog as catalog


def test_specialized_models_are_not_conversation_candidates_and_unknown_needs_validation():
    assert catalog.sanitize_model({"id": "text-embedding-4", "created": 1})["compatibility"] == "incompatible"
    assert catalog.sanitize_model({"id": "gpt-new-model-2031", "created": 2})["validation_required"] is True
    assert catalog.sanitize_model({"id": "gpt-4o-mini", "created": 3})["compatibility"] == "known_compatible"


def test_credential_fingerprint_is_not_the_credential():
    key = "synthetic-openai-key-for-fingerprint-test-only"
    fingerprint = catalog.credential_fingerprint(key)
    assert key not in fingerprint
    assert fingerprint != catalog.credential_fingerprint(key + "x")


@pytest.mark.asyncio
async def test_catalog_is_scoped_to_tenant_key_and_persists_a_sanitized_snapshot(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000001"
    db = SimpleNamespace(add=AsyncMock(), flush=AsyncMock(), execute=AsyncMock())
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=(None, "default", "tenant-scope-hash")))
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(catalog, "_list_models_from_openai", AsyncMock(return_value=[{
        "id": "gpt-new", "name": "gpt-new", "provider": "openai", "availability": "available",
        "compatibility": "needs_validation", "compatibility_known": False, "validation_required": True,
        "source": "openai_api_v1_models", "fetched_at": None,
    }]))
    monkeypatch.setattr(catalog, "_with_validation_state", AsyncMock(side_effect=lambda _db, _tenant, _fp, items: items))

    result = await catalog.list_models(db, tenant)

    assert result["items"][0]["id"] == "gpt-new"
    assert result["items"][0]["validation_required"] is True
    catalog.get_tenant_api_key.assert_awaited_once_with(tenant, db)
    catalog._list_models_from_openai.assert_awaited_once_with("tenant-key", None)
    db.execute.assert_awaited_once()
    assert "tenant-key" not in repr(db.execute.await_args)


@pytest.mark.asyncio
async def test_catalog_falls_back_to_stale_valid_cache_without_erasing_it(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000002"
    valid = [{"id": "gpt-old", "compatibility": "needs_validation"}]
    cached = SimpleNamespace(models_json=valid, fetched_at=datetime.utcnow() - timedelta(hours=2))
    db = SimpleNamespace()
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=(None, "default", "scope")))
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=cached))
    monkeypatch.setattr(catalog, "_list_models_from_openai", AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(catalog, "_with_validation_state", AsyncMock(side_effect=lambda _db, _tenant, _fp, items: items))

    result = await catalog.list_models(db, tenant, refresh=True)

    assert result["stale"] is True
    assert result["refresh_error"] == "provider_timeout"
    assert result["items"] == valid
    assert cached.models_json == valid


@pytest.mark.asyncio
async def test_catalog_uses_fresh_cache_and_explicit_refresh_updates_snapshot(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000006"
    cached = SimpleNamespace(models_json=[{"id": "gpt-old"}], fetched_at=datetime.utcnow())
    db = SimpleNamespace(flush=AsyncMock())
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=(None, "default", "scope")))
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=cached))
    monkeypatch.setattr(catalog, "_with_validation_state", AsyncMock(side_effect=lambda _db, _tenant, _fp, items: items))
    upstream = AsyncMock(return_value=[{"id": "gpt-new"}])
    monkeypatch.setattr(catalog, "_list_models_from_openai", upstream)

    from_cache = await catalog.list_models(db, tenant)
    assert from_cache["source"] == "cache"
    upstream.assert_not_awaited()

    refreshed = await catalog.list_models(db, tenant, refresh=True)
    assert refreshed["source"] == "openai_api_v1_models"
    assert cached.models_json[0]["id"] == "gpt-new"
    upstream.assert_awaited_once_with("tenant-key", None)


@pytest.mark.asyncio
async def test_catalog_without_tenant_key_never_uses_platform_fallback(monkeypatch):
    db = SimpleNamespace()
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(side_effect=ValueError("Esta empresa ainda não configurou a chave da OpenAI.")))
    result = await catalog.list_models(db, "00000000-0000-0000-0000-000000000003")
    assert result["credential_status"] == "not_configured"
    assert result["items"] == []


@pytest.mark.asyncio
async def test_explicit_compatibility_test_sends_only_minimal_synthetic_chat_request(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000004"
    captured = {}

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

        async def close(self):
            return None

    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=(None, "default", "scope")))
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=SimpleNamespace(models_json=[{"id": "gpt-new"}])))
    monkeypatch.setattr(catalog, "_save_validation", AsyncMock())
    with patch("openai.AsyncOpenAI", FakeClient):
        result = await catalog.validate_chat_model(SimpleNamespace(), tenant, "gpt-new")

    assert result["compatibility"] == "verified_compatible"
    assert captured["model"] == "gpt-new"
    assert captured["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert "tenant-key" not in repr(result)


@pytest.mark.asyncio
async def test_catalog_reader_consumes_all_openai_model_pages(monkeypatch):
    captured = {}

    class ModelPages:
        async def list(self):
            async def rows():
                yield SimpleNamespace(id="gpt-page-one", created=1, owned_by="org")
                yield SimpleNamespace(id="text-embedding-3-small", created=2, owned_by="org")
                yield SimpleNamespace(id="gpt-page-two", created=3, owned_by="org")
            return rows()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = ModelPages()

        async def close(self):
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    items = await catalog._list_models_from_openai("tenant-key")

    by_id = {item["id"]: item for item in items}
    assert set(by_id) == {"gpt-page-one", "text-embedding-3-small", "gpt-page-two"}
    assert by_id["text-embedding-3-small"]["compatibility"] == "incompatible"
    assert all(item["availability"] == "listed" for item in items)
    assert captured["api_key"] == "tenant-key"


@pytest.mark.asyncio
async def test_known_model_lookup_uses_tenant_project_scoped_snapshot(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000005"
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=("proj-test", "project-fingerprint", "credential-scope")))
    snapshot = SimpleNamespace(models_json=[{"id": "gpt-4o-mini"}])
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=snapshot))

    result = await catalog.model_is_validated(db, tenant, "gpt-4o-mini")

    assert result is True
    catalog._snapshot.assert_awaited_once_with(db, tenant, "credential-scope", "project-fingerprint")


@pytest.mark.asyncio
async def test_removed_model_cannot_be_reselected_from_an_old_validation_record(monkeypatch):
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(validation_status="compatible")))
    monkeypatch.setattr(catalog, "get_tenant_api_key", AsyncMock(return_value="tenant-key"))
    monkeypatch.setattr(catalog, "_project_context", AsyncMock(return_value=(None, "default", "credential-scope")))
    monkeypatch.setattr(catalog, "_snapshot", AsyncMock(return_value=None))

    result = await catalog.model_is_validated(db, "00000000-0000-0000-0000-000000000008", "gpt-removed")

    assert result is False
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_incompatible_model_result_is_retained_in_catalog_state():
    rows = [SimpleNamespace(model_id="gpt-new", validation_status="incompatible")]
    execute_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    item = {"id": "gpt-new", "compatibility": "needs_validation", "validation_required": True}

    result = await catalog._with_validation_state(db, "tenant", "scope", [item])

    assert result[0]["compatibility"] == "incompatible"
    assert result[0]["validation_required"] is False
