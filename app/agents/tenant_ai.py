from __future__ import annotations

from typing import Any

OPENAI_STATUS_BLOCKLIST = {"chave_invalida", "sem_credito", "erro", "desativado", "inativo"}


def _clean_key(value: Any) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    return value or None


def _get_attr_or_item(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def get_tenant_ai_status(empresa: Any) -> dict[str, Any]:
    if empresa is None:
        return {"enabled": False, "reason": "empresa_missing"}

    openai_key = _clean_key(_get_attr_or_item(empresa, "openai_api_key"))
    credenciais = _get_attr_or_item(empresa, "credenciais_canais")
    if not isinstance(credenciais, dict):
        credenciais = {}
    legacy_key = _clean_key(credenciais.get("openai_api_key"))
    alternate_key = _clean_key(credenciais.get("api_key_openai"))
    status_openai = _clean_key(_get_attr_or_item(empresa, "status_openai"))
    status_normalized = status_openai.lower() if status_openai else None

    if status_normalized in OPENAI_STATUS_BLOCKLIST:
        return {
            "enabled": False,
            "reason": "openai_status_blocked",
            "status_openai": status_normalized,
        }

    if openai_key or legacy_key or alternate_key:
        return {
            "enabled": True,
            "reason": "openai_key_present",
            "status_openai": status_normalized,
        }

    return {
        "enabled": False,
        "reason": "missing_openai_key",
        "status_openai": status_normalized,
    }


def tenant_ai_enabled(empresa: Any) -> bool:
    return bool(get_tenant_ai_status(empresa).get("enabled"))
