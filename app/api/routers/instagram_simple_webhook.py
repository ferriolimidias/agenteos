from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.instagram_simple import process_comment, process_direct_message, resolve_webhook_connection
from app.services.meta.instagram_api_settings import load_instagram_api_settings
from db.database import get_db

router = APIRouter(prefix="/api/webhooks/meta/instagram", tags=["Instagram webhook"])
MAX_WEBHOOK_BODY_BYTES = 1_000_000


def valid_signature(raw_body: bytes, signature: str | None, app_secret: str | None) -> bool:
    supplied = str(signature or "").strip()
    secret = str(app_secret or "").strip()
    if not supplied.startswith("sha256=") or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def comment_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("object") != "instagram":
        return []
    events: list[dict[str, Any]] = []
    for entry in payload.get("entry") if isinstance(payload.get("entry"), list) else []:
        if not isinstance(entry, dict):
            continue
        object_id = str(entry.get("id") or "").strip()
        for change in entry.get("changes") if isinstance(entry.get("changes"), list) else []:
            if not isinstance(change, dict) or change.get("field") != "comments":
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            sender = value.get("from") if isinstance(value.get("from"), dict) else {}
            media = value.get("media") if isinstance(value.get("media"), dict) else {}
            comment_id = str(value.get("id") or value.get("comment_id") or "").strip()
            if comment_id:
                events.append({
                    "object_id": object_id, "comment_id": comment_id,
                    "media_id": str(media.get("id") or value.get("post_id") or "").strip() or None,
                    "sender_id": str(sender.get("id") or value.get("sender_id") or "").strip() or None,
                    "username": str(sender.get("username") or sender.get("name") or "").strip() or None,
                    "text": value.get("text") if value.get("text") is not None else value.get("message"),
                })
    return events


def direct_message_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("object") != "instagram":
        return []
    events: list[dict[str, Any]] = []
    for entry in payload.get("entry") if isinstance(payload.get("entry"), list) else []:
        if not isinstance(entry, dict):
            continue
        object_id = str(entry.get("id") or "").strip()
        for item in entry.get("messaging") if isinstance(entry.get("messaging"), list) else []:
            if not isinstance(item, dict):
                continue
            sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
            recipient = item.get("recipient") if isinstance(item.get("recipient"), dict) else {}
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            igsid = str(sender.get("id") or "").strip()
            recipient_id = str(recipient.get("id") or "").strip() or None
            message_id = str(message.get("mid") or "").strip()
            if (
                not object_id
                or not igsid
                or not message_id
                or message.get("is_echo") is True
                or igsid == object_id
            ):
                continue
            events.append({
                "object_id": object_id,
                "igsid": igsid,
                "recipient_id": recipient_id,
                "message_id": message_id,
                "text": message.get("text"),
            })
    return events


@router.get("")
async def verify_webhook(
    request: Request,
    mode: str | None = Query(None, alias="hub.mode"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    db: AsyncSession = Depends(get_db),
):
    # FastAPI already resolved the query parameters. Remove them from the ASGI
    # scope before Uvicorn writes the access log so the verify token is not logged.
    request.scope["query_string"] = b""
    settings = await load_instagram_api_settings(db)
    expected = str(settings.verify_token or "").strip()
    if not expected:
        raise HTTPException(503, "Verify Token global do Instagram indisponível.")
    if mode == "subscribe" and expected and hmac.compare_digest(str(verify_token or ""), expected):
        return Response(content=str(challenge or ""), media_type="text/plain")
    raise HTTPException(403, "Falha na verificação do webhook.")


@router.post("")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(413, "Payload do webhook excede o limite.")
        except ValueError:
            raise HTTPException(400, "Content-Length inválido.")
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(413, "Payload do webhook excede o limite.")
    settings = await load_instagram_api_settings(db)
    if not str(settings.app_secret or "").strip():
        raise HTTPException(503, "App Secret global do Instagram indisponível.")
    if not valid_signature(raw_body, request.headers.get("x-hub-signature-256"), settings.app_secret):
        raise HTTPException(401, "Assinatura inválida.")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "JSON inválido.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload inválido.")
    results: list[dict[str, Any]] = []
    for event in comment_changes(payload):
        connection = await resolve_webhook_connection(db, event["object_id"])
        if not connection:
            results.append({"status": "unresolved", "comment_id": event["comment_id"]})
            continue
        try:
            results.append(await process_comment(db, connection=connection, **{k: event[k] for k in ("comment_id", "media_id", "sender_id", "username", "text")}))
        except Exception:
            results.append({"status": "failed", "comment_id": event["comment_id"]})
    for event in direct_message_events(payload):
        connection = await resolve_webhook_connection(db, event["object_id"])
        if not connection:
            results.append({"status": "unresolved", "message_id": event["message_id"]})
            continue
        try:
            results.append(await process_direct_message(
                db,
                connection=connection,
                **{key: event[key] for key in ("igsid", "recipient_id", "message_id", "text")},
            ))
        except Exception:
            results.append({"status": "failed", "message_id": event["message_id"]})
    # HMAC/payload failures remain fail-closed above.  Once a signed delivery
    # has been durably reserved, report the internal result without asking Meta
    # to redeliver side effects; event/execution records carry the failure.
    return {"received": True, "events": results}
