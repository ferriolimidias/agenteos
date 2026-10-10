"""Minimal, tenant-scoped persistence helpers for Meta Click-to-WhatsApp attribution."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm.attributes import flag_modified

META_ATTRIBUTION_KEY = "meta_attribution"
_REFERRAL_FIELDS = ("ctwa_clid", "source_id", "source_url", "source_type", "headline", "body", "media_type", "thumbnail_url")


def extract_meta_referral(referral: Any) -> dict[str, str]:
    """Keep only documented referral fields actually supplied by Meta."""
    if not isinstance(referral, Mapping):
        return {}
    return {field: value for field in _REFERRAL_FIELDS if isinstance((value := referral.get(field)), str) and value != ""}


def build_meta_attribution_touch(referral: Mapping[str, str] | None, *, captured_at: str | None = None, provider: str = "meta_whatsapp", channel: str = "whatsapp") -> dict[str, str]:
    touch = dict(referral or {})
    if not touch:
        return {}
    touch.update({"captured_at": captured_at or datetime.now(timezone.utc).isoformat(), "provider": provider, "channel": channel})
    return touch


def apply_meta_attribution(lead: Any, referral: Mapping[str, str] | None, *, captured_at: str | None = None, provider: str = "meta_whatsapp", channel: str = "whatsapp") -> bool:
    """Apply first/last touch without replacing the lead's first attribution."""
    touch = build_meta_attribution_touch(referral, captured_at=captured_at, provider=provider, channel=channel)
    if not touch:
        return False
    dados = dict(getattr(lead, "dados_adicionais", None) or {})
    attribution = dict(dados.get(META_ATTRIBUTION_KEY) or {})
    first_touch = attribution.get("first_touch")
    last_touch = attribution.get("last_touch")
    attribution["first_touch"] = dict(first_touch) if isinstance(first_touch, Mapping) else deepcopy(touch)
    incoming_ctwa = touch.get("ctwa_clid")
    last_ctwa = last_touch.get("ctwa_clid") if isinstance(last_touch, Mapping) else None
    if isinstance(last_touch, Mapping) and incoming_ctwa and incoming_ctwa == last_ctwa:
        updated_last = dict(last_touch)
        if updated_last.get("last_seen_at") != touch["captured_at"]:
            updated_last["last_seen_at"] = touch["captured_at"]
        attribution["last_touch"] = updated_last
    else:
        attribution["last_touch"] = deepcopy(touch)
    dados[META_ATTRIBUTION_KEY] = attribution
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    return True


def dados_adicionais_for_public_response(value: Any) -> dict[str, Any]:
    """Do not expose raw CTWA identifiers through CRM/frontend responses."""
    dados = dict(value or {}) if isinstance(value, Mapping) else {}
    dados.pop(META_ATTRIBUTION_KEY, None)
    return dados
