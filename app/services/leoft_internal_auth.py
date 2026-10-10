from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from fastapi import HTTPException, Request

from app.services.leoft_hmac_v1 import build_hmac_v1_signing_input

CATALOG_MAX_BODY_BYTES = 16 * 1024 * 1024
CATALOG_READ_MAX_BODY_BYTES = 1 * 1024 * 1024
CATALOG_READ_EVENTS = {
    "leoft.catalog.read.meta",
    "leoft.catalog.read.entry",
    "leoft.catalog.read.search",
    "leoft.catalog.read.media",
    "leoft.catalog.read.association",
}

GENERATION_SCOPES = {
    "leoft.catalog.read",
    "leoft.generation.read",
    "leoft.generation.write",
    "leoft.generation.dispatch",
}
GENERATION_MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class HermesAuthContext:
    key_id: str
    scopes: frozenset[str]
    company_ids: frozenset[UUID] | None
    event: str
    idempotency_key: str
    request_hash: str

    def require_company(self, company_id: UUID) -> None:
        if self.company_ids is not None and company_id not in self.company_ids:
            raise HTTPException(403, "hermes_company_forbidden")


@dataclass(frozen=True)
class _HermesCredential:
    key_id: str
    secret: str
    scopes: frozenset[str]
    company_ids: frozenset[UUID] | None


def _secret() -> str:
    value = str(os.getenv("HERMES_LEOFT_HMAC_SECRET") or "").strip()
    if not value:
        raise HTTPException(503, "internal_auth_unconfigured")
    return value


