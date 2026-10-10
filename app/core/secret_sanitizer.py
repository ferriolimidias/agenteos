from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "***REDACTED***"

_SENSITIVE_KEYS = {
    "openai_api_key",
    "api_key_openai",
    "meta_access_token",
    "access_token",
    "x_access_token",
    "refresh_token",
    "token",
    "apikey",
    "api_key",
    "x_api_key",
    "key",
    "authorization",
    "bearer",
    "secret",
    "client_secret",
    "password",
    "senha",
    "webhook_secret",
    "verify_token",
    "evolution_apikey",
    "global_apikey",
}

_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_access_token",
    "_token",
    "_secret",
    "_password",
    "_senha",
)

_JSON_SECRET_KEYS = (
    "apikey",
    "api_key",
    "access_token",
    "authorization",
    "token",
    "secret",
    "openai_api_key",
    "api_key_openai",
    "meta_access_token",
    "evolution_apikey",
    "global_apikey",
    "refresh_token",
    "client_secret",
    "password",
    "senha",
    "webhook_secret",
    "verify_token",
)

_QUERY_SECRET_KEYS = (
    "apikey",
    "api_key",
    "access_token",
    "authorization",
    "token",
    "openai_api_key",
    "api_key_openai",
    "meta_access_token",
    "evolution_apikey",
    "global_apikey",
    "refresh_token",
    "client_secret",
    "secret",
    "password",
    "senha",
    "webhook_secret",
    "verify_token",
)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(key or "").strip().lower()).strip("_")


def is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if not normalized:
        return False
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def mask_secret(value: str | None) -> str:
    return REDACTED if str(value or "").strip() else ""


def sanitize_string(value: str) -> str:
    text = str(value or "")
    if not text:
        return text

    text = re.sub(r"\bsk-proj-[A-Za-z0-9_\-]+\b", "sk-proj-" + REDACTED, text)
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]+\b", "sk-" + REDACTED, text)
    text = re.sub(r"\bEAAT[A-Za-z0-9_\-]+\b", "EAAT" + REDACTED, text)
    text = re.sub(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._\-+/=]+", r"\1" + REDACTED, text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]+", "Bearer " + REDACTED, text)

    def redact_json_value(match: re.Match[str]) -> str:
        key = match.group(2)
        if not is_sensitive_key(key):
            return match.group(0)
        return f"{match.group(1)}{REDACTED}{match.group(4)}"

    text = re.sub(
        r'(?i)("([^"]+)"\s*:\s*")([^"]*)(")',
        redact_json_value,
        text,
    )
    text = re.sub(
        r"(?i)('([^']+)'\s*:\s*')([^']*)(')",
        redact_json_value,
        text,
    )

    def redact_query_value(match: re.Match[str]) -> str:
        key = match.group(2)
        if not is_sensitive_key(key):
            return match.group(0)
        return f"{match.group(1)}{key}{match.group(3)}{REDACTED}"

    text = re.sub(
        r"(?i)(^|[?&;\s])([A-Za-z0-9][A-Za-z0-9_-]*)(=)([^&;\s\"'<>]+)",
        redact_query_value,
        text,
    )

    def redact_header_value(match: re.Match[str]) -> str:
        key = match.group(2)
        if not is_sensitive_key(key):
            return match.group(0)
        value_text = match.group(4).strip()
        if _normalize_key(key) == "authorization" and value_text.lower().startswith("bearer "):
            redacted_value = "Bearer " + REDACTED
        else:
            redacted_value = REDACTED
        return f"{match.group(1)}{key}{match.group(3)}{redacted_value}"

    text = re.sub(
        r"(?im)(^|[ \t])([A-Za-z0-9][A-Za-z0-9_-]*)(\s*:\s*)([^\r\n,{}]+)",
        redact_header_value,
        text,
    )
    return text


def sanitize_for_log(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        safe: dict[str, Any] = {}
        for key, value in obj.items():
            key_text = str(key)
            if is_sensitive_key(key_text):
                safe[key_text] = REDACTED if value not in (None, "") else ""
            else:
                safe[key_text] = sanitize_for_log(value)
        return safe
    if isinstance(obj, list):
        return [sanitize_for_log(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_for_log(item) for item in obj)
    if isinstance(obj, str):
        return sanitize_string(obj)
    return obj


def sanitize_for_api(obj: Any) -> Any:
    return sanitize_for_log(obj)


def sanitize_credenciais_canais(obj: Any) -> Any:
    return sanitize_for_api(obj)
