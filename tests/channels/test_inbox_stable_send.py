"""Regression coverage for Inbox sends resolved through tenant-bound identity."""

import asyncio
import uuid
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.routers import inbox
from app.schemas import ConversaListaResponse


class _Scalars:
    def __init__(self, rows):
        self.rows = list(rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return _Scalars(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _EmptySession:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _Result()


class _SendSession:
    def __init__(self, *, tenant_id, lead, conversation, contact, connection, missing_lead=False,
                 missing_conversation=False, duplicate=None):
        self.tenant_id = tenant_id
        self.lead = lead
        self.conversation = conversation
        self.contact = contact
        self.connection = connection
        self.missing_lead = missing_lead
        self.missing_conversation = missing_conversation
        self.duplicate = duplicate
        self.statements = []
        self.added = []
        self.committed = False

    async def execute(self, statement, params=None):
        rendered = str(statement)
        self.statements.append(rendered)
        if "pg_advisory_xact_lock" in rendered:
            return _Result()
        if "mensagens_historico" in rendered:
            return _Result([self.duplicate] if self.duplicate else [])
        if "channel_conversations" in rendered:
            if self.missing_conversation:
                return _Result()
            return _Result([(self.conversation, self.contact, self.connection)])
        if "crm_leads" in rendered:
            return _Result([] if self.missing_lead else [self.lead])
        return _Result()

    def add(self, message):
        message.id = message.id or uuid.uuid4()
        message.criado_em = message.criado_em or datetime.utcnow()
        self.added.append(message)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def refresh(self, _message):
        return None


def _send_context(*, provider="evolution", channel="whatsapp", tenant_id=None, lead_id=None, connection_type=None):
    tenant_id = tenant_id or uuid.uuid4()
    lead_id = lead_id or uuid.uuid4()
    conversation_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    lead = SimpleNamespace(
        id=lead_id,
        empresa_id=tenant_id,
        telefone_contato="5511888800000",  # CRM address intentionally differs from the conversation contact.
        dados_adicionais={},
        ia_ativa=True,
    )
    contact = SimpleNamespace(
        id=uuid.uuid4(),
        empresa_id=tenant_id,
        conexao_id=connection_id,
        lead_id=lead_id,
        channel=channel,
        provider=provider,
        external_contact_id="5511999900001",
        phone_e164="+5511999900001",
        normalized_address="5511999900001",
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        empresa_id=tenant_id,
        lead_id=lead_id,
        contact_id=contact.id,
        conexao_id=connection_id,
        channel=channel,
        provider=provider,
        last_message_at=None,
        last_outbound_at=None,
    )
    connection = SimpleNamespace(
        id=connection_id,
        empresa_id=tenant_id,
        channel=channel,
        provider=provider,
        tipo=connection_type or {
            "evolution": "evolution",
            "meta_whatsapp": "meta",
            "meta_instagram": "instagram",
        }.get(provider, provider),
        status="connected",
        instagram_business_account_id=None,
    )
    session = _SendSession(
        tenant_id=tenant_id,
        lead=lead,
        conversation=conversation,
        contact=contact,
        connection=connection,
    )
    return tenant_id, lead, conversation, contact, connection, session


class InboxStableSendBaselineTests(unittest.TestCase):
    def test_baseline_reproduces_phone_lookup_404_for_conversation_address(self):
        """The pre-fix endpoint requires a phone-equal CRM lead before dispatch."""
        tenant_id = str(uuid.uuid4())
        conversation_address = "5511999900001"
        db = _EmptySession()

        async def invoke():
            with self.assertRaises(HTTPException) as raised:
                await inbox.enviar_mensagem(
                    empresa_id=tenant_id,
                    telefone=conversation_address,
                    payload=inbox.SendMessagePayload(texto="synthetic response"),
                    db=db,
                )
            return raised.exception

        error = asyncio.run(invoke())
        self.assertEqual(error.status_code, 404)
        self.assertEqual(error.detail, "Lead não encontrado")
        self.assertIn("crm_leads.empresa_id", db.statements[0])
        self.assertIn("crm_leads.telefone_contato", db.statements[0])

    def test_stable_lead_and_conversation_send_route_exists(self):
        expected = "/api/empresas/{empresa_id}/inbox/leads/{lead_id}/conversations/{conversation_id}/send"
        paths = {route.path for route in inbox.router.routes}
        self.assertIn(expected, paths)

    def test_inbox_list_exposes_separate_persisted_conversation_choices(self):
        tenant_id, lead, selected, contact, _connection, _db = _send_context()
        other_contact = SimpleNamespace(
            id=uuid.uuid4(), lead_id=lead.id, display_name="Contato secundário sintético",
            phone_e164="+5511999900002",
        )
        other_conversation = SimpleNamespace(
            id=uuid.uuid4(), lead_id=lead.id, channel="whatsapp", provider="evolution",
            last_message_at=datetime.utcnow(), atualizado_em=datetime.utcnow(),
        )
        lead.nome_contato = "Lead sintético"
        lead.foto_url = None
        lead.historico_resumo = ""
        lead.status_atendimento = "aberto"
        lead.bot_pausado_ate = None
        lead.ia_ativa = True
        lead.etapa = None
        lead.tags = []
        lead.criado_em = datetime.utcnow()
        lead.dados_adicionais = {}
        main_row = (lead, None, None, 0, selected.id, {}, None, None)

        class ListSession:
            def __init__(self):
                self.calls = []

            async def execute(self, statement):
                self.calls.append(str(statement))
                if len(self.calls) == 1:
                    return _Result([])  # Tenant tags.
                if len(self.calls) == 2:
                    return _Result([main_row])
                return _Result([(selected, contact), (other_conversation, other_contact)])

        session = ListSession()

        async def invoke():
            with patch.object(inbox, "AsyncSessionLocal", lambda: _AsyncSessionContext(session)):
                return await inbox.listar_inbox(empresa_id=str(tenant_id))

        response = asyncio.run(invoke())
        validated = ConversaListaResponse(**response[0])
        options = response[0]["conversation_options"]
        self.assertEqual([item["conversation_id"] for item in options], [str(selected.id), str(other_conversation.id)])
        self.assertEqual(options[1]["contact_id"], str(other_contact.id))
        self.assertEqual(options[1]["contact_name"], "Contato secundário sintético")
        self.assertEqual(len(validated.conversation_options), 2)
        self.assertTrue(any("channel_conversations.empresa_id" in statement for statement in session.calls))

    def test_conversation_history_endpoint_returns_message_content_for_exact_thread(self):
        tenant, lead, conversation, _contact, _connection, _db = _send_context()
        message = SimpleNamespace(
            id=uuid.uuid4(), texto="synthetic history", conexao_id=conversation.conexao_id,
            channel="whatsapp", provider="evolution", external_message_id="synthetic-id",
            direction="inbound", message_status="delivered", provider_status="delivered",
            tipo_mensagem="text", origem_mensagem="humano", from_me=False,
            media_url=None, metadata_json={}, criado_em=datetime.utcnow(),
        )

        class HistorySession:
            def __init__(self):
                self.calls = []

            async def execute(self, statement):
                self.calls.append(str(statement))
                return _Result([conversation] if len(self.calls) == 1 else [message])

        session = HistorySession()

        async def invoke():
            with patch.object(inbox, "AsyncSessionLocal", lambda: _AsyncSessionContext(session)):
                return await inbox.listar_mensagens_conversa_multicanal(
                    empresa_id=str(tenant), conversation_id=str(conversation.id),
                )

        response = asyncio.run(invoke())
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["conversation_id"], str(conversation.id))
        self.assertEqual(response[0]["texto"], "synthetic history")
        self.assertIn("mensagens_historico.channel_conversation_id", session.calls[1])
        self.assertIn("mensagens_historico.empresa_id", session.calls[1])

    def test_stable_text_send_uses_persisted_contact_and_selected_connection_once(self):
        tenant, lead, conversation, contact, connection, db = _send_context()
        dispatcher = AsyncMock()

        async def invoke():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher), \
                 patch.object(inbox.manager, "broadcast_to_empresa", new=AsyncMock()), \
                 patch("app.services.followup_engine.registrar_followup_message_event", new=AsyncMock()):
                response = await inbox.enviar_mensagem_por_conversa(
                    empresa_id=str(tenant),
                    lead_id=str(lead.id),
                    conversation_id=str(conversation.id),
                    payload=inbox.SendMessagePayload(texto="synthetic response", client_message_id="client-1"),
                    db=db,
                )
            return response

        response = asyncio.run(invoke())
        self.assertEqual(response["status"], "success")
        self.assertEqual(len(db.added), 1)
        self.assertTrue(db.committed)
        dispatcher.assert_awaited_once()
        kwargs = dispatcher.await_args.kwargs
        self.assertEqual(kwargs["empresa_id"], tenant)
        self.assertIs(kwargs["conexao"], connection)
        self.assertEqual(kwargs["payload"].identificador_contato, "5511999900001")
        self.assertEqual(db.added[0].lead_id, lead.id)
        self.assertEqual(db.added[0].channel_conversation_id, conversation.id)
        self.assertEqual(db.added[0].metadata_json["inbox_client_message_id"], "client-1")

    def test_same_client_message_id_returns_existing_message_without_dispatching_again(self):
        tenant, lead, conversation, contact, connection, db = _send_context()
        db.duplicate = SimpleNamespace(
            id=uuid.uuid4(), texto="synthetic response", from_me=True,
            tipo_mensagem="text", origem_mensagem="humano", media_url=None,
            criado_em=datetime.utcnow(), message_status="sent",
        )
        dispatcher = AsyncMock()

        async def invoke():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher), \
                 patch.object(inbox.manager, "broadcast_to_empresa", new=AsyncMock()):
                return await inbox.enviar_mensagem_por_conversa(
                    empresa_id=str(tenant),
                    lead_id=str(lead.id),
                    conversation_id=str(conversation.id),
                    payload=inbox.SendMessagePayload(texto="synthetic response", client_message_id="client-1"),
                    db=db,
                )

        response = asyncio.run(invoke())
        self.assertTrue(response["deduplicated"])
        self.assertEqual(response["mensagem"]["id"], str(db.duplicate.id))
        dispatcher.assert_not_awaited()
        self.assertEqual(db.added, [])

    def test_stable_text_route_requires_idempotency_key_before_dispatch(self):
        tenant, lead, conversation, _contact, _connection, db = _send_context()
        dispatcher = AsyncMock()

        async def invoke():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic response"), db=db,
                    )
            return raised.exception

        error = asyncio.run(invoke())
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.detail, "Identificador da mensagem obrigatório")
        dispatcher.assert_not_awaited()
        self.assertEqual(db.added, [])

    def test_foreign_tenant_lead_and_wrong_conversation_never_reach_dispatcher(self):
        tenant, lead, conversation, _contact, _connection, db = _send_context()
        db.missing_lead = True
        dispatcher = AsyncMock()

        async def invoke_foreign_lead():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(uuid.uuid4()),
                        lead_id=str(lead.id),
                        conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic"),
                        db=db,
                    )
            return raised.exception

        error = asyncio.run(invoke_foreign_lead())
        self.assertEqual(error.status_code, 404)
        dispatcher.assert_not_awaited()
        self.assertIn("crm_leads.empresa_id", db.statements[0])

    def test_provider_or_stored_address_mismatch_blocks_dispatch(self):
        tenant, lead, conversation, contact, connection, db = _send_context()
        connection.provider = "meta_whatsapp"
        dispatcher = AsyncMock()

        async def invoke():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic"), db=db,
                    )
            return raised.exception

        self.assertEqual(asyncio.run(invoke()).status_code, 409)
        dispatcher.assert_not_awaited()

        _tenant, _lead, _conversation, contact, _connection, db = _send_context()
        contact.normalized_address = "5511888800000"  # Legacy projection may be stale; it is never a destination source.
        resolved = asyncio.run(inbox._resolve_inbox_send_context(
            db, empresa_id=db.tenant_id, lead_id=db.lead.id, conversation_id=db.conversation.id,
        ))
        self.assertEqual(resolved["destination"], "5511999900001")

        _tenant, _lead, _conversation, contact, _connection, db = _send_context()
        contact.phone_e164 = "+5511888800000"
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(inbox._resolve_inbox_send_context(
                db, empresa_id=db.tenant_id, lead_id=db.lead.id, conversation_id=db.conversation.id,
            ))
        self.assertEqual(raised.exception.status_code, 409)

    def test_instagram_send_requires_a_persisted_direct_message_identity(self):
        tenant, lead, conversation, contact, _connection, db = _send_context(
            provider="meta_instagram", channel="instagram",
        )
        contact.metadata_json = {"identity_type": "IGSID"}
        resolved = asyncio.run(inbox._resolve_inbox_send_context(
            db, empresa_id=tenant, lead_id=lead.id, conversation_id=conversation.id,
        ))
        self.assertEqual(resolved["destination"], contact.external_contact_id)

        contact.metadata_json = {"identity_type": "instagram_commenter_id"}
        lead.dados_adicionais["instagram_igsid"] = "different-persisted-id"
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(inbox._resolve_inbox_send_context(
                db, empresa_id=tenant, lead_id=lead.id, conversation_id=conversation.id,
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "IGSID indisponível para esta conversa Direct")

    def test_impossible_provider_channel_pairs_never_reach_any_dispatcher(self):
        for provider, channel in (
            ("evolution", "instagram"),
            ("meta_whatsapp", "instagram"),
            ("meta_instagram", "whatsapp"),
        ):
            with self.subTest(provider=provider, channel=channel):
                tenant, lead, conversation, _contact, _connection, db = _send_context(
                    provider=provider, channel=channel,
                )
                whatsapp_dispatcher = AsyncMock()
                official_dispatcher = AsyncMock()

                async def invoke():
                    with patch.object(inbox, "dispatch_outbound_message", whatsapp_dispatcher), \
                         patch.object(inbox, "despachar_mensagem", official_dispatcher):
                        with self.assertRaises(HTTPException) as raised:
                            await inbox.enviar_mensagem_por_conversa(
                                empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                                payload=inbox.SendMessagePayload(texto="synthetic", client_message_id="pair-check"),
                                db=db,
                            )
                    return raised.exception

                error = asyncio.run(invoke())
                self.assertEqual(error.status_code, 409)
                whatsapp_dispatcher.assert_not_awaited()
                official_dispatcher.assert_not_awaited()

    def test_connection_provider_and_type_mismatch_never_reaches_dispatcher(self):
        tenant, lead, conversation, _contact, connection, db = _send_context(
            provider="evolution", channel="whatsapp", connection_type="meta",
        )
        whatsapp_dispatcher = AsyncMock()
        official_dispatcher = AsyncMock()

        async def invoke():
            with patch.object(inbox, "dispatch_outbound_message", whatsapp_dispatcher), \
                 patch.object(inbox, "despachar_mensagem", official_dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic", client_message_id="provider-type-check"),
                        db=db,
                    )
            return raised.exception

        error = asyncio.run(invoke())
        self.assertEqual(error.status_code, 409)
        whatsapp_dispatcher.assert_not_awaited()
        official_dispatcher.assert_not_awaited()

    def test_unlinked_conversation_and_contact_lead_mismatch_never_dispatch(self):
        tenant, lead, conversation, contact, _connection, db = _send_context()
        db.missing_conversation = True
        dispatcher = AsyncMock()

        async def invoke_wrong_conversation():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic"), db=db,
                    )
            return raised.exception

        self.assertEqual(asyncio.run(invoke_wrong_conversation()).status_code, 404)
        dispatcher.assert_not_awaited()

        tenant, lead, conversation, contact, _connection, db = _send_context()
        contact.lead_id = uuid.uuid4()

        async def invoke_mismatched_contact():
            with patch.object(inbox, "dispatch_outbound_message", dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox.enviar_mensagem_por_conversa(
                        empresa_id=str(tenant), lead_id=str(lead.id), conversation_id=str(conversation.id),
                        payload=inbox.SendMessagePayload(texto="synthetic"), db=db,
                    )
            return raised.exception

        self.assertEqual(asyncio.run(invoke_mismatched_contact()).status_code, 409)
        dispatcher.assert_not_awaited()

    def test_media_uses_selected_conversation_contact_and_existing_dispatcher(self):
        tenant, lead, conversation, contact, connection, db = _send_context()
        file = SimpleNamespace(
            content_type="image/png", filename="synthetic.png",
            read=AsyncMock(return_value=b"synthetic-media-bytes"),
        )
        media_dispatcher = AsyncMock(return_value={"key": {"id": "simulated-message"}})

        async def invoke():
            with patch("app.services.evolution_service.enviar_midia_base64", media_dispatcher), \
                 patch("app.services.outbound_origin_tracker.marcar_outbound_backend", new=AsyncMock()), \
                 patch("app.services.followup_engine.registrar_followup_message_event", new=AsyncMock()), \
                 patch.object(inbox.manager, "broadcast_to_empresa", new=AsyncMock()):
                return await inbox._dispatch_manual_media(
                    db, context=await inbox._resolve_inbox_send_context(
                        db, empresa_id=tenant, lead_id=lead.id, conversation_id=conversation.id,
                    ),
                    file=file, caption="synthetic caption", tipo_mensagem="image", client_message_id="media-1",
                )

        response = asyncio.run(invoke())
        self.assertEqual(response["status"], "success")
        media_dispatcher.assert_awaited_once()
        self.assertIs(media_dispatcher.await_args.kwargs["conexao"], connection)
        self.assertEqual(media_dispatcher.await_args.kwargs["numero"], "5511999900001")
        self.assertEqual(db.added[0].channel_conversation_id, conversation.id)
        self.assertEqual(db.added[0].metadata_json["inbox_client_message_id"], "media-1")

    def test_stable_media_helper_requires_idempotency_key_before_dispatch(self):
        tenant, lead, conversation, _contact, _connection, db = _send_context()
        context = asyncio.run(inbox._resolve_inbox_send_context(
            db, empresa_id=tenant, lead_id=lead.id, conversation_id=conversation.id,
        ))
        file = SimpleNamespace(
            content_type="image/png", filename="synthetic.png", read=AsyncMock(return_value=b"synthetic"),
        )
        media_dispatcher = AsyncMock()

        async def invoke():
            with patch("app.services.evolution_service.enviar_midia_base64", media_dispatcher):
                with self.assertRaises(HTTPException) as raised:
                    await inbox._dispatch_manual_media(
                        db, context=context, file=file, tipo_mensagem="image", require_client_message_id=True,
                    )
            return raised.exception

        error = asyncio.run(invoke())
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.detail, "Identificador da mensagem obrigatório")
        file.read.assert_not_awaited()
        media_dispatcher.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
