"""Tenant-scoped Instagram API with manually configured Instagram ID and token.

Tokens and Instagram IDs are provided manually by each tenant
connection. This module does not implement OAuth or an authorized-profile
catalog.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.credential_crypto import decrypt_meta_secret, encrypt_meta_secret

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.instagram.com/{GRAPH_VERSION}"
REQUIRED_PERMISSIONS = frozenset({
    "instagram_business_basic",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
})
DEFAULT_PUBLIC_REPLY = "Obrigada pelo interesse! Acabei de te enviar as informações no direct."
INSTAGRAM_USER_IDENTIFIER_LABEL = "Instagram ID"


class InstagramGraphError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        meta_message: str | None = None,
        error_type: str | None = None,
        code: int | str | None = None,
        error_subcode: int | str | None = None,
        error_user_title: str | None = None,
        error_user_msg: str | None = None,
        fbtrace_id: str | None = None,
        request_method: str | None = None,
        request_url: str | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.meta_message = meta_message
        self.error_type = error_type
        self.code = code
        self.error_subcode = error_subcode
        self.error_user_title = error_user_title
        self.error_user_msg = error_user_msg
        self.fbtrace_id = fbtrace_id
        self.request_method = request_method
        self.request_url = request_url
        self.request_body = request_body
        super().__init__(message)

    def sanitized_diagnostic(
        self,
        *,
        operation: str,
        object_type: str,
        object_id: str,
        token_diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "request": {
                "method": self.request_method,
                "url": self.request_url,
                "object_type": object_type,
                "object_id": object_id,
                "body": self.request_body,
            },
            "token": token_diagnostic,
            "response": {
                "http_status": self.status_code,
                "message": self.meta_message,
                "type": self.error_type,
                "code": self.code,
                "error_subcode": self.error_subcode,
                "error_user_title": self.error_user_title,
                "error_user_msg": self.error_user_msg,
                "fbtrace_id": self.fbtrace_id,
            },
        }


def _sanitized_request_body(*, data: Any = None, json_body: Any = None) -> dict[str, Any] | None:
    if isinstance(json_body, dict):
        recipient = json_body.get("recipient") if isinstance(json_body.get("recipient"), dict) else {}
        message = json_body.get("message") if isinstance(json_body.get("message"), dict) else {}
        text = str(message.get("text") or "")
        sanitized_recipient: dict[str, Any] = {}
        if _clean(recipient.get("comment_id")):
            sanitized_recipient["comment_id"] = _clean(recipient.get("comment_id"))
        if _clean(recipient.get("id")):
            sanitized_recipient["id"] = _clean(recipient.get("id"))
        sanitized_message: dict[str, Any]
        attachment = message.get("attachment") if isinstance(message.get("attachment"), dict) else None
        if attachment:
            payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else {}
            buttons = payload.get("buttons") if isinstance(payload.get("buttons"), list) else []
            sanitized_message = {
                "attachment": {
                    "type": _clean(attachment.get("type")),
                    "payload": {
                        "template_type": _clean(payload.get("template_type")),
                        "text": f"<redacted:{len(str(payload.get('text') or ''))} chars>",
                        "buttons": [
                            {
                                "type": _clean(button.get("type")) if isinstance(button, dict) else "",
                                "title": f"<redacted:{len(str(button.get('title') or ''))} chars>" if isinstance(button, dict) else "",
                            }
                            for button in buttons[:3]
                            if isinstance(button, dict)
                        ],
                    },
                },
            }
        else:
            sanitized_message = {"text": f"<redacted:{len(text)} chars>"}
        sanitized: dict[str, Any] = {
            "recipient": sanitized_recipient,
            "message": sanitized_message,
        }
        if _clean(json_body.get("messaging_type")):
            sanitized["messaging_type"] = _clean(json_body.get("messaging_type"))
        return sanitized
    if isinstance(data, dict):
        text = str(data.get("message") or "")
        return {"message": f"<redacted:{len(text)} chars>"}
    return None


class GraphTransport(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...
    async def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass
class InstagramGraphClient:
    access_token: str
    transport: GraphTransport | None = None
    timeout_seconds: float = 15.0

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        client = self.transport or httpx.AsyncClient(timeout=self.timeout_seconds)
        close = self.transport is None
        try:
            url = f"{GRAPH_BASE_URL}/{str(path).strip().lstrip('/')}"
            headers = kwargs.pop("headers", {"Authorization": f"Bearer {self.access_token}"})
            response = await (client.get(url, headers=headers, params=kwargs.get("params")) if method == "GET" else client.post(url, headers=headers, data=kwargs.get("data"), json=kwargs.get("json")))
            body = response.json() if hasattr(response, "json") else {}
            body = body if isinstance(body, dict) else {}
            status = int(getattr(response, "status_code", 0) or 0)
            logger.info("Instagram Graph validation response method=%s path=%s status=%s", method, str(path).strip().lstrip("/"), status)
            if 200 <= status < 300:
                return body
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            logger.warning("Instagram Graph validation rejected status=%s code=%s subcode=%s", status, error.get("code"), error.get("error_subcode"))
            raise InstagramGraphError(
                "A Meta recusou a operação solicitada.",
                status_code=status,
                meta_message=_clean(error.get("message")) or None,
                error_type=_clean(error.get("type")) or None,
                code=error.get("code"),
                error_subcode=error.get("error_subcode"),
                error_user_title=_clean(error.get("error_user_title")) or None,
                error_user_msg=_clean(error.get("error_user_msg")) or None,
                fbtrace_id=_clean(error.get("fbtrace_id")) or None,
                request_method=method,
                request_url=url,
                request_body=_sanitized_request_body(data=kwargs.get("data"), json_body=kwargs.get("json")),
            )
        except InstagramGraphError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise InstagramGraphError("Falha temporária ao consultar a Meta.") from exc
        finally:
            if close:
                await client.aclose()

    async def identify(self, professional_account_id: str) -> dict[str, Any]:
        account_id = _clean(professional_account_id)
        if not account_id:
            raise ValueError(f"{INSTAGRAM_USER_IDENTIFIER_LABEL} é obrigatório.")
        account = await self._request(
            "GET",
            "me",
            params={"access_token": self.access_token, "fields": "id,username,account_type,media_count"},
            headers={},
        )
        resolved_id = _clean(account.get("id"))
        if not resolved_id:
            raise InstagramGraphError("A Meta não retornou o identificador da conta Instagram acessível pelo token.")
        return {
            "instagram_professional_account_id": account_id,
            "instagram_business_account_id": account_id,
            "resolved_instagram_graph_user_id": resolved_id,
            "instagram_username": _clean(account.get("username")),
            "permissions": [],
            "permissions_verified": False,
            "account_type": _clean(account.get("account_type")),
            "media_count": account.get("media_count"),
            "token_type": "INSTAGRAM_USER",
            "token_is_valid": True,
            "token_expires_at": _unix_iso(account.get("expires_at")),
            "data_access_expires_at": None,
        }

    async def media(self, account_id: str, *, after: str | None, limit: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp",
            "limit": max(1, min(limit, 100)),
        }
        if after:
            params["after"] = after
        return await self._request("GET", f"{account_id}/media", params=params)

    async def comment(self, comment_id: str) -> dict[str, Any]:
        return await self._request("GET", comment_id, params={"fields": "id,text,from{id,username},media{id,owner{id}}"})

    async def public_reply(self, comment_id: str, message: str) -> dict[str, Any]:
        return await self._request("POST", f"{comment_id}/replies", data={"message": message})

    async def private_reply(self, instagram_user_id: str, comment_id: str, message: str | dict[str, Any]) -> dict[str, Any]:
        message_payload = message if isinstance(message, dict) else {"text": message}
        return await self._request(
            "POST",
            f"{instagram_user_id}/messages",
            json={"recipient": {"comment_id": comment_id}, "message": message_payload},
        )

    async def direct_reply(self, instagram_user_id: str, recipient_id: str, message: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"{instagram_user_id}/messages",
            json={
                "recipient": {"id": recipient_id},
                "messaging_type": "RESPONSE",
                "message": {"text": message},
            },
        )

    async def token_diagnostic(self) -> dict[str, Any]:
        body = await self._request("GET", "me/permissions")
        return {
            "type": "INSTAGRAM_USER",
            "is_valid": True,
            "permissions": sorted(
                _clean(item.get("permission"))
                for item in (body.get("data") or [])
                if isinstance(item, dict)
                and _clean(item.get("status")).lower() == "granted"
                and _clean(item.get("permission"))
            ),
        }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _unix_iso(value: Any) -> str | None:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.utcfromtimestamp(timestamp).replace(microsecond=0).isoformat() + "Z"


def normalize_text(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", _clean(value))
    return re.sub(r"[\W_]+", " ", "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()).strip()


def comment_matches(comment: str | None, keywords: list[str] | None) -> bool:
    text = normalize_text(comment)
    normalized_keywords = [normalize_text(value) for value in (keywords or []) if normalize_text(value)]
    return not normalized_keywords or any(keyword in text for keyword in normalized_keywords)


def render_private(template: str | None, *, username: str | None, comment: str | None, link: str | None) -> str:
    rendered = _clean(template)
    for key, value in {"username": username, "comment": comment, "link": link}.items():
        rendered = rendered.replace("{{" + key + "}}", _clean(value))
    if link and "{{link}}" not in _clean(template):
        rendered = f"{rendered.rstrip()}\n{_clean(link)}".strip()
    return rendered


def _render_template_value(template: str | None, *, username: str | None, comment: str | None) -> str:
    rendered = _clean(template)
    for key, value in {"username": username, "comment": comment}.items():
        rendered = rendered.replace("{{" + key + "}}", _clean(value))
    return rendered


def _automation_private_reply_config(automation: Any) -> dict[str, Any]:
    config = getattr(automation, "private_reply_config", None)
    if isinstance(config, dict):
        return config
    return {
        "message_type": "text",
        "text": getattr(automation, "private_message", None),
        "buttons": [],
    }


def build_private_reply_message(
    automation: Any,
    *,
    username: str | None,
    comment: str | None,
) -> tuple[str, dict[str, Any], str]:
    config = _automation_private_reply_config(automation)
    message_type = _clean(config.get("message_type")).lower() if isinstance(config, dict) else "text"
    if message_type != "button_template":
        text = render_private(config.get("text") or getattr(automation, "private_message", None), username=username, comment=comment, link=getattr(automation, "private_link", None))
        return text, {"text": text}, "text"

    payload_text = _render_template_value(config.get("text"), username=username, comment=comment)
    buttons: list[dict[str, Any]] = []
    for raw_button in (config.get("buttons") or [])[:3]:
        if not isinstance(raw_button, dict):
            continue
        button_type = _clean(raw_button.get("type"))
        title = _render_template_value(raw_button.get("title"), username=username, comment=comment)
        if button_type == "web_url":
            buttons.append({"type": "web_url", "title": title, "url": _clean(raw_button.get("url"))})
        elif button_type == "postback":
            buttons.append({"type": "postback", "title": title, "payload": _clean(raw_button.get("payload"))})
    message = {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": payload_text,
                "buttons": buttons,
            },
        },
    }
    return payload_text, message, "button_template"


def execution_status_from_results(
    *,
    failures: list[dict[str, Any]],
    public_reply_status: str,
    private_reply_status: str,
) -> str:
    if not failures:
        return "completed"
    if public_reply_status == "public_reply_sent" or private_reply_status == "private_reply_sent":
        return "partial"
    return "failed"


def webhook_reservation_decision(
    status: str | None,
    updated_at: datetime | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 300,
) -> str:
    """Return retry/duplicate/in_progress without touching persistence."""
    normalized = _clean(status).lower()
    if normalized in {"processed", "ignored"}:
        return "duplicate"
    # A failed delivery is terminal.  Provider redelivery must not silently
    # repeat side effects; an explicit operator workflow is required for any
    # future recovery.
    if normalized == "failed":
        return "duplicate"
    current = now or datetime.utcnow()
    if normalized in {"processing", "received"}:
        if updated_at is None:
            return "retry"
        comparable_now = current
        if updated_at.tzinfo and current.tzinfo is None:
            comparable_now = current.replace(tzinfo=updated_at.tzinfo)
        elif updated_at.tzinfo is None and current.tzinfo:
            comparable_now = current.replace(tzinfo=None)
        if comparable_now - updated_at >= timedelta(seconds=max(1, stale_after_seconds)):
            return "retry"
    return "in_progress"


async def _existing_execution_for_comment(
    db: AsyncSession,
    *,
    connection: Conexao,
    comment_id: str,
) -> bool:
    """Check idempotency before CRM/automation side effects."""
    from db.models import InstagramCommentExecution

    result = await db.execute(select(InstagramCommentExecution.id).where(
        InstagramCommentExecution.empresa_id == connection.empresa_id,
        InstagramCommentExecution.connection_id == connection.id,
        InstagramCommentExecution.comment_id == _clean(comment_id),
    ).limit(1))
    return result.scalar_one_or_none() is not None


async def _registered_public_reply(
    db: AsyncSession,
    *,
    connection: Conexao,
    comment_id: str,
) -> bool:
    """Return whether Meta's comment id was recorded as our public reply."""
    from db.models import InstagramCommentAutomation

    result = await db.execute(select(InstagramCommentAutomation.recent_public_replies).where(
        InstagramCommentAutomation.empresa_id == connection.empresa_id,
        InstagramCommentAutomation.connection_id == connection.id,
    ))
    wanted = _clean(comment_id)
    for (entries,) in result.all():
        for entry in entries or []:
            if isinstance(entry, str) and _clean(entry) == wanted:
                return True
            if isinstance(entry, dict) and _clean(
                entry.get("comment_id") or entry.get("reply_comment_id") or entry.get("id")
            ) == wanted:
                return True
    return False


