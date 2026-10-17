import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.marketing_api import MetaMarketingApiClient
from app.services.meta.marketing_credentials import (
    ADMIN_MARKETING_SOURCE,
    MetaMarketingCapability,
    MarketingCredentialResolver,
)


class MarketingCredentialScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant_id = uuid.uuid4()
        self.config = type("Config", (), {
            "empresa_id": self.tenant_id,
            "business_id": "business-tenant-a",
            "ad_account_id": "act_12345",
        })()
        self.grant = type("Grant", (), {
            "empresa_id": self.tenant_id,
            "business_id": "business-tenant-a",
            "ad_account_id": "12345",
            "credential_source": "ADMIN_MANAGED_MARKETING_TOKEN",
            "read_enabled": True,
            "verified_at": object(),
            "verification_method": "business_owner",
        })()
        self.resolver = MarketingCredentialResolver()

    def session(self, grant=None):
        return type("Session", (), {"scalar": AsyncMock(return_value=grant)})()

    async def test_tenant_marketing_oauth_takes_priority_over_admin_token(self):
        connection = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"encrypted_marketing_access_token": "marketing-encrypted"},
            "metadata_json": {"marketing_granted_permissions": ["ads_read"]},
        })()
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=connection)):
                    with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="tenant-marketing-token"):
                        credential = await self.resolver.resolve(self.session(self.grant), self.tenant_id, "ads_read", allow_admin_fallback=True)
        self.assertEqual(credential.credential_source, "tenant_marketing_oauth")
        self.assertEqual(credential.access_token, "tenant-marketing-token")

    async def test_explicit_admin_tenant_business_account_grant_enables_read_only(self):
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=None)):
                    session = self.session(self.grant)
                    credential = await self.resolver.resolve(
                        session, self.tenant_id, MetaMarketingCapability.ADS_READ,
                        ad_account_id="12345", allow_admin_fallback=True,
                    )
        self.assertEqual(credential.credential_source, ADMIN_MARKETING_SOURCE)
        self.assertTrue(credential.read_only)
        self.assertEqual(credential.ad_account_id, "12345")
        self.assertEqual(credential.business_id, "business-tenant-a")
        self.assertEqual(session.scalar.await_count, 1)

    async def test_admin_token_without_effective_allowlist_is_denied(self):
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=None)):
                    with self.assertRaisesRegex(MetaCredentialResolutionError, "ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED"):
                        await self.resolver.resolve(self.session(None), self.tenant_id, "ads_read", allow_admin_fallback=True)

    async def test_tenant_cannot_request_another_configured_account(self):
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with self.assertRaisesRegex(MetaCredentialResolutionError, "MARKETING_ACCOUNT_OUT_OF_SCOPE"):
                    await self.resolver.resolve(self.session(self.grant), self.tenant_id, "ads_read", ad_account_id="99999", allow_admin_fallback=True)

    async def test_whatsapp_token_is_never_used_as_marketing_credential(self):
        connection = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"encrypted_access_token": "whatsapp-encrypted"},
            "metadata_json": {"granted_permissions": ["ads_management", "business_management"]},
        })()
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": ""}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=connection)):
                    with patch("app.services.meta.marketing_credentials.decrypt_meta_secret") as decrypt:
                        with self.assertRaisesRegex(MetaCredentialResolutionError, "TENANT_MARKETING_CREDENTIAL_UNAVAILABLE"):
                            await self.resolver.resolve(self.session(None), self.tenant_id, "ads_read")
        decrypt.assert_not_called()

    async def test_admin_token_cannot_be_resolved_for_write_capability(self):
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
                with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=None)):
                    with self.assertRaisesRegex(MetaCredentialResolutionError, "TENANT_MARKETING_CREDENTIAL_UNAVAILABLE"):
                        await self.resolver.resolve(self.session(self.grant), self.tenant_id, "ads_management", allow_admin_fallback=True)

    async def test_separate_marketing_oauth_credential_is_bound_to_its_business_and_account(self):
        matching = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"encrypted_marketing_access_token": "marketing-encrypted", "marketing_granted_permissions": ["ads_management"]},
            "metadata_json": {"marketing_business_id": "business-tenant-a", "marketing_ad_account_id": "12345"},
        })()
        with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="tenant-token"):
            credential = self.resolver._tenant_marketing_credential(
                str(self.tenant_id), matching, MetaMarketingCapability.ADS_MANAGEMENT,
                "business-tenant-a", "12345",
            )
            wrong_account = self.resolver._tenant_marketing_credential(
                str(self.tenant_id), matching, MetaMarketingCapability.ADS_READ,
                "business-tenant-a", "99999",
            )
            wrong_business = self.resolver._tenant_marketing_credential(
                str(self.tenant_id), matching, MetaMarketingCapability.ADS_READ,
                "business-tenant-b", "12345",
            )
        self.assertEqual(credential.access_token, "tenant-token")
        self.assertFalse(credential.read_only)
        self.assertIsNone(wrong_account)
        self.assertIsNone(wrong_business)

    async def test_marketing_oauth_missing_write_scope_falls_back_to_legacy_marketing_token(self):
        newer_read_only = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"encrypted_marketing_access_token": "read-only-encrypted", "marketing_granted_permissions": ["ads_read"]},
            "metadata_json": {"marketing_business_id": "business-tenant-a", "marketing_ad_account_id": "12345"},
        })()
        legacy_write = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {("encrypted_" + "marketing_access_token"): "fixture-encrypted", "marketing_granted_permissions": ["ads_management"]},
            "metadata_json": {},
        })()
        session = self.session(self.grant)
        with patch.object(self.resolver, "_tenant_config", AsyncMock(return_value=self.config)):
            with patch.object(self.resolver, "_embedded_connection", AsyncMock(return_value=newer_read_only)):
                with patch.object(self.resolver, "_legacy_marketing_connections", AsyncMock(return_value=[legacy_write])):
                    with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="legacy-write-token"):
                        credential = await self.resolver.resolve(session, self.tenant_id, "ads_management")
        self.assertEqual(credential.access_token, "legacy-write-token")
        self.assertFalse(credential.read_only)

    async def test_admin_read_only_client_blocks_create_even_if_environment_flags_are_on(self):
        client = MetaMarketingApiClient("opaque-token", read_only=True)
        with patch.dict(os.environ, {
            "META_MARKETING_WRITE_ENABLED": "true",
            "META_MARKETING_CAMPAIGN_CREATE_ENABLED": "true",
        }, clear=False):
            with self.assertRaisesRegex(RuntimeError, "META_MARKETING_CREDENTIAL_READ_ONLY"):
                await client.create_campaign("12345", {"name": "not-sent"})

    async def test_scoped_get_checks_account_before_fetching_details(self):
        graph = MagicMock()
        graph._request = AsyncMock(side_effect=[
            {"id": "ad-from-b", "account_id": "99999"},
        ])
        client = MetaMarketingApiClient("opaque-token")
        with patch.object(MetaMarketingApiClient, "_client", return_value=graph):
            with self.assertRaisesRegex(RuntimeError, "MARKETING_ASSET_OUT_OF_SCOPE"):
                await client.get_scoped_ad("ad-from-b", "12345")
        graph._request.assert_awaited_once_with("GET", "ad-from-b", params={"fields": "id,account_id"})

    async def test_scoped_get_returns_details_only_after_account_matches(self):
        graph = MagicMock()
        graph._request = AsyncMock(side_effect=[
            {"id": "ad-a", "account_id": "act_12345"},
            {"id": "ad-a", "account_id": "12345", "name": "safe"},
        ])
        client = MetaMarketingApiClient("opaque-token")
        with patch.object(MetaMarketingApiClient, "_client", return_value=graph):
            result = await client.get_scoped_ad("ad-a", "12345")
        self.assertEqual(result["name"], "safe")
        self.assertEqual(graph._request.await_count, 2)


if __name__ == "__main__":
    unittest.main()