async def _require_hmac(
    request: Request,
    *,
    event_header: str,
    timestamp_header: str,
    signature_header: str,
    idempotency_header: str,
    max_body_bytes: int,
) -> tuple[str, str]:
    body = await request.body()
    if len(body) > max_body_bytes:
        raise HTTPException(413, "body_too_large")
    timestamp = str(request.headers.get(timestamp_header) or "").strip()
    signature = str(request.headers.get(signature_header) or "").strip().lower()
    key = str(request.headers.get(idempotency_header) or "").strip()
    if not timestamp or not signature or not key or len(key) > 256:
        raise HTTPException(401, "invalid_internal_auth")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(401, "invalid_internal_auth")
    if abs(int(time.time()) - ts) > int(
        os.getenv("HERMES_LEOFT_HMAC_TOLERANCE_SECONDS", "300")
    ):
        raise HTTPException(401, "expired_internal_auth")
    expected = hmac.new(_secret().encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid_internal_auth")
    return key, hashlib.sha256(body).hexdigest()


async def require_hermes_hmac(request: Request) -> tuple[str, str]:
    return await _require_hmac(
        request,
        event_header="X-AgenteOS-Event",
        timestamp_header="X-AgenteOS-Timestamp",
        signature_header="X-AgenteOS-Signature",
        idempotency_header="X-AgenteOS-Idempotency-Key",
        max_body_bytes=2 * 1024 * 1024,
    )


async def require_hermes_catalog_hmac(request: Request) -> tuple[str, str]:
    if (
        str(request.headers.get("X-Hermes-Event") or "").strip()
        != "leoft.catalog.import"
    ):
        raise HTTPException(401, "invalid_catalog_event_header")
    return await _require_hmac(
        request,
        event_header="X-Hermes-Event",
        timestamp_header="X-Hermes-Timestamp",
        signature_header="X-Hermes-Signature",
        idempotency_header="X-Hermes-Idempotency-Key",
        max_body_bytes=CATALOG_MAX_BODY_BYTES,
    )


async def require_hermes_catalog_read_hmac(request: Request) -> tuple[str, str]:
    event = str(request.headers.get("X-Hermes-Event") or "").strip()
    if event not in CATALOG_READ_EVENTS:
        raise HTTPException(401, "invalid_catalog_read_event_header")
    if request.method.upper() == "GET" and await request.body() != b"":
        raise HTTPException(400, "get_body_must_be_empty")
    return await _require_hmac(
        request,
        event_header="X-Hermes-Event",
        timestamp_header="X-Hermes-Timestamp",
        signature_header="X-Hermes-Signature",
        idempotency_header="X-Hermes-Idempotency-Key",
        max_body_bytes=CATALOG_READ_MAX_BODY_BYTES,
    )


def require_hermes_generation_hmac(*, scope: str, event: str):
    if scope not in GENERATION_SCOPES or scope == "leoft.catalog.read":
        raise ValueError("invalid_generation_scope")

    async def dependency(request: Request) -> HermesAuthContext:
        return await _require_scoped_hermes_hmac(
            request,
            required_scope=scope,
            required_event=event,
        )

    return dependency


async def _require_scoped_hermes_hmac(
    request: Request,
    *,
    required_scope: str,
    required_event: str,
) -> HermesAuthContext:
    body = await request.body()
    if len(body) > GENERATION_MAX_BODY_BYTES:
        raise HTTPException(413, "body_too_large")
    if request.method.upper() == "GET" and body != b"":
        raise HTTPException(400, "get_body_must_be_empty")
    _require_secure_transport(request)

    event = str(request.headers.get("X-Hermes-Event") or "").strip()
    timestamp = str(request.headers.get("X-Hermes-Timestamp") or "").strip()
    signature = str(request.headers.get("X-Hermes-Signature") or "").strip().lower()
    idempotency_key = str(request.headers.get("X-Hermes-Idempotency-Key") or "").strip()
    key_id = str(request.headers.get("X-Hermes-Key-Id") or "").strip()
    if (
        event != required_event
        or not timestamp
        or not signature
        or not idempotency_key
        or not key_id
        or len(idempotency_key) > 256
        or len(key_id) > 128
    ):
        raise HTTPException(401, "invalid_hermes_auth")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(401, "invalid_hermes_auth") from exc
    tolerance = max(
        30, min(900, int(os.getenv("HERMES_LEOFT_HMAC_TOLERANCE_SECONDS", "300")))
    )
    if abs(int(time.time()) - timestamp_value) > tolerance:
        raise HTTPException(401, "expired_hermes_auth")

    credential = _load_generation_credentials().get(key_id)
    if credential is None:
        raise HTTPException(401, "unknown_hermes_key")
    if required_scope not in credential.scopes:
        raise HTTPException(403, "hermes_scope_forbidden")
    try:
        signing_input = build_hmac_v1_signing_input(
            method=request.method,
            path=request.url.path,
            event=event,
            timestamp=timestamp,
            idempotency_key=idempotency_key,
            key_id=key_id,
            body=body,
        )
    except ValueError as exc:
        raise HTTPException(401, "invalid_hermes_auth") from exc
    expected = hmac.new(
        credential.secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid_hermes_auth")

    request_hash = hashlib.sha256(
        request.method.upper().encode()
        + b"\n"
        + request.url.path.encode()
        + b"\n"
        + body
    ).hexdigest()
    replay_material = f"{key_id}\n{event}\n{idempotency_key}\n{request_hash}".encode()
    replay_key = "leoft:hermes:replay:" + hashlib.sha256(replay_material).hexdigest()
    await _consume_replay_key(replay_key, ttl_seconds=tolerance * 2)
    return HermesAuthContext(
        key_id=key_id,
        scopes=credential.scopes,
        company_ids=credential.company_ids,
        event=event,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


def _load_generation_credentials() -> dict[str, _HermesCredential]:
    credentials: dict[str, _HermesCredential] = {}
    raw_registry = str(os.getenv("HERMES_LEOFT_HMAC_KEYS_JSON") or "").strip()
    if raw_registry:
        try:
            document = json.loads(raw_registry)
        except json.JSONDecodeError as exc:
            raise HTTPException(503, "hermes_key_registry_invalid") from exc
        if not isinstance(document, dict):
            raise HTTPException(503, "hermes_key_registry_invalid")
        for key_id, record in document.items():
            if not isinstance(record, dict):
                raise HTTPException(503, "hermes_key_registry_invalid")
            credentials[str(key_id)] = _parse_credential(str(key_id), record)

    generation_secret = str(
        os.getenv("HERMES_LEOFT_GENERATION_HMAC_SECRET") or ""
    ).strip()
    if generation_secret:
        key_id = str(
            os.getenv("HERMES_LEOFT_GENERATION_HMAC_KEY_ID") or "generation-default"
        ).strip()
        company_text = str(
            os.getenv("HERMES_LEOFT_GENERATION_COMPANY_IDS") or ""
        ).strip()
        if not company_text:
            raise HTTPException(503, "hermes_generation_company_scope_unconfigured")
        credentials[key_id] = _HermesCredential(
            key_id=key_id,
            secret=generation_secret,
            scopes=frozenset(
                {
                    "leoft.catalog.read",
                    "leoft.generation.read",
                    "leoft.generation.write",
                    "leoft.generation.dispatch",
                }
            ),
            company_ids=_parse_company_ids(company_text),
        )
    return credentials


def _parse_credential(key_id: str, record: dict[str, Any]) -> _HermesCredential:
    secret = str(record.get("secret") or "").strip()
    scopes = frozenset(str(item).strip() for item in record.get("scopes", []))
    if not secret or not scopes or not scopes.issubset(GENERATION_SCOPES):
        raise HTTPException(503, "hermes_key_registry_invalid")
    companies = record.get("company_ids")
    if companies == "*":
        company_ids = None
    elif isinstance(companies, list) and companies:
        company_ids = _parse_company_ids(",".join(str(item) for item in companies))
    else:
        raise HTTPException(503, "hermes_key_company_scope_invalid")
    return _HermesCredential(
        key_id=key_id,
        secret=secret,
        scopes=scopes,
        company_ids=company_ids,
    )


def _parse_company_ids(value: str) -> frozenset[UUID] | None:
    text = value.strip()
    if text == "*":
        return None
    try:
        result = frozenset(
            UUID(item.strip()) for item in text.split(",") if item.strip()
        )
    except ValueError as exc:
        raise HTTPException(503, "hermes_key_company_scope_invalid") from exc
    if not result:
        raise HTTPException(503, "hermes_key_company_scope_invalid")
    return result


async def _consume_replay_key(key: str, *, ttl_seconds: int) -> None:
    client = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    try:
        accepted = await client.set(key, "1", ex=ttl_seconds, nx=True)
    except Exception as exc:
        raise HTTPException(503, "hermes_replay_store_unavailable") from exc
    finally:
        await client.aclose()
    if not accepted:
        raise HTTPException(409, "hermes_replay_detected")


def _require_secure_transport(request: Request) -> None:
    scheme = request.url.scheme.lower()
    host = (request.url.hostname or "").lower()
    local_hosts = {"127.0.0.1", "localhost", "testserver"}
    trusted_proxies = {
        item.strip()
        for item in str(os.getenv("HERMES_TRUSTED_PROXY_IPS") or "").split(",")
        if item.strip()
    }
    client_host = request.client.host if request.client else ""
    if client_host in trusted_proxies:
        forwarded = str(request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0]
        scheme = forwarded.strip().lower() or scheme
    if scheme == "https" or (scheme == "http" and host in local_hosts):
        return
    raise HTTPException(400, "https_required")
