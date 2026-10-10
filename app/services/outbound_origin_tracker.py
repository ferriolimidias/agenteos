"""
Marca mensagens outbound enviadas pelo backend (IA/API) para distinguir do app físico do WhatsApp.

Quando o webhook Evolution chega com `fromMe=True`, só desliga `ia_ativa` se NÃO for eco do backend.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

OUTBOUND_MSG_ID_TTL_SECONDS = 300
OUTBOUND_TEXT_HASH_TTL_SECONDS = 120


def _normalizar_telefone(telefone: str | None) -> str:
    return re.sub(r"\D", "", str(telefone or "").strip())


def _hash_texto_outbound(texto: str | None) -> str:
    limpo = str(texto or "").strip()
    if not limpo:
        return ""
    return hashlib.sha256(limpo.encode("utf-8")).hexdigest()[:24]


def extrair_message_id_resposta_evolution(resposta: dict[str, Any] | None) -> str | None:
    """Extrai `key.id` da resposta JSON da Evolution após sendText/sendMedia."""
    if not isinstance(resposta, dict):
        return None

    candidatos: list[Any] = []

    key = resposta.get("key")
    if isinstance(key, dict):
        candidatos.append(key.get("id"))

    data = resposta.get("data")
    if isinstance(data, dict):
        data_key = data.get("key")
        if isinstance(data_key, dict):
            candidatos.append(data_key.get("id"))
        candidatos.append(data.get("id"))

    message = resposta.get("message")
    if isinstance(message, dict):
        msg_key = message.get("key")
        if isinstance(msg_key, dict):
            candidatos.append(msg_key.get("id"))

    for valor in candidatos:
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    return None


async def marcar_outbound_backend(
    empresa_id: str,
    telefone: str,
    *,
    message_id: str | None = None,
    texto: str | None = None,
) -> None:
    """Registra envio do backend antes do webhook `fromMe` de eco chegar."""
    from app.api.main import redis_client

    if redis_client is None:
        return

    emp = str(empresa_id or "").strip()
    phone = _normalizar_telefone(telefone)
    if not emp or not phone:
        return

    mid = str(message_id or "").strip()
    if mid:
        await redis_client.setex(
            f"outbound:backend:msg:{emp}:{mid}",
            OUTBOUND_MSG_ID_TTL_SECONDS,
            phone,
        )

    digest = _hash_texto_outbound(texto)
    if digest:
        await redis_client.setex(
            f"outbound:backend:text:{emp}:{phone}:{digest}",
            OUTBOUND_TEXT_HASH_TTL_SECONDS,
            "1",
        )


async def outbound_mensagem_veio_do_backend(
    empresa_id: str,
    telefone: str,
    *,
    message_id: str | None = None,
    texto: str | None = None,
) -> bool:
    """
    True se o eco `fromMe` provavelmente veio de envio nosso (IA/API), não do celular.
    """
    from app.api.main import redis_client

    if redis_client is None:
        return False

    emp = str(empresa_id or "").strip()
    phone = _normalizar_telefone(telefone)
    if not emp:
        return False

    mid = str(message_id or "").strip()
    if mid:
        chave_msg = f"outbound:backend:msg:{emp}:{mid}"
        marcado = await redis_client.get(chave_msg)
        if marcado is not None:
            return True

    if phone:
        digest = _hash_texto_outbound(texto)
        if digest:
            chave_texto = f"outbound:backend:text:{emp}:{phone}:{digest}"
            if await redis_client.get(chave_texto):
                return True

    return False