def _public_reply_id(response: dict[str, Any] | None) -> str | None:
    if not isinstance(response, dict):
        return None
    for key in ("id", "comment_id", "reply_id"):
        value = _clean(response.get(key))
        if value:
            return value
    return None


def classify_comment_origin(*, sender_id: str | None, account_id: str | None, registered_public_reply: bool) -> str | None:
    """Classify only proven self/output comments; never use text/username."""
    if _clean(sender_id) and _clean(sender_id) == _clean(account_id):
        return "ignored_self_generated_comment"
    if registered_public_reply:
        return "ignored_registered_public_reply"
    return None


async def _reserve_webhook_event(
    db: AsyncSession,
    *,
    connection: Conexao,
    comment_id: str,
    media_id: str | None,
    sender_id: str | None,
) -> tuple[InstagramWebhookEvent, str]:
    from db.models import InstagramWebhookEvent

    async def locked_existing() -> InstagramWebhookEvent | None:
        return (await db.execute(
            select(InstagramWebhookEvent).where(
                InstagramWebhookEvent.connection_id == connection.id,
                InstagramWebhookEvent.comment_id == comment_id,
            ).with_for_update()
        )).scalars().first()

    event = await locked_existing()
    if event is None:
        event = InstagramWebhookEvent(
            empresa_id=connection.empresa_id,
            connection_id=connection.id,
            comment_id=comment_id,
            media_id=media_id,
            sender_id=sender_id,
            status="processing",
            error_detail=None,
        )
        db.add(event)
        try:
            await db.commit()
            await db.refresh(event)
            return event, "reserved"
        except IntegrityError:
            # A concurrent delivery won the unique (connection, comment) insert.
            await db.rollback()
            event = await locked_existing()
            if event is None:
                raise

    decision = webhook_reservation_decision(event.status, event.atualizado_em)
    if decision != "retry":
        await db.rollback()  # releases the row lock without mutating the event
        return event, decision
    event.status = "processing"
    event.error_detail = None
    event.media_id = media_id or event.media_id
    event.sender_id = sender_id or event.sender_id
    event.atualizado_em = datetime.utcnow()
    await db.commit()
    await db.refresh(event)
    return event, "reserved"


