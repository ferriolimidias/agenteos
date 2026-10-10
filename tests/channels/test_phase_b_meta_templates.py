from __future__ import annotations

import pathlib
import unittest
from unittest.mock import AsyncMock

from app.services.meta_templates import (
    normalize_meta_template_category,
    normalize_meta_template_status,
    parse_template_components,
    render_template_preview,
    sanitize_meta_payload,
    validate_template_parameters,
)
from app.services.channels.contracts import ChannelConnectionContext
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppError, MetaWhatsAppProvider
from app.services.channels.types import Channel, Provider
from db.models import MetaMessageTemplate, MetaTemplateComponent, MetaTemplateSyncRun


ROOT = pathlib.Path(__file__).resolve().parents[2]


class MetaTemplateModelTests(unittest.TestCase):
    def test_models_have_tenant_safe_constraints(self):
        self.assertIn("uq_meta_templates_remote_id", {c.name for c in MetaMessageTemplate.__table__.constraints})
        self.assertIn("uq_meta_templates_name_language_waba", {c.name for c in MetaMessageTemplate.__table__.constraints})
        self.assertIn("fk_meta_templates_empresa_conexao", {c.name for c in MetaMessageTemplate.__table__.constraints})
        self.assertIn("fk_meta_template_components_empresa_template", {c.name for c in MetaTemplateComponent.__table__.constraints})
        self.assertIn("uq_meta_template_sync_runs_idempotency", {c.name for c in MetaTemplateSyncRun.__table__.constraints})

    def test_migration_files_are_additive_and_have_rollback(self):
        upgrade = (ROOT / "db/migrations/2026_07_21_phase_b_meta_templates.sql").read_text(encoding="utf-8")
        rollback = (ROOT / "db/migrations/2026_07_21_phase_b_meta_templates_rollback.sql").read_text(encoding="utf-8")
        for table in ("meta_message_templates", "meta_template_components", "meta_template_sync_runs"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", upgrade)
            self.assertIn(f"DROP TABLE IF EXISTS {table}", rollback)
        self.assertNotIn("DELETE FROM", upgrade.upper())
        self.assertNotIn("TRUNCATE", upgrade.upper())
        self.assertNotIn("DROP TABLE", upgrade.upper())


class MetaTemplateParserTests(unittest.TestCase):
    def test_status_and_category_are_extensible(self):
        self.assertEqual(normalize_meta_template_status("APPROVED"), "approved")
        self.assertEqual(normalize_meta_template_status("BRAND_NEW_STATUS"), "unknown")
        self.assertEqual(normalize_meta_template_category("MARKETING"), "marketing")
        self.assertEqual(normalize_meta_template_category("NEW_CATEGORY"), "unknown")

    def test_components_placeholders_media_buttons_carousel_auth_unknown(self):
        parsed = parse_template_components(
            [
                {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["media"]}},
                {"type": "BODY", "text": "Olá {{1}}, pedido {{pedido}}"},
                {"type": "FOOTER", "text": "Rodapé"},
                {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://x.test/{{code}}"}, {"type": "OTP"}]},
                {"type": "CAROUSEL", "cards": []},
                {"type": "FUTURE_COMPONENT", "text": "x"},
            ]
        )
        names = {item.name for item in parsed.placeholders}
        self.assertTrue({"header_image", "1", "pedido", "button_0_code"}.issubset(names))
        self.assertTrue(parsed.requires_media)
        self.assertTrue(parsed.has_buttons)
        self.assertTrue(parsed.has_dynamic_url)
        self.assertTrue(parsed.has_carousel)
        self.assertTrue(parsed.has_authentication)
        self.assertIn("FUTURE_COMPONENT", parsed.unknown_components)

    def test_parameter_validation_and_preview(self):
        components = [{"type": "BODY", "text": "Olá {{nome}}"}]
        parsed = parse_template_components(components)
        self.assertFalse(validate_template_parameters(parsed, {})["valid"])
        self.assertFalse(validate_template_parameters(parsed, {"nome": "Ana", "extra": "x"})["valid"])
        preview = render_template_preview(components, {"nome": "Ana"})
        self.assertTrue(preview["validation"]["valid"])
        self.assertEqual(preview["components"][0]["text"], "Olá Ana")

    def test_sanitization_removes_secrets(self):
        payload = sanitize_meta_payload({"access_token": "secret", "nested": {"Authorization": "Bearer x"}, "name": "ok"})
        self.assertEqual(payload["access_token"], "<redacted>")
        self.assertEqual(payload["nested"]["Authorization"], "<redacted>")
        self.assertEqual(payload["name"], "ok")


class MetaTemplateStaticSafetyTests(unittest.TestCase):
    def test_router_has_tenant_access_and_no_send_endpoint(self):
        source = (ROOT / "app/api/routers/meta_templates.py").read_text(encoding="utf-8")
        self.assertIn("require_tenant_access", source)
        self.assertNotIn("send_template", source)
        self.assertNotIn("/send", source)

    def test_template_list_serializes_components_rejection_reason_and_scopes_query(self):
        source = (ROOT / "app/api/routers/meta_templates.py").read_text(encoding="utf-8")
        self.assertIn("MetaMessageTemplate.empresa_id == empresa_uuid", source)
        self.assertIn("MetaMessageTemplate.conexao_id == _uuid_or_400(conexao_id, \"conexao_id\")", source)
        self.assertIn("selectinload(MetaMessageTemplate.components)", source)
        self.assertIn("_serialize_template(template, include_components=True)", source)
        self.assertIn("\"rejection_reason\": template.rejection_reason", source)

    def test_provider_requires_injected_transport_and_no_evolution_fallback(self):
        source = (ROOT / "app/services/channels/providers/meta_whatsapp.py").read_text(encoding="utf-8")
        self.assertIn("transport is None", source)
        self.assertIn("list_templates_page", source)
        self.assertNotIn("Evolution", source)

    def test_frontend_hides_internal_flags_and_tokens(self):
        source = (ROOT / "frontend/src/pages/tenant/TemplatesWhatsApp.jsx").read_text(encoding="utf-8")
        self.assertIn("Templates do WhatsApp", source)
        self.assertNotIn("META_LIVE_ACTIONS_ENABLED", source)
        self.assertNotIn("App Secret", source)
        self.assertNotIn("payload bruto", source.lower())

    def test_no_real_graph_call_in_phase_b_tests(self):
        verifier = (ROOT / "scripts/verificar_fase_b_meta_templates.py").read_text(encoding="utf-8")
        tests = (ROOT / "tests/channels/test_phase_b_meta_templates.py").read_text(encoding="utf-8")
        self.assertNotIn("graph.facebook.com", verifier)
        self.assertNotRegex(tests, r"(^|\n)\s*(import|from)\s+httpx\b")
        self.assertNotIn("Async" + "Client(", tests)


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {"id": "123456", "status": "PENDING", "category": "MARKETING"}
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeTransport:
    def __init__(self):
        self.posts = []
        self.byte_posts = []
        self.multipart_posts = []
        self.deletes = []

    async def post(self, url, *, headers, json):
        self.posts.append({"url": url, "headers": headers, "json": json})
        if "/uploads" in url:
            return _FakeResponse({"id": "upload:session-1"})
        return _FakeResponse()

    async def post_bytes(self, url, *, headers, content):
        self.byte_posts.append({"url": url, "headers": headers, "content": content})
        return _FakeResponse({"h": "header-handle-1"})

    async def post_multipart(self, url, *, headers, data, files):
        self.multipart_posts.append({"url": url, "headers": headers, "data": data, "files": files})
        return _FakeResponse({"id": "media-123"})

    async def delete(self, url, *, headers):
        self.deletes.append({"url": url, "headers": headers})
        return _FakeResponse({"success": True})


class _FakeConnection:
    id = "conn-1"
    empresa_id = "tenant-1"
    phone_number_id = "phone-1"
    waba_id = "waba-1"
    credenciais = {"access_token": "token-test"}


class MetaTemplateCreateProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_template_posts_to_connection_waba(self):
        transport = _FakeTransport()
        provider = MetaWhatsAppProvider(
            ChannelConnectionContext(
                empresa_id="tenant-1",
                connection_id="conn-1",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                status="connected",
                external_account_id="phone-1",
                metadata={"waba_id": "waba-1"},
            ),
            connection=_FakeConnection(),
            transport=transport,
        )
        result = await provider.create_template(
            waba_id="waba-1",
            template_payload={"name": "retomada", "language": "pt_BR", "category": "MARKETING", "components": []},
        )
        self.assertEqual(result["http_status"], 200)
        self.assertIn("/waba-1/message_templates", transport.posts[0]["url"])
        self.assertNotIn("token-test", str(result))

    async def test_create_template_blocks_cross_waba(self):
        provider = MetaWhatsAppProvider(
            ChannelConnectionContext(
                empresa_id="tenant-1",
                connection_id="conn-1",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                status="connected",
                external_account_id="phone-1",
                metadata={"waba_id": "waba-1"},
            ),
            connection=_FakeConnection(),
            transport=_FakeTransport(),
        )
        with self.assertRaises(MetaWhatsAppError):
            await provider.create_template(
                waba_id="waba-2",
                template_payload={"name": "retomada", "language": "pt_BR", "category": "MARKETING", "components": []},
            )

    async def test_resumable_upload_returns_header_handle(self):
        transport = _FakeTransport()
        provider = MetaWhatsAppProvider(
            ChannelConnectionContext(
                empresa_id="tenant-1",
                connection_id="conn-1",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                status="connected",
                external_account_id="phone-1",
                metadata={"waba_id": "waba-1"},
            ),
            connection=_FakeConnection(),
            transport=transport,
        )
        result = await provider.create_template_header_handle(
            app_id="app-1",
            file_name="header.png",
            mime_type="image/png",
            content=b"image-bytes",
        )
        self.assertEqual(result["header_handle"], "header-handle-1")
        self.assertIn("/app-1/uploads", transport.posts[0]["url"])
        self.assertIn("upload:session-1", transport.byte_posts[0]["url"])
        self.assertEqual(transport.byte_posts[0]["headers"]["file_offset"], "0")

    async def test_campaign_media_upload_uses_phone_number_media_endpoint(self):
        transport = _FakeTransport()
        provider = MetaWhatsAppProvider(
            ChannelConnectionContext(
                empresa_id="tenant-1",
                connection_id="conn-1",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                status="connected",
                external_account_id="phone-1",
                metadata={"waba_id": "waba-1"},
            ),
            connection=_FakeConnection(),
            transport=transport,
        )
        result = await provider.upload_media_bytes(mime_type="image/jpeg", content=b"image-bytes", file_name="campaign.jpg")
        self.assertEqual(result["media_id"], "media-123")
        self.assertIn("/phone-1/media", transport.multipart_posts[0]["url"])
        self.assertEqual(transport.multipart_posts[0]["data"]["messaging_product"], "whatsapp")

    async def test_media_cleanup_uses_media_id_endpoint(self):
        transport = _FakeTransport()
        provider = MetaWhatsAppProvider(
            ChannelConnectionContext(
                empresa_id="tenant-1",
                connection_id="conn-1",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                status="connected",
                external_account_id="phone-1",
                metadata={"waba_id": "waba-1"},
            ),
            connection=_FakeConnection(),
            transport=transport,
        )
        result = await provider.delete_media(media_id="media-123")
        self.assertEqual(result["http_status"], 200)
        self.assertIn("/media-123", transport.deletes[0]["url"])


if __name__ == "__main__":
    unittest.main()
