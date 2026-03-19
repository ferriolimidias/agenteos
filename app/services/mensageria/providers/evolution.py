import re
from typing import Any
import traceback

import httpx
from fastapi import HTTPException

from app.services.mensageria.providers.base import BaseProvider
from app.services.mensageria.schemas import StandardIncomingMessage, StandardOutgoingMessage


def _normalizar_identificador_whatsapp(identificador: str | None) -> str:
    valor = str(identificador or "").strip()
    if "@s.whatsapp.net" in valor:
        valor = valor.replace("@s.whatsapp.net", "")
    return re.sub(r"\D", "", valor)


def _mask_secret(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}***{raw[-4:]}"


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
            cfg.get("evolution_url")
            or cfg.get("api_url")
            or cfg.get("base_url")
            or cfg.get("url")
            or ""
        ).strip().rstrip("/")
        evolution_apikey = str(
            cfg.get("evolution_apikey")
            or cfg.get("api_key")
            or cfg.get("apikey")
            or cfg.get("token")
            or cfg.get("access_token")
            or ""
        ).strip()
        instance_name = str(
            cfg.get("evolution_instance")
            or cfg.get("instanceName")
            or cfg.get("instance_name")
            or cfg.get("nome_instancia")
            or ""
        ).strip()

        if not all([evolution_url, evolution_apikey, instance_name]):
            print(
                "[EvolutionProvider] Configuração incompleta:"
                f" url={'ok' if evolution_url else 'vazio'}"
                f" apikey={'ok' if evolution_apikey else 'vazio'}"
                f" instance={'ok' if instance_name else 'vazio'}"
            )
            raise HTTPException(
                status_code=400,
                detail="Configuração Evolution incompleta (url/apikey/instance).",
            )
        return evolution_url, evolution_apikey, instance_name

    async def _post(self, endpoint: str, payload: dict[str, Any], apikey: str) -> dict[str, Any]:
        headers_apikey = {"apikey": apikey, "Content-Type": "application/json"}
        headers_bearer = {"Authorization": f"Bearer {apikey}", "Content-Type": "application/json"}
        headers_apikey_log = {"apikey": _mask_secret(apikey), "Content-Type": "application/json"}
        headers_bearer_log = {"Authorization": f"Bearer {_mask_secret(apikey)}", "Content-Type": "application/json"}
        print(f"[EvolutionProvider] URL -> {endpoint}")
        print(f"[EvolutionProvider] Headers apikey -> {headers_apikey_log}")
        print(f"[EvolutionProvider] Headers bearer -> {headers_bearer_log}")
        print(f"[EvolutionProvider] Payload -> {payload}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(endpoint, headers=headers_apikey, json=payload)
                print(f"[EvolutionProvider] Status Code -> {response.status_code}")
                print(f"[EvolutionProvider] Response -> {response.text}")

                # Fallback para versões da Evolution que usam Authorization Bearer
                if response.status_code in (400, 401):
                    print("[EvolutionProvider] Tentando fallback com Authorization Bearer...")
                    response = await client.post(endpoint, headers=headers_bearer, json=payload)
                    print(f"[EvolutionProvider] Status Code (Bearer) -> {response.status_code}")
                    print(f"[EvolutionProvider] Response (Bearer) -> {response.text}")
        except Exception as exc:
            print(f"[EvolutionProvider] Falha de rede ao enviar para Evolution: {exc}")
            traceback.print_exc()
            raise HTTPException(status_code=502, detail=f"Falha de rede com Evolution: {exc}") from exc

        if response.status_code not in (200, 201):
            print(
                f"[EvolutionProvider] Evolution recusou o envio."
                f" status={response.status_code} body={response.text}"
            )
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
        print(
            f"[EvolutionProvider] send_text raw_identificador='{payload.identificador_contato}' "
            f"normalizado='{number}' instance='{instance_name}'"
        )
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
