import hashlib
import hmac
import json
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.routers.instagram_simple_webhook import comment_changes, direct_message_events, valid_signature
from app.services.instagram_simple import (
    GRAPH_BASE_URL,
    InstagramGraphError,
    InstagramGraphClient,
    _persist_comment_context,
    _persist_private_reply_outbound,
    build_private_reply_message,
    comment_matches,
    execution_status_from_results,
    identify_connection,
    persist_connection,
    reconfigure_connection,
    comment_payload_has_matching_fields,
    process_direct_message,
    render_private,
    webhook_reservation_decision,
    _public_reply_id,
    classify_comment_origin,
)
from app.api.routers.instagram_simple import AutomationInput


class _Response:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response(self.responses.pop(0))

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _Response(self.responses.pop(0))


class _Scalars:
    def __init__(self, *, first=None, values=None):
        self._first = first
        self._values = list(values or [])

    def first(self):
        return self._first

    def all(self):
        return self._values


class _Result:
    def __init__(self, *, first=None, values=None):
        self._scalars = _Scalars(first=first, values=values)

    def scalars(self):
        return self._scalars


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()

class InstagramSimpleGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_comment_fields_skip_graph_enrichment(self):
        self.assertTrue(comment_payload_has_matching_fields(media_id="18118744063655207", text="Teste"))
        self.assertFalse(comment_payload_has_matching_fields(media_id=None, text="Teste"))
        self.assertFalse(comment_payload_has_matching_fields(media_id="18118744063655207", text=None))

    async def test_identification_matches_n8n_direct_account_query(self):
        transport = _Transport([
            {"id": "ig-token-namespace", "username": "brand"},
        ])
        result = await identify_connection(professional_account_id="ig-1", access_token="instagram-user-token", transport=transport)

        self.assertEqual(result["instagram_professional_account_id"], "ig-1")
        self.assertEqual(result["instagram_business_account_id"], "ig-1")
        self.assertEqual(result["resolved_instagram_graph_user_id"], "ig-token-namespace")
        self.assertEqual(result["token_type"], "INSTAGRAM_USER")
        self.assertTrue(result["token_is_valid"])
        self.assertEqual(result["permissions"], [])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1], GRAPH_BASE_URL + "/me")
        self.assertEqual(transport.calls[0][2]["params"], {"access_token": "instagram-user-token", "fields": "id,username,account_type,media_count"})
        self.assertEqual(transport.calls[0][2]["headers"], {})
        self.assertNotIn("graph.facebook.com", transport.calls[0][1])
        self.assertIsNone(result["validation"]["relationship_verified"])

    async def test_identification_allows_username_absent(self):
        transport = _Transport([
            {"id": "ig-1"},
        ])
        result = await identify_connection(professional_account_id="ig-1", access_token="instagram-user-token", transport=transport)
        self.assertEqual(result["instagram_username"], "")

    async def test_difference_between_meta_namespaces_does_not_invalidate_connection(self):
        transport = _Transport([
            {"id": "ig-returned", "username": "brand"},
        ])
        result = await identify_connection(
            professional_account_id="ig-informed",
            access_token="instagram-user-token",
            transport=transport,
        )
        self.assertEqual(result["instagram_professional_account_id"], "ig-informed")
        self.assertEqual(result["resolved_instagram_graph_user_id"], "ig-returned")
        self.assertIsNone(result["validation"]["relationship_verified"])

    async def test_persist_requires_prevalidated_matching_id(self):
        db = _Session([_Result(first=None)])
        identified = {
            "instagram_professional_account_id": "ig-1",
            "instagram_business_account_id": "ig-1",
            "instagram_username": "brand",
            "permissions": [],
            "permissions_verified": False,
            "token_is_valid": True,
            "token_expires_at": None,
            "data_access_expires_at": None,
            "validation": {
                "validated_at": "2026-08-08T00:00:00Z",
                "relationship_verified": True,
            },
        }
        with patch.dict("os.environ", {"META_CREDENTIAL_ENCRYPTION_KEY": "x" * 32}):
            connection = await persist_connection(
                db,
                empresa_id=uuid.uuid4(),
                professional_account_id="ig-1",
                access_token="instagram-user-token",
                identified=identified,
                name="Brand",
            )
        self.assertEqual(db.commits, 1)
        self.assertEqual(connection.instagram_business_account_id, "ig-1")
        self.assertEqual(connection.status, "connected")
        self.assertTrue(connection.metadata_json["token_is_valid"])
        self.assertEqual(connection.metadata_json["configuration_state"], "configured")

    async def test_cross_namespace_id_difference_does_not_block_persist(self):
        db = _Session([_Result(first=None)])
        identified = {"instagram_professional_account_id": "ig-token-owner", "token_is_valid": True}
        with patch.dict("os.environ", {"META_CREDENTIAL_ENCRYPTION_KEY": "x" * 32}):
            await persist_connection(
                db,
                empresa_id=uuid.uuid4(),
                professional_account_id="ig-informed",
                access_token="instagram-user-token",
                identified=identified,
                name="Brand",
            )
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.added[0].instagram_business_account_id, "ig-informed")

    async def test_invalid_reconfigure_preserves_existing_credentials(self):
        empresa = uuid.uuid4()
        connection_id = uuid.uuid4()
        current = SimpleNamespace(
            id=connection_id,
            empresa_id=empresa,
            instagram_business_account_id="ig-old",
            credenciais={"encrypted_access_token": "encrypted-old"},
            status="connected",
            external_account_id="ig-old",
            page_id=None,
            credential_source="instagram_user_token",
            nome_instancia="Old",
            metadata_json={"token_is_valid": True},
        )
        db = _Session([_Result(first=current)])
        with patch(
            "app.services.instagram_simple.identify_connection",
            new=AsyncMock(side_effect=InstagramGraphError("Token inválido ou expirado.", code=190)),
        ):
            with self.assertRaisesRegex(Exception, "Token inválido"):
                await reconfigure_connection(
                    db,
                    empresa_id=empresa,
                    connection_id=connection_id,
                    professional_account_id="ig-new",
                    access_token="new-token",
                )
        self.assertEqual(db.commits, 0)
        self.assertEqual(current.credenciais, {"encrypted_access_token": "encrypted-old"})
        self.assertEqual(current.instagram_business_account_id, "ig-old")

    async def test_reply_paths_are_exact(self):
        transport = _Transport([{"id": "reply-1"}, {"message_id": "dm-1"}, {"message_id": "dm-2"}])
        client = InstagramGraphClient("token", transport=transport)
        await client.public_reply("comment-1", "Olá")
        await client.private_reply("ig-1", "comment-1", "Direct")
        await client.direct_reply("ig-1", "igsid-contact", "Resposta")

        self.assertEqual(transport.calls[0][1], GRAPH_BASE_URL + "/comment-1/replies")
        self.assertEqual(transport.calls[1][1], GRAPH_BASE_URL + "/ig-1/messages")
        self.assertEqual(transport.calls[1][2]["json"]["recipient"], {"comment_id": "comment-1"})
        self.assertEqual(transport.calls[2][1], GRAPH_BASE_URL + "/ig-1/messages")
        self.assertEqual(transport.calls[2][2]["json"]["recipient"], {"id": "igsid-contact"})
        self.assertEqual(transport.calls[2][2]["json"]["messaging_type"], "RESPONSE")

    async def test_private_reply_button_template_payload(self):
        transport = _Transport([{"message_id": "dm-1"}])
        client = InstagramGraphClient("token", transport=transport)
        message = {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": "Escolha uma opção",
                    "buttons": [{"type": "web_url", "title": "Abrir", "url": "https://example.com"}],
                },
            },
        }
        await client.private_reply("ig-1", "comment-1", message)

        self.assertEqual(transport.calls[0][1], GRAPH_BASE_URL + "/ig-1/messages")
        self.assertEqual(transport.calls[0][2]["json"], {"recipient": {"comment_id": "comment-1"}, "message": message})

    def test_build_private_reply_button_template_supports_three_buttons_and_postback(self):
        automation = SimpleNamespace(
            private_message=None,
            private_link="https://legacy.example",
            private_reply_config={
                "message_type": "button_template",
                "text": "Oi {{username}}",
                "buttons": [
                    {"type": "web_url", "title": "Site", "url": "https://example.com"},
                    {"type": "postback", "title": "Quero", "payload": "WANT_INFO"},
                    {"type": "web_url", "title": "Agenda", "url": "http://example.com/a"},
                ],
            },
        )
        text, message, message_type = build_private_reply_message(automation, username="Ana", comment="preço")

        payload = message["attachment"]["payload"]
        self.assertEqual(text, "Oi Ana")
        self.assertEqual(message_type, "button_template")
        self.assertEqual(message["attachment"]["type"], "template")
        self.assertEqual(payload["template_type"], "button")
        self.assertEqual(payload["buttons"][1], {"type": "postback", "title": "Quero", "payload": "WANT_INFO"})
        self.assertEqual(len(payload["buttons"]), 3)

    def test_build_private_reply_legacy_text_preserves_link_append(self):
        automation = SimpleNamespace(private_message="Oi", private_link="https://example.com", private_reply_config=None)
        text, message, message_type = build_private_reply_message(automation, username="Ana", comment="preço")

        self.assertEqual(text, "Oi\nhttps://example.com")
        self.assertEqual(message, {"text": "Oi\nhttps://example.com"})
        self.assertEqual(message_type, "text")

    async def test_meta_error_is_allowlisted_and_request_body_is_sanitized(self):
        transport = _Transport([{
            "error": {
                "message": "Permission denied",
                "type": "OAuthException",
                "code": 10,
                "error_subcode": 2018278,
                "error_user_title": "Permission required",
                "error_user_msg": "Enable messaging",
                "fbtrace_id": "trace-safe",
            }
        }])
        original_post = transport.post

        async def rejected_post(url, **kwargs):
            response = await original_post(url, **kwargs)
            response.status_code = 400
            return response

        transport.post = rejected_post
        client = InstagramGraphClient("secret-token", transport=transport)
        with self.assertRaises(Exception) as raised:
            await client.private_reply("ig-1", "comment-1", "mensagem secreta")

        diagnostic = raised.exception.sanitized_diagnostic(
            operation="private_reply",
            object_type="instagram_professional_account",
            object_id="ig-1",
            token_diagnostic={"type": "INSTAGRAM_USER", "is_valid": True, "permissions": ["instagram_business_manage_messages"]},
        )
        self.assertEqual(diagnostic["request"]["url"], GRAPH_BASE_URL + "/ig-1/messages")
        self.assertEqual(diagnostic["request"]["body"]["recipient"], {"comment_id": "comment-1"})
        self.assertEqual(diagnostic["request"]["body"]["message"]["text"], "<redacted:16 chars>")
        self.assertNotIn("mensagem secreta", repr(diagnostic))
        self.assertNotIn("secret-token", repr(diagnostic))
        self.assertEqual(diagnostic["response"]["code"], 10)
        self.assertEqual(diagnostic["response"]["error_subcode"], 2018278)
        self.assertEqual(diagnostic["response"]["fbtrace_id"], "trace-safe")

    async def test_button_template_error_body_is_sanitized(self):
        transport = _Transport([{"error": {"message": "bad", "code": 100}}])
        original_post = transport.post

        async def rejected_post(url, **kwargs):
            response = await original_post(url, **kwargs)
            response.status_code = 400
            return response

        transport.post = rejected_post
        client = InstagramGraphClient("secret-token", transport=transport)
        with self.assertRaises(Exception) as raised:
            await client.private_reply("ig-1", "comment-1", {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "button",
                        "text": "texto secreto",
                        "buttons": [{"type": "web_url", "title": "botao secreto", "url": "https://example.com"}],
                    },
                },
            })

        diagnostic = raised.exception.sanitized_diagnostic(
            operation="private_reply",
            object_type="instagram_professional_account",
            object_id="ig-1",
            token_diagnostic={"type": "INSTAGRAM_USER", "is_valid": True, "permissions": []},
        )
        self.assertEqual(diagnostic["request"]["body"]["message"]["attachment"]["payload"]["template_type"], "button")
        self.assertEqual(diagnostic["request"]["body"]["message"]["attachment"]["payload"]["text"], "<redacted:13 chars>")
        self.assertEqual(diagnostic["request"]["body"]["message"]["attachment"]["payload"]["buttons"][0]["type"], "web_url")
        self.assertNotIn("texto secreto", repr(diagnostic))

    def test_automation_input_rejects_invalid_button_configs(self):
        base = {
            "connection_id": uuid.uuid4(),
            "name": "Auto",
            "public_reply_enabled": True,
            "fixed_reply": "Publico",
            "private_reply_enabled": True,
        }
        valid = {
            **base,
            "private_reply_config": {
                "message_type": "button_template",
                "text": "Direct",
                "buttons": [{"type": "postback", "title": "Sim", "payload": "YES"}],
            },
        }
        self.assertEqual(AutomationInput(**valid).private_reply_config.buttons[0].type, "postback")
        invalid_cases = [
            {**base, "private_reply_config": {"message_type": "button_template", "text": "Direct", "buttons": [{"type": "web_url", "title": "Site", "url": "ftp://example.com"}]}},
            {**base, "private_reply_config": {"message_type": "button_template", "text": "Direct", "buttons": [{"type": "web_url", "title": "", "url": "https://example.com"}]}},
            {**base, "private_reply_config": {"message_type": "button_template", "text": "Direct", "buttons": [{"type": "postback", "title": str(index), "payload": str(index)} for index in range(4)]}},
        ]
        for payload in invalid_cases:
            with self.assertRaises(Exception):
                AutomationInput(**payload)


