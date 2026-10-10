import hashlib
import hmac
import json
import os
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api.routers.meta_webhook import receive_meta_webhook, verify_meta_webhook
from app.api.routers.conexoes import _montar_webhook_url
from app.services.channels.types import Channel, Provider
from app.services.meta_webhook import (
    CanonicalMetaWebhookEvent,
    MetaWebhookAuthenticationError,
    MetaWebhookBatchProcessingError,
    MetaWebhookConfigurationError,
    MetaWebhookPayloadError,
    MetaWebhookProcessResult,
    ResolvedMetaConnection,
    acquire_meta_event_lock,
    meta_event_dedup_key,
    normalize_meta_payload,
    process_meta_webhook,
    resolve_meta_connection,
    verify_meta_signature,
)
from db.models import TipoConexao


APP_SECRET = "test-app-secret-not-real"


def signed(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={digest}"


def whatsapp_payload(*, message_id="wamid.test-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-test",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "phone-1"},
                            "contacts": [
                                {
                                    "wa_id": "contact-1",
                                    "profile": {"name": "Nome Real"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": "contact-1",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "mensagem de teste"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def whatsapp_button_reply_payload(*, message_id="wamid.test-1"):
    payload = whatsapp_payload(message_id=message_id)
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "button"
    message["button"] = {"text": "Participar", "payload": "Participar"}
    message["context"] = {"id": "wamid.origin-1", "from": "555481080988"}
    message.pop("text", None)
    return payload


def whatsapp_interactive_button_reply_payload(*, message_id="wamid.test-1"):
    payload = whatsapp_payload(message_id=message_id)
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "interactive"
    message["interactive"] = {
        "type": "button_reply",
        "button_reply": {"id": "participar", "title": "Participar"},
    }
    message["context"] = {"id": "wamid.origin-1", "from": "555481080988"}
    message.pop("text", None)
    return payload


class MetaWebhookSignatureTests(unittest.TestCase):
    def test_valid_signature_uses_raw_body(self):
        raw, signature = signed(whatsapp_payload())
        self.assertTrue(verify_meta_signature(raw, signature, APP_SECRET))
        self.assertFalse(verify_meta_signature(raw + b" ", signature, APP_SECRET))

    def test_signature_rejects_wrong_prefix_and_uppercase_prefix(self):
        raw, signature = signed(whatsapp_payload())
        self.assertFalse(verify_meta_signature(raw, signature.replace("sha256=", "sha1="), APP_SECRET))
        self.assertFalse(verify_meta_signature(raw, signature.replace("sha256=", "SHA256="), APP_SECRET))

    def test_signature_rejects_malformed_digest(self):
        raw, _ = signed(whatsapp_payload())
        self.assertFalse(verify_meta_signature(raw, "sha256=abc", APP_SECRET))
        self.assertFalse(verify_meta_signature(raw, "sha256=" + "g" * 64, APP_SECRET))

    def test_missing_secret_fails_closed_without_echoing_secret(self):
        raw, signature = signed(whatsapp_payload())
        with self.assertRaises(MetaWebhookConfigurationError) as context:
            verify_meta_signature(raw, signature, None)
        self.assertNotIn(APP_SECRET, str(context.exception))

    def test_invalid_json_and_oversized_payload_are_rejected(self):
        from app.services.meta_webhook import parse_meta_payload

        with self.assertRaises(MetaWebhookPayloadError):
            parse_meta_payload(b"not-json")
        with self.assertRaises(MetaWebhookPayloadError):
            parse_meta_payload(b"{}", max_body_bytes=1)


class MetaWebhookNormalizationTests(unittest.TestCase):
    def test_referral_is_extracted_for_text_and_media_envelopes(self):
        payload = whatsapp_payload()
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message["referral"] = {"ctwa_clid": "Exact-CTWA", "source_id": "ad-source", "unknown": "discard"}
        message["type"] = "image"
        message.pop("text")
        event = normalize_meta_payload(payload)[0]
        self.assertEqual(event.message_type, "image")
        self.assertEqual(event.referral, {"ctwa_clid": "Exact-CTWA", "source_id": "ad-source"})

    def test_referral_without_ctwa_and_message_without_referral_are_safe(self):
        payload = whatsapp_payload()
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["referral"] = {"headline": "Campaign"}
        self.assertEqual(normalize_meta_payload(payload)[0].referral, {"headline": "Campaign"})
        self.assertIsNone(normalize_meta_payload(whatsapp_payload())[0].referral)

    def test_normalizes_whatsapp_message_and_status(self):
        payload = whatsapp_payload()
        value = payload["entry"][0]["changes"][0]["value"]
        value["statuses"] = [
            {"id": "wamid.out-1", "status": "delivered", "timestamp": "1700000001", "recipient_id": "contact-1"}
        ]

        events = normalize_meta_payload(payload)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].channel, Channel.WHATSAPP)
        self.assertEqual(events[0].provider, Provider.META_WHATSAPP)
        self.assertEqual(events[0].external_account_id, "phone-1")
        self.assertEqual(events[1].event_kind, "status")
        self.assertIn("delivered", events[1].external_event_id)
        self.assertEqual(events[0].profile_name, "Nome Real")

    def test_normalizes_whatsapp_status_error_details(self):
        payload = whatsapp_payload()
        value = payload["entry"][0]["changes"][0]["value"]
        value["statuses"] = [
            {
                "id": "wamid.out-1",
                "status": "failed",
                "timestamp": "1700000001",
                "recipient_id": "contact-1",
                "errors": [
                    {
                        "code": 131026,
                        "title": "Message undeliverable",
                        "message": "Message Undeliverable.",
                        "error_data": {"details": "Recipient phone number is not a WhatsApp phone number."},
                    }
                ],
            }
        ]

        event = normalize_meta_payload(payload)[1]

        self.assertEqual(event.event_kind, "status")
        self.assertEqual(event.status, "failed")
        self.assertEqual(event.error_code, "131026")
        self.assertEqual(event.error_detail["title"], "Message undeliverable")
        self.assertEqual(
            event.error_detail["error_data"]["details"],
            "Recipient phone number is not a WhatsApp phone number.",
        )

    def test_normalizes_whatsapp_button_reply_text_and_metadata(self):
        payload = whatsapp_button_reply_payload()
        event = normalize_meta_payload(payload)[0]
        self.assertEqual(event.message_type, "button")
        self.assertEqual(event.text, "Participar")
        self.assertEqual(event.metadata["button"], {"text": "Participar", "payload": "Participar"})
        self.assertEqual(event.metadata["context"]["id"], "wamid.origin-1")

    def test_normalizes_whatsapp_interactive_button_reply_text_and_metadata(self):
        payload = whatsapp_interactive_button_reply_payload()
        event = normalize_meta_payload(payload)[0]
        self.assertEqual(event.message_type, "interactive")
        self.assertEqual(event.text, "Participar")
        self.assertEqual(event.metadata["interactive"]["type"], "button_reply")
        self.assertEqual(event.metadata["interactive"]["button_reply"]["id"], "participar")

    def test_normalizes_whatsapp_profile_name_from_contacts(self):
        payload = whatsapp_payload()
        event = normalize_meta_payload(payload)[0]
        self.assertEqual(event.profile_name, "Nome Real")

    def test_normalizes_reaction_and_reply_context_without_losing_target_fields(self):
        payload = whatsapp_payload()
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message["type"] = "reaction"
        message["reaction"] = {"message_id": "wamid.target", "emoji": "❤"}
        message["context"] = {"from": "business", "id": "wamid.target"}

        event = normalize_meta_payload(payload)[0]

        assert event.metadata["reaction"] == {"message_id": "wamid.target", "emoji": "❤"}
        assert event.metadata["context"]["id"] == "wamid.target"

    def test_normalizes_whatsapp_fallback_name_when_profile_is_missing(self):
        payload = whatsapp_payload()
        value = payload["entry"][0]["changes"][0]["value"]
        value["contacts"] = []
        event = normalize_meta_payload(payload)[0]
        self.assertIsNone(event.profile_name)

    def test_shared_meta_webhook_ignores_instagram_message(self):
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "ig-business-1",
                    "messaging": [
                        {
                            "sender": {"id": "ig-user-1"},
                            "recipient": {"id": "ig-business-1"},
                            "timestamp": 1700000002,
                            "message": {"mid": "ig-mid-1", "text": "oi"},
                        }
                    ],
                }
            ],
        }

        self.assertEqual(normalize_meta_payload(payload), [])

    def test_event_without_official_id_is_ignored(self):
        self.assertEqual(normalize_meta_payload(whatsapp_payload(message_id=None)), [])


