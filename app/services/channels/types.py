from __future__ import annotations

from enum import Enum


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"


class Provider(str, Enum):
    EVOLUTION_LEGACY = "evolution_legacy"
    META_WHATSAPP = "meta_whatsapp"
    META_INSTAGRAM = "meta_instagram"


def normalize_channel(value: str | Channel) -> Channel:
    if isinstance(value, Channel):
        return value
    normalized = str(value or "").strip().lower()
    if normalized == "evolution":
        return Channel.WHATSAPP
    try:
        return Channel(normalized)
    except ValueError as exc:
        raise ValueError(f"Channel invalido: {value!r}") from exc


def normalize_provider(value: str | Provider) -> Provider:
    if isinstance(value, Provider):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"evolution", "whatsapp_evolution"}:
        return Provider.EVOLUTION_LEGACY
    if normalized in {"meta", "meta_official", "whatsapp_meta_official"}:
        return Provider.META_WHATSAPP
    try:
        return Provider(normalized)
    except ValueError as exc:
        raise ValueError(f"Provider invalido: {value!r}") from exc
