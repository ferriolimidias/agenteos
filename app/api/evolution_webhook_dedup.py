"""
Idempotência de webhooks Evolution API (retries / entregas duplicadas).

A trava deve ser adquirida antes de qualquer I/O pesado (DB, LangGraph, mídia).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

WEBHOOK_MSG_LOCK_TTL_SECONDS = int(os.getenv("EVOLUTION_WEBHOOK_DEDUP_TTL", "86400"))


def _normalizar_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else {}
    if isinstance(data, dict):
        return data
    return {}


def extrair_message_id_evolution(payload: dict[str, Any] | None) -> str | None:
    """
    ID estável da mensagem WhatsApp no payload Evolution (messages.upsert).

    Ordem de prioridade alinhada à API atual:
    - data.key.id  (padrão Baileys / Evolution v2)
    - data.id, data.messageId
    - payload.key.id (alguns proxies)
    """
    if not isinstance(payload, dict):
        return None

    data = _normalizar_data_payload(payload)
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    payload_key = payload.get("key") if isinstance(payload.get("key"), dict) else {}

    candidatos = (
        key.get("id"),
        data.get("id"),
        data.get("messageId"),
        data.get("message_id"),
        payload_key.get("id"),
        payload.get("id"),
    )
    for valor in candidatos:
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    return None


def fallback_message_id_evolution(payload: dict[str, Any] | None) -> str:
    """
    Hash determinístico quando `key.id` não veio no payload (evita trava inútil).
    Não é tão forte quanto o ID real, mas reduz duplicidade em retries idênticos.
    """
    if not isinstance(payload, dict):
        return "fb:empty"

    data = _normalizar_data_payload(payload)
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    message = data.get("message") if isinstance(data.get("message"), dict) else {}

    estavel: dict[str, Any] = {
        "event": str(payload.get("event") or ""),
        "instance": str(payload.get("instance") or payload.get("instanceName") or ""),
        "remoteJid": str(key.get("remoteJid") or ""),
        "fromMe": bool(key.get("fromMe", False)),
        "messageTimestamp": data.get("messageTimestamp") or data.get("message_timestamp"),
        "msg_keys": sorted(message.keys()) if message else [],
    }
    raw = json.dumps(estavel, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"fb:{digest}"


def chave_trava_webhook_mensagem(empresa_id: str, message_id: str) -> str:
    emp = str(empresa_id or "").strip()
    mid = str(message_id or "").strip()
    return f"webhook:ev:{emp}:{mid}"


async def adquirir_trava_webhook_mensagem(
    redis_client: Any,
    empresa_id: str,
    message_id: str,
    *,
    ttl_seconds: int | None = None,
) -> bool:
    """SET NX — True se esta entrega deve ser processada."""
    if redis_client is None:
        return True
    ttl = ttl_seconds if ttl_seconds is not None else WEBHOOK_MSG_LOCK_TTL_SECONDS
    chave = chave_trava_webhook_mensagem(empresa_id, message_id)
    return bool(await redis_client.set(chave, "1", nx=True, ex=max(60, int(ttl))))