class MetaWebhookProcessingTests(unittest.IsolatedAsyncioTestCase):
    def test_persistence_materializes_message_before_commit_without_refresh(self):
        from app.api.routers import meta_webhook

        source = inspect.getsource(meta_webhook._persist_event)
        self.assertIn("await session.flush()", source)
        self.assertNotIn("await session.refresh(nova_msg)", source)

    def test_persistence_applies_whatsapp_referral_to_lead_attribution(self):
        from app.api.routers import meta_webhook

        source = inspect.getsource(meta_webhook._persist_event)
        self.assertIn("apply_meta_attribution(", source)
        self.assertIn("event.referral", source)
        self.assertIn("captured_at=", source)

    def connection(self, event, **overrides):
        data = {
            "empresa_id": "empresa-a",
            "connection_id": "connection-a",
            "channel": event.channel,
            "provider": event.provider,
        }
        data.update(overrides)
        return ResolvedMetaConnection(**data)

    async def test_accepts_known_event_and_never_calls_http(self):
        raw, signature = signed(whatsapp_payload())
        resolver = AsyncMock(side_effect=lambda event: self.connection(event))
        dedup = AsyncMock(return_value=True)

        result = await process_meta_webhook(
            raw,
            signature,
            APP_SECRET,
            resolve_connection=resolver,
            acquire_dedup=dedup,
        )

        self.assertEqual((result.received, result.accepted), (1, 1))
        resolver.assert_awaited_once()
        dedup.assert_awaited_once()

    async def test_unknown_or_ambiguous_connection_is_retryable_without_fallback(self):
        raw, signature = signed(whatsapp_payload())
        resolver = AsyncMock(return_value=None)
        dedup = AsyncMock(return_value=True)

        with self.assertRaises(MetaWebhookBatchProcessingError):
            await process_meta_webhook(
                raw,
                signature,
                APP_SECRET,
                resolve_connection=resolver,
                acquire_dedup=dedup,
            )
        dedup.assert_not_awaited()

    async def test_incompatible_resolver_result_is_retryable(self):
        raw, signature = signed(whatsapp_payload())
        resolver = AsyncMock(
            side_effect=lambda event: self.connection(
                event, channel=Channel.INSTAGRAM, provider=Provider.META_INSTAGRAM
            )
        )
        dedup = AsyncMock(return_value=True)

        with self.assertRaises(MetaWebhookBatchProcessingError):
            await process_meta_webhook(
                raw,
                signature,
                APP_SECRET,
                resolve_connection=resolver,
                acquire_dedup=dedup,
            )
        dedup.assert_not_awaited()

    async def test_duplicate_is_not_accepted_twice(self):
        raw, signature = signed(whatsapp_payload())
        resolver = AsyncMock(side_effect=lambda event: self.connection(event))
        dedup = AsyncMock(return_value=False)

        result = await process_meta_webhook(
            raw,
            signature,
            APP_SECRET,
            resolve_connection=resolver,
            acquire_dedup=dedup,
        )

        self.assertEqual((result.accepted, result.duplicates), (0, 1))

    async def test_invalid_signature_aborts_before_resolver_and_dedup(self):
        raw, _ = signed(whatsapp_payload())
        resolver = AsyncMock()
        dedup = AsyncMock()

        with patch("app.services.meta_webhook.parse_meta_payload") as parser:
            with self.assertRaises(MetaWebhookAuthenticationError):
                await process_meta_webhook(
                    raw,
                    "sha256=" + "0" * 64,
                    APP_SECRET,
                    resolve_connection=resolver,
                    acquire_dedup=dedup,
                )

        parser.assert_not_called()
        resolver.assert_not_awaited()
        dedup.assert_not_awaited()

    async def test_invalid_signature_whatsapp_never_uses_instagram_fallback(self):
        raw, _ = signed(whatsapp_payload())
        fallback = AsyncMock(return_value=True)
        with self.assertRaises(MetaWebhookAuthenticationError):
            await process_meta_webhook(
                raw,
                "sha256=" + "0" * 64,
                APP_SECRET,
                resolve_connection=AsyncMock(),
                acquire_dedup=AsyncMock(),
                fallback_authenticate=fallback,
            )
        fallback.assert_not_awaited()

    async def test_invalid_signature_on_shared_route_does_not_resume_instagram_pipeline(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "ig-business-1",
                "changes": [{"field": "comments", "value": {"id": "comment-1", "media": {"id": "media-1"}, "from": {"id": "user-1"}, "text": "interesse"}}],
            }],
        }
        raw, _ = signed(payload)
        handled = AsyncMock()
        with self.assertRaises(MetaWebhookAuthenticationError):
            await process_meta_webhook(
                raw,
                "sha256=" + "0" * 64,
                APP_SECRET,
                resolve_connection=AsyncMock(),
                acquire_dedup=AsyncMock(),
                handle_event=handled,
                request_id="request-test",
                fallback_authenticate=AsyncMock(return_value=True),
            )
        handled.assert_not_awaited()

    async def test_redis_lock_is_tenant_connection_scoped_and_fail_closed(self):
        event = CanonicalMetaWebhookEvent(
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
            external_account_id="phone-1",
            external_event_id="wamid.test-1",
            event_kind="message",
        )
        connection = self.connection(event)
        redis = AsyncMock()
        redis.set.return_value = True

        self.assertTrue(await acquire_meta_event_lock(redis, event, connection))
        key = redis.set.await_args.args[0]
        self.assertEqual(key, meta_event_dedup_key(event, connection))
        self.assertNotIn("wamid.test-1", key)

        redis.set.return_value = False
        self.assertFalse(await acquire_meta_event_lock(redis, event, connection))
        self.assertTrue(redis.set.await_args.kwargs["nx"])

    async def test_event_without_id_never_reaches_resolver_or_dedup(self):
        raw, signature = signed(whatsapp_payload(message_id=None))
        resolver = AsyncMock()
        dedup = AsyncMock()

        result = await process_meta_webhook(
            raw,
            signature,
            APP_SECRET,
            resolve_connection=resolver,
            acquire_dedup=dedup,
        )

        self.assertEqual(result.received, 0)
        self.assertEqual(result.parser_drops, 1)
        resolver.assert_not_awaited()
        dedup.assert_not_awaited()

    async def test_distinct_status_and_instagram_ids_have_distinct_dedup_keys(self):
        whatsapp = CanonicalMetaWebhookEvent(
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
            external_account_id="phone-1",
            external_event_id="wamid.out:status:read:1",
            event_kind="status",
        )
        instagram = CanonicalMetaWebhookEvent(
            channel=Channel.INSTAGRAM,
            provider=Provider.META_INSTAGRAM,
            external_account_id="ig-business-1",
            external_event_id="ig-mid-1",
            event_kind="message",
        )
        self.assertNotEqual(
            meta_event_dedup_key(whatsapp, self.connection(whatsapp)),
            meta_event_dedup_key(instagram, self.connection(instagram)),
        )