def serialize_connection(connection: Conexao) -> dict[str, Any]:
    metadata = dict(connection.metadata_json or {})
    permissions = sorted(
        value for value in (metadata.get("permissions") or [])
        if _clean(value) in REQUIRED_PERMISSIONS
    )
    token_configured = bool((connection.credenciais or {}).get("encrypted_access_token"))
    operational = bool(
        str(connection.status or "").lower() == "connected"
        and token_configured
        and metadata.get("token_is_valid") is True
        and not metadata.get("last_error")
    )
    return {
        "id": str(connection.id),
        "connection_id": str(connection.id),
        "name": connection.nome_instancia,
        "status": connection.status,
        "instagram_professional_account_id": connection.instagram_business_account_id,
        "instagram_business_account_id": connection.instagram_business_account_id,
        "instagram_username": metadata.get("instagram_username"),
        "permissions": permissions,
        "token_configured": token_configured,
        "operational": operational,
        "configuration_state": metadata.get("configuration_state") or connection.status,
        "token_is_valid": metadata.get("token_is_valid"),
        "token_expires_at": metadata.get("token_expires_at"),
        "data_access_expires_at": metadata.get("data_access_expires_at"),
        "last_validated_at": metadata.get("last_validated_at"),
        "last_error": metadata.get("last_error"),
    }


