from io import BytesIO
import base64
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.services.meta.ad_copy_generator import (
    AdCopyGenerationError,
    ImageAdCopyResult,
    TenantAdCopyContext,
    _ensure_no_unsupported_image_claims,
    generate_image_ad_copy,
)
from app.api.routers.traffic_meta import _ensure_full_ad_input
from app.services.meta.image_validation import ImageValidationError, validate_image_bytes
from app.services.meta.traffic_campaign_builder import build_ad_payload, build_adset_payload


class _Structured:
    def __init__(self, value):
        self.value = value
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return self.value


class _FakeVisionLLM:
    model_name = "fake-vision"

    def __init__(self, value):
        self.structured = _Structured(value)

    def with_structured_output(self, _schema):
        return self.structured


class MetaBatchImageCopyTests(unittest.IsolatedAsyncioTestCase):
    def test_valid_jpeg_and_magic_mismatch(self):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), (220, 30, 70)).save(buffer, format="JPEG")
        jpeg = buffer.getvalue()
        width, height, fmt = validate_image_bytes(jpeg, "image/jpeg")
        self.assertEqual(fmt, "JPEG")
        self.assertGreaterEqual(width, 10)
        with self.assertRaises(ImageValidationError):
            validate_image_bytes(jpeg, "image/png")

    def test_copy_guard_rejects_unconfigured_price(self):
        context = TenantAdCopyContext(empresa_id="tenant", nome_empresa="Empresa")
        result = ImageAdCopyResult(primary_text="Oferta por R$ 97", headline="Fale conosco")
        with self.assertRaisesRegex(AdCopyGenerationError, "COPY_UNSUPPORTED_PRICE"):
            _ensure_no_unsupported_image_claims(result, context)

    async def test_multimodal_structured_output_uses_image_content(self):
        context = TenantAdCopyContext(empresa_id="tenant", nome_empresa="Empresa")
        llm = _FakeVisionLLM({"primary_text": "Converse com a equipe.", "headline": "Fale no WhatsApp", "description": ""})
        with patch("app.services.meta.ad_copy_generator.load_tenant_ad_copy_context", new=AsyncMock(return_value=context)):
            result, _, metadata = await generate_image_ad_copy(
                session=object(),
                empresa_id="tenant",
                image_data_url="data:image/jpeg;base64,AAAA",
                objective="OUTCOME_LEADS",
                llm=llm,
            )
        self.assertEqual(result.headline, "Fale no WhatsApp")
        self.assertEqual(metadata["model"], "fake-vision")
        self.assertEqual(llm.structured.messages[1]["content"][1]["type"], "image_url")

    def test_batch_keeps_golden_adset_and_ad_contracts(self):
        adset = build_adset_payload(
            name="batch",
            campaign_id="campaign",
            daily_budget_cents=2000,
            page_id="page",
            phone_number="+55 11 99999-0000",
            targeting={"geo_locations": {"countries": ["BR"]}},
        )
        self.assertNotIn("whats_app_business_phone_number_id", adset["promoted_object"])
        self.assertEqual(adset["promoted_object"]["smart_pse_enabled"], False)
        self.assertEqual(build_ad_payload(name="ad", adset_id="adset", creative_id="creative")["status"], "PAUSED")

    def test_batch_item_key_is_normalized_without_runtime_signature_error(self):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="JPEG")
        prepared = _ensure_full_ad_input(
            {
                "item_key": "x" * 200,
                "media_type": "image",
                "media_data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(),
                "primary_text": "Teste",
                "headline": "Teste",
            },
            1,
        )
        self.assertEqual(len(prepared["item_key"]), 160)


if __name__ == "__main__":
    unittest.main()