class MetaConnectionResolverTests(unittest.IsolatedAsyncioTestCase):
    class Result:
        def __init__(self, matches):
            self.matches = matches

        def scalars(self):
            return self

        def all(self):
            return self.matches

    class Session:
        def __init__(self, matches):
            self.matches = matches
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return MetaConnectionResolverTests.Result(self.matches)

    def event(self, channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP, account="phone-1"):
        return CanonicalMetaWebhookEvent(
            channel=channel,
            provider=provider,
            external_account_id=account,
            external_event_id="event-1",
            event_kind="message",
        )

    async def test_resolver_requires_single_whatsapp_match_and_all_scope_fields(self):
        row = SimpleNamespace(empresa_id="empresa-a", id="connection-a")
        session = self.Session([row])

        resolved = await resolve_meta_connection(session, self.event())

        self.assertEqual(resolved.connection_id, "connection-a")
        statement = str(session.statement)
        for fragment in ("conexoes.phone_number_id", "conexoes.channel", "conexoes.provider", "conexoes.status"):
            self.assertIn(fragment, statement)

    async def test_resolver_rejects_zero_or_multiple_matches_without_fallback(self):
        self.assertIsNone(await resolve_meta_connection(self.Session([]), self.event()))
        rows = [
            SimpleNamespace(empresa_id="empresa-a", id="connection-a"),
            SimpleNamespace(empresa_id="empresa-b", id="connection-b"),
        ]
        self.assertIsNone(await resolve_meta_connection(self.Session(rows), self.event()))

    async def test_instagram_resolver_uses_business_account_id(self):
        session = self.Session([])
        await resolve_meta_connection(
            session,
            self.event(Channel.INSTAGRAM, Provider.META_INSTAGRAM, "ig-business-1"),
        )
        self.assertIn("conexoes.instagram_business_account_id", str(session.statement))


class MetaWebhookVerificationEndpointTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request():
        return Request({
            "type": "http", "method": "GET", "path": "/api/webhook/meta", "headers": [],
            "query_string": b"hub.mode=subscribe&hub.verify_token=verify-test&hub.challenge=12345",
            "server": ("test", 80), "client": ("test", 1), "scheme": "http", "root_path": "",
        })

    async def test_get_verification_returns_plain_challenge(self):
        with patch.dict(os.environ, {"META_WEBHOOK_VERIFY_TOKEN": "verify-test"}, clear=False):
            response = await verify_meta_webhook(self._request(), mode="subscribe", token="verify-test", challenge="12345")
        self.assertEqual(response.body, b"12345")

    async def test_get_verification_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as context:
                await verify_meta_webhook(self._request(), mode="subscribe", token="anything", challenge="12345")
        self.assertEqual(context.exception.status_code, 503)

    async def test_get_rejects_invalid_mode_token_and_empty_challenge(self):
        with patch.dict(os.environ, {"META_WEBHOOK_VERIFY_TOKEN": "verify-test"}, clear=False):
            cases = (
                ("unsubscribe", "verify-test", "12345"),
                ("subscribe", "wrong", "12345"),
                ("subscribe", "verify-test", ""),
            )
            for mode, token, challenge in cases:
                with self.subTest(mode=mode, token=token, challenge=challenge):
                    with self.assertRaises(HTTPException) as context:
                        await verify_meta_webhook(self._request(), mode=mode, token=token, challenge=challenge)
                    self.assertEqual(context.exception.status_code, 403)

    async def test_post_route_acknowledges_oversize_drop_without_reading_or_processing(self):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/webhook/meta",
            "headers": [(b"content-length", str(3 * 1024 * 1024).encode())],
        }
        request = Request(scope)
        with patch("app.api.routers.meta_webhook.process_meta_webhook") as processor:
            with self.assertLogs("app.api.routers.meta_webhook", level="INFO") as captured:
                response = await receive_meta_webhook(request)
        self.assertEqual(response, {"status": "accepted"})
        self.assertIn("outcome=oversize_limit_unverified http_status=200", "\n".join(captured.output))
        processor.assert_not_called()

    async def test_post_route_stream_cap_handles_missing_or_undersized_content_length(self):
        limit = receive_meta_webhook.__globals__["_svc"].META_WEBHOOK_MAX_BODY_BYTES
        chunks = [b"a" * (limit // 2), b"b" * (limit // 2), b"c" * 8]
        for content_length in (None, "8"):
            with self.subTest(content_length=content_length):
                delivered = 0

                async def receive():
                    nonlocal delivered
                    chunk = chunks[delivered]
                    delivered += 1
                    return {"type": "http.request", "body": chunk, "more_body": delivered < len(chunks)}

                headers = [] if content_length is None else [(b"content-length", content_length.encode())]
                request = Request({
                    "type": "http", "method": "POST", "path": "/api/webhook/meta", "headers": headers,
                }, receive)
                with patch("app.api.routers.meta_webhook.process_meta_webhook") as processor:
                    with patch.object(Request, "body", autospec=True, side_effect=AssertionError("unbounded body read")) as body_reader:
                        with self.assertLogs("app.api.routers.meta_webhook", level="INFO") as captured:
                            response = await receive_meta_webhook(request)
                self.assertEqual(response, {"status": "accepted"})
                self.assertEqual(delivered, len(chunks))
                self.assertIn("outcome=oversize_limit_unverified http_status=200", "\n".join(captured.output))
                body_reader.assert_not_awaited()
                processor.assert_not_called()

    async def test_post_response_and_logs_do_not_expose_body_or_secret(self):
        payload_secret = "sensitive-message-body"
        body = json.dumps({"object": "unknown", "secret": payload_secret}).encode()
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/webhook/meta",
            "headers": [(b"x-hub-signature-256", b"sha256=" + b"0" * 64)],
        }
        request = Request(scope, receive)
        mocked_result = MetaWebhookProcessResult(received=1, accepted=0, duplicates=0, unresolved=1, parser_drops=1)
        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with patch("app.api.routers.meta_webhook.process_meta_webhook", AsyncMock(return_value=mocked_result)):
                with self.assertLogs("app.api.routers.meta_webhook", level="INFO") as logs:
                    response = await receive_meta_webhook(request)

        self.assertEqual(response, {"status": "accepted"})
        rendered = "\n".join(logs.output)
        self.assertNotIn(payload_secret, rendered)
        self.assertNotIn(APP_SECRET, rendered)
        self.assertIn("outcome=partial_or_ignored", rendered)
        self.assertIn("parser_drops=1", rendered)

    async def test_signed_post_has_no_dispatch_history_write_or_migration(self):
        payload = whatsapp_payload()
        raw, signature = signed(payload)
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/webhook/meta",
            "headers": [(b"x-hub-signature-256", signature.encode())],
        }
        request = Request(scope, receive)
        connection = ResolvedMetaConnection(
            empresa_id="empresa-a",
            connection_id="connection-a",
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
        )

        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with patch(
                "app.api.routers.meta_webhook._resolve_event_connection",
                AsyncMock(return_value=connection),
            ) as resolver:
                with patch(
                    "app.api.routers.meta_webhook._deduplicate_event",
                    AsyncMock(return_value=True),
                ) as dedup:
                    with patch("app.api.routers.meta_webhook._persist_event", AsyncMock(return_value=False)) as persist:
                        with patch("app.services.channel_factory.despachar_mensagem", AsyncMock()) as dispatch:
                            with patch("app.api.utils._salvar_historico_saida_ia", AsyncMock()) as save_history:
                                with patch("db.manual_migrations.ensure_meta_capi_tables", AsyncMock(), create=True) as migration:
                                    response = await receive_meta_webhook(request)

        self.assertEqual(response, {"status": "accepted"})
        resolver.assert_awaited_once()
        dedup.assert_awaited_once()
        persist.assert_awaited_once()
        dispatch.assert_not_awaited()
        save_history.assert_not_awaited()
        migration.assert_not_awaited()

    async def test_panel_webhook_path_remains_legacy_and_is_not_cut_over(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "scheme": "https",
            "server": ("agentos.example", 443),
            "headers": [],
        }
        request = Request(scope)

        url, path = _montar_webhook_url(request, "empresa-a", TipoConexao.META)

        self.assertEqual(path, "/api/webhook/empresa-a/meta")
        self.assertTrue(url.endswith(path))
        self.assertNotEqual(path, "/api/webhook/meta")


if __name__ == "__main__":
    unittest.main()
