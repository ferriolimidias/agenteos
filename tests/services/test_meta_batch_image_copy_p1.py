import base64
import unittest
from io import BytesIO

from PIL import Image

from app.api.routers.traffic_meta import TrafficMetaBatchCreateRequest, _ensure_full_ad_input


class MetaBatchImageCopyP1Tests(unittest.TestCase):
    def test_batch_request_caps_items_at_five(self):
        with self.assertRaises(ValueError):
            TrafficMetaBatchCreateRequest(ads=[{}] * 6)

    def test_full_input_preserves_stable_item_key(self):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), (20, 30, 40)).save(buffer, format="JPEG")
        self.assertEqual(
            _ensure_full_ad_input(
                {
                    "item_key": "stable-item",
                    "media_type": "image",
                    "media_data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(),
                    "primary_text": "Texto",
                    "headline": "Titulo",
                },
                1,
            )["item_key"],
            "stable-item",
        )

    def test_draft_input_accepts_missing_copy_before_ai(self):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), (20, 30, 40)).save(buffer, format="JPEG")
        prepared = _ensure_full_ad_input(
            {
                "item_key": "draft-item",
                "media_type": "image",
                "media_data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(),
            },
            1,
            require_copy=False,
        )
        self.assertEqual(prepared["primary_text"], "")
        self.assertEqual(prepared["headline"], "")

    def test_frontend_resume_contract_is_present(self):
        with open("frontend/src/pages/tenant/TrafficMeta.jsx", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("resume_creation_id", source)
        self.assertIn("loadCampaignDraft", source)
        self.assertIn("saveCampaignDraft", source)
        self.assertIn("fingerprintFile", source)
        self.assertIn("Criar campanha e anúncios", source)
        self.assertIn("draft_session_id", source)
        self.assertIn("Gerando texto, título e descrição", source)
        self.assertIn("animate-pulse", source)


if __name__ == "__main__":
    unittest.main()
