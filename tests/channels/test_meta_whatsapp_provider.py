import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.channels.contracts import ChannelConnectionContext, ChannelOutboundMessage
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppError, MetaWhatsAppProvider
from app.services.channels.types import Channel, Provider
from app.services.meta.credential_crypto import encrypt_meta_secret
from unittest.mock import patch
import os


class FakeResponse:
    status_code = 200

    def json(self):
        return {"messages": [{"id": "wamid.mock-1"}]}


class FakeJsonResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class MetaWhatsAppProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = ChannelConnectionContext(
            empresa_id="empresa-a", connection_id="wa-1", channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP, status="ativo", external_account_id="phone-1",
        )
        self.connection = SimpleNamespace(
            phone_number_id="phone-1", credenciais={"access_token": "secret-token"}
        )

    def message(self, **kwargs):
        values = {
            "empresa_id": "empresa-a", "connection_id": "wa-1", "channel": Channel.WHATSAPP,
            "provider": Provider.META_WHATSAPP, "destination": "5511999999999", "text": "ola", **kwargs
        }
        return ChannelOutboundMessage(**values)

    async def test_sends_text_through_injected_transport(self):
        transport = SimpleNamespace(post=AsyncMock(return_value=FakeResponse()))
        provider = MetaWhatsAppProvider(self.context, self.connection, transport=transport)
        result = await provider.send_text(self.message(idempotency_key="idem-1"))
        self.assertEqual(result["external_message_id"], "wamid.mock-1")
        transport.post.assert_awaited_once()
        args = transport.post.await_args.kwargs
        self.assertIn("/v25.0/phone-1/messages", transport.post.await_args.args[0])
        self.assertEqual(args["json"]["messaging_product"], "whatsapp")
        self.assertEqual(args["json"]["text"]["body"], "ola")
        self.assertEqual(args["headers"]["Authorization"], "Bearer secret-token")

    async def test_no_transport_fails_closed_without_http(self):
        provider = MetaWhatsAppProvider(self.context, self.connection)
        with self.assertRaisesRegex(MetaWhatsAppError, "Transporte Meta"):
            await provider.send_text(self.message())

    async def test_create_template_error_preserves_structured_meta_error(self):
        connection = SimpleNamespace(waba_id="waba-1", credenciais={"access_token": "secret-token"})
        context = ChannelConnectionContext(
            empresa_id="empresa-a",
            connection_id="wa-1",
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
            status="ativo",
            external_account_id="phone-1",
            metadata={"waba_id": "waba-1"},
        )
        response = FakeJsonResponse(
            400,
            {
                "error": {
                    "message": "Invalid parameter",
                    "type": "OAuthException",
                    "code": 100,
                    "error_subcode": 2388043,
                    "error_user_title": "Botão inválido",
                    "error_user_msg": "URL inválida",
                    "error_data": {"details": "buttons[0].url"},
                    "fbtrace_id": "trace-123",
                }
            },
        )
        provider = MetaWhatsAppProvider(context, connection, transport=SimpleNamespace(post=AsyncMock(return_value=response)))
        with self.assertRaisesRegex(MetaWhatsAppError, "Invalid parameter") as error:
            await provider.create_template(
                waba_id="waba-1",
                template_payload={"name": "circular", "language": "pt_BR", "category": "UTILITY", "components": [{"type": "BODY", "text": "Oi"}]},
            )
        self.assertEqual(error.exception.http_status, 400)
        self.assertEqual(error.exception.meta_error["code"], 100)
        self.assertEqual(error.exception.meta_error["error_data"]["details"], "buttons[0].url")
        self.assertNotIn("secret-token", str(error.exception))

    async def test_official_resumable_upload_contract_uses_h_as_header_handle(self):
        connection = SimpleNamespace(waba_id="waba-1", credenciais={"access_token": "secret-token"})
        context = ChannelConnectionContext(
            empresa_id="empresa-a",
            connection_id="wa-1",
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
            status="ativo",
            external_account_id="phone-1",
            metadata={"waba_id": "waba-1"},
        )
        transport = SimpleNamespace(
            post=AsyncMock(return_value=FakeJsonResponse(200, {"id": "upload:session-1"})),
            post_bytes=AsyncMock(return_value=FakeJsonResponse(200, {"h": "HEADER_HANDLE_MOCK"})),
        )
        provider = MetaWhatsAppProvider(context, connection, transport=transport)
        result = await provider.create_template_header_handle(
            app_id="app-1",
            file_name="example.png",
            mime_type="image/png",
            content=b"fake-image-bytes",
        )
        self.assertEqual(result["header_handle"], "HEADER_HANDLE_MOCK")
        self.assertEqual(result["upload_session_id"], "upload:session-1")
        self.assertIn("/v25.0/app-1/uploads", transport.post.await_args.args[0])
        self.assertIn("file_name=example.png", transport.post.await_args.args[0])
        self.assertIn("/v25.0/upload:session-1", transport.post_bytes.await_args.args[0])
        self.assertEqual(transport.post_bytes.await_args.kwargs["headers"]["file_offset"], "0")

    async def test_retries_transient_failure_without_exposing_secret(self):
        response = SimpleNamespace(status_code=503, json=lambda: {"error": {"message": "private"}})
        transport = SimpleNamespace(post=AsyncMock(return_value=response))
        provider = MetaWhatsAppProvider(self.context, self.connection, transport=transport)
        with self.assertRaisesRegex(MetaWhatsAppError, "status=503") as error:
            await provider.send_text(self.message())
        self.assertEqual(transport.post.await_count, 2)
        self.assertNotIn("secret-token", str(error.exception))

    async def test_missing_phone_or_token_fails_before_transport(self):
        transport = SimpleNamespace(post=AsyncMock())
        connection = SimpleNamespace(phone_number_id=None, credenciais={})
        context = ChannelConnectionContext(
            empresa_id="empresa-a", connection_id="wa-1", channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP, status="ativo",
        )
        provider = MetaWhatsAppProvider(context, connection, transport=transport)
        with self.assertRaisesRegex(MetaWhatsAppError, "phone_number_id"):
            await provider.send_text(self.message())
        transport.post.assert_not_awaited()

    async def test_encrypted_token_is_decrypted_only_for_transport(self):
        with patch.dict(os.environ, {"META_CREDENTIAL_ENCRYPTION_KEY": "test-meta-credential-key-with-32chars"}, clear=False):
            encrypted = encrypt_meta_secret("secret-token")
            connection = SimpleNamespace(phone_number_id="phone-1", credenciais={"encrypted_access_token": encrypted})
            transport = SimpleNamespace(post=AsyncMock(return_value=FakeResponse()))
            provider = MetaWhatsAppProvider(self.context, connection, transport=transport)
            await provider.send_text(self.message())
            args = transport.post.await_args.kwargs
            self.assertEqual(args["headers"]["Authorization"], "Bearer secret-token")

    async def test_context_mismatch_is_rejected(self):
        transport = SimpleNamespace(post=AsyncMock())
        provider = MetaWhatsAppProvider(self.context, self.connection, transport=transport)
        with self.assertRaises(ValueError):
            await provider.send_text(self.message(empresa_id="empresa-b"))
        transport.post.assert_not_awaited()

    async def test_send_template_requires_wamid_on_success(self):
        response = FakeJsonResponse(200, {"messages": [{}]})
        transport = SimpleNamespace(post=AsyncMock(return_value=response))
        provider = MetaWhatsAppProvider(self.context, self.connection, transport=transport)
        with self.assertRaisesRegex(MetaWhatsAppError, "wamid"):
            await provider.send_template(
                self.message(message_type="template", text=""),
                template_payload={"name": "approved_template", "language": {"code": "pt_BR"}},
            )


if __name__ == "__main__":
    unittest.main()
