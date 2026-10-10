"""AgentOS -> Hermes generation dispatch contract."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.leoft_hmac_v1 import build_hmac_v1_signing_input

logger = logging.getLogger(__name__)

GENERATION_START_PATH = "/api/internal/agentos/leoft/generation/v1/jobs/start"
GENERATION_START_EVENT = "leoft.generation.job.start"


class GenerationDispatchError(RuntimeError):
    def __init__(
        self, code: str, *, retryable: bool, status_code: int | None = None
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(code)


class GenerationStartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_job_id: UUID
    company_id: UUID
    target_version_id: UUID
    schema_version: str = Field(min_length=1, max_length=80)
    correlation_id: UUID
    callback_base_url: str = Field(min_length=1, max_length=2048)
    requested_duration_weeks: int = Field(ge=1, le=52)
    sessions_per_week: int = Field(ge=1, le=14)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=256)

    @field_validator("callback_base_url")
    @classmethod
    def callback_must_be_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("callback_base_url must be an HTTPS origin")
        return value.rstrip("/")


@dataclass(frozen=True)
class GenerationDispatchConfig:
    base_url: str
    secret: str
    key_id: str
    timeout_seconds: float = 15.0
    maximum_attempts: int = 3

    @classmethod
    def from_environment(cls) -> GenerationDispatchConfig:
        callback_url = str(
            os.getenv("HERMES_LEOFT_GENERATION_CALLBACK_URL") or ""
        ).strip()
        base_url = str(os.getenv("HERMES_REMOTE_BASE_URL") or "").strip()
        if callback_url:
            parsed_callback = urlparse(callback_url)
            base_url = f"{parsed_callback.scheme}://{parsed_callback.netloc}"
        secret = str(
            os.getenv("HERMES_LEOFT_GENERATION_CALLBACK_HMAC_SECRET")
            or os.getenv("HERMES_LEOFT_GENERATION_HMAC_SECRET")
            or ""
        ).strip()
        key_id = str(
            os.getenv("HERMES_LEOFT_GENERATION_CALLBACK_HMAC_KEY_ID")
            or os.getenv("HERMES_LEOFT_GENERATION_HMAC_KEY_ID")
            or "generation-default"
        ).strip()
        if not base_url or not secret or not key_id:
            raise GenerationDispatchError(
                "generation_dispatch_unconfigured", retryable=False
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise GenerationDispatchError(
                "generation_dispatch_https_required", retryable=False
            )
        timeout = max(
            1.0,
            min(60.0, float(os.getenv("HERMES_REMOTE_TIMEOUT_SECONDS", "15"))),
        )
        attempts = max(
            1,
            min(5, int(os.getenv("HERMES_LEOFT_GENERATION_MAX_ATTEMPTS", "3"))),
        )
        return cls(
            base_url=base_url,
            secret=secret,
            key_id=key_id,
            timeout_seconds=timeout,
            maximum_attempts=attempts,
        )


def canonical_generation_body(
    payload: GenerationStartPayload | dict[str, Any],
) -> bytes:
    document = (
        payload.model_dump(mode="json", exclude={"idempotency_key"})
        if isinstance(payload, GenerationStartPayload)
        else payload
    )
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_generation_dispatch_headers(
    *,
    payload: GenerationStartPayload,
    body: bytes,
    config: GenerationDispatchConfig,
    timestamp: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    signing_input = build_hmac_v1_signing_input(
        method="POST",
        path=GENERATION_START_PATH,
        event=GENERATION_START_EVENT,
        timestamp=timestamp,
        idempotency_key=payload.idempotency_key,
        key_id=config.key_id,
        body=body,
    )
    signature = hmac.new(
        config.secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Hermes-Event": GENERATION_START_EVENT,
        "X-Hermes-Timestamp": timestamp,
        "X-Hermes-Signature": signature,
        "X-Hermes-Idempotency-Key": payload.idempotency_key,
        "X-Hermes-Key-Id": config.key_id,
        "X-Correlation-Id": str(payload.correlation_id),
    }


Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class HermesGenerationDispatchClient:
    def __init__(
        self,
        *,
        config: GenerationDispatchConfig | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config or GenerationDispatchConfig.from_environment()
        self.transport = transport or _urlopen_transport

    async def start_job(self, payload: GenerationStartPayload) -> dict[str, Any]:
        body = canonical_generation_body(payload)
        endpoint = urljoin(
            self.config.base_url.rstrip("/") + "/", GENERATION_START_PATH.lstrip("/")
        )
        last_error: GenerationDispatchError | None = None
        for attempt in range(1, self.config.maximum_attempts + 1):
            headers = build_generation_dispatch_headers(
                payload=payload, body=body, config=self.config
            )
            request = urllib.request.Request(
                endpoint, data=body, headers=headers, method="POST"
            )
            try:
                status, response_body = await asyncio.to_thread(
                    self.transport, request, self.config.timeout_seconds
                )
                if status not in {200, 202}:
                    raise GenerationDispatchError(
                        _http_error_code(status),
                        retryable=status >= 500 or status == 429,
                        status_code=status,
                    )
                response = json.loads(response_body.decode("utf-8"))
                if not isinstance(response, dict):
                    raise GenerationDispatchError(
                        "generation_dispatch_invalid_response", retryable=False
                    )
                return response
            except GenerationDispatchError as exc:
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning(
                    "leoft_generation_dispatch_network_error attempt=%s error_type=%s",
                    attempt,
                    type(exc).__name__,
                )
                last_error = GenerationDispatchError(
                    "generation_dispatch_network_error", retryable=True
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GenerationDispatchError(
                    "generation_dispatch_invalid_response", retryable=False
                ) from exc
            if last_error is None or not last_error.retryable:
                raise last_error or GenerationDispatchError(
                    "generation_dispatch_failed", retryable=False
                )
            if attempt < self.config.maximum_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
        raise last_error or GenerationDispatchError(
            "generation_dispatch_failed", retryable=True
        )


def _urlopen_transport(
    request: urllib.request.Request, timeout: float
) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(4096)


def _http_error_code(status: int) -> str:
    if status == 401:
        return "generation_dispatch_unauthorized"
    if status == 403:
        return "generation_dispatch_forbidden"
    if status == 409:
        return "generation_dispatch_idempotency_conflict"
    if status == 422:
        return "generation_dispatch_contract_error"
    if status == 429:
        return "generation_dispatch_rate_limited"
    if status >= 500:
        return "generation_dispatch_upstream_error"
    return "generation_dispatch_http_error"
