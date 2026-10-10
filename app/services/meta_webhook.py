from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from app.services.channels.types import Channel, Provider

logger = logging.getLogger(__name__)


META_WEBHOOK_MAX_BODY_BYTES = int(os.getenv("META_WEBHOOK_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
META_WEBHOOK_DEDUP_TTL_SECONDS = int(os.getenv("META_WEBHOOK_DEDUP_TTL_SECONDS", "604800"))
META_WEBHOOK_LEASE_TTL_SECONDS = 300


class MetaWebhookError(ValueError):
    pass


class MetaWebhookConfigurationError(MetaWebhookError):
    pass


class MetaWebhookAuthenticationError(MetaWebhookError):
    pass


class MetaWebhookPayloadError(MetaWebhookError):
    pass


class MetaWebhookUnavailableError(MetaWebhookError):
    pass


class MetaWebhookProcessingError(RuntimeError):
    """A sanitized processing failure that must not be acknowledged as success."""

    def __init__(
        self,
        *,
        stage: str,
        event_kind: str,
        exception_type: str,
        sqlstate: str | None = None,
        constraint: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__("Meta webhook event processing failed.")
        self.stage = stage
        self.event_kind = event_kind
        self.exception_type = exception_type
        self.sqlstate = sqlstate
        self.constraint = constraint
        self.retryable = retryable


class MetaWebhookBatchProcessingError(RuntimeError):
    def __init__(self, failures: list[MetaWebhookProcessingError]) -> None:
        super().__init__("One or more Meta webhook events failed.")
        self.failures = tuple(failures)
        self.retryable = any(failure.retryable for failure in failures)


@dataclass(frozen=True)
class CanonicalMetaWebhookEvent:
    channel: Channel
    provider: Provider
    external_account_id: str
    external_event_id: str
    event_kind: str
    external_message_id: str | None = None
    sender_id: str | None = None
    recipient_id: str | None = None
    timestamp: str | None = None
    message_type: str | None = None
    text: str | None = None
    status: str | None = None
    error_code: str | None = None
    error_detail: Any = None
    referral: dict[str, str] | None = None
    comment_id: str | None = None
    media_id: str | None = None
    username: str | None = None
    profile_name: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResolvedMetaConnection:
    empresa_id: str
    connection_id: str
    channel: Channel
    provider: Provider


@dataclass(frozen=True)
class MetaWebhookProcessResult:
    received: int
    accepted: int
    duplicates: int
    unresolved: int
    parser_drops: int = 0


ConnectionResolver = Callable[[CanonicalMetaWebhookEvent], Awaitable[ResolvedMetaConnection | None]]
DedupAcquirer = Callable[[CanonicalMetaWebhookEvent, ResolvedMetaConnection], Awaitable[str | bool]]
DedupReleaser = Callable[[CanonicalMetaWebhookEvent, ResolvedMetaConnection, str], Awaitable[None]]
EventHandler = Callable[[CanonicalMetaWebhookEvent, ResolvedMetaConnection], Awaitable[bool | None]]
FallbackAuthenticator = Callable[[CanonicalMetaWebhookEvent], Awaitable[bool]]


def _masked_identifier(value: str | None) -> str:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else "not_present"


def _log_instagram_fallback(event_name: str, event: CanonicalMetaWebhookEvent, *, verification_result: str, failure_classification: str, started: float) -> None:
    logger.info(
        "event=%s request_id=%s comment_id_present=%s media_id_present=%s verification_result=%s failure_classification=%s duration_ms=%s",
        event_name, event.request_id or "not_present", bool(event.comment_id), bool(event.media_id),
        verification_result, failure_classification, int((time.monotonic() - started) * 1000),
    )


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str | None) -> bool:
    secret = str(app_secret or "").strip()
    if not secret:
        raise MetaWebhookConfigurationError("Meta webhook authentication is not configured.")

    signature = str(signature_header or "").strip()
    if not signature.startswith("sha256="):
        return False
    supplied_digest = signature.removeprefix("sha256=")
    if len(supplied_digest) != 64 or any(char not in "0123456789abcdef" for char in supplied_digest):
        return False

    expected_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_digest, supplied_digest)


def parse_meta_payload(raw_body: bytes, *, max_body_bytes: int = META_WEBHOOK_MAX_BODY_BYTES) -> dict[str, Any]:
    if len(raw_body) > max_body_bytes:
        raise MetaWebhookPayloadError("Meta webhook payload exceeds the configured limit.")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetaWebhookPayloadError("Meta webhook payload is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise MetaWebhookPayloadError("Meta webhook payload must be a JSON object.")
    return payload


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _extract_whatsapp_profile_name(value: dict[str, Any], sender_id: str | None) -> str | None:
    contacts = value.get("contacts") if isinstance(value.get("contacts"), list) else []
    if not contacts:
        return None

    sender_clean = _clean(sender_id)
    fallback_name: str | None = None

    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        name = _clean(profile.get("name"))
        if not name:
            name = _clean(contact.get("name"))
        if not name:
            continue

        wa_id = _clean(contact.get("wa_id"))
        if sender_clean and wa_id and wa_id == sender_clean:
            return name
        if fallback_name is None:
            fallback_name = name

    if fallback_name and len(contacts) == 1:
        return fallback_name
    return fallback_name if not sender_clean else None


def _extract_whatsapp_fallback_name(sender_id: str | None) -> str:
    sender_clean = _clean(sender_id)
    if not sender_clean:
        return "Contato"
    digits = "".join(ch for ch in sender_clean if ch.isdigit())
    if len(digits) >= 4:
        return f"Contato • final {digits[-4:]}"
    return f"Contato • {sender_clean[-4:]}" if len(sender_clean) >= 4 else "Contato"


def _extract_whatsapp_message_text(message: dict[str, Any], message_type: str | None) -> str | None:
    if message_type == "text":
        text_data = message.get("text") if isinstance(message.get("text"), dict) else {}
        return _clean(text_data.get("body"))

    if message_type == "button":
        button_data = message.get("button") if isinstance(message.get("button"), dict) else {}
        return _clean(button_data.get("text") or button_data.get("title") or button_data.get("payload"))

    if message_type == "interactive":
        interactive_data = message.get("interactive") if isinstance(message.get("interactive"), dict) else {}
        interactive_type = _clean(interactive_data.get("type"))
        if interactive_type == "button_reply":
            button_reply = interactive_data.get("button_reply") if isinstance(interactive_data.get("button_reply"), dict) else {}
            return _clean(button_reply.get("title") or button_reply.get("text") or button_reply.get("id"))
        if interactive_type == "list_reply":
            list_reply = interactive_data.get("list_reply") if isinstance(interactive_data.get("list_reply"), dict) else {}
            return _clean(list_reply.get("title") or list_reply.get("description") or list_reply.get("id"))
    return None


def _normalize_whatsapp(payload: dict[str, Any]) -> list[CanonicalMetaWebhookEvent]:
    events: list[CanonicalMetaWebhookEvent] = []
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = _clean(metadata.get("phone_number_id"))
            if not phone_number_id:
                continue

            messages = value.get("messages") if isinstance(value.get("messages"), list) else []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = _clean(message.get("id"))
                if not message_id:
                    continue
                message_type = _clean(message.get("type"))
                structured_metadata: dict[str, Any] = {}
                for field in (
                    "context", "reaction", "interactive", "button", "contacts", "location",
                    "order", "system", "errors", "identity",
                ):
                    value_data = message.get(field)
                    if isinstance(value_data, (dict, list)):
                        structured_metadata[field] = value_data
                media_data = message.get(message_type) if message_type in {
                    "image", "audio", "video", "document", "sticker",
                } else None
                if isinstance(media_data, dict):
                    structured_metadata["media"] = media_data
                from app.services.meta_attribution import extract_meta_referral
                referral = extract_meta_referral(message.get("referral"))
                profile_name = _extract_whatsapp_profile_name(value, message.get("from"))
                events.append(
                    CanonicalMetaWebhookEvent(
                        channel=Channel.WHATSAPP,
                        provider=Provider.META_WHATSAPP,
                        external_account_id=phone_number_id,
                        external_event_id=message_id,
                        event_kind="message",
                        external_message_id=message_id,
                        sender_id=_clean(message.get("from")),
                        recipient_id=phone_number_id,
                        timestamp=_clean(message.get("timestamp")),
                        message_type=message_type,
                        text=_extract_whatsapp_message_text(message, message_type),
                        referral=referral or None,
                        profile_name=profile_name,
                        metadata=structured_metadata or None,
                    )
                )

            statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
            for status_data in statuses:
                if not isinstance(status_data, dict):
                    continue
                message_id = _clean(status_data.get("id"))
                status = _clean(status_data.get("status"))
                timestamp = _clean(status_data.get("timestamp"))
                if not message_id or not status:
                    continue
                errors = status_data.get("errors") if isinstance(status_data.get("errors"), list) else []
                first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
                error_code = _clean(first_error.get("code")) if first_error else None
                error_detail: dict[str, Any] | None = None
                if first_error:
                    error_detail = {
                        key: first_error.get(key)
                        for key in ("code", "title", "message", "error_data", "href")
                        if first_error.get(key) not in (None, "")
                    }
                event_id = f"{message_id}:status:{status}:{timestamp or 'no-timestamp'}"
                events.append(
                    CanonicalMetaWebhookEvent(
                        channel=Channel.WHATSAPP,
                        provider=Provider.META_WHATSAPP,
                        external_account_id=phone_number_id,
                        external_event_id=event_id,
                        event_kind="status",
                        external_message_id=message_id,
                        recipient_id=_clean(status_data.get("recipient_id")),
                        timestamp=timestamp,
                        status=status,
                        error_code=error_code,
                        error_detail=error_detail,
                    )
                )
    return events


def _normalize_instagram(payload: dict[str, Any]) -> list[CanonicalMetaWebhookEvent]:
    events: list[CanonicalMetaWebhookEvent] = []
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_account_id = _clean(entry.get("id"))
        changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
        if isinstance(entry.get("field"), str):
            changes.append({"field": entry.get("field"), "value": entry.get("value")})
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "comments":
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            comment_id = _clean(value.get("id"))
            if not entry_account_id or not comment_id:
                continue
            media = value.get("media") if isinstance(value.get("media"), dict) else {}
            sender = value.get("from") if isinstance(value.get("from"), dict) else {}
            events.append(CanonicalMetaWebhookEvent(
                channel=Channel.INSTAGRAM, provider=Provider.META_INSTAGRAM,
                external_account_id=entry_account_id, external_event_id=f"comment:{comment_id}",
                event_kind="comment", comment_id=comment_id, media_id=_clean(media.get("id")),
                sender_id=_clean(sender.get("id")), username=_clean(sender.get("username")),
                text=_clean(value.get("text")), timestamp=_clean(value.get("created_time")),
            ))
        messaging = entry.get("messaging") if isinstance(entry.get("messaging"), list) else []
        for item in messaging:
            if not isinstance(item, dict):
                continue
            sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
            recipient = item.get("recipient") if isinstance(item.get("recipient"), dict) else {}
            account_id = entry_account_id or _clean(recipient.get("id"))
            if not account_id:
                continue

            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            message_id = _clean(message.get("mid"))
            if message_id:
                events.append(
                    CanonicalMetaWebhookEvent(
                        channel=Channel.INSTAGRAM,
                        provider=Provider.META_INSTAGRAM,
                        external_account_id=account_id,
                        external_event_id=message_id,
                        event_kind="message",
                        sender_id=_clean(sender.get("id")),
                        recipient_id=_clean(recipient.get("id")),
                        timestamp=_clean(item.get("timestamp")),
                        message_type="text" if _clean(message.get("text")) else "unknown",
                        text=_clean(message.get("text")),
                    )
                )

            delivery = item.get("delivery") if isinstance(item.get("delivery"), dict) else {}
            mids = delivery.get("mids") if isinstance(delivery.get("mids"), list) else []
            for mid in mids:
                clean_mid = _clean(mid)
                if not clean_mid:
                    continue
                watermark = _clean(delivery.get("watermark"))
                events.append(
                    CanonicalMetaWebhookEvent(
                        channel=Channel.INSTAGRAM,
                        provider=Provider.META_INSTAGRAM,
                        external_account_id=account_id,
                        external_event_id=f"{clean_mid}:status:delivered:{watermark or 'no-watermark'}",
                        event_kind="status",
                        sender_id=_clean(sender.get("id")),
                        recipient_id=_clean(recipient.get("id")),
                        timestamp=watermark,
                        status="delivered",
                    )
                )
    return events


def normalize_meta_payload(payload: dict[str, Any]) -> list[CanonicalMetaWebhookEvent]:
    object_type = str(payload.get("object") or "").strip().lower()
    if object_type == "whatsapp_business_account":
        return _normalize_whatsapp(payload)
    if object_type == "instagram":
        # Instagram has a dedicated canonical webhook. The shared Meta route
        # remains exclusively WhatsApp and must never persist or dispatch
        # Instagram comments, Direct messages, or statuses.
        return []
    return []


def meta_event_dedup_key(event: CanonicalMetaWebhookEvent, connection: ResolvedMetaConnection) -> str:
    material = ":".join(
        (
            connection.empresa_id,
            connection.connection_id,
            event.channel.value,
            event.provider.value,
            event.external_event_id,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"webhook:meta:{digest}"


async def acquire_meta_event_lock(
    redis_client: Any,
    event: CanonicalMetaWebhookEvent,
    connection: ResolvedMetaConnection,
    *,
    ttl_seconds: int = META_WEBHOOK_DEDUP_TTL_SECONDS,
) -> str | bool:
    if redis_client is None:
        # PostgreSQL transaction locking and its durable event lookup are the
        # correctness boundary. Redis is only a short-lived work lease.
        return False
    try:
        owner = uuid.uuid4().hex
        acquired = await redis_client.set(
            meta_event_dedup_key(event, connection),
            owner,
            nx=True,
            ex=max(30, min(META_WEBHOOK_LEASE_TTL_SECONDS, int(ttl_seconds))),
        )
        return owner if acquired else False
    except Exception:
        # Redis must never be the only record that a Meta event was processed.
        logger.warning("event=META_WEBHOOK_DEDUP_LEASE_UNAVAILABLE request_id=%s", event.request_id or "unknown")
        return False


async def release_meta_event_lock(
    redis_client: Any,
    event: CanonicalMetaWebhookEvent,
    connection: ResolvedMetaConnection,
    owner: str,
) -> None:
    if redis_client is None:
        return
    key = meta_event_dedup_key(event, connection)
    try:
        # Compare-and-delete protects a lease acquired by a newer request.
        await redis_client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            owner,
        )
    except Exception:
        logger.warning("event=META_WEBHOOK_DEDUP_LEASE_RELEASE_FAILED request_id=%s", event.request_id or "unknown")


def _safe_field_name(value: Any) -> str:
    raw = str(value or "unknown")[:64]
    return "".join(char if char.isalnum() or char in "_.-" else "_" for char in raw) or "unknown"


def _parser_drop_counts(payload: dict[str, Any]) -> Counter[str]:
    drops: Counter[str] = Counter()
    if str(payload.get("object") or "").strip().lower() != "whatsapp_business_account":
        return drops

    if not isinstance(payload.get("entry"), list):
        drops["invalid_entry_collection"] += 1
        return drops
    entries = payload["entry"]
    for entry in entries:
        if not isinstance(entry, dict):
            drops["invalid_entry"] += 1
            continue
        if not isinstance(entry.get("changes"), list):
            drops["invalid_changes_collection"] += 1
            continue
        changes = entry["changes"]
        for change in changes:
            if not isinstance(change, dict):
                drops["invalid_change"] += 1
                continue
            if not isinstance(change.get("value"), dict):
                drops["invalid_change_value"] += 1
                continue
            value = change["value"]
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            has_phone_number_id = bool(_clean(metadata.get("phone_number_id")))
            if "messages" in value and not isinstance(value.get("messages"), list):
                drops["invalid_messages_collection"] += 1
            messages = value.get("messages") if isinstance(value.get("messages"), list) else []
            for message in messages:
                if not isinstance(message, dict):
                    drops["invalid_message_item"] += 1
                elif not has_phone_number_id:
                    drops["missing_phone_number_id"] += 1
                elif not _clean(message.get("id")):
                    drops["missing_message_id"] += 1

            if "statuses" in value and not isinstance(value.get("statuses"), list):
                drops["invalid_statuses_collection"] += 1
            statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
            for status in statuses:
                if not isinstance(status, dict):
                    drops["invalid_status_item"] += 1
                elif not has_phone_number_id:
                    drops["missing_phone_number_id"] += 1
                elif not _clean(status.get("id")) or not _clean(status.get("status")):
                    drops["missing_status_identity"] += 1

            if "errors" in value and not isinstance(value.get("errors"), list):
                drops["invalid_errors_collection"] += 1
            errors = value.get("errors") if isinstance(value.get("errors"), list) else []
            if errors:
                # Meta error notifications are classified and acknowledged as
                # valid callbacks; they are not chat messages or delivery status.
                drops["error_callback_ignored"] += len(errors)
    return drops


def _classify_payload(payload: dict[str, Any], events: list[CanonicalMetaWebhookEvent], request_id: str) -> Counter[str]:
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    change_count = 0
    fields: set[str] = set()
    raw_kinds: Counter[str] = Counter()
    raw_message_types: Counter[str] = Counter()
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) and isinstance(entry.get("changes"), list) else []
        change_count += len(changes)
        for change in changes:
            if not isinstance(change, dict):
                continue
            fields.add(_safe_field_name(change.get("field")))
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            messages = value.get("messages") if isinstance(value.get("messages"), list) else []
            statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
            errors = value.get("errors") if isinstance(value.get("errors"), list) else []
            if messages:
                raw_kinds["message"] += len(messages)
                for message in messages:
                    if isinstance(message, dict):
                        raw_message_types[_safe_field_name(message.get("type"))] += 1
            if statuses:
                raw_kinds["status"] += len(statuses)
            if errors:
                raw_kinds["error"] += len(errors)
    if not raw_kinds:
        raw_kinds["unknown"] = 1
    kinds_text = ",".join(f"{key}:{raw_kinds[key]}" for key in sorted(raw_kinds))
    message_types = raw_message_types
    types_text = ",".join(f"{key}:{message_types[key]}" for key in sorted(message_types)) or "none"
    normalized_kinds = Counter(event.event_kind for event in events)
    normalized_text = ",".join(f"{key}:{normalized_kinds[key]}" for key in sorted(normalized_kinds)) or "none"
    drops = _parser_drop_counts(payload)
    drop_text = ",".join(f"{key}:{drops[key]}" for key in sorted(drops)) or "none"
    logger.info(
        "event=META_WEBHOOK_CLASSIFIED request_id=%s provider=meta object=%s entry_count=%d change_count=%d fields=%s event_kinds=%s message_types=%s normalized_count=%d normalized_event_kinds=%s parser_drops=%s",
        request_id,
        _safe_field_name(payload.get("object")),
        len(entries),
        change_count,
        ",".join(sorted(fields)) or "none",
        kinds_text,
        types_text,
        len(events),
        normalized_text,
        drop_text,
    )
    if drops:
        logger.warning(
            "event=META_WEBHOOK_PARSER_DROPS request_id=%s dropped_count=%d reasons=%s",
            request_id, sum(drops.values()), drop_text,
        )
    elif not events:
        logger.info(
            "event=META_WEBHOOK_IGNORED request_id=%s event_kind=unknown reason=valid_no_supported_events object=%s",
            request_id, _safe_field_name(payload.get("object")),
        )
    return drops


async def resolve_meta_connection(session: Any, event: CanonicalMetaWebhookEvent) -> ResolvedMetaConnection | None:
    from sqlalchemy import select

    from db.models import Conexao, TipoConexao

    provider_aliases = [event.provider.value]
    if event.provider == Provider.META_WHATSAPP:
        provider_aliases.extend(["meta_official", "meta", "whatsapp_meta_official"])
    active_statuses = ["ativo", "connected", "open", "online"]
    conditions = [
        Conexao.tipo == TipoConexao.META if event.channel == Channel.WHATSAPP else Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.channel == event.channel.value,
        Conexao.provider.in_(provider_aliases),
        Conexao.status.in_(active_statuses),
    ]
    if event.channel == Channel.WHATSAPP:
        conditions.append(Conexao.phone_number_id == event.external_account_id)
    else:
        conditions.append(Conexao.instagram_business_account_id == event.external_account_id)

    result = await session.execute(select(Conexao).where(*conditions).limit(2))
    matches = list(result.scalars().all())
    if len(matches) != 1:
        return None
    connection = matches[0]
    return ResolvedMetaConnection(
        empresa_id=str(connection.empresa_id),
        connection_id=str(connection.id),
        channel=event.channel,
        provider=event.provider,
    )


async def process_meta_webhook(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str | None,
    *,
    resolve_connection: ConnectionResolver,
    acquire_dedup: DedupAcquirer,
    handle_event: EventHandler | None = None,
    release_dedup: DedupReleaser | None = None,
    max_body_bytes: int = META_WEBHOOK_MAX_BODY_BYTES,
    request_id: str | None = None,
    fallback_authenticate: FallbackAuthenticator | None = None,
) -> MetaWebhookProcessResult:
    request_id = request_id or uuid.uuid4().hex
    logger.info("event=META_WEBHOOK_RECEIVED request_id=%s provider=meta", request_id)
    signature_valid = verify_meta_signature(raw_body, signature_header, app_secret)
    if not signature_valid:
        if fallback_authenticate is None:
            raise MetaWebhookAuthenticationError("Meta webhook signature is invalid.")
        payload = parse_meta_payload(raw_body, max_body_bytes=max_body_bytes)
        fallback_events = [replace(event, request_id=request_id) for event in normalize_meta_payload(payload)]
        if not fallback_events or any(event.channel != Channel.INSTAGRAM or event.event_kind != "comment" for event in fallback_events):
            raise MetaWebhookAuthenticationError("Meta webhook signature is invalid.")
        for event in fallback_events:
            started = time.monotonic()
            _log_instagram_fallback("instagram_comment_hmac_failed_fallback_started", event, verification_result="started", failure_classification="not_available", started=started)
            try:
                verified = await fallback_authenticate(event)
            except Exception as exc:
                _log_instagram_fallback("instagram_comment_fallback_rejected", event, verification_result="false", failure_classification=f"FALLBACK_EXCEPTION:{type(exc).__name__}", started=started)
                raise MetaWebhookAuthenticationError("Meta webhook signature is invalid.") from exc
            if not verified:
                _log_instagram_fallback("instagram_comment_fallback_rejected", event, verification_result="false", failure_classification="GRAPH_VERIFICATION_FAILED", started=started)
                raise MetaWebhookAuthenticationError("Meta webhook signature is invalid.")
    try:
        payload = parse_meta_payload(raw_body, max_body_bytes=max_body_bytes)
    except MetaWebhookPayloadError:
        if not signature_valid:
            raise
        # A signed payload with invalid JSON/shape or an exceeded parser limit
        # is permanently unprocessable. Acknowledge it after recording a
        # sanitized drop so the provider does not retry an unrecoverable body.
        # Invalid signatures are never acknowledged as permanently malformed.
        reason = "payload_too_large" if len(raw_body) > max_body_bytes else "invalid_json_or_shape"
        logger.warning(
            "event=META_WEBHOOK_CLASSIFIED request_id=%s provider=meta object=unknown entry_count=0 change_count=0 event_kinds=unknown:1 message_types=none normalized_count=0 normalized_event_kinds=none parser_drops=%s:1",
            request_id,
            reason,
        )
        logger.warning(
            "event=META_WEBHOOK_PARSER_DROPS request_id=%s dropped_count=1 reasons=%s:1",
            request_id,
            reason,
        )
        logger.warning(
            "event=META_WEBHOOK_PARSER_DROP_ACK request_id=%s dropped_count=1 outcome=authenticated_unparseable_payload http_status=200",
            request_id,
        )
        return MetaWebhookProcessResult(received=0, accepted=0, duplicates=0, unresolved=0, parser_drops=1)
    events = [replace(event, request_id=request_id) for event in normalize_meta_payload(payload)] if request_id else normalize_meta_payload(payload)
    parser_drops = _classify_payload(payload, events, request_id)

    if not signature_valid:
        for event in events:
            if event.channel == Channel.INSTAGRAM and event.event_kind == "comment":
                _log_instagram_fallback("instagram_comment_pipeline_resumed", event, verification_result="true", failure_classification="none", started=time.monotonic())

    accepted = 0
    duplicates = 0
    unresolved = 0
    failures: list[MetaWebhookProcessingError] = []
    for event in events:
        stage = "connection_resolution"
        try:
            connection = await resolve_connection(event)
        except Exception as exc:
            failures.append(_processing_failure(event, stage, exc))
            continue
        if connection is None:
            unresolved += 1
            logger.warning(
                "event=META_WEBHOOK_CONNECTION_UNRESOLVED request_id=%s event_kind=%s external_account_hash=%s",
                request_id,
                event.event_kind,
                _masked_identifier(event.external_account_id),
            )
            failures.append(_processing_failure(event, stage, ConnectionError("No unique active Meta connection was resolved.")))
            continue
        if connection.channel != event.channel or connection.provider != event.provider:
            unresolved += 1
            logger.warning(
                "event=META_WEBHOOK_CONNECTION_UNRESOLVED request_id=%s event_kind=%s external_account_hash=%s reason=provider_channel_mismatch",
                request_id,
                event.event_kind,
                _masked_identifier(event.external_account_id),
            )
            failures.append(_processing_failure(event, stage, ConnectionError("Resolved Meta connection does not match the callback channel/provider.")))
            continue
        logger.info(
            "event=META_WEBHOOK_CONNECTION_RESOLVED request_id=%s event_kind=%s tenant_hash=%s connection_hash=%s",
            request_id,
            event.event_kind,
            _masked_identifier(connection.empresa_id),
            _masked_identifier(connection.connection_id),
        )

        lease_owner: str | None = None
        stage = "idempotency"
        try:
            lease = await acquire_dedup(event, connection)
            if isinstance(lease, str):
                lease_owner = lease
            elif not lease and handle_event is None:
                # Compatibility for parser-only callers without a durable event
                # handler. Runtime processing always uses the PostgreSQL-backed
                # handler below; Redis alone is never proof of completion.
                duplicates += 1
                logger.info(
                    "event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=%s proof=redis_test_adapter",
                    request_id,
                    event.event_kind,
                )
                continue
            elif not lease:
                logger.info(
                    "event=META_WEBHOOK_DEDUP_LEASE_BUSY request_id=%s event_kind=%s durable_check=required",
                    request_id,
                    event.event_kind,
                )
        except Exception as exc:
            # Redis is an optimization; the database transaction and canonical
            # event lookup below remain authoritative.
            logger.warning(
                "event=META_WEBHOOK_DEDUP_LEASE_UNAVAILABLE request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )

        try:
            if handle_event is None:
                accepted += 1
                continue
            stage = "message_parse"
            logger.info(
                "event=META_WEBHOOK_MESSAGE_PARSED request_id=%s event_kind=%s message_type=%s external_event_hash=%s",
                request_id,
                event.event_kind,
                _safe_field_name(event.message_type),
                _masked_identifier(event.external_event_id),
            )
            is_duplicate = bool(await handle_event(event, connection))
            if is_duplicate:
                duplicates += 1
                logger.info(
                    "event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=%s proof=postgres_canonical_record",
                    request_id,
                    event.event_kind,
                )
            else:
                accepted += 1
        except MetaWebhookProcessingError as exc:
            failures.append(exc)
        except Exception as exc:
            failures.append(_processing_failure(event, stage, exc))
        finally:
            if lease_owner and release_dedup is not None:
                try:
                    await release_dedup(event, connection, lease_owner)
                except Exception as exc:
                    logger.warning(
                        "event=META_WEBHOOK_DEDUP_LEASE_RELEASE_FAILED request_id=%s exception_type=%s",
                        request_id,
                        type(exc).__name__,
                    )

    if failures:
        raise MetaWebhookBatchProcessingError(failures)

    malformed_drops = sum(
        count for reason, count in parser_drops.items()
        if reason != "error_callback_ignored"
    )
    if malformed_drops:
        # These signed callback items are permanently unprocessable (for
        # example, a message without its provider ID). Retrying cannot repair
        # their payload and may cause Meta to redeliver valid siblings for days.
        # Keep the drop visible and acknowledge only after every processable
        # sibling has completed; persistence failures above still return 5xx.
        logger.warning(
            "event=META_WEBHOOK_PARSER_DROP_ACK request_id=%s dropped_count=%d outcome=unprocessable_item http_status=200",
            request_id,
            malformed_drops,
        )

    return MetaWebhookProcessResult(
        received=len(events),
        accepted=accepted,
        duplicates=duplicates,
        unresolved=unresolved,
        parser_drops=malformed_drops,
    )


def _processing_failure(
    event: CanonicalMetaWebhookEvent,
    stage: str,
    exc: Exception,
) -> MetaWebhookProcessingError:
    sqlstate = _sqlstate(exc)
    constraint = _constraint_name(exc)
    failure = MetaWebhookProcessingError(
        stage=stage,
        event_kind=event.event_kind,
        exception_type=type(exc).__name__,
        sqlstate=str(sqlstate) if sqlstate else None,
        constraint=str(constraint) if constraint else None,
        retryable=True,
    )
    return failure


def _processing_http_status(exception_type: str, sqlstate: str | None) -> int:
    if exception_type in {"OperationalError", "TimeoutError", "ConnectionError"}:
        return 503
    if (sqlstate or "").startswith(("08", "53", "57")):
        return 503
    return 500


def _constraint_name(exc: Exception) -> str | None:
    """Read constraint names from asyncpg and psycopg SQLAlchemy errors."""
    pending = [exc]
    visited: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate is None or id(candidate) in visited:
            continue
        visited.add(id(candidate))
        direct = getattr(candidate, "constraint_name", None)
        if direct:
            return str(direct)
        diagnostic = getattr(candidate, "diag", None)
        value = getattr(diagnostic, "constraint_name", None) if diagnostic is not None else None
        if value:
            return str(value)
        pending.extend((
            getattr(candidate, "orig", None),
            getattr(candidate, "__cause__", None),
            getattr(candidate, "__context__", None),
        ))
    return None


def _sqlstate(exc: Exception) -> str | None:
    pending = [exc]
    visited: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate is None or id(candidate) in visited:
            continue
        visited.add(id(candidate))
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if value:
            return str(value)
        pending.extend((
            getattr(candidate, "orig", None),
            getattr(candidate, "__cause__", None),
            getattr(candidate, "__context__", None),
        ))
    return None
