from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
import unittest
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.routers import meta_webhook as router
from app.services.channels.types import Channel, Provider
from app.services.meta_webhook import (
    MetaWebhookAuthenticationError,
    MetaWebhookBatchProcessingError,
    MetaWebhookProcessingError,
    MetaWebhookProcessResult,
    ResolvedMetaConnection,
    process_meta_webhook,
)


APP_SECRET = "unit-test-meta-secret"


def _signed(payload: dict) -> tuple[bytes, str]:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={digest}"


def _payload(*, include_message: bool = True, include_status: str | None = None) -> dict:
    value = {
        "metadata": {"phone_number_id": "phone-safe-test"},
        "contacts": [{"wa_id": "contact-safe-test", "profile": {"name": "Test"}}],
    }
    if include_message:
        value["messages"] = [{
            "id": "wamid.safe-test-1", "from": "contact-safe-test", "timestamp": "1700000000",
            "type": "text", "text": {"body": "private body that must not be logged"},
        }]
    if include_status:
        value["statuses"] = [{
            "id": "wamid.safe-out-1", "status": include_status, "timestamp": "1700000001",
            "recipient_id": "contact-safe-test",
        }]
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba-safe", "changes": [{"field": "messages", "value": value}]}]}


def _connection() -> ResolvedMetaConnection:
    return ResolvedMetaConnection(
        empresa_id="5a20573a-b056-4aac-9bd2-45e96d1a57c6",
        connection_id="f9d33db6-9c18-4be3-96e9-342a86e7d37a",
        channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
    )


def _inbound_event(*, message_type: str = "text", referral: dict | None = None):
    from app.services.meta_webhook import CanonicalMetaWebhookEvent

    return CanonicalMetaWebhookEvent(
        channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
        external_account_id="waba-safe-test", external_event_id="wamid.safe-test-1",
        external_message_id="wamid.safe-test-1", sender_id="5511999999999",
        event_kind="message", message_type=message_type, text="safe test message",
        timestamp="1700000000", referral=referral, request_id="request-safe-test",
    )


class _MemorySession:
    """Small async-session double for transaction-boundary behavior tests."""

    def __init__(self, *, flush_error: Exception | None = None, commit_error: Exception | None = None, rows: dict | None = None):
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.rows = rows or {}
        self.added = []
        self.commit = AsyncMock(side_effect=commit_error)
        self.rollback = AsyncMock()
        self.in_transaction = unittest.mock.Mock(return_value=True)
        self.get = AsyncMock(side_effect=lambda model, _identity: self.rows.get(model))
        self.flush = AsyncMock(side_effect=self._flush)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False

    def add(self, row):
        self.added.append(row)

    async def _flush(self):
        if self.flush_error is not None:
            raise self.flush_error
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()
            if getattr(row, "criado_em", None) is None:
                row.criado_em = datetime(2026, 10, 7, 12, 0, 0)
            self.rows[type(row)] = row


class _MemorySessionFactory:
    def __init__(self, *sessions: _MemorySession):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


async def _request(body: bytes = b"{}", signature: str | None = None) -> Request:
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"x-hub-signature-256", signature.encode())] if signature else []
    return Request({"type": "http", "method": "POST", "path": "/api/webhook/meta", "headers": headers}, receive)


