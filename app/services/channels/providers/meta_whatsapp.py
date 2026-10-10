from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from app.services.channels.contracts import (
    ChannelConnectionContext,
    ChannelOutboundMessage,
    ProviderCapabilities,
)
from app.services.channels.resolver import ChannelResolutionError, assert_message_matches_context
from app.services.channels.types import Channel, Provider
from app.services.meta.credential_crypto import decrypt_meta_secret
from app.services.meta_templates import parse_template_components, validate_template_parameters


GRAPH_API_VERSION = "v25.0"


class MetaWhatsAppTransport(Protocol):
    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any:
        ...

    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
        ...

    async def delete(self, url: str, *, headers: dict[str, str]) -> Any:
        ...

    async def post_multipart(self, url: str, *, headers: dict[str, str], data: dict[str, Any], files: dict[str, Any]) -> Any:
        ...

    async def post_bytes(self, url: str, *, headers: dict[str, str], content: bytes) -> Any:
        ...


class MetaWhatsAppError(ValueError):
    """Erro sanitizado do provider; nunca carrega token ou corpo completo."""

    def __init__(self, message: str, *, http_status: int | None = None, meta_error: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.meta_error = meta_error or {}


def _extract_meta_error(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return {}
    error = body["error"]
    meta_error = {
        "message": str(error.get("message") or "").strip() or None,
        "type": str(error.get("type") or "").strip() or None,
        "code": error.get("code"),
        "error_subcode": error.get("error_subcode"),
        "error_user_title": str(error.get("error_user_title") or "").strip() or None,
        "error_user_msg": str(error.get("error_user_msg") or "").strip() or None,
        "error_data": error.get("error_data") if isinstance(error.get("error_data"), dict) else None,
        "fbtrace_id": str(error.get("fbtrace_id") or "").strip() or None,
    }
    return {key: value for key, value in meta_error.items() if value not in (None, "", {})}


def _meta_error_message(prefix: str, *, status: int | None, body: Any) -> tuple[str, dict[str, Any]]:
    meta_error = _extract_meta_error(body)
    detail = str(meta_error.get("message") or meta_error.get("error_user_msg") or "").strip()
    code = str(meta_error.get("code") or "").strip()
    if detail:
        return f"{prefix} (status={status or 'desconhecido'}, code={code or 'n/a'}): {detail[:240]}", meta_error
    return f"{prefix} (status={status or 'desconhecido'}).", meta_error


@dataclass(frozen=True)
class MetaWhatsAppConfig:
    graph_api_version: str = GRAPH_API_VERSION
    max_attempts: int = 2
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.graph_api_version.startswith("v"):
            raise ValueError("graph_api_version invalida.")
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("max_attempts deve estar entre 1 e 3.")


class MetaWhatsAppProvider:
    """Adapter Cloud API sem cliente HTTP implícito; transporte é sempre injetado."""

    def __init__(
        self,
        context: ChannelConnectionContext,
        connection: Any | None = None,
        *,
        transport: MetaWhatsAppTransport | None = None,
        config: MetaWhatsAppConfig | None = None,
    ) -> None:
        self.context = context
        self.connection = connection
        self.transport = transport
        self.config = config or MetaWhatsAppConfig()

    @property
    def provider_name(self) -> Provider:
        return Provider.META_WHATSAPP

    @property
    def channel_name(self) -> Channel:
        return Channel.WHATSAPP

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_media=True,
            supports_templates=True,
            supports_mark_read=True,
            supports_freeform=True,
            supports_status_webhook=True,
            supports_multiple_connections=True,
        )

    def _phone_number_id(self) -> str:
        value = getattr(self.connection, "phone_number_id", None) or self.context.external_account_id
        if not str(value or "").strip():
            raise MetaWhatsAppError("Conexao WhatsApp oficial sem phone_number_id.")
        return str(value).strip()

    def _access_token(self) -> str:
        credentials = dict(getattr(self.connection, "credenciais", None) or {})
        token = credentials.get("access_token") or credentials.get("system_user_token")
        if not token and credentials.get("encrypted_access_token"):
            token = decrypt_meta_secret(str(credentials.get("encrypted_access_token")))
        if not str(token or "").strip():
            raise MetaWhatsAppError("Credencial WhatsApp oficial indisponivel.")
        return str(token).strip()

    async def send_text(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        assert_message_matches_context(message, self.context)
        if message.message_type != "text" or not str(message.text or "").strip():
            raise MetaWhatsAppError("Apenas mensagem de texto valida e suportada nesta etapa.")
        if self.transport is None:
            raise MetaWhatsAppError("Transporte Meta nao configurado para este ambiente.")

        phone_number_id = self._phone_number_id()
        token = self._access_token()
        url = f"https://graph.facebook.com/{self.config.graph_api_version}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": str(message.destination).strip(),
            "type": "text",
            "text": {"preview_url": False, "body": message.text},
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        last_status: int | None = None
        for attempt in range(self.config.max_attempts):
            try:
                response = await self.transport.post(url, headers=headers, json=payload)
            except Exception as exc:
                if attempt + 1 == self.config.max_attempts:
                    raise MetaWhatsAppError("Falha de transporte da Meta WhatsApp.") from exc
                continue
            last_status = int(getattr(response, "status_code", 0) or 0)
            if 200 <= last_status < 300:
                body = getattr(response, "json", lambda: {})()
                messages = body.get("messages") if isinstance(body, dict) else None
                external_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
                if not str(external_id or "").strip():
                    raise MetaWhatsAppError("Meta WhatsApp aceitou HTTP mas nao retornou wamid.", http_status=last_status)
                return {
                    "provider": self.provider_name.value,
                    "channel": self.channel_name.value,
                    "connection_id": self.context.connection_id,
                    "external_message_id": external_id,
                    "status": "accepted",
                    "attempts": attempt + 1,
                }
            if last_status not in {408, 429} and last_status < 500:
                break
        raise MetaWhatsAppError(f"Meta WhatsApp rejeitou o envio (status={last_status or 'desconhecido'}).")

    async def send_audio(self, message: ChannelOutboundMessage) -> dict[str, Any]:
        assert_message_matches_context(message, self.context)
        if message.message_type != "audio" or not isinstance(message.media, dict):
            raise MetaWhatsAppError("Audio outbound invalido.")
        if self.transport is None or not hasattr(self.transport, "post_multipart"):
            raise MetaWhatsAppError("Transporte Meta sem suporte a upload de audio.")
        path = str(message.media.get("path") or "").strip()
        mime_type = str(message.media.get("mime_type") or "audio/ogg").strip()
        if not path:
            raise MetaWhatsAppError("Arquivo de audio ausente.")
        phone_number_id = self._phone_number_id()
        token = self._access_token()
        upload_url = f"https://graph.facebook.com/{self.config.graph_api_version}/{phone_number_id}/media"
        try:
            with open(path, "rb") as audio_file:
                response = await self.transport.post_multipart(
                    upload_url,
                    headers={"Authorization": f"Bearer {token}"},
                    data={"messaging_product": "whatsapp", "type": mime_type},
                    files={"file": (str(message.media.get("filename") or "audio"), audio_file, mime_type)},
                )
        except OSError as exc:
            raise MetaWhatsAppError("Arquivo de audio indisponivel.") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        body = getattr(response, "json", lambda: {})()
        media_id = body.get("id") if isinstance(body, dict) else None
        if status < 200 or status >= 300 or not str(media_id or "").strip():
            raise MetaWhatsAppError(f"Meta WhatsApp rejeitou upload de audio (status={status or 'desconhecido'}).")
        send_url = f"https://graph.facebook.com/{self.config.graph_api_version}/{phone_number_id}/messages"
        audio_payload = {"id": str(media_id)}
        if bool(message.metadata.get("send_as_voice")):
            audio_payload["voice"] = True
        if str(message.text or "").strip():
            audio_payload["caption"] = str(message.text).strip()
        response = await self.transport.post(
            send_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": str(message.destination).strip(),
                "type": "audio",
                "audio": audio_payload,
            },
        )
        status = int(getattr(response, "status_code", 0) or 0)
        body = getattr(response, "json", lambda: {})()
        messages = body.get("messages") if isinstance(body, dict) else None
        external_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
        if status < 200 or status >= 300:
            raise MetaWhatsAppError(f"Meta WhatsApp rejeitou audio (status={status or 'desconhecido'}).")
        return {
            "provider": self.provider_name.value,
            "channel": self.channel_name.value,
            "connection_id": self.context.connection_id,
            "external_message_id": external_id,
            "media_id": str(media_id),
            "status": "accepted",
        }

    async def send_template(
        self,
        message: ChannelOutboundMessage,
        *,
        template_payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert_message_matches_context(message, self.context)
        if self.transport is None:
            raise MetaWhatsAppError("Transporte Meta nao configurado para este ambiente.")
        if not isinstance(template_payload, dict) or not str(template_payload.get("name") or "").strip():
            raise MetaWhatsAppError("Template Meta invalido.")

        phone_number_id = self._phone_number_id()
        token = self._access_token()
        url = f"https://graph.facebook.com/{self.config.graph_api_version}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": str(message.destination).strip(),
            "type": "template",
            "template": template_payload,
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        last_status: int | None = None
        for attempt in range(self.config.max_attempts):
            try:
                response = await self.transport.post(url, headers=headers, json=payload)
            except Exception as exc:
                if attempt + 1 == self.config.max_attempts:
                    raise MetaWhatsAppError("Falha de transporte da Meta WhatsApp.") from exc
                continue
            last_status = int(getattr(response, "status_code", 0) or 0)
            if 200 <= last_status < 300:
                body = getattr(response, "json", lambda: {})()
                messages = body.get("messages") if isinstance(body, dict) else None
                external_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
                if not str(external_id or "").strip():
                    raise MetaWhatsAppError("Meta WhatsApp aceitou HTTP mas nao retornou wamid.", http_status=last_status)
                return {
                    "provider": self.provider_name.value,
                    "channel": self.channel_name.value,
                    "connection_id": self.context.connection_id,
                    "external_message_id": external_id,
                    "status": "accepted",
                    "attempts": attempt + 1,
                }
            if last_status not in {408, 429} and last_status < 500:
                break
        message, meta_error = _meta_error_message("Meta WhatsApp rejeitou o template", status=last_status, body=body if "body" in locals() else {})
        raise MetaWhatsAppError(message, http_status=last_status or None, meta_error=meta_error)

    def _waba_id(self) -> str:
        value = getattr(self.connection, "waba_id", None) or self.context.metadata.get("waba_id")
        if not str(value or "").strip():
            raise MetaWhatsAppError("Conexao WhatsApp oficial sem WABA.")
        return str(value).strip()

    async def list_templates_page(self, *, waba_id: str, after: str | None = None) -> dict[str, Any]:
        if self.transport is None:
            raise MetaWhatsAppError("Transporte Meta nao configurado para listar templates.")
        if str(waba_id or "").strip() != self._waba_id():
            raise MetaWhatsAppError("WABA nao pertence a conexao informada.")
        token = self._access_token()
        params: dict[str, Any] = {
            "fields": "id,name,language,category,status,quality_score,components,parameter_format",
            "limit": 100,
        }
        if after:
            params["after"] = after
        url = f"https://graph.facebook.com/{self.config.graph_api_version}/{waba_id}/message_templates"
        response = await self.transport.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        status = int(getattr(response, "status_code", 0) or 0)
        body = getattr(response, "json", lambda: {})()
        if 200 <= status < 300 and isinstance(body, dict):
            return body
        raise MetaWhatsAppError(f"Meta WhatsApp nao retornou templates (status={status or 'desconhecido'}).")

    async def list_templates(self, *, waba_id: str, after: str | None = None) -> dict[str, Any]:
        return await self.list_templates_page(waba_id=waba_id, after=after)

    async def create_template(self, *, waba_id: str, template_payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is None:
            raise MetaWhatsAppError("Transporte Meta nao configurado para criar templates.")
        if str(waba_id or "").strip() != self._waba_id():
            raise MetaWhatsAppError("WABA nao pertence a conexao informada.")
        if not isinstance(template_payload, dict) or not str(template_payload.get("name") or "").strip():
            raise MetaWhatsAppError("Payload de template Meta invalido.")

        token = self._access_token()
        url = f"https://graph.facebook.com/{self.config.graph_api_version}/{waba_id}/message_templates"
        response = await self.transport.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=template_payload,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            body = getattr(response, "json", lambda: {})()
        except Exception:
            body = {}
        if 200 <= status < 300 and isinstance(body, dict):
            return {"http_status": status, "body": body}
        error_message, meta_error = _meta_error_message("Meta WhatsApp rejeitou criacao de template", status=status, body=body)
        raise MetaWhatsAppError(error_message, http_status=status or None, meta_error=meta_error)

    async def create_template_header_handle(
        self,
        *,
        app_id: str,
        file_name: str,
        mime_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        if self.transport is None or not hasattr(self.transport, "post_bytes"):
            raise MetaWhatsAppError("Transporte Meta sem suporte a upload resumable.")
        app = str(app_id or "").strip()
        if not app:
            raise MetaWhatsAppError("Meta App ID indisponivel para upload de template.")
        if not content:
            raise MetaWhatsAppError("Arquivo de imagem ausente.")
        token = self._access_token()
        session_url = (
            f"https://graph.facebook.com/{self.config.graph_api_version}/{app}/uploads"
            f"?file_name={quote(str(file_name or 'template-header.jpg'))}"
            f"&file_length={len(content)}"
            f"&file_type={quote(str(mime_type or 'image/jpeg'))}"
        )
        session_response = await self.transport.post(
            session_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={},
        )
        session_status = int(getattr(session_response, "status_code", 0) or 0)
        session_body = getattr(session_response, "json", lambda: {})()
        session_id = session_body.get("id") if isinstance(session_body, dict) else None
        if session_status < 200 or session_status >= 300 or not str(session_id or "").strip():
            raise MetaWhatsAppError(f"Meta WhatsApp nao criou sessao de upload (status={session_status or 'desconhecido'}).")
        upload_url = f"https://graph.facebook.com/{self.config.graph_api_version}/{session_id}"
        upload_response = await self.transport.post_bytes(
            upload_url,
            headers={"Authorization": f"OAuth {token}", "file_offset": "0"},
            content=content,
        )
        upload_status = int(getattr(upload_response, "status_code", 0) or 0)
        upload_body = getattr(upload_response, "json", lambda: {})()
        handle = upload_body.get("h") if isinstance(upload_body, dict) else None
        if upload_status < 200 or upload_status >= 300 or not str(handle or "").strip():
            raise MetaWhatsAppError(f"Meta WhatsApp nao retornou header_handle (status={upload_status or 'desconhecido'}).")
        return {
            "http_status": upload_status,
            "header_handle": str(handle),
            "upload_session_id": str(session_id),
            "mime_type": str(mime_type),
            "byte_size": len(content),
        }

    async def upload_media_bytes(
        self,
        *,
        mime_type: str,
        content: bytes,
        file_name: str = "campaign-image",
    ) -> dict[str, Any]:
        if self.transport is None or not hasattr(self.transport, "post_multipart"):
            raise MetaWhatsAppError("Transporte Meta sem suporte a upload de midia.")
        if not content:
            raise MetaWhatsAppError("Arquivo de midia ausente.")
        phone_number_id = self._phone_number_id()
        token = self._access_token()
        upload_url = f"https://graph.facebook.com/{self.config.graph_api_version}/{phone_number_id}/media"
        response = await self.transport.post_multipart(
            upload_url,
            headers={"Authorization": f"Bearer {token}"},
            data={"messaging_product": "whatsapp", "type": str(mime_type or "image/jpeg")},
            files={"file": (str(file_name or "campaign-image"), content, str(mime_type or "image/jpeg"))},
        )
        status = int(getattr(response, "status_code", 0) or 0)
        body = getattr(response, "json", lambda: {})()
        media_id = body.get("id") if isinstance(body, dict) else None
        if status < 200 or status >= 300 or not str(media_id or "").strip():
            raise MetaWhatsAppError(f"Meta WhatsApp rejeitou upload de midia (status={status or 'desconhecido'}).")
        return {
            "http_status": status,
            "media_id": str(media_id),
            "phone_number_id": phone_number_id,
            "mime_type": str(mime_type),
            "byte_size": len(content),
        }

    async def delete_media(self, *, media_id: str) -> dict[str, Any]:
        if self.transport is None or not hasattr(self.transport, "delete"):
            raise MetaWhatsAppError("Transporte Meta sem suporte a limpeza de midia.")
        media = str(media_id or "").strip()
        if not media:
            raise MetaWhatsAppError("media_id ausente para limpeza.")
        token = self._access_token()
        url = f"https://graph.facebook.com/{self.config.graph_api_version}/{media}"
        response = await self.transport.delete(url, headers={"Authorization": f"Bearer {token}"})
        status = int(getattr(response, "status_code", 0) or 0)
        body = getattr(response, "json", lambda: {})()
        if status < 200 or status >= 300:
            raise MetaWhatsAppError(f"Meta WhatsApp rejeitou limpeza de midia (status={status or 'desconhecido'}).")
        return {"http_status": status, "body": body}

    async def validate_template_for_use(self, *, template: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        components = template.get("components") if isinstance(template.get("components"), list) else []
        parsed = parse_template_components(components)
        validation = validate_template_parameters(parsed, parameters)
        return {
            "valid": validation["valid"] and str(template.get("status") or "").upper() == "APPROVED",
            "validation": validation,
        }

    async def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name.value,
            "channel": self.channel_name.value,
            "connection_id": self.context.connection_id,
            "status": self.context.status,
            "transport_configured": self.transport is not None,
        }
