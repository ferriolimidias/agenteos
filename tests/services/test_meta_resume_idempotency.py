import os
import unittest
import uuid
from unittest.mock import patch

from app.api.routers.traffic_meta import (
    _ensure_resume_item_names,
    _is_golden_whatsapp_binding,
    _resume_item_write_plan,
    _resume_parent_write_plan,
)
from app.services.meta.graph_client import MetaGraphError
from app.services.meta.marketing_api import MetaMarketingApiClient


class _Response:
    status_code = 500

    def json(self):
        return {"error": {"code": 1, "type": "OAuthException", "message": "An unknown error has occurred."}}


class _Transport:
    def __init__(self):
        self.posts = []

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return _Response()


class MetaResumeIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_parents_and_five_pending_items_plan_only_item_writes(self):
        self.assertEqual(_resume_parent_write_plan(meta_campaign_id="campaign-1", meta_adset_id="adset-1"), {"campaign": False, "adset": False})
        company_id = uuid.UUID("a7384cb4-98b2-4750-94bc-3b329d35156d")
        states = [
            _ensure_resume_item_names(state={"sequence": index}, company_name="Leonardo Personal", empresa_id=company_id, campaign_sequence=8, item_sequence=index)
            for index in range(1, 6)
        ]
        self.assertTrue(all(_resume_item_write_plan(state) == {"media": True, "creative": True, "ad": True} for state in states))
        self.assertEqual(len({state["creative_name"] for state in states}), 5)
        self.assertEqual(len({state["ad_name"] for state in states}), 5)

    def test_confirmed_item_is_fully_reused(self):
        state = {"media_reference": "hash-1", "creative_id": "creative-1", "ad_id": "ad-1"}
        self.assertEqual(_resume_item_write_plan(state), {"media": False, "creative": False, "ad": False})

    def test_canonical_graph_phone_does_not_trigger_binding_update(self):
        self.assertTrue(_is_golden_whatsapp_binding({"page_id": "page-1", "smart_pse_enabled": False, "whatsapp_phone_number": "5554999995742"}, page_id="page-1", whatsapp_phone_number="+55 54 99999-5742"))

    async def test_ambiguous_graph_write_has_one_attempt(self):
        transport = _Transport()
        client = MetaMarketingApiClient("token", graph_version="v26.0", transport=transport, max_attempts=2)
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_CREATIVE_CREATE_ENABLED": "true"}, clear=False):
            with self.assertRaises(MetaGraphError):
                await client.create_creative("111", {"name": "creative"})
        self.assertEqual(len(transport.posts), 1)


if __name__ == "__main__":
    unittest.main()