class InstagramSimpleWebhookTests(unittest.TestCase):
    def test_raw_body_hmac(self):
        raw = b'{"object":"instagram"}'
        signature = "sha256=" + hmac.new(b"principal", raw, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(raw, signature, "principal"))
        self.assertFalse(valid_signature(raw + b" ", signature, "principal"))
        self.assertFalse(valid_signature(raw, signature, None))
        self.assertFalse(valid_signature(raw, "sha256=abc", "principal"))
        self.assertFalse(valid_signature(raw, "sha1=" + "a" * 40, "principal"))

    def test_official_comments_payload(self):
        payload = {"object": "instagram", "entry": [{"id": "ig-1", "changes": [{"field": "comments", "value": {"id": "comment-1", "text": "Quero", "from": {"id": "user-1", "username": "ana"}, "media": {"id": "media-1"}}}]}]}
        events = comment_changes(payload)
        self.assertEqual(events, [{"object_id": "ig-1", "comment_id": "comment-1", "media_id": "media-1", "sender_id": "user-1", "username": "ana", "text": "Quero"}])

    def test_real_direct_message_extracts_igsid_and_ignores_echo(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "ig-professional-1",
                "messaging": [
                    {"sender": {"id": "igsid-1"}, "recipient": {"id": "ig-professional-1"}, "message": {"mid": "mid-1", "text": "Olá"}},
                    {"sender": {"id": "ig-professional-1"}, "recipient": {"id": "igsid-1"}, "message": {"mid": "mid-echo", "text": "Eco", "is_echo": True}},
                ],
            }],
        }
        self.assertEqual(direct_message_events(payload), [{
            "object_id": "ig-professional-1",
            "igsid": "igsid-1",
            "recipient_id": "ig-professional-1",
            "message_id": "mid-1",
            "text": "Olá",
        }])

    def test_matching_and_private_template(self):
        self.assertTrue(comment_matches("Quero informações", ["INFORMACOES"]))
        self.assertFalse(comment_matches("Olá", ["preço"]))
        self.assertEqual(render_private("Oi {{username}}", username="ana", comment="x", link="https://x"), "Oi ana\nhttps://x")

    def test_failed_is_terminal_and_stale_processing_is_retryable(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        self.assertEqual(webhook_reservation_decision("failed", now, now=now), "duplicate")
        self.assertEqual(webhook_reservation_decision("received", None, now=now), "retry")
        self.assertEqual(
            webhook_reservation_decision("processing", now - timedelta(minutes=6), now=now),
            "retry",
        )
        self.assertEqual(
            webhook_reservation_decision("processing", now - timedelta(seconds=10), now=now),
            "in_progress",
        )
        self.assertEqual(webhook_reservation_decision("processed", now, now=now), "duplicate")

    def test_public_reply_id_is_extracted_without_changing_send_contract(self):
        self.assertEqual(_public_reply_id({"id": "reply-1"}), "reply-1")
        self.assertEqual(_public_reply_id({"comment_id": "reply-2"}), "reply-2")
        self.assertIsNone(_public_reply_id({"message_id": "dm-1"}))

    def test_self_profile_comparison_is_string_exact(self):
        self.assertEqual(str("17841458184539865").strip(), "17841458184539865")
        self.assertNotEqual(str("17841458184539865").strip(), "17841458184539865.0")

    def test_only_proven_self_or_registered_output_is_ignored(self):
        self.assertEqual(
            classify_comment_origin(
                sender_id="17841458184539865",
                account_id="17841458184539865",
                registered_public_reply=False,
            ),
            "ignored_self_generated_comment",
        )
        self.assertEqual(
            classify_comment_origin(sender_id="user-1", account_id="ig-1", registered_public_reply=True),
            "ignored_registered_public_reply",
        )
        self.assertIsNone(
            classify_comment_origin(sender_id="user-1", account_id="ig-1", registered_public_reply=False)
        )

    def test_private_reply_failure_is_never_completed(self):
        self.assertEqual(execution_status_from_results(
            failures=[{"operation": "private_reply"}],
            public_reply_status="public_reply_sent",
            private_reply_status="private_reply_failed",
        ), "partial")
        self.assertEqual(execution_status_from_results(
            failures=[{"operation": "private_reply"}],
            public_reply_status="public_reply_failed",
            private_reply_status="private_reply_failed",
        ), "failed")


class InstagramSimplePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_comment_creates_only_crm_context(self):
        session = _Session([_Result(first=None), _Result(first=None)])
        connection = SimpleNamespace(id=uuid.uuid4(), empresa_id=uuid.uuid4())

        lead = await _persist_comment_context(
            session,
            connection=connection,
            sender_id="commenter-id",
            username="ana",
            comment_id="comment-1",
            media_id="media-1",
            text="Quero",
        )

        self.assertEqual(len(session.added), 2)
        self.assertIs(session.added[0], lead)
        self.assertEqual(session.added[1].external_contact_id, "commenter-id")
        self.assertIsNone(lead.telefone_contato)
        self.assertEqual(lead.dados_adicionais["origem"], "instagram_comment")
        self.assertEqual(lead.dados_adicionais["instagram_comment_context"]["comment_id"], "comment-1")

    async def test_successful_private_reply_is_an_outbound_context_event(self):
        session = _Session([])
        connection = SimpleNamespace(
            id=uuid.uuid4(),
            empresa_id=uuid.uuid4(),
            instagram_business_account_id="ig-professional-1",
        )
        lead = SimpleNamespace(id=uuid.uuid4())

        outbound = await _persist_private_reply_outbound(
            session,
            connection=connection,
            lead=lead,
            comment_id="comment-1",
            message="Mensagem privada",
            graph_response={"message_id": "mid-private-1"},
        )

        self.assertEqual(outbound.direction, "outbound")
        self.assertTrue(outbound.from_me)
        self.assertIsNone(outbound.channel_conversation_id)
        self.assertEqual(outbound.metadata_json["origin"], "instagram_comment_private_reply")
        self.assertEqual(outbound.external_message_id, "mid-private-1")

    async def test_real_direct_message_opens_conversation_with_igsid(self):
        session = _Session([_Result(first=None), _Result(first=None), _Result(first=None)])
        connection = SimpleNamespace(
            id=uuid.uuid4(),
            empresa_id=uuid.uuid4(),
            instagram_business_account_id="ig-professional-1",
        )
        conversation = SimpleNamespace(id=uuid.uuid4())
        operational = SimpleNamespace(conversation=conversation)

        with patch(
            "app.services.operational_identity.ensure_operational_conversation",
            new=AsyncMock(return_value=operational),
        ) as ensure:
            result = await process_direct_message(
                session,
                connection=connection,
                igsid="igsid-1",
                recipient_id="ig-professional-1",
                message_id="mid-1",
                text="Olá",
            )

        self.assertEqual(result["status"], "processed")
        self.assertEqual(session.commits, 1)
        self.assertEqual(ensure.await_args.kwargs["external_contact_id"], "igsid-1")
        inbound = session.added[-1]
        self.assertEqual(inbound.direction, "inbound")
        self.assertEqual(inbound.external_message_id, "mid-1")
        self.assertEqual(inbound.metadata_json["origin"], "instagram_message")


class InstagramSimpleContractTests(unittest.TestCase):
    def test_persistent_dedup_and_tenant_connection_filters_are_declared(self):
        model = Path("db/models.py").read_text(encoding="utf-8")
        service = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        migration = Path("db/migrations/2026_08_03_instagram_simple_page_flow.sql").read_text(encoding="utf-8")
        self.assertIn('UniqueConstraint("connection_id", "comment_id"', model)
        self.assertIn("Conexao.empresa_id == empresa_id", service)
        self.assertIn("InstagramCommentAutomation.connection_id == connection.id", service)
        self.assertIn("Conexao.instagram_business_account_id == _clean(object_id)", service)
        self.assertNotIn("Conexao.page_id == _clean(object_id)", service)
        self.assertIn("return matches[0] if len(matches) == 1 else None", service)
        self.assertIn(".with_for_update()", service)
        self.assertIn('event.status = "processing"', service)
        self.assertIn("uq_instagram_webhook_event_comment", migration)
        self.assertIn("uq_conexoes_instagram_page_global", migration)
        self.assertIn("uq_conexoes_instagram_account_global", migration)
        self.assertGreaterEqual(migration.count("tipo = 'INSTAGRAM'::tipo_conexao_enum"), 4)

    def test_instagram_manual_token_transport_uses_n8n_graph_host_and_version(self):
        source = "\n".join(
            Path(path).read_text(encoding="utf-8")
            for path in ("app/services/instagram_simple.py", "app/api/routers/instagram_simple.py", "app/api/routers/instagram_simple_webhook.py")
        )
        self.assertIn('GRAPH_VERSION = "v21.0"', source)
        self.assertIn("graph.instagram.com", source)
        self.assertNotIn("graph.facebook.com", source)
        self.assertNotIn("pages_messaging", source)
        self.assertNotIn("SYSTEM_USER", source)
        self.assertNotIn("auth_model", source)
        self.assertNotIn("authorized_instagram_profile", source)
        self.assertNotIn("/oauth", source.casefold())

    def test_processing_failure_is_reported_without_meta_redelivery(self):
        source = Path("app/api/routers/instagram_simple_webhook.py").read_text(encoding="utf-8")
        self.assertNotIn("had_processing_failure = True", source)
        self.assertIn('return {"received": True, "events": results}', source)

    def test_verify_token_is_removed_from_access_log_scope(self):
        source = Path("app/api/routers/instagram_simple_webhook.py").read_text(encoding="utf-8")
        self.assertIn('request.scope["query_string"] = b""', source)

    def test_graph_failures_are_persisted_as_sanitized_json(self):
        source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        self.assertIn('"graph_failures": failures', source)
        self.assertIn('operation="private_reply"', source)
        self.assertIn('object_type="instagram_professional_account"', source)

    def test_comment_is_context_not_direct_message_and_private_reply_is_outbound(self):
        source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        comment_section = source.split("async def _persist_comment_context", 1)[1].split("async def _persist_private_reply_outbound", 1)[0]
        self.assertNotIn("ensure_operational_conversation", comment_section)
        self.assertNotIn("MensagemHistorico(", comment_section)
        self.assertIn('"origem": "instagram_comment"', comment_section)
        self.assertIn('"origin": "instagram_comment_private_reply"', source)
        self.assertIn('direction="outbound"', source)
        self.assertIn("channel_conversation_id=None", source)

    def test_private_reply_uses_connection_id_token_comment_id_and_is_deduplicated(self):
        source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        self.assertIn("client = InstagramGraphClient(connection_token(connection))", source)
        self.assertIn("await client.private_reply(", source)
        self.assertIn("_clean(connection.instagram_business_account_id)", source)
        self.assertIn("comment_id,", source)
        self.assertIn("existing_execution =", source)
        self.assertIn("if existing_execution:", source)

    def test_reconfiguration_is_in_place_and_pending_connections_do_not_use_legacy_tokens(self):
        router = Path("app/api/routers/instagram_simple.py").read_text(encoding="utf-8")
        service = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        frontend = Path("frontend/src/pages/tenant/Instagram.jsx").read_text(encoding="utf-8")
        self.assertIn('/connection/{connection_id}/reconfigure', router)
        self.assertIn('reconfigure_connection(', router)
        self.assertIn('instagram_id:', router)
        self.assertNotIn('/connection/test', router)
        self.assertNotIn('validation_receipt', router)
        self.assertNotIn('não corresponde à conexão em reconfiguração', router)
        self.assertIn('identify_connection(professional_account_id=requested_id', service)
        self.assertIn('setAccessToken("")', frontend)
        self.assertIn('Salvar reconfiguração', frontend)
        self.assertIn('Pendente de reconfiguração', frontend)
        self.assertIn('disabled={busy}', frontend)
        self.assertNotIn('Testar conexão', frontend)
        self.assertNotIn('teste o novo token antes de salvar', frontend)
        self.assertIn('connectionMode', frontend)
        self.assertIn('Cancelar', frontend)

    def test_manual_save_uses_graph_precommit_validation_without_manual_test_gate(self):
        router = Path("app/api/routers/instagram_simple.py").read_text(encoding="utf-8")
        service = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        frontend = Path("frontend/src/pages/tenant/Instagram.jsx").read_text(encoding="utf-8")
        save_section = router.split('async def save_connection', 1)[1].split('@router.post("/connection/{connection_id}/revalidate")', 1)[0]
        persist_section = service.split('async def persist_connection', 1)[1].split('async def reconfigure_connection', 1)[0]
        self.assertIn('identification = await identify_connection(', save_section)
        self.assertIn('identification = await identify_connection(', save_section)
        self.assertIn('identified=identification', save_section)
        self.assertNotIn('validation_receipt', save_section)
        self.assertNotIn('resolved_id != professional_id', persist_section)
        self.assertNotIn('outra conta Instagram', service)
        self.assertIn('encrypt_meta_secret', persist_section)
        self.assertNotIn('validation_receipt:', frontend)

    def test_direct_message_persistence_uses_igsid_and_tenant_connection_scope(self):
        source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
        direct_section = source.split("async def process_direct_message", 1)[1].split("async def _graph_failure_diagnostic", 1)[0]
        self.assertIn("external_contact_id == scoped_id", direct_section)
        self.assertIn("ChannelContact.conexao_id == connection.id", direct_section)
        self.assertIn('"identity_type": "IGSID"', direct_section)
        self.assertIn('"origin": "instagram_message"', direct_section)

    def test_inbox_direct_reply_requires_legitimate_igsid_without_changing_whatsapp_branches(self):
        source = Path("app/api/routers/inbox.py").read_text(encoding="utf-8")
        self.assertIn('elif provider == "meta_instagram":', source)
        self.assertIn('igsid = str(getattr(contact, "external_contact_id", "")', source)
        self.assertIn("InstagramGraphClient(connection_token(conexao)).direct_reply(", source)
        self.assertIn("igsid,", source)
        self.assertIn('detail="IGSID indisponível para esta conversa Direct"', source)
        self.assertIn('conversation_provider != "meta_instagram"', source)
        self.assertIn('getattr(conversation, "channel", "")', source)
        self.assertIn('if provider == "meta_whatsapp":', source)
        self.assertIn('elif provider in {"evolution", "evolution_legacy"}:', source)

    def test_webhook_uses_environment_only_settings(self):
        router_source = Path("app/api/routers/instagram_simple_webhook.py").read_text(encoding="utf-8")
        settings_source = Path("app/services/meta/instagram_api_settings.py").read_text(encoding="utf-8")
        self.assertIn("load_instagram_api_settings", router_source)
        self.assertIn("settings.app_secret", router_source)
        self.assertNotIn("META_APP_SECRET", router_source)
        self.assertNotIn("META_WEBHOOK_VERIFY_TOKEN", router_source)
        self.assertIn('os.getenv("META_APP_SECRET")', settings_source)
        self.assertIn('os.getenv("META_WEBHOOK_VERIFY_TOKEN")', settings_source)
        self.assertNotIn("ConfiguracoesGlobais", settings_source)
        self.assertNotIn("META_INSTAGRAM_V2_VERIFY_TOKEN", settings_source)

    def test_global_configuration_has_no_instagram_write_endpoint(self):
        router = Path("app/api/routers/configuracoes.py").read_text(encoding="utf-8")
        self.assertNotIn("instagram_app_secret", router)
        self.assertNotIn("instagram_verify_token", router)
        self.assertNotIn("instagram-api/test", router)


if __name__ == "__main__":
    unittest.main()
