from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any


STATE_TTL_SECONDS = 10 * 60
STATE_PURPOSE = "meta_embedded_signup"
MARKETING_OAUTH_STATE_PURPOSE = "meta_marketing_ads_oauth"
ALLOWED_STATE_PURPOSES = {STATE_PURPOSE, MARKETING_OAUTH_STATE_PURPOSE}
STANDARD_EMBEDDED_SIGNUP = "standard_embedded_signup"
BUSINESS_APP_ONBOARDING = "business_app_onboarding"
ALLOWED_ONBOARDING_MODES = {STANDARD_EMBEDDED_SIGNUP, BUSINESS_APP_ONBOARDING}
_USED_NONCES: dict[str, float] = {}


class MetaSignupStateError(ValueError):
    pass


@dataclass(frozen=True)
class MetaSignupState:
    empresa_id: str
    usuario_id: str
    session_id: str
    purpose: str
    nonce: str
    onboarding_mode: str
    issued_at: int
    expires_at: int


def _signing_key() -> bytes:
    key = str(os.getenv("SECRET_KEY") or "").strip()
    if len(key) < 32:
        raise MetaSignupStateError("SECRET_KEY ausente ou curta demais para assinar state.")
    return key.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def normalize_onboarding_mode(value: str | None) -> str:
    mode = str(value or STANDARD_EMBEDDED_SIGNUP).strip()
    if mode not in ALLOWED_ONBOARDING_MODES:
        raise MetaSignupStateError("modo de onboarding Meta invalido.")
    return mode


def create_signup_state(
    *,
    empresa_id: str,
    usuario_id: str,
    session_id: str | None = None,
    ttl_seconds: int = STATE_TTL_SECONDS,
    onboarding_mode: str | None = None,
    purpose: str = STATE_PURPOSE,
) -> str:
    now = int(time.time())
    mode = normalize_onboarding_mode(onboarding_mode)
    payload = {
        "empresa_id": str(empresa_id),
        "usuario_id": str(usuario_id),
        "session_id": str(session_id or secrets.token_urlsafe(18)),
        "purpose": _normalize_purpose(purpose),
        "nonce": secrets.token_urlsafe(24),
        "onboarding_mode": mode,
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_signing_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}"


async def _mark_nonce_used(redis_client: Any, nonce: str, expires_at: int) -> None:
    ttl = max(1, expires_at - int(time.time()))
    key = f"meta:embedded-signup:state:{nonce}"
    if redis_client is not None:
        ok = await redis_client.set(key, "1", ex=ttl, nx=True)
        if not ok:
            raise MetaSignupStateError("state ja utilizado.")
        return

    now = time.time()
    for item, expiry in list(_USED_NONCES.items()):
        if expiry <= now:
            _USED_NONCES.pop(item, None)
    if nonce in _USED_NONCES:
        raise MetaSignupStateError("state ja utilizado.")
    _USED_NONCES[nonce] = float(expires_at)


async def validate_signup_state(
    token: str,
    *,
    empresa_id: str,
    usuario_id: str,
    onboarding_mode: str | None = None,
    redis_client: Any = None,
    consume: bool = True,
    purpose: str = STATE_PURPOSE,
) -> MetaSignupState:
    raw = str(token or "").strip()
    try:
        body_b64, signature_b64 = raw.split(".", 1)
        body = _unb64(body_b64)
        supplied = _unb64(signature_b64)
    except Exception as exc:
        raise MetaSignupStateError("state malformado.") from exc

    expected = hmac.new(_signing_key(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, supplied):
        raise MetaSignupStateError("assinatura de state invalida.")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise MetaSignupStateError("payload de state invalido.") from exc

    if payload.get("purpose") != _normalize_purpose(purpose):
        raise MetaSignupStateError("finalidade de state invalida.")
    if str(payload.get("empresa_id")) != str(empresa_id):
        raise MetaSignupStateError("state pertence a outra empresa.")
    if str(payload.get("usuario_id")) != str(usuario_id):
        raise MetaSignupStateError("state pertence a outro usuario.")
    mode = normalize_onboarding_mode(str(payload.get("onboarding_mode") or STANDARD_EMBEDDED_SIGNUP))
    expected_mode = normalize_onboarding_mode(onboarding_mode)
    if mode != expected_mode:
        raise MetaSignupStateError("state pertence a outro modo de onboarding.")

    expires_at = int(payload.get("exp") or 0)
    if expires_at < int(time.time()):
        raise MetaSignupStateError("state expirado.")

    state = MetaSignupState(
        empresa_id=str(payload["empresa_id"]),
        usuario_id=str(payload["usuario_id"]),
        session_id=str(payload["session_id"]),
        purpose=str(payload["purpose"]),
        nonce=str(payload["nonce"]),
        onboarding_mode=mode,
        issued_at=int(payload.get("iat") or 0),
        expires_at=expires_at,
    )
    if consume:
        await _mark_nonce_used(redis_client, state.nonce, state.expires_at)
    return state


def _normalize_purpose(value: str) -> str:
    purpose = str(value or "").strip()
    if purpose not in ALLOWED_STATE_PURPOSES:
        raise MetaSignupStateError("finalidade de state invalida.")
    return purpose
