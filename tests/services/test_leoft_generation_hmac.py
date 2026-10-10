from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import b64decode
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services import leoft_internal_auth as auth_module
from app.services.leoft_generation_dispatch_client import (
    GENERATION_START_EVENT,
    GenerationDispatchConfig,
    GenerationStartPayload,
    build_generation_dispatch_headers,
    canonical_generation_body,
)
from app.services.leoft_hmac_v1 import build_hmac_v1_signing_input


def _request(
    *,
    body: bytes = b"{}",
    event: str = "leoft.generation.job.read.v1",
    timestamp: int | None = None,
    signature: str | None = None,
    key_id: str = "generation-test",
    method: str = "POST",
    host: str = "testserver",
    idempotency_key: str | None = None,
    path: str = "/api/internal/hermes/leoft/generation/v1/jobs/00000000-0000-0000-0000-000000000001",
    signing_path: str | None = None,
    forwarded_proto: str | None = None,
) -> Request:
    secret = "synthetic-generation-secret"
    effective_timestamp = str(timestamp or int(time.time()))
    idempotency_key = idempotency_key or f"idem-{uuid4()}"
    signing_input = build_hmac_v1_signing_input(
        method=method,
        path=signing_path or path,
        event=event,
        timestamp=effective_timestamp,
        idempotency_key=idempotency_key,
        key_id=key_id,
        body=body,
    )
    headers = {
        "host": host,
        "x-hermes-event": event,
        "x-hermes-timestamp": effective_timestamp,
        "x-hermes-signature": signature
        or hmac.new(secret.encode(), signing_input, hashlib.sha256).hexdigest(),
        "x-hermes-idempotency-key": idempotency_key,
        "x-hermes-key-id": key_id,
    }
    if forwarded_proto:
        headers["x-forwarded-proto"] = forwarded_proto
    scope = {
        "type": "http",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        "server": (host, 80),
        "client": ("198.51.100.8", 12345),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def generation_key(monkeypatch):
    company_id = uuid4()
    monkeypatch.setenv(
        "HERMES_LEOFT_HMAC_KEYS_JSON",
        json.dumps(
            {
                "generation-test": {
                    "secret": "synthetic-generation-secret",
                    "scopes": [
                        "leoft.generation.read",
                        "leoft.generation.write",
                    ],
                    "company_ids": [str(company_id)],
                },
                "catalog-test": {
                    "secret": "synthetic-catalog-secret",
                    "scopes": ["leoft.catalog.read"],
                    "company_ids": [str(company_id)],
                },
            }
        ),
    )
    return company_id


@pytest.mark.asyncio
async def test_generation_hmac_valid_invalid_expired_scope_company_and_replay(
    monkeypatch, generation_key
):
    consumed: set[str] = set()

    async def consume(key: str, *, ttl_seconds: int) -> None:
        assert ttl_seconds >= 60
        if key in consumed:
            raise HTTPException(409, "hermes_replay_detected")
        consumed.add(key)

    monkeypatch.setattr(auth_module, "_consume_replay_key", consume)
    dependency = auth_module.require_hermes_generation_hmac(
        scope="leoft.generation.read", event="leoft.generation.job.read.v1"
    )
    request = _request()
    context = await dependency(request)
    context.require_company(generation_key)
    with pytest.raises(HTTPException, match="hermes_replay_detected"):
        await dependency(request)

    with pytest.raises(HTTPException, match="invalid_hermes_auth"):
        await dependency(_request(signature="0" * 64))
    with pytest.raises(HTTPException, match="expired_hermes_auth"):
        await dependency(_request(timestamp=int(time.time()) - 9999))
    with pytest.raises(HTTPException, match="unknown_hermes_key"):
        await dependency(_request(key_id="unknown"))
    with pytest.raises(HTTPException, match="hermes_scope_forbidden"):
        await dependency(_request(key_id="catalog-test"))
    with pytest.raises(HTTPException, match="hermes_company_forbidden"):
        context.require_company(uuid4())


@pytest.mark.asyncio
async def test_generation_get_requires_empty_body(monkeypatch):
    async def consume(key: str, *, ttl_seconds: int) -> None:
        return None

    monkeypatch.setattr(auth_module, "_consume_replay_key", consume)
    dependency = auth_module.require_hermes_generation_hmac(
        scope="leoft.generation.read", event="leoft.generation.job.read.v1"
    )
    with pytest.raises(HTTPException, match="get_body_must_be_empty"):
        await dependency(_request(body=b" ", method="GET"))


@pytest.mark.asyncio
async def test_generation_hmac_binds_path_and_replay_survives_timestamp_refresh(
    monkeypatch,
):
    consumed: set[str] = set()

    async def consume(key: str, *, ttl_seconds: int) -> None:
        if key in consumed:
            raise HTTPException(409, "hermes_replay_detected")
        consumed.add(key)

    monkeypatch.setattr(auth_module, "_consume_replay_key", consume)
    dependency = auth_module.require_hermes_generation_hmac(
        scope="leoft.generation.read", event="leoft.generation.job.read.v1"
    )
    idem = "idem-fixed-replay-test"
    await dependency(_request(idempotency_key=idem, timestamp=int(time.time())))
    with pytest.raises(HTTPException, match="hermes_replay_detected"):
        await dependency(_request(idempotency_key=idem, timestamp=int(time.time()) + 1))
    with pytest.raises(HTTPException, match="invalid_hermes_auth"):
        await dependency(
            _request(
                path="/api/internal/hermes/leoft/generation/v1/jobs/00000000-0000-0000-0000-000000000002",
                signing_path="/api/internal/hermes/leoft/generation/v1/jobs/00000000-0000-0000-0000-000000000001",
            )
        )


@pytest.mark.asyncio
async def test_untrusted_proxy_cannot_forge_https(monkeypatch):
    monkeypatch.delenv("HERMES_TRUSTED_PROXY_IPS", raising=False)
    dependency = auth_module.require_hermes_generation_hmac(
        scope="leoft.generation.read", event="leoft.generation.job.read.v1"
    )
    with pytest.raises(HTTPException, match="https_required"):
        await dependency(_request(host="api.example", forwarded_proto="https"))


def test_outbound_generation_contract_is_deterministic_and_signed():
    payload = GenerationStartPayload(
        generation_job_id=uuid4(),
        company_id=uuid4(),
        target_version_id=uuid4(),
        schema_version="leoft.training-plan.v2",
        correlation_id=uuid4(),
        callback_base_url="https://agentos.example/api/internal/hermes/leoft",
        requested_duration_weeks=4,
        sessions_per_week=3,
        snapshot_hash="a" * 64,
        idempotency_key="synthetic-idempotency",
    )
    body = canonical_generation_body(payload)
    assert "idempotency_key" not in json.loads(body.decode("utf-8"))
    assert body == canonical_generation_body(
        payload.model_dump(mode="json", exclude={"idempotency_key"})
    )
    config = GenerationDispatchConfig(
        base_url="https://hermes.example",
        secret="synthetic-generation-secret",
        key_id="generation-test",
    )
    headers = build_generation_dispatch_headers(
        payload=payload, body=body, config=config
    )
    assert headers["X-Hermes-Event"] == GENERATION_START_EVENT
    assert headers["X-Hermes-Key-Id"] == "generation-test"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Correlation-Id"] == str(payload.correlation_id)
    signing_input = build_hmac_v1_signing_input(
        method="POST",
        path="/api/internal/agentos/leoft/generation/v1/jobs/start",
        event=headers["X-Hermes-Event"],
        timestamp=headers["X-Hermes-Timestamp"],
        idempotency_key=headers["X-Hermes-Idempotency-Key"],
        key_id=headers["X-Hermes-Key-Id"],
        body=body,
    )
    assert hmac.compare_digest(
        headers["X-Hermes-Signature"],
        hmac.new(config.secret.encode(), signing_input, hashlib.sha256).hexdigest(),
    )


def test_outbound_generation_matches_hermes_synthetic_vector():
    vector = json.loads(
        Path(
            "reports/leoft-e2e-s4b-r5/"
            "agentos_generation_callback_hmac_test_vector.json"
        ).read_text(encoding="utf-8")
    )
    body = b64decode(vector["body_base64"])
    assert body.decode("utf-8") == vector["body_json"]
    assert hashlib.sha256(body).hexdigest() == vector["body_sha256"]

    signing_input = build_hmac_v1_signing_input(
        method=vector["method"],
        path=vector["path"],
        event=vector["event"],
        timestamp=vector["timestamp"],
        idempotency_key=vector["idempotency_key"],
        key_id=vector["key_id"],
        body=body,
    )
    assert signing_input == b64decode(vector["signing_input_base64"])
    assert len(signing_input) == vector["signing_input_bytes"]
    assert signing_input.endswith(body)
    assert signing_input[-len(body) - 1 : -len(body)] == b"\n"

    signature = hmac.new(
        vector["secret"].encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    assert signature == vector["expected_signature"]
    assert signature == signature.lower()

    payload = GenerationStartPayload(
        generation_job_id=uuid4(),
        company_id=uuid4(),
        target_version_id=uuid4(),
        schema_version="leoft.training-plan.v2",
        correlation_id=uuid4(),
        callback_base_url="https://agentos.example/api/internal/hermes/leoft",
        requested_duration_weeks=4,
        sessions_per_week=3,
        snapshot_hash="a" * 64,
        idempotency_key=vector["idempotency_key"],
    )
    headers = build_generation_dispatch_headers(
        payload=payload,
        body=body,
        config=GenerationDispatchConfig(
            base_url="https://app.leoft.com.br",
            secret=vector["secret"],
            key_id=vector["key_id"],
        ),
        timestamp=vector["timestamp"],
    )
    for name, value in vector["expected_headers"].items():
        assert headers[name] == value