async def get_connection(db: AsyncSession, empresa_id: uuid.UUID, connection_id: uuid.UUID) -> Conexao | None:
    from db.models import Conexao, TipoConexao

    return (await db.execute(select(Conexao).where(
        Conexao.id == connection_id,
        Conexao.empresa_id == empresa_id,
        Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.channel == "instagram",
        Conexao.provider == "meta_instagram",
    ))).scalars().first()


def connection_token(connection: Conexao) -> str:
    return decrypt_meta_secret(_clean((connection.credenciais or {}).get("encrypted_access_token")))


def comment_payload_has_matching_fields(*, media_id: str | None, text: str | None) -> bool:
    return bool(_clean(media_id)) and text is not None


async def identify_connection(
    *, professional_account_id: str, access_token: str,
    transport: GraphTransport | None = None,
) -> dict[str, Any]:
    requested_id = _clean(professional_account_id)
    if not requested_id:
        raise ValueError(f"{INSTAGRAM_USER_IDENTIFIER_LABEL} é obrigatório.")
    identified = await InstagramGraphClient(access_token=_clean(access_token), transport=transport).identify(requested_id)
    identified["validation"] = validation_summary(
        supplied_id=requested_id,
        identified=identified,
        result="valid",
    )
    return identified


def validation_summary(*, supplied_id: str, identified: dict[str, Any], result: str) -> dict[str, Any]:
    resolved_id = _clean(identified.get("resolved_instagram_graph_user_id"))
    return {
        "app_token_valid": bool(identified.get("token_is_valid")),
        "account_object_type": INSTAGRAM_USER_IDENTIFIER_LABEL,
        "resolved_account_id": resolved_id,
        "supplied_id": _clean(supplied_id),
        "relationship_verified": None,
        "permissions": list(identified.get("permissions") or []),
        "permissions_verified": bool(identified.get("permissions_verified")),
        "validated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "result": result,
    }


def _validated_connection_metadata(*, professional_id: str, identified: dict[str, Any]) -> dict[str, Any]:
    summary = dict(identified.get("validation") or validation_summary(
        supplied_id=professional_id,
        identified=identified,
        result="valid",
    ))
    return {
        "instagram_professional_account_id": professional_id,
        "instagram_business_account_id": professional_id,
        "instagram_username": _clean(identified.get("instagram_username")) or None,
        "permissions": list(identified.get("permissions") or []),
        "permissions_verified": bool(identified.get("permissions_verified")),
        "account_type": _clean(identified.get("account_type")) or None,
        "media_count": identified.get("media_count"),
        "token_type": "INSTAGRAM_USER",
        "token_is_valid": True,
        "token_expires_at": identified.get("token_expires_at"),
        "data_access_expires_at": identified.get("data_access_expires_at"),
        "configuration_state": "configured",
        "last_validated_at": summary["validated_at"],
        "last_error": None,
        "graph_api_version": GRAPH_VERSION,
        "graph_host": "graph.instagram.com",
        "credential_model": "instagram_user_access_token",
        "validation": summary,
    }