async def _persist_test_inbound(
    *, event=None, existing_message=None, flush_error=None, commit_error=None,
    realtime_error=None, followup_error=None, monitor_error=None,
    conversion_error=None, lead_created=False, agent_error=None,
):
    from db.models import CRMLead, ChannelContact, ChannelConversation, Empresa, MensagemHistorico

    event = event or _inbound_event()
    operational = _connection()
    lead = SimpleNamespace(id=uuid.uuid4(), nome_contato="Test", dados_adicionais={})
    contact = SimpleNamespace(id=uuid.uuid4())
    conversation = SimpleNamespace(
        id=uuid.uuid4(), lead_id=lead.id, conversation_key="whatsapp:meta:test-contact",
        external_conversation_id="test-contact",
    )
    empresa = SimpleNamespace(id=uuid.UUID(operational.empresa_id))
    op_conversation = SimpleNamespace(conversation=conversation, contact=contact)
    rows = {
        Empresa: empresa, CRMLead: lead, ChannelConversation: conversation,
        ChannelContact: contact,
    }
    core = _MemorySession(flush_error=flush_error, commit_error=commit_error, rows=rows)
    side = _MemorySession(rows=rows)
    ai = _MemorySession(rows=rows)
    factory = _MemorySessionFactory(core, side, ai)
    broadcast = AsyncMock(return_value=0, side_effect=realtime_error)
    lead_lookup = AsyncMock(return_value=(lead, lead_created))
    monitor = AsyncMock(side_effect=monitor_error, return_value=SimpleNamespace(state="active" if agent_error else "inactive"))
    debouncer = AsyncMock(side_effect=agent_error)
    followup = AsyncMock(side_effect=followup_error)
    conversion = AsyncMock(side_effect=conversion_error)

    with ExitStack() as stack:
        stack.enter_context(patch.object(router, "AsyncSessionLocal", factory))
        stack.enter_context(patch.object(router, "_lock_meta_event", AsyncMock()))
        stack.enter_context(patch.object(router, "_find_canonical_inbound", AsyncMock(side_effect=[existing_message, None] if existing_message is not None else [None, None])))
        stack.enter_context(patch.object(router, "get_or_create_lead", lead_lookup))
        stack.enter_context(patch.object(router, "ensure_operational_conversation", AsyncMock(return_value=op_conversation)))
        stack.enter_context(patch.object(router.manager, "broadcast_to_empresa", broadcast))
        stack.enter_context(patch.object(router, "resolve_conversation_ai_state", monitor))
        stack.enter_context(patch.object(router, "handle_debouncer", debouncer))
        stack.enter_context(patch("app.services.followup_engine.registrar_followup_message_event", followup))
        stack.enter_context(patch("app.services.meta_attribution.apply_meta_attribution"))
        stack.enter_context(patch("app.services.meta.business_messaging_conversion_engine.emit_conversion_domain_event", conversion))
        try:
            result = await router._persist_event(event, operational)
        except Exception as exc:
            # Expose the simulated session so failure tests can verify rollback.
            exc._test_core_session = core
            raise
    message = core.added[0] if core.added else None
    return result, core, side, ai, broadcast, followup, monitor, debouncer, conversion, message


class MetaWebhookDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_unparseable_payload_is_sanitized_and_acknowledged(self):
        raw = b"not-json"
        signature = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        resolver = AsyncMock()
        dedup = AsyncMock()

        with self.assertLogs("app.services.meta_webhook", level="WARNING") as captured:
            result = await process_meta_webhook(
                raw, signature, APP_SECRET,
                resolve_connection=resolver,
                acquire_dedup=dedup,
                request_id="request-safe-test",
            )

        self.assertEqual((result.received, result.parser_drops), (0, 1))
        resolver.assert_not_awaited()
        dedup.assert_not_awaited()
        output = "\n".join(captured.output)
        self.assertIn("invalid_json_or_shape:1", output)
        self.assertIn("outcome=authenticated_unparseable_payload", output)
        self.assertIn("http_status=200", output)

        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with self.assertLogs("app.api.routers.meta_webhook", level="INFO") as route_logs:
                response = await router.receive_meta_webhook(await _request(raw, signature))
        self.assertEqual(response, {"status": "accepted"})
        self.assertIn("http_status=200 outcome=partial_or_ignored", "\n".join(route_logs.output))

        with self.assertRaises(MetaWebhookAuthenticationError):
            await process_meta_webhook(
                raw, None, APP_SECRET,
                resolve_connection=resolver,
                acquire_dedup=dedup,
                request_id="request-invalid-signature",
            )

    async def test_redis_lease_contention_is_not_durable_duplicate_proof(self):
        raw, signature = _signed(_payload())
        handler = AsyncMock(return_value=False)
        result = await process_meta_webhook(
            raw, signature, APP_SECRET,
            resolve_connection=AsyncMock(return_value=_connection()),
            acquire_dedup=AsyncMock(return_value=False),
            handle_event=handler,
            request_id="request-safe-test",
        )
        self.assertEqual((result.accepted, result.duplicates), (1, 0))
        handler.assert_awaited_once()

    async def test_callback_classification_logs_counts_without_message_content(self):
        raw, signature = _signed(_payload(include_status="delivered"))
        with self.assertLogs("app.services.meta_webhook", level="INFO") as captured:
            await process_meta_webhook(
                raw, signature, APP_SECRET,
                resolve_connection=AsyncMock(return_value=_connection()),
                acquire_dedup=AsyncMock(return_value=False),
                handle_event=AsyncMock(return_value=False),
                request_id="request-safe-test",
            )
        output = "\n".join(captured.output)
        self.assertIn("event=META_WEBHOOK_CLASSIFIED", output)
        self.assertIn("event_kinds=message:1,status:1", output)
        self.assertNotIn("private body", output)

    async def test_parser_drop_reasons_are_logged_and_permanently_unprocessable_items_acknowledged(self):
        scenarios = [
            ("missing_message_id", lambda value: value["messages"][0].update({"id": ""})),
            ("missing_phone_number_id", lambda value: value.pop("metadata")),
        ]
        for expected_reason, mutate in scenarios:
            payload = _payload()
            mutate(payload["entry"][0]["changes"][0]["value"])
            raw, signature = _signed(payload)
            with self.subTest(reason=expected_reason):
                with self.assertLogs("app.services.meta_webhook", level="INFO") as captured:
                    result = await process_meta_webhook(
                        raw, signature, APP_SECRET,
                        resolve_connection=AsyncMock(return_value=_connection()),
                        acquire_dedup=AsyncMock(return_value=False),
                        handle_event=AsyncMock(return_value=False),
                        request_id="request-safe-test",
                    )
                output = "\n".join(captured.output)
                self.assertEqual(result.accepted, 0)
                self.assertEqual(result.parser_drops, 1)
                self.assertIn("normalized_count=0", output)
                self.assertIn(f"parser_drops={expected_reason}:1", output)
                self.assertIn("event=META_WEBHOOK_PARSER_DROP_ACK", output)
                self.assertIn("http_status=200", output)
                self.assertNotIn("private body", output)

    async def test_valid_sibling_is_processed_when_another_message_item_is_malformed(self):
        payload = _payload()
        value = payload["entry"][0]["changes"][0]["value"]
        value["messages"].insert(0, {"id": "", "from": "contact", "type": "text"})
        raw, signature = _signed(payload)
        handler = AsyncMock(return_value=False)

        with self.assertLogs("app.services.meta_webhook", level="WARNING") as captured:
            result = await process_meta_webhook(
                raw, signature, APP_SECRET,
                resolve_connection=AsyncMock(return_value=_connection()),
                acquire_dedup=AsyncMock(return_value=False),
                handle_event=handler,
                request_id="request-safe-test",
            )

        self.assertEqual((result.received, result.accepted, result.parser_drops), (1, 1, 1))
        handler.assert_awaited_once()
        self.assertIn("http_status=200", "\n".join(captured.output))

    async def test_committed_canonical_duplicate_is_acknowledgeable(self):
        raw, signature = _signed(_payload())
        handler = AsyncMock(return_value=True)
        result = await process_meta_webhook(
            raw, signature, APP_SECRET,
            resolve_connection=AsyncMock(return_value=_connection()),
            acquire_dedup=AsyncMock(return_value=False),
            handle_event=handler,
            request_id="request-safe-test",
        )
        self.assertEqual((result.accepted, result.duplicates), (0, 1))
        handler.assert_awaited_once()

    async def test_status_failure_does_not_skip_message_in_same_callback(self):
        payload = _payload(include_message=False, include_status="read")
        second_change = _payload(include_message=True, include_status=None)["entry"][0]["changes"][0]
        payload["entry"][0]["changes"].append(second_change)
        raw, signature = _signed(payload)
        visited: list[str] = []

        async def handle(event, _connection):
            visited.append(event.event_kind)
            if event.event_kind == "status":
                raise MetaWebhookProcessingError(
                    stage="status_update", event_kind="status", exception_type="IntegrityError",
                    sqlstate="23505", constraint="uq_message_status_events_idempotency", retryable=True,
                )
            return False

        with self.assertRaises(MetaWebhookBatchProcessingError):
            await process_meta_webhook(
                raw, signature, APP_SECRET,
                resolve_connection=AsyncMock(return_value=_connection()),
                acquire_dedup=AsyncMock(return_value=False),
                handle_event=handle,
                request_id="request-safe-test",
            )
        self.assertEqual(visited, ["status", "message"])

    async def test_status_read_and_delivered_are_classified_without_becoming_messages(self):
        seen: list[tuple[str, str | None]] = []

        async def handle(event, _connection):
            seen.append((event.event_kind, event.status))
            return False

        for status in ("delivered", "read"):
            raw, signature = _signed(_payload(include_message=False, include_status=status))
            await process_meta_webhook(
                raw, signature, APP_SECRET,
                resolve_connection=AsyncMock(return_value=_connection()),
                acquire_dedup=AsyncMock(return_value=False),
                handle_event=handle,
                request_id=f"request-{status}",
            )
        self.assertEqual(seen, [("status", "delivered"), ("status", "read")])

    async def test_failed_integrity_error_returns_5xx_not_success_ack(self):
        failure = MetaWebhookBatchProcessingError([
            MetaWebhookProcessingError(
                stage="message_insert", event_kind="message", exception_type="IntegrityError",
                sqlstate="23505", constraint="unknown_constraint", retryable=True,
            ),
        ])
        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with patch.object(router, "process_meta_webhook", AsyncMock(side_effect=failure)):
                with self.assertRaises(HTTPException) as caught:
                    await router.receive_meta_webhook(await _request())
        self.assertEqual(caught.exception.status_code, 500)

    async def test_transient_database_failure_returns_retryable_503(self):
        failure = MetaWebhookBatchProcessingError([
            MetaWebhookProcessingError(
                stage="commit", event_kind="message", exception_type="OperationalError",
                sqlstate="08006", constraint=None, retryable=True,
            ),
        ])
        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with patch.object(router, "process_meta_webhook", AsyncMock(side_effect=failure)):
                with self.assertRaises(HTTPException) as caught:
                    await router.receive_meta_webhook(await _request())
        self.assertEqual(caught.exception.status_code, 503)

    def test_verification_route_uses_meta_hub_query_parameter_names(self):
        app = FastAPI()
        app.include_router(router.router)
        with patch.dict(os.environ, {"META_WEBHOOK_VERIFY_TOKEN": "verify-safe-test"}, clear=False):
            response = TestClient(app).get(
                "/webhook/meta?hub.mode=subscribe&hub.verify_token=verify-safe-test&hub.challenge=challenge-safe"
            )
            rejected = TestClient(app).get(
                "/webhook/meta?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=challenge-safe"
            )
        self.assertEqual((response.status_code, response.text), (200, "challenge-safe"))
        self.assertEqual(rejected.status_code, 403)
        scope = {
            "type": "http", "method": "GET", "path": "/webhook/meta", "headers": [],
            "query_string": b"hub.mode=subscribe&hub.verify_token=verify-safe-test&hub.challenge=challenge-safe",
            "server": ("test", 80), "client": ("test", 1), "scheme": "http", "root_path": "",
        }
        request = Request(scope, lambda: None)
        with patch.dict(os.environ, {"META_WEBHOOK_VERIFY_TOKEN": "verify-safe-test"}, clear=False):
            verified = asyncio.run(router.verify_meta_webhook(
                request, mode="subscribe", token="verify-safe-test", challenge="challenge-safe"
            ))
        self.assertEqual(verified.body, b"challenge-safe")
        self.assertEqual(request.scope["query_string"], b"")

    def test_core_commit_precedes_isolated_side_effects_and_realtime(self):
        import inspect

        source = inspect.getsource(router._persist_event)
        commit = source.index("await session.commit()", source.index("nova_msg = MensagemHistorico"))
        self.assertLess(commit, source.index("await manager.broadcast_to_empresa", commit))
        self.assertLess(commit, source.index("emit_conversion_domain_event", commit))
        self.assertLess(commit, source.index("registrar_followup_message_event", commit))
        self.assertNotIn("Inbound Meta compatibility handler skipped", source)
        self.assertIn('stage = "status_update" if event.event_kind == "status" else "idempotency"', source)
        self.assertIn('stage = "status_update" if event.event_kind == "status" else "idempotency"', source)

    def test_lead_resolution_prefers_canonical_phone_before_legacy_update(self):
        import inspect
        from app.services import lead_service

        source = inspect.getsource(lead_service.get_or_create_lead)
        self.assertLess(source.index("canonical_result = await session.execute"), source.index("lead_existente = result.scalars().first()"))
        self.assertIn('constraint != "idx_unique_lead_telefone"', source)
        self.assertIn("session.begin_nested()", source)

    async def test_unknown_failure_telemetry_has_sqlstate_without_message_or_phone(self):
        class _NativeAsyncpgError(Exception):
            constraint_name = "idx_unique_lead_telefone"

        class _AdapterError(Exception):
            sqlstate = "23505"

            def __init__(self):
                super().__init__("adapted asyncpg integrity error")
                self.__cause__ = _NativeAsyncpgError("duplicate")

        from app.services.meta_webhook import _processing_failure
        from app.services.meta_webhook import CanonicalMetaWebhookEvent

        event = CanonicalMetaWebhookEvent(
            channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
            external_account_id="private-phone", external_event_id="private-message-id",
            event_kind="message", message_type="text", text="private message body",
            request_id="request-safe-test",
        )
        error = RuntimeError("private-phone private message body")
        error.orig = _AdapterError()
        failure = _processing_failure(event, "contact_resolution", error)
        self.assertEqual((failure.sqlstate, failure.constraint), ("23505", "idx_unique_lead_telefone"))

    async def test_failure_ack_log_uses_actual_retryable_http_status(self):
        failure = MetaWebhookBatchProcessingError([
            MetaWebhookProcessingError(
                stage="commit", event_kind="message", exception_type="OperationalError",
                sqlstate="08006", constraint=None, retryable=True,
            ),
        ])
        with patch.dict(os.environ, {"META_APP_SECRET": APP_SECRET}, clear=False):
            with patch.object(router, "process_meta_webhook", AsyncMock(side_effect=failure)):
                with self.assertLogs("app.api.routers.meta_webhook", level="ERROR") as captured:
                    with self.assertRaises(HTTPException) as caught:
                        await router.receive_meta_webhook(await _request())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("event=META_WEBHOOK_FAILED", "\n".join(captured.output))
        self.assertIn("http_status=503", "\n".join(captured.output))

    async def test_legacy_phone_conflict_resolves_to_existing_canonical_lead(self):
        import uuid
        from app.services.lead_service import get_or_create_lead

        class _Result:
            def __init__(self, row=None):
                self.row = row

            def scalars(self):
                return self

            def first(self):
                return self.row

        class _Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _type, _value, _traceback):
                return False

        class _Diag:
            constraint_name = "idx_unique_lead_telefone"

        class _NativeAsyncpgError(Exception):
            # asyncpg exposes this directly, behind SQLAlchemy's adapter cause.
            constraint_name = "idx_unique_lead_telefone"

        class _Original(Exception):
            sqlstate = "23505"

            def __init__(self):
                super().__init__("adapted asyncpg integrity error")
                self.__cause__ = _NativeAsyncpgError("duplicate")

        error = __import__("sqlalchemy.exc", fromlist=["IntegrityError"]).IntegrityError(
            "UPDATE crm_leads", {}, _Original()
        )
        tenant_id = "5a20573a-b056-4aac-9bd2-45e96d1a57c6"
        old_phone = "551188888888"
        old_lead = SimpleNamespace(id=uuid.uuid4(), telefone_contato=old_phone, nome_contato="Old", dados_adicionais={})
        canonical_lead = SimpleNamespace(id=uuid.uuid4(), telefone_contato="5511988888888", nome_contato="Canonical", dados_adicionais={})
        rows = [_Result(), _Result(), _Result(old_lead), _Result(canonical_lead)]
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=rows),
            begin_nested=lambda: _Savepoint(),
            flush=AsyncMock(side_effect=error),
        )

        result, created = await get_or_create_lead(
            session, empresa_id=tenant_id, telefone=old_phone, nome_inicial="Test"
        )
        self.assertIs(result, canonical_lead)
        self.assertFalse(created)

    async def test_new_lead_known_phone_unique_race_returns_canonical_winner(self):
        from app.services.lead_service import get_or_create_lead
        from sqlalchemy.exc import IntegrityError

        class _Result:
            def __init__(self, row=None):
                self.row = row

            def scalars(self):
                return self

            def first(self):
                return self.row

        class _Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _type, _value, _traceback):
                return False

        class _NativeAsyncpgError(Exception):
            constraint_name = "idx_unique_lead_telefone"

        class _AdapterError(Exception):
            sqlstate = "23505"

            def __init__(self):
                self.__cause__ = _NativeAsyncpgError("phone duplicate")

        winner = SimpleNamespace(id=uuid.uuid4(), telefone_contato="5511999999999")
        error = IntegrityError("INSERT INTO crm_leads", {}, _AdapterError())
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result(), _Result(), _Result(), _Result(), _Result(winner)]),
            begin_nested=lambda: _Savepoint(), add=unittest.mock.Mock(),
            flush=AsyncMock(side_effect=error), rollback=AsyncMock(),
        )

        result, created = await get_or_create_lead(
            session, empresa_id="5a20573a-b056-4aac-9bd2-45e96d1a57c6",
            telefone="5511999999999", nome_inicial="Test",
        )
        self.assertIs(result, winner)
        self.assertFalse(created)
        session.rollback.assert_not_awaited()

    async def test_inbound_persistence_commits_and_zero_socket_target_is_safe(self):
        for message_type in ("text", "interactive"):
            with self.subTest(message_type=message_type):
                result, core, _side, _ai, broadcast, followup, _monitor, _debouncer, _conversion, message = await _persist_test_inbound(event=_inbound_event(message_type=message_type))
                self.assertFalse(result)
                self.assertIsNotNone(message)
                self.assertEqual(message.tipo_mensagem, message_type)
                core.commit.assert_awaited_once()
                core.rollback.assert_not_awaited()
                broadcast.assert_awaited_once()
                self.assertEqual(broadcast.await_args.args[1]["type"], "inbox.message.created")
                self.assertTrue(followup.await_count)

    async def test_confirmed_inbound_duplicate_is_not_inserted_twice(self):
        existing = SimpleNamespace(id=uuid.uuid4())
        result, core, _side, _ai, broadcast, followup, _monitor, _debouncer, _conversion, message = await _persist_test_inbound(existing_message=existing)
        self.assertTrue(result)
        self.assertIsNone(message)
        self.assertEqual(core.added, [])
        core.commit.assert_not_awaited()
        core.rollback.assert_awaited_once()
        broadcast.assert_not_awaited()
        followup.assert_not_awaited()

    async def test_precommit_integrity_and_operational_errors_rollback_and_are_retryable(self):
        from sqlalchemy.exc import IntegrityError, OperationalError
        from app.services.meta_webhook import MetaWebhookProcessingError, _processing_http_status

        class _DatabaseError(Exception):
            def __init__(self, sqlstate, constraint):
                self.sqlstate = sqlstate
                self.constraint_name = constraint

        cases = [
            ("23503", "fk_message_lead", IntegrityError, "message_insert", "flush_error"),
            ("23502", None, IntegrityError, "message_insert", "flush_error"),
            ("23514", "ck_message_direction", IntegrityError, "message_insert", "flush_error"),
            ("08006", None, OperationalError, "message_insert", "flush_error"),
            ("08006", None, OperationalError, "commit", "commit_error"),
        ]
        for sqlstate, constraint, error_type, expected_stage, error_field in cases:
            with self.subTest(sqlstate=sqlstate):
                failure = error_type("INSERT message", {}, _DatabaseError(sqlstate, constraint))
                with self.assertRaises(MetaWebhookProcessingError) as caught:
                    await _persist_test_inbound(**{error_field: failure})
                self.assertEqual(caught.exception.stage, expected_stage)
                caught.exception._test_core_session.rollback.assert_awaited_once()
                self.assertEqual(caught.exception.sqlstate, sqlstate)
                self.assertEqual(caught.exception.constraint, constraint)
                self.assertEqual(_processing_http_status(caught.exception.exception_type, sqlstate), 503 if sqlstate.startswith("08") else 500)

    async def test_retry_after_precommit_failure_can_commit_once_then_duplicate(self):
        from sqlalchemy.exc import OperationalError

        class _Unavailable(Exception):
            sqlstate = "08006"

        with self.assertRaises(MetaWebhookProcessingError) as caught:
            await _persist_test_inbound(flush_error=OperationalError("INSERT", {}, _Unavailable("db unavailable")))
        caught.exception._test_core_session.rollback.assert_awaited_once()
        first = await _persist_test_inbound()
        duplicate = await _persist_test_inbound(existing_message=first[-1])
        self.assertFalse(first[0])
        self.assertIsNotNone(first[-1])
        first[1].commit.assert_awaited_once()
        self.assertTrue(duplicate[0])
        duplicate[1].commit.assert_not_awaited()
        self.assertEqual(len(first[1].added) + len(duplicate[1].added), 1)

    async def test_postcommit_realtime_followup_monitor_conversion_and_agent_failures_do_not_rollback(self):
        scenarios = [
            {"realtime_error": RuntimeError("socket")},
            {"followup_error": RuntimeError("followup")},
            {"monitor_error": RuntimeError("monitor")},
            {"agent_error": RuntimeError("tag or agent pipeline")},
            {
                "event": _inbound_event(referral={"ctwa_clid": "ctwa-safe"}),
                "lead_created": True,
                "conversion_error": RuntimeError("conversion"),
            },
        ]
        for scenario in scenarios:
            with self.subTest(stage=next((key for key in scenario if key.endswith("error")), "conversion")):
                result, core, _side, _ai, broadcast, followup, monitor, debouncer, conversion, message = await _persist_test_inbound(**scenario)
                self.assertFalse(result)
                self.assertIsNotNone(message)
                core.commit.assert_awaited_once()
                core.rollback.assert_not_awaited()
                broadcast.assert_awaited_once()
                if scenario.get("agent_error"):
                    debouncer.assert_awaited_once()
                if scenario.get("monitor_error"):
                    monitor.assert_awaited_once()
                if scenario.get("followup_error"):
                    followup.assert_awaited_once()
                if scenario.get("conversion_error"):
                    conversion.assert_awaited_once()

    async def test_status_persistence_and_duplicate_path_are_isolated(self):
        from dataclasses import replace
        from app.services.meta_webhook import CanonicalMetaWebhookEvent

        event = replace(_inbound_event(), event_kind="status", status="delivered")
        core = _MemorySession()
        with ExitStack() as stack:
            stack.enter_context(patch.object(router, "AsyncSessionLocal", _MemorySessionFactory(core)))
            stack.enter_context(patch.object(router, "_lock_meta_event", AsyncMock()))
            stack.enter_context(patch.object(router, "_status_event_exists", AsyncMock(return_value=False)))
            record = stack.enter_context(patch.object(router, "record_message_status_event", AsyncMock()))
            result = await router._persist_event(event, _connection())
        self.assertFalse(result)
        record.assert_awaited_once()
        core.commit.assert_awaited_once()

        duplicate_session = _MemorySession()
        with ExitStack() as stack:
            stack.enter_context(patch.object(router, "AsyncSessionLocal", _MemorySessionFactory(duplicate_session)))
            stack.enter_context(patch.object(router, "_lock_meta_event", AsyncMock()))
            stack.enter_context(patch.object(router, "_status_event_exists", AsyncMock(return_value=True)))
            record = stack.enter_context(patch.object(router, "record_message_status_event", AsyncMock()))
            duplicate = await router._persist_event(event, _connection())
        self.assertTrue(duplicate)
        record.assert_not_awaited()
        duplicate_session.commit.assert_not_awaited()
        duplicate_session.rollback.assert_awaited_once()

    async def test_status_unique_integrity_error_ack_requires_postgres_duplicate_proof(self):
        from dataclasses import replace
        from app.services.meta_webhook import MetaWebhookProcessingError
        from sqlalchemy.exc import IntegrityError

        class _AsyncpgUniqueViolation(Exception):
            constraint_name = "uq_message_status_events_idempotency"
            sqlstate = "23505"

        event = replace(_inbound_event(), event_kind="status", status="delivered")
        core = _MemorySession(flush_error=IntegrityError("INSERT status", {}, _AsyncpgUniqueViolation("duplicate")))
        proof_session = _MemorySession()
        with ExitStack() as stack:
            stack.enter_context(patch.object(router, "AsyncSessionLocal", _MemorySessionFactory(core, proof_session)))
            stack.enter_context(patch.object(router, "_lock_meta_event", AsyncMock()))
            exists = stack.enter_context(patch.object(router, "_status_event_exists", AsyncMock(side_effect=[False, True])))
            record = stack.enter_context(patch.object(router, "record_message_status_event", AsyncMock()))
            duplicate = await router._persist_event(event, _connection())
        self.assertTrue(duplicate)
        self.assertEqual(exists.await_count, 2)
        record.assert_awaited_once()
        core.rollback.assert_awaited_once()
        core.commit.assert_not_awaited()

        failed_core = _MemorySession(flush_error=IntegrityError("INSERT status", {}, _AsyncpgUniqueViolation("duplicate")))
        failed_proof_session = _MemorySession()
        with ExitStack() as stack:
            stack.enter_context(patch.object(router, "AsyncSessionLocal", _MemorySessionFactory(failed_core, failed_proof_session)))
            stack.enter_context(patch.object(router, "_lock_meta_event", AsyncMock()))
            stack.enter_context(patch.object(router, "_status_event_exists", AsyncMock(side_effect=[False, False])))
            stack.enter_context(patch.object(router, "record_message_status_event", AsyncMock()))
            with self.assertRaises(MetaWebhookProcessingError):
                await router._persist_event(event, _connection())
        failed_core.rollback.assert_awaited_once()

    async def test_new_lead_unknown_integrity_error_is_not_treated_as_duplicate(self):
        from app.services.lead_service import get_or_create_lead
        from sqlalchemy.exc import IntegrityError

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return None

        class _Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _type, _value, _traceback):
                return False

        class _NativeAsyncpgError(Exception):
            constraint_name = "crm_leads_required_name"

        class _AdapterError(Exception):
            sqlstate = "23502"

            def __init__(self):
                self.__cause__ = _NativeAsyncpgError("not null violation")

        failure = IntegrityError("INSERT crm_leads", {}, _AdapterError())
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result(), _Result(), _Result(), _Result()]),
            begin_nested=lambda: _Savepoint(),
            add=unittest.mock.Mock(),
            flush=AsyncMock(side_effect=failure),
            rollback=AsyncMock(),
        )

        with self.assertRaises(IntegrityError):
            await get_or_create_lead(
                session, empresa_id="5a20573a-b056-4aac-9bd2-45e96d1a57c6",
                telefone="5511999999999", nome_inicial="Test",
            )
        session.rollback.assert_not_awaited()
        self.assertEqual(session.execute.await_count, 4)


if __name__ == "__main__":
    unittest.main()
