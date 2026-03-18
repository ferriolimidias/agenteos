import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.services.mensageria.providers.base import BaseProvider
from app.services.mensageria.schemas import StandardIncomingMessage, StandardOutgoingMessage


def _normalizar_identificador_whatsapp(identificador: str | None) -> str:
    valor = str(identificador or "").strip()
    if "@s.whatsapp.net" in valor:
        valor = valor.replace("@s.whatsapp.net", "")
    return re.sub(r"\D", "", valor)


class EvolutionProvider(BaseProvider):
    def parse_webhook(self, payload: dict) -> StandardIncomingMessage:
        data = payload.get("data", {}) or {}
        key = data.get("key", {}) or {}
        message = data.get("message", {}) or {}

        remote_jid = key.get("remoteJid", "")
        identificador_contato = _normalizar_identificador_whatsapp(remote_jid)
        nome_contato = data.get("pushName") or payload.get("pushName")

        texto = (
            message.get("conversation")
            or (message.get("extendedTextMessage", {}) or {}).get("text")
            or (message.get("imageMessage", {}) or {}).get("caption")
            or (message.get("videoMessage", {}) or {}).get("caption")
            or ""
        )

        tipo = "text"
        media_url = None
        if message.get("audioMessage"):
            tipo = "audio"
            texto = texto or "[Áudio]"
        elif message.get("imageMessage"):
            tipo = "image"
            texto = texto or "[Imagem]"
        elif message.get("documentMessage") or message.get("videoMessage"):
            tipo = "document"
            texto = texto or "[Arquivo]"

        return StandardIncomingMessage(
            identificador_contato=identificador_contato,
            canal="whatsapp",
            nome_contato=nome_contato,
            texto=str(texto or ""),
            tipo=tipo,
            media_url=media_url,
            raw_payload=payload or {},
        )

    def _obter_config(self, credenciais: dict[str, Any]) -> tuple[str, str, str]:
        cfg = credenciais or {}
        evolution_url = str(
            cfg.get("evolution_url") or cfg.get("url") or ""
        ).strip().rstrip("/")
        evolution_apikey = str(
            cfg.get("evolution_apikey") or cfg.get("apikey") or ""
        ).strip()
        instance_name = str(
            cfg.get("evolution_instance")
            or cfg.get("instanceName")
            or cfg.get("nome_instancia")
            or ""
        ).strip()

        if not all([evolution_url, evolution_apikey, instance_name]):
            raise HTTPException(
                status_code=400,
                detail="Configuração Evolution incompleta (url/apikey/instance).",
            )
        return evolution_url, evolution_apikey, instance_name

    async def _post(self, endpoint: str, payload: dict[str, Any], apikey: str) -> dict[str, Any]:
        headers = {"apikey": apikey, "Content-Type": "application/json"}
        print(f"DEBUG ENVIO: URL -> {endpoint}")
        print(f"DEBUG ENVIO: Payload -> {payload}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Falha de rede com Evolution: {exc}") from exc

        print(f"DEBUG ENVIO: Status -> {response.status_code}")
        print(f"DEBUG ENVIO: Response -> {response.text}")

        if response.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Evolution recusou envio ({response.status_code}): {response.text[:300]}",
            )

        try:
            return response.json() if response.content else {"ok": True}
        except Exception:
            return {"ok": True, "raw_response": response.text}

    async def send_text(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        evolution_url, evolution_apikey, instance_name = self._obter_config(credenciais)
        endpoint = f"{evolution_url}/message/sendText/{instance_name}"
        number = _normalizar_identificador_whatsapp(payload.identificador_contato)
        body = {"number": number, "text": str(payload.texto or "")}
        return await self._post(endpoint, body, evolution_apikey)

    async def send_media(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        evolution_url, evolution_apikey, instance_name = self._obter_config(credenciais)
        endpoint = f"{evolution_url}/message/sendMedia/{instance_name}"
        number = _normalizar_identificador_whatsapp(payload.identificador_contato)
        body = {
            "number": number,
            "mediatype": payload.tipo if payload.tipo in {"image", "document"} else "document",
            "media": str(payload.media_url or ""),
            "caption": str(payload.texto or ""),
        }
        return await self._post(endpoint, body, evolution_apikey)

    async def send_audio(
        self,
        payload: StandardOutgoingMessage,
        credenciais: dict[str, Any],
    ) -> dict[str, Any]:
        evolution_url, evolution_apikey, instance_name = self._obter_config(credenciais)
        endpoint = f"{evolution_url}/message/sendWhatsAppAudio/{instance_name}"
        number = _normalizar_identificador_whatsapp(payload.identificador_contato)
        body = {
            "number": number,
            "audio": str(payload.media_url or ""),
        }
        return await self._post(endpoint, body, evolution_apikey)