async def persist_connection(
    db: AsyncSession, *, empresa_id: uuid.UUID, professional_account_id: str,
    access_token: str, identified: dict[str, Any], name: str | None = None,
) -> Conexao:
    from db.models import Conexao, TipoConexao

    professional_id = _clean(professional_account_id)
    if not professional_id:
        raise ValueError(f"{INSTAGRAM_USER_IDENTIFIER_LABEL} é obrigatório.")
    owner = (await db.execute(select(Conexao).where(
        Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.instagram_business_account_id == professional_id,
    ))).scalars().first()
    if owner and owner.empresa_id != empresa_id:
        raise ValueError("Esta conta profissional já pertence a outra empresa.")
    existing = owner
    if existing:
        existing.credenciais = {"encrypted_access_token": encrypt_meta_secret(_clean(access_token))}
        existing.status = "connected"
        existing.external_account_id = professional_id
        existing.instagram_business_account_id = professional_id
        existing.credential_source = "instagram_user_token"
        existing.nome_instancia = _clean(name) or existing.nome_instancia or f"Instagram {professional_id}"
        existing.metadata_json = {
            **dict(existing.metadata_json or {}),
            **_validated_connection_metadata(professional_id=professional_id, identified=identified),
        }
        await db.commit()
        await db.refresh(existing)
        return existing
    connection = Conexao(
        empresa_id=empresa_id,
        tipo=TipoConexao.INSTAGRAM,
        channel="instagram",
        provider="meta_instagram",
        nome_instancia=_clean(name) or f"Instagram {professional_id}",
        credenciais={"encrypted_access_token": encrypt_meta_secret(_clean(access_token))},
        status="connected",
        external_account_id=professional_id,
        page_id=None,
        instagram_business_account_id=professional_id,
        credential_source="instagram_user_token",
        metadata_json=_validated_connection_metadata(professional_id=professional_id, identified=identified),
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def reconfigure_connection(
    db: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    connection_id: uuid.UUID,
    professional_account_id: str,
    access_token: str,
    name: str | None = None,
) -> Conexao:
    """Validate new credentials and update the existing row in place."""
    from db.models import Conexao, TipoConexao

    current = await get_connection(db, empresa_id, connection_id)
    if current is None:
        raise ValueError("Conexão Instagram não encontrada.")
    requested_id = _clean(professional_account_id)
    identified = await identify_connection(professional_account_id=requested_id, access_token=access_token)
    owner = (await db.execute(select(Conexao).where(
        Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.instagram_business_account_id == requested_id,
    ))).scalars().first()
    if owner and owner.id != current.id:
        raise ValueError("Esta conta profissional já pertence a outra empresa.")
    current.credenciais = {"encrypted_access_token": encrypt_meta_secret(_clean(access_token))}
    current.status = "connected"
    current.external_account_id = requested_id
    current.instagram_business_account_id = requested_id
    current.page_id = None
    current.credential_source = "instagram_user_token"
    current.nome_instancia = _clean(name) or current.nome_instancia or f"Instagram {requested_id}"
    current.metadata_json = {
        **dict(current.metadata_json or {}),
        **_validated_connection_metadata(professional_id=requested_id, identified=identified),
    }
    await db.commit()
    await db.refresh(current)
    return current


async def revalidate_connection(db: AsyncSession, connection: Conexao) -> Conexao:
    current_metadata = dict(connection.metadata_json or {})
    if current_metadata.get("configuration_state") == "pending_reconfiguration":
        raise ValueError("A conexão está pendente de novo ID e token Instagram informados manualmente.")
    try:
        identified = await identify_connection(
            professional_account_id=_clean(connection.instagram_business_account_id),
            access_token=connection_token(connection),
        )
    except Exception as exc:
        connection.status = "error"
        connection.metadata_json = {**dict(connection.metadata_json or {}), "last_error": str(exc)[:240]}
        await db.commit()
        raise
    connection.instagram_business_account_id = _clean(connection.instagram_business_account_id)
    connection.external_account_id = _clean(connection.instagram_business_account_id)
    connection.status = "connected"
    connection.metadata_json = {
        **dict(connection.metadata_json or {}),
        **_validated_connection_metadata(
            professional_id=_clean(connection.instagram_business_account_id),
            identified=identified,
        ),
    }
    await db.commit()
    await db.refresh(connection)
    return connection


async def resolve_webhook_connection(db: AsyncSession, object_id: str) -> Conexao | None:
    from db.models import Conexao, TipoConexao

    matches = (await db.execute(select(Conexao).where(
        Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.channel == "instagram",
        Conexao.provider == "meta_instagram",
        Conexao.status == "connected",
        Conexao.instagram_business_account_id == _clean(object_id),
    ).limit(2))).scalars().all()
    # Never route an externally addressed event to an arbitrary tenant.
    return matches[0] if len(matches) == 1 else None


async def _persist_comment_context(
    db: AsyncSession, *, connection: Conexao, sender_id: str | None, username: str | None,
    comment_id: str, media_id: str | None, text: str,
) -> CRMLead:
    """Associate a public comment with CRM without fabricating a Direct thread."""
    from db.models import ChannelContact, CRMLead

    normalized_sender = _clean(sender_id) or None
    contact = None
    if normalized_sender:
        contact = (await db.execute(select(ChannelContact).where(
            ChannelContact.empresa_id == connection.empresa_id,
            ChannelContact.conexao_id == connection.id,
            ChannelContact.channel == "instagram",
            ChannelContact.provider == "meta_instagram",
            ChannelContact.external_contact_id == normalized_sender,
        ))).scalars().first()
    lead = None
    if contact and contact.lead_id:
        lead = (await db.execute(select(CRMLead).where(CRMLead.id == contact.lead_id, CRMLead.empresa_id == connection.empresa_id))).scalars().first()
    if not lead and normalized_sender:
        lead = (await db.execute(select(CRMLead).where(
            CRMLead.empresa_id == connection.empresa_id,
            CRMLead.dados_adicionais["instagram_user_id"].astext == normalized_sender,
        ))).scalars().first()
    if not lead:
        lead = CRMLead(
            empresa_id=connection.empresa_id,
            nome_contato=_clean(username) or f"Instagram {sender_id}",
            telefone_contato=None,
            dados_adicionais={"origem": "instagram_comment", **({"instagram_user_id": normalized_sender} if normalized_sender else {})},
        )
        db.add(lead)
        await db.flush()
    elif lead.telefone_contato and str(lead.telefone_contato).startswith("instagram:"):
        lead.telefone_contato = None
    if normalized_sender and not contact:
        contact = ChannelContact(
            empresa_id=connection.empresa_id,
            conexao_id=connection.id,
            lead_id=lead.id,
            channel="instagram",
            provider="meta_instagram",
            external_contact_id=normalized_sender,
            username=_clean(username) or None,
            display_name=_clean(username) or None,
            phone_e164=None,
            normalized_address=normalized_sender,
            metadata_json={"identity_type": "instagram_commenter_id"},
        )
        db.add(contact)
    lead_metadata = dict(lead.dados_adicionais or {})
    lead_metadata.setdefault("origem", "instagram_comment")
    lead.dados_adicionais = {
        **lead_metadata,
        "instagram_comment_context": {
            "comment_id": comment_id,
            "media_id": media_id,
            "sender_id": normalized_sender,
            "text": text,
        },
    }
    return lead


async def _persist_private_reply_outbound(
    db: AsyncSession,
    *,
    connection: Conexao,
    lead: CRMLead,
    comment_id: str,
    message: str,
    graph_response: dict[str, Any],
    message_type: str = "text",
) -> MensagemHistorico:
    from db.models import MensagemHistorico

    outbound = MensagemHistorico(
        empresa_id=connection.empresa_id,
        lead_id=lead.id,
        conexao_id=connection.id,
        channel_conversation_id=None,
        channel="instagram",
        provider="meta_instagram",
        external_message_id=_clean(graph_response.get("message_id") or graph_response.get("id")) or None,
        external_conversation_id=f"instagram_comment:{comment_id}",
        direction="outbound",
        message_status="sent",
        provider_status="sent",
        texto=message,
        tipo_mensagem=message_type,
        metadata_json={
            "origin": "instagram_comment_private_reply",
            "comment_id": comment_id,
            "instagram_professional_account_id": _clean(connection.instagram_business_account_id),
            "private_reply_message_type": message_type,
            "meta_result": {
                "message_id_present": bool(_clean(graph_response.get("message_id") or graph_response.get("id"))),
            },
        },
        from_me=True,
    )
    db.add(outbound)
    await db.flush()
    return outbound


async def process_direct_message(
    db: AsyncSession,
    *,
    connection: Conexao,
    igsid: str,
    recipient_id: str | None,
    message_id: str,
    text: str | None,
) -> dict[str, Any]:
    """Persist a real Instagram messaging event using its scoped user ID."""
    from app.services.operational_identity import ensure_operational_conversation
    from db.models import ChannelContact, CRMLead, MensagemHistorico

    scoped_id = _clean(igsid)
    external_message_id = _clean(message_id)
    if not scoped_id or not external_message_id:
        raise ValueError("Evento de mensagem Instagram sem IGSID ou message_id.")
    if recipient_id and _clean(recipient_id) != _clean(connection.instagram_business_account_id):
        raise ValueError("Mensagem Instagram destinada a outra conta profissional.")
    existing_message = (await db.execute(select(MensagemHistorico).where(
        MensagemHistorico.empresa_id == connection.empresa_id,
        MensagemHistorico.conexao_id == connection.id,
        MensagemHistorico.channel == "instagram",
        MensagemHistorico.provider == "meta_instagram",
        MensagemHistorico.external_message_id == external_message_id,
    ))).scalars().first()
    if existing_message:
        return {"status": "duplicate", "message_id": external_message_id}

    contact = (await db.execute(select(ChannelContact).where(
        ChannelContact.empresa_id == connection.empresa_id,
        ChannelContact.conexao_id == connection.id,
        ChannelContact.channel == "instagram",
        ChannelContact.provider == "meta_instagram",
        ChannelContact.external_contact_id == scoped_id,
    ))).scalars().first()
    lead = None
    if contact and contact.lead_id:
        lead = (await db.execute(select(CRMLead).where(
            CRMLead.id == contact.lead_id,
            CRMLead.empresa_id == connection.empresa_id,
        ))).scalars().first()
    if not lead:
        lead = (await db.execute(select(CRMLead).where(
            CRMLead.empresa_id == connection.empresa_id,
            CRMLead.telefone_contato == f"instagram:{scoped_id}",
        ))).scalars().first()
    if not lead:
        lead = CRMLead(
            empresa_id=connection.empresa_id,
            nome_contato=f"Instagram {scoped_id}",
            telefone_contato=f"instagram:{scoped_id}",
            dados_adicionais={"origem": "instagram_message", "instagram_igsid": scoped_id},
        )
        db.add(lead)
        await db.flush()
    lead.dados_adicionais = {
        **dict(lead.dados_adicionais or {}),
        "origem": "instagram_message",
        "instagram_igsid": scoped_id,
    }

    operational = await ensure_operational_conversation(
        db,
        empresa_id=connection.empresa_id,
        conexao_id=connection.id,
        external_contact_id=scoped_id,
        lead=lead,
        direction="inbound",
        external_conversation_id=f"instagram:{scoped_id}",
        metadata={"source": "instagram_message", "identity_type": "IGSID"},
    )
    inbound = MensagemHistorico(
        empresa_id=connection.empresa_id,
        lead_id=lead.id,
        conexao_id=connection.id,
        channel_conversation_id=operational.conversation.id,
        channel="instagram",
        provider="meta_instagram",
        external_message_id=external_message_id,
        external_conversation_id=f"instagram:{scoped_id}",
        direction="inbound",
        message_status="received",
        provider_status="received",
        texto=str(text or ""),
        tipo_mensagem="text",
        metadata_json={"origin": "instagram_message", "instagram_igsid": scoped_id},
        from_me=False,
    )
    db.add(inbound)
    conversation_id = str(operational.conversation.id)
    await db.commit()
    return {
        "status": "processed",
        "message_id": external_message_id,
        "conversation_id": conversation_id,
    }


async def _graph_failure_diagnostic(
    client: InstagramGraphClient,
    connection: Conexao,
    exc: InstagramGraphError,
    *,
    operation: str,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    metadata = dict(connection.metadata_json or {})
    token_diagnostic: dict[str, Any] = {
        "type": metadata.get("token_type") or "INSTAGRAM_USER",
        "is_valid": metadata.get("token_is_valid"),
        "permissions": sorted(_clean(value) for value in (metadata.get("permissions") or []) if _clean(value)),
    }
    try:
        token_diagnostic = await client.token_diagnostic()
    except InstagramGraphError:
        token_diagnostic["diagnostic_lookup_failed"] = True
    return exc.sanitized_diagnostic(
        operation=operation,
        object_type=object_type,
        object_id=object_id,
        token_diagnostic=token_diagnostic,
    )


async def process_comment(
    db: AsyncSession, *, connection: Conexao, comment_id: str, media_id: str | None,
    sender_id: str | None, username: str | None, text: str | None,
) -> dict[str, Any]:
    from db.models import InstagramCommentAutomation, InstagramCommentExecution, InstagramWebhookEvent

    event, reservation = await _reserve_webhook_event(
        db,
        connection=connection,
        comment_id=comment_id,
        media_id=media_id,
        sender_id=sender_id,
    )
    if reservation != "reserved":
        return {"status": reservation, "comment_id": comment_id}
    event_id = event.id

    try:
        normalized_sender = _clean(sender_id)
        normalized_account = _clean(connection.instagram_business_account_id)
        own_profile = bool(normalized_sender and normalized_account and normalized_sender == normalized_account)
        generated_reply = False if own_profile else await _registered_public_reply(
            db, connection=connection, comment_id=comment_id,
        )
        ignored_reason = classify_comment_origin(
            sender_id=normalized_sender,
            account_id=normalized_account,
            registered_public_reply=generated_reply,
        )
        if ignored_reason:
            event.status = "ignored"
            event.error_detail = json.dumps({
                "reason": ignored_reason,
            }, ensure_ascii=False, separators=(",", ":"))
            await db.commit()
            logger.info(
                "Instagram comment ignored before CRM/automation reason=%s comment_id=%s",
                ignored_reason,
                comment_id,
            )
            return {"status": ignored_reason, "comment_id": comment_id}

        # A stale/redelivered event may be reserved again after its first
        # execution committed.  Stop before CRM, automation, or either send
        # stage; the existing execution is the idempotency authority.
        if await _existing_execution_for_comment(db, connection=connection, comment_id=comment_id):
            event.status = "processed"
            event.error_detail = None
            await db.commit()
            logger.info("Instagram comment duplicate ignored before CRM comment_id=%s", comment_id)
            return {"status": "duplicate", "comment_id": comment_id}

        client = InstagramGraphClient(connection_token(connection))
        # The signed webhook is canonical. Graph is enrichment/fallback only when
        # fields required for matching are absent; it must not re-authenticate a
        # complete event or compare optional author/owner fields.
        if comment_payload_has_matching_fields(media_id=media_id, text=text):
            logger.info("Instagram comment webhook fields complete; skipping Graph lookup comment_id=%s", comment_id)
        else:
            logger.info("Instagram comment webhook fields incomplete; using optional Graph lookup comment_id=%s", comment_id)
            try:
                remote = await client.comment(comment_id)
                remote_from = remote.get("from") if isinstance(remote.get("from"), dict) else {}
                remote_media = remote.get("media") if isinstance(remote.get("media"), dict) else {}
                remote_id = _clean(remote.get("id"))
                remote_media_id = _clean(remote_media.get("id"))
                if remote_id and remote_id != comment_id:
                    raise InstagramGraphError("A Graph retornou outro comment_id.")
                if media_id and remote_media_id and remote_media_id != _clean(media_id):
                    raise InstagramGraphError("A Graph retornou outro media_id.")
                sender_id = _clean(remote_from.get("id") or sender_id) or None
                username = _clean(remote_from.get("username") or username) or None
                text = str(remote.get("text") if remote.get("text") is not None else text or "")
                media_id = remote_media_id or _clean(media_id) or None
            except InstagramGraphError as exc:
                if str(exc).startswith("A Graph retornou"):
                    raise
                if not media_id:
                    raise
                logger.warning("Optional Instagram comment Graph lookup failed; continuing with signed webhook comment_id=%s", comment_id)
        sender_id = _clean(sender_id) or None
        text = str(text or "")

        lead = await _persist_comment_context(
            db, connection=connection, sender_id=sender_id, username=username,
            comment_id=comment_id, media_id=media_id, text=text,
        )
        automations = (await db.execute(select(InstagramCommentAutomation).where(
            InstagramCommentAutomation.empresa_id == connection.empresa_id,
            InstagramCommentAutomation.active.is_(True),
            InstagramCommentAutomation.connection_id == connection.id,
        ))).scalars().all()
        matched = 0
        for automation in automations:
            if automation.instagram_account_id and _clean(automation.instagram_account_id) != _clean(connection.instagram_business_account_id):
                continue
            if automation.media_id and _clean(automation.media_id) != _clean(media_id):
                continue
            if not comment_matches(text, automation.keywords):
                continue
            existing_execution = (await db.execute(select(InstagramCommentExecution).where(
                InstagramCommentExecution.empresa_id == connection.empresa_id,
                InstagramCommentExecution.instagram_account_id == _clean(connection.instagram_business_account_id),
                InstagramCommentExecution.comment_id == comment_id,
                InstagramCommentExecution.automation_id == automation.id,
            ))).scalars().first()
            if existing_execution:
                matched += 1
                continue
            execution = InstagramCommentExecution(
                empresa_id=connection.empresa_id, connection_id=connection.id,
                automation_id=automation.id, instagram_account_id=_clean(connection.instagram_business_account_id),
                comment_id=comment_id, media_id=media_id, username=username, comment_text=text,
                webhook_event_id=event.id, lead_id=lead.id, message_id=None,
            )
            db.add(execution)
            try:
                await db.flush()
            except IntegrityError:
                # The database unique constraint is the final concurrency
                # guard.  A concurrent worker already owns this execution;
                # never turn that benign race into a failed event/retry.
                await db.rollback()
                stored_event = (await db.execute(select(InstagramWebhookEvent).where(
                    InstagramWebhookEvent.id == event_id,
                ))).scalars().first()
                if stored_event:
                    stored_event.status = "processed"
                    stored_event.error_detail = None
                    await db.commit()
                logger.info("Instagram comment concurrent duplicate ignored comment_id=%s", comment_id)
                return {"status": "duplicate", "comment_id": comment_id}
            matched += 1
            failures: list[dict[str, Any]] = []
            if automation.public_reply_enabled:
                public_text = _clean(automation.fixed_reply or automation.fallback_reply or DEFAULT_PUBLIC_REPLY)
                execution.public_reply = public_text
                execution.public_reply_origin = "fixed"
                try:
                    graph_response = await client.public_reply(comment_id, public_text)
                    execution.public_reply_status = "public_reply_sent"
                    reply_id = _public_reply_id(graph_response)
                    if reply_id:
                        recent = list(automation.recent_public_replies or [])
                        recent.append({
                            "comment_id": reply_id,
                            "source_comment_id": _clean(comment_id),
                            "origin": "instagram_automation_public_reply",
                        })
                        automation.recent_public_replies = recent[-100:]
                except InstagramGraphError as exc:
                    execution.public_reply_status = "public_reply_failed"
                    failures.append(await _graph_failure_diagnostic(
                        client,
                        connection,
                        exc,
                        operation="public_reply",
                        object_type="instagram_comment",
                        object_id=comment_id,
                    ))
            else:
                execution.public_reply_status = "ignored"
            private_config = _automation_private_reply_config(automation)
            if automation.private_reply_enabled and _clean(private_config.get("text")):
                private_text, private_message, private_message_type = build_private_reply_message(
                    automation,
                    username=username,
                    comment=text,
                )
                execution.private_reply = private_text
                try:
                    graph_response = await client.private_reply(
                        _clean(connection.instagram_business_account_id),
                        comment_id,
                        private_message,
                    )
                    logger.info(
                        "Instagram private reply sent message_type=%s meta_message_id_present=%s",
                        private_message_type,
                        bool(_clean(graph_response.get("message_id") or graph_response.get("id"))),
                    )
                    outbound = await _persist_private_reply_outbound(
                        db,
                        connection=connection,
                        lead=lead,
                        comment_id=comment_id,
                        message=private_text,
                        graph_response=graph_response,
                        message_type=private_message_type,
                    )
                    execution.message_id = outbound.id
                    execution.private_reply_status = "private_reply_sent"
                except InstagramGraphError as exc:
                    execution.private_reply_status = "private_reply_failed"
                    failures.append(await _graph_failure_diagnostic(
                        client,
                        connection,
                        exc,
                        operation="private_reply",
                        object_type="instagram_professional_account",
                        object_id=_clean(connection.instagram_business_account_id),
                    ))
            else:
                execution.private_reply_status = "ignored"
            execution.status = execution_status_from_results(
                failures=failures,
                public_reply_status=execution.public_reply_status,
                private_reply_status=execution.private_reply_status,
            )
            execution.error_detail = json.dumps(
                {"graph_failures": failures},
                ensure_ascii=False,
                separators=(",", ":"),
            ) if failures else None
        event.status = "processed"
        await db.commit()
        return {"status": "processed", "comment_id": comment_id, "matched_automations": matched}
    except Exception as exc:
        await db.rollback()
        stored = (await db.execute(select(InstagramWebhookEvent).where(InstagramWebhookEvent.id == event_id))).scalars().first()
        if stored:
            stored.status = "failed"
            stored.error_detail = json.dumps(
                {"error": type(exc).__name__, "message": str(exc)[:400]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await db.commit()
        raise
