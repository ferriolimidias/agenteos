from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.services.channel_factory import despachar_mensagem
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppProvider
from app.services.channels.contracts import ChannelConnectionContext, ChannelOutboundMessage
from app.services.channels.types import Channel, Provider
from app.services.outbox_service import dispatch_outbox_now


class _Response:
    status_code = 200

    def json(self):
        return {"messages": [{"id": "wamid.test-followup"}]}


class _Transport:
    async def post(self, url, *, headers, json):
        return _Response()


class _SessionContext:
    def __init__(self):
        self.session = object()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


class FollowupExternalIdTests(IsolatedAsyncioTestCase):
    def _provider(self):
        connection = SimpleNamespace(phone_number_id="phone-1", credenciais={"access_token": "token"})
        context = ChannelConnectionContext(
            empresa_id="tenant-1", connection_id="connection-1", channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP, status="connected", external_account_id="phone-1",
        )
        return MetaWhatsAppProvider(context, connection=connection, transport=_Transport())

    async def test_meta_provider_extracts_graph_message_id(self):
        result = await self._provider().send_text(ChannelOutboundMessage(
            empresa_id="tenant-1", connection_id="connection-1", channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP, destination="5511999999999", text="oi",
        ))
        self.assertEqual(result["external_message_id"], "wamid.test-followup")

    async def test_outbox_persists_id_from_channel_details(self):
        outbox = SimpleNamespace(empresa_id=uuid.uuid4(), conexao_id=uuid.uuid4(), channel="whatsapp", provider="meta_whatsapp")
        record_attempt = AsyncMock()
        dispatcher = AsyncMock(return_value={"sent": True, "external_message_ids": ["wamid.test-followup"]})
        with patch("app.services.outbox_service.record_attempt", record_attempt):
            result = await dispatch_outbox_now(object(), outbox=outbox, identificador_origem="contact", texto="oi", dispatcher=dispatcher)
        self.assertTrue(result)
        self.assertEqual(record_attempt.await_args.kwargs["external_message_id"], "wamid.test-followup")
        self.assertTrue(dispatcher.await_args.kwargs["return_details"])

    async def test_channel_factory_returns_id_without_fabricating_one(self):
        connection = SimpleNamespace(
            id=uuid.uuid4(), empresa_id=uuid.uuid4(), tipo="META", provider="meta_official", channel="whatsapp",
            status="connected", phone_number_id="phone-1", waba_id="waba-1", credenciais={"access_token": "token"},
        )
        with patch("app.services.channel_factory.AsyncSessionLocal", return_value=_SessionContext()), \
             patch("app.services.channel_factory._resolver_conexao_whatsapp_explicita", new=AsyncMock(return_value=connection)), \
             patch("app.services.channel_factory._meta_dispatch_allowed", new=AsyncMock(return_value=(True, "ok"))), \
             patch("app.services.channel_factory.MetaWhatsAppProvider.send_text", new=AsyncMock(return_value={"external_message_id": "wamid.test-followup"})), \
             patch("app.services.channel_factory.asyncio.sleep", new=AsyncMock()):
            result = await despachar_mensagem(
                canal="whatsapp", identificador_origem="contact", texto="oi",
                conexao_id=str(connection.id), empresa_id=str(connection.empresa_id), return_details=True,
            )
        self.assertEqual(result, {"sent": True, "external_message_ids": ["wamid.test-followup"]})

    async def test_provider_without_id_is_rejected(self):
        provider = self._provider()
        provider.transport = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {"messages": []})))
        with self.assertRaises(Exception):
            await provider.send_text(ChannelOutboundMessage(
                empresa_id="tenant-1", connection_id="connection-1", channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP, destination="contact", text="oi",
            ))
