from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.agents.tenant_ai import tenant_ai_enabled


_SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|access[_-]?token|token|secret|password|webhook|bearer|authorization)", re.IGNORECASE)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _raw_text(value: Any) -> str | None:
    """Return configured text without changing intentional whitespace."""
    if value is None:
        return None
    return str(value)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _SECRET_KEY_PATTERN.search(key_text):
                continue
            sanitized = _scrub_value(nested)
            if sanitized in (None, "", {}, []):
                continue
            cleaned[key_text] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_list = [_scrub_value(item) for item in value]
        return [item for item in cleaned_list if item not in (None, "", {}, [])]
    if isinstance(value, tuple):
        cleaned_list = [_scrub_value(item) for item in value]
        return [item for item in cleaned_list if item not in (None, "", {}, [])]
    return value


def build_tenant_agent_config(empresa) -> dict[str, Any]:
    credenciais = _get(empresa, "credenciais_canais", {}) or {}
    if not isinstance(credenciais, Mapping):
        credenciais = {}

    tokenized_links = credenciais.get("links_tokenizados")
    anamnese_available = any(
        isinstance(config, Mapping)
        and bool(config.get("ativo", True))
        and str(config.get("finalidade") or "").strip().lower() == "anamnese"
        for config in (tokenized_links.values() if isinstance(tokenized_links, Mapping) else [])
    )

    return {
        "empresa_id": _clean_text(_get(empresa, "id")),
        "nome_empresa": _clean_text(_get(empresa, "nome_empresa")),
        "nome_agente": _clean_text(_get(empresa, "nome_agente")) or "Assistente Virtual",
        "modelo_ia": _clean_text(_get(empresa, "modelo_ia")) or "gpt-4o-mini",
        "modelo_ia_request_profile": _clean_text(_get(empresa, "modelo_ia_request_profile")) or "legacy",
        "modelo_ia_reasoning_effort": _clean_text(_get(empresa, "modelo_ia_reasoning_effort")) or "default",
        "mensagem_saudacao": _raw_text(_get(empresa, "mensagem_saudacao")),
        "ia_identidade": _clean_text(_get(empresa, "ia_personalidade")) or _clean_text(_get(empresa, "ia_identidade")),
        "ia_regras_negocio": _clean_text(_get(empresa, "ia_regras_negocio")),
        "atendente_prompt": _clean_text(_get(empresa, "atendente_prompt")),
        "conversion_monitor_prompt": _raw_text(_get(empresa, "conversion_monitor_prompt")),
        "monitor_funnel_spec": (dict(credenciais).get("monitor_funnel_spec") if isinstance(dict(credenciais).get("monitor_funnel_spec"), dict) else None),
        "credenciais_canais": _scrub_value(dict(credenciais)),
        "anamnese_available": anamnese_available,
        "has_ai": tenant_ai_enabled(empresa),
    }
