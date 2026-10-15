import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.routers.traffic_meta import (
    TrafficMetaExistingPostPreviewRequest,
    list_existing_ad_posts,
    preview_existing_post_ad_creative,
)
from app.services.meta.existing_post_creatives import (
    ExistingPostSelectionError,
    build_existing_post_creative_preview,
    issue_existing_post_selection_token,
    verify_existing_post_selection_token,
)
from app.services.meta.marketing_api import MetaMarketingApiClient


class ExistingPostCreativeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        self.page_id = "page_123"
        self.ig_account_id = "ig_456"
        self.ad_account_id = "987654"
        self.config = SimpleNamespace(ad_account_id=self.ad_account_id, page_id=self.page_id)
        self.admin = SimpleNamespace(role="admin_empresa", empresa_id=self.tenant_id, email="operator@example.test")

    def _token(self, *, platform="instagram", source_id="ig_media_456", media_type="VIDEO", product="REELS", tenant=None):
        return issue_existing_post_selection_token(
            empresa_id=tenant or self.tenant_id,
            ad_account_id=self.ad_account_id,
            page_id=self.page_id,
            instagram_account_id=self.ig_account_id if platform == "instagram" else None,
            platform=platform,
            source_id=source_id,
            media_type=media_type,
            media_product_type=product,
        )

    def test_signed_selection_is_tenant_scoped_tamper_evident_and_short_lived(self):
        with patch.dict(os.environ, {"SECRET_KEY": "a" * 48}, clear=False):
            token = self._token()
            claims = verify_existing_post_selection_token(token, empresa_id=self.tenant_id)
            self.assertEqual(claims["source_id"], "ig_media_456")
            self.assertEqual(claims["instagram_account_id"], self.ig_account_id)
            with self.assertRaisesRegex(ExistingPostSelectionError, "SELECTION_TENANT_MISMATCH"):
                verify_existing_post_selection_token(token, empresa_id=self.other_tenant_id)
            encoded, signature = token.split(".", 1)
            tampered = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature
            with self.assertRaisesRegex(ExistingPostSelectionError, "SELECTION_TOKEN_INVALID"):
                verify_existing_post_selection_token(tampered, empresa_id=self.tenant_id)
            expired = issue_existing_post_selection_token(
                empresa_id=self.tenant_id, ad_account_id=self.ad_account_id, page_id=self.page_id,
                instagram_account_id=self.ig_account_id, platform="instagram", source_id="ig_media_456",
                media_type="VIDEO", media_product_type="REELS", now=100,
            )
            with self.assertRaisesRegex(ExistingPostSelectionError, "SELECTION_TOKEN_EXPIRED"):
                verify_existing_post_selection_token(expired, empresa_id=self.tenant_id, now=100 + 15 * 60)

    def test_preview_keeps_facebook_post_and_instagram_media_ids_distinct(self):
        facebook = build_existing_post_creative_preview(
            name="Facebook post preview", ad_account_id=self.ad_account_id, page_id=self.page_id,
            platform="facebook", source_id="page_123_post_7", instagram_account_id=None,
            media_type="IMAGE", media_product_type=None, objective="OUTCOME_SALES",
        )
        instagram = build_existing_post_creative_preview(
            name="Instagram media preview", ad_account_id=self.ad_account_id, page_id=self.page_id,
            platform="instagram", source_id="ig_media_456", instagram_account_id=self.ig_account_id,
            media_type="VIDEO", media_product_type="REELS", objective="OUTCOME_SALES",
        )
        self.assertEqual(facebook["creative_payload_preview"]["object_story_id"], "page_123_post_7")
        self.assertNotIn("source_instagram_media_id", facebook["creative_payload_preview"])
        self.assertEqual(instagram["creative_payload_preview"]["source_instagram_media_id"], "ig_media_456")
        self.assertEqual(instagram["creative_payload_preview"]["instagram_user_id"], self.ig_account_id)
        self.assertNotIn("object_story_id", instagram["creative_payload_preview"])
        for preview in (facebook, instagram):
            self.assertIsNone(preview["id_mapping"]["creative_id"])
            self.assertIsNone(preview["id_mapping"]["comment_id"])
            self.assertFalse(preview["meta_write_performed"])
            self.assertEqual(preview["compatibility"]["status"], "preliminary_compatible")
            self.assertFalse(preview["compatibility"]["meta_acceptance_verified"])

    def test_builder_rejects_unsupported_media_product_and_objective(self):
        for values in (
            {"platform": "instagram", "source_id": "ig_1", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED", "objective": "OUTCOME_SALES"},
            {"platform": "instagram", "source_id": "ig_1", "media_type": "IMAGE", "media_product_type": "STORY", "objective": "OUTCOME_SALES"},
            {"platform": "facebook", "source_id": "page_123_4", "media_type": "IMAGE", "media_product_type": None, "objective": "OUTCOME_ENGAGEMENT"},
        ):
            with self.assertRaisesRegex(ValueError, "EXISTING_POST_NOT_COMPATIBLE_FOR_PREVIEW"):
                build_existing_post_creative_preview(
                    name="Preview", ad_account_id=self.ad_account_id, page_id=self.page_id,
                    instagram_account_id=self.ig_account_id if values["platform"] == "instagram" else None,
                    **values,
                )

    async def test_meta_list_methods_use_bounded_graph_gets_only(self):
        graph = SimpleNamespace(_request=AsyncMock(return_value={"data": [], "paging": {}}))
        client = MetaMarketingApiClient("opaque", graph_version="v26.0")
        with patch.object(MetaMarketingApiClient, "_client", return_value=graph):
            await client.list_page_published_posts(self.page_id, limit=1000, after="cursor")
            await client.list_instagram_media_page(self.ig_account_id, limit=7)
        calls = graph._request.await_args_list
        self.assertEqual([call.args[0] for call in calls], ["GET", "GET"])
        self.assertEqual(calls[0].args[1], f"{self.page_id}/published_posts")
        self.assertEqual(calls[0].kwargs["params"]["limit"], 50)
        self.assertEqual(calls[1].args[1], f"{self.ig_account_id}/media")
        self.assertEqual(calls[1].kwargs["params"]["limit"], 7)

    async def test_mock_flow_lists_tenant_post_then_returns_preview_without_meta_write(self):
        page = {
            "id": self.page_id,
            "name": "Página de teste",
            "instagram_business_account": {"id": self.ig_account_id, "username": "brand_test"},
        }

        class Client:
            async def list_pages(inner_self):
                return [page]

            async def list_instagram_media_page(inner_self, account_id, *, limit, after):
                self.assertEqual(account_id, self.ig_account_id)
                return {"data": [{
                    "id": "ig_media_456", "caption": "Post sintético", "media_type": "VIDEO",
                    "media_product_type": "REELS", "media_url": "https://cdn.example.test/reel.mp4",
                    "thumbnail_url": "https://cdn.example.test/reel.jpg", "permalink": "https://instagram.example/reel",
                    "timestamp": "2026-10-01T12:00:00+0000",
                }], "paging": {"cursors": {"after": "cursor-next"}}}

            async def get_instagram_media(inner_self, media_id):
                self.assertEqual(media_id, "ig_media_456")
                return {"id": media_id, "media_type": "VIDEO", "media_product_type": "REELS"}

        with patch.dict(os.environ, {"SECRET_KEY": "a" * 48}, clear=False), \
             patch("app.api.routers.traffic_meta._get_config", AsyncMock(return_value=self.config)), \
             patch("app.api.routers.traffic_meta._client", AsyncMock(return_value=(Client(), {"read_only": True, "graph_version": "v26.0"}))) as get_client:
            listing = await list_existing_ad_posts(
                str(self.tenant_id), "instagram", self.page_id, q=None, limit=25, after=None,
                db=object(), _=self.admin,
            )
            self.assertEqual(listing["items"][0]["id_mapping"]["media_id"], "ig_media_456")
            self.assertIsNone(listing["items"][0]["id_mapping"]["post_id"])
            self.assertIsNone(listing["items"][0]["id_mapping"]["creative_id"])
            self.assertTrue(listing["has_more"])
            request = TrafficMetaExistingPostPreviewRequest(
                selection_token=listing["items"][0]["selection_token"], objective="OUTCOME_SALES",
            )
            result = await preview_existing_post_ad_creative(
                str(self.tenant_id), request, db=object(), current_user=self.admin,
            )
        self.assertEqual(result["preview"]["state"], "simulated")
        self.assertFalse(result["preview"]["meta_write_performed"])
        self.assertEqual(result["preview"]["creative_payload_preview"]["source_instagram_media_id"], "ig_media_456")
        self.assertEqual(get_client.await_count, 2)

    async def test_tenant_cannot_reuse_another_tenants_signed_selection(self):
        with patch.dict(os.environ, {"SECRET_KEY": "a" * 48}, clear=False):
            token = self._token()
            other_admin = SimpleNamespace(role="admin_empresa", empresa_id=self.other_tenant_id, email="other@example.test")
            request = TrafficMetaExistingPostPreviewRequest(selection_token=token)
            with self.assertRaises(HTTPException) as raised:
                await preview_existing_post_ad_creative(str(self.other_tenant_id), request, db=object(), current_user=other_admin)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
