import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from app.services.meta.marketing_credentials import (
    MetaMarketingCapability,
    MetaMarketingCredentialResolver,
    supports_ads_read,
)
from app.services.meta.graph_client import MetaGraphClient
from app.services.meta.marketing_readiness import _error_readiness, granted_permissions, marketing_ads_readiness


class MarketingReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_marketing_resolver_uses_latest_official_connection_for_reauthorization(self):
        captured = {}
        connection = object()

        class Result:
            def scalars(self):
                return self

            def first(self):
                return connection

        class Session:
            async def execute(self, statement):
                captured["statement"] = statement
                return Result()

        selected = await MetaMarketingCredentialResolver()._embedded_connection(Session(), uuid.uuid4())
        statement = str(captured["statement"])
        self.assertIs(selected, connection)
        self.assertIn("conexoes.channel =", statement)
        self.assertIn("ORDER BY conexoes.criado_em DESC, conexoes.id DESC", statement)

    def _readiness_fakes(self, permissions, *, listed_accounts=None):
        empresa_id = uuid.uuid4()
        connection = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"token_type": "bearer"},
            "metadata_json": {},
        })()
        config = type("Config", (), {
            "empresa_id": empresa_id,
            "ad_account_id": "act_123",
            "business_id": "business-1",
        })()

        class Result:
            def one(self):
                return (None, 0)

        class Session:
            async def scalar(self, _statement):
                return config

            async def execute(self, _statement):
                return Result()

            async def commit(self):
                return None

            async def flush(self):
                return None

        client = MagicMock()
        client.get_permissions = AsyncMock(return_value={"data": [
            {"permission": name, "status": "granted"} for name in permissions
        ]})
        client.list_ad_accounts = AsyncMock(return_value=listed_accounts or [])
        client.validate_ad_account = AsyncMock(return_value={
            "id": "act_123", "account_id": "123", "account_status": 1,
            "business": {"id": "business-1"},
        })
        client.list_campaigns = AsyncMock(return_value=[])
        client.list_adsets = AsyncMock(return_value=[])
        client.list_ads = AsyncMock(return_value=[])
        client.get_insights = AsyncMock(return_value=[])
        credential = type("Credential", (), {
            "access_token": "tenant-only-token",
            "credential_source": "admin_managed_marketing_token",
            "graph_version": "v26.0",
            "read_only": True,
        })()
        return empresa_id, connection, Session(), client, credential

    def _readiness_patches(self, connection, client, credential):
        settings = type("Settings", (), {
            "app_id": "app-id", "app_secret": "app-secret",
            "credential_encryption_key": "configured", "live_actions_enabled": True,
            "marketing_graph_api_version": "v26.0",
        })()
        return (
            patch("app.services.meta.marketing_readiness.meta_settings", return_value=settings),
            patch("app.services.meta.marketing_readiness.MetaMarketingCredentialResolver.resolve_tenant_for_permission_check", new=AsyncMock(return_value=credential)),
            patch("app.services.meta.marketing_readiness.MetaMarketingCredentialResolver._embedded_connection", new=AsyncMock(return_value=connection)),
            patch("app.services.meta.marketing_readiness.MetaMarketingApiClient", return_value=client),
        )

    async def test_missing_marketing_grant_is_permission_denied_and_skips_asset_reads(self):
        empresa_id, connection, session, client, credential = self._readiness_fakes({"whatsapp_business_management"})
        patches = self._readiness_patches(connection, client, credential)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await marketing_ads_readiness(session, empresa_id=empresa_id)
        self.assertEqual(result["marketing_readiness_status"], "PERMISSION_DENIED")
        self.assertEqual(result["status"], "ACTION_REQUIRED")
        self.assertFalse(result["permissions"]["ads_read"])
        self.assertFalse(result["permissions"]["ads_management"])
        client.validate_ad_account.assert_not_awaited()

    async def test_management_permission_and_account_graph_reads_can_reach_ready(self):
        empresa_id, connection, session, client, credential = self._readiness_fakes(
            {"ads_management"},
            listed_accounts=[{"id": "act_123", "account_id": "123", "business": {"id": "business-1"}}],
        )
        patches = self._readiness_patches(connection, client, credential)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await marketing_ads_readiness(session, empresa_id=empresa_id)
        self.assertEqual(result["marketing_readiness_status"], "READY_ADMIN_MANAGED_READ")
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["permissions"]["ads_read"])
        self.assertTrue(result["permissions"]["ads_management"])
        self.assertTrue(all(result["asset_access"].values()))
        self.assertEqual(result["insights"]["preflight"], "pass")
        client.get_insights.assert_awaited_once()

    async def test_permission_without_configured_account_access_is_not_ready(self):
        empresa_id, connection, session, client, credential = self._readiness_fakes({"ads_read"})
        client.validate_ad_account = AsyncMock(return_value={
            "id": "act_999", "account_id": "999", "account_status": 1,
            "business": {"id": "business-1"},
        })
        patches = self._readiness_patches(connection, client, credential)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await marketing_ads_readiness(session, empresa_id=empresa_id)
        self.assertEqual(result["marketing_readiness_status"], "ASSET_ACCESS_REQUIRED")
        self.assertFalse(result["asset_access"]["ad_account"])
        client.list_campaigns.assert_not_awaited()
        client.get_insights.assert_not_awaited()

    async def test_missing_business_binding_is_action_required_without_graph_access(self):
        empresa_id, connection, session, client, credential = self._readiness_fakes({"ads_read"})
        config = type("Config", (), {"empresa_id": empresa_id, "ad_account_id": "act_123", "business_id": None})()

        class Result:
            def one(self):
                return (None, 0)

        session.scalar = AsyncMock(return_value=config)
        with patch("app.services.meta.marketing_readiness.meta_settings", return_value=type("Settings", (), {
            "app_id": None, "app_secret": None, "credential_encryption_key": None,
            "live_actions_enabled": False, "marketing_graph_api_version": "v26.0",
        })()):
            result = await marketing_ads_readiness(session, empresa_id=empresa_id)
        self.assertEqual(result["marketing_readiness_status"], "BUSINESS_BINDING_REQUIRED")
        self.assertEqual(result["status"], "ACTION_REQUIRED")
        client.get_permissions.assert_not_awaited()

    async def test_ads_management_is_valid_read_capability_but_not_ads_read_grant(self):
        self.assertFalse(supports_ads_read(set()))
        self.assertTrue(supports_ads_read({"ads_read"}))
        self.assertTrue(supports_ads_read({"ads_management"}))

    async def test_permission_inventory_only_counts_granted_scopes(self):
        self.assertEqual(granted_permissions({"data": [
            {"permission": "ads_read", "status": "granted"},
            {"permission": "ads_management", "status": "declined"},
            {"permission": "public_profile", "status": "granted"},
        ]}), {"ads_read", "public_profile"})

    async def test_read_resolver_accepts_management_scope_as_meta_get_capability(self):
        connection = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {"encrypted_marketing_access_token": "encrypted"},
            "metadata_json": {"marketing_granted_permissions": ["ads_management"]},
        })()
        config = type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})()
        resolver = MetaMarketingCredentialResolver()
        with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=connection)):
            with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="tenant-token"):
                    resolved = await resolver.resolve(
                        object(), "tenant-a", MetaMarketingCapability.ADS_READ,
                        allow_admin_fallback=False,
                    )
        self.assertEqual(resolved.credential_source, "tenant_marketing_oauth")
        self.assertEqual(resolved.access_token, "tenant-token")

    async def test_marketing_read_token_is_separate_from_whatsapp_token_and_never_used_for_writes(self):
        connection = type("Connection", (), {
            "id": uuid.uuid4(),
            "credenciais": {
                "encrypted_access_token": "whatsapp-encrypted",
                "encrypted_marketing_access_token": "marketing-encrypted",
                "marketing_granted_permissions": ["ads_read"],
                "granted_permissions": ["whatsapp_business_management", "ads_management"],
            },
            "metadata_json": {},
        })()
        config = type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})()
        resolver = MetaMarketingCredentialResolver()
        with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=connection)):
            with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", side_effect=lambda value: {
                    "whatsapp-encrypted": "whatsapp-token", "marketing-encrypted": "marketing-token",
                }[value]):
                    read = await resolver.resolve(object(), "tenant-a", "ads_read", allow_admin_fallback=False)
                    with self.assertRaisesRegex(ValueError, "TENANT_MARKETING_CREDENTIAL_UNAVAILABLE"):
                        await resolver.resolve(object(), "tenant-a", "ads_management", allow_admin_fallback=False)
        self.assertEqual(read.access_token, "marketing-token")
        self.assertEqual(read.credential_source, "tenant_marketing_oauth")

    async def test_expired_token_and_asset_errors_are_not_reported_ready(self):
        class GraphFailure(Exception):
            code = "token_expired"

        self.assertEqual(_error_readiness(GraphFailure()), "REAUTH_MARKETING_REQUIRED")

        class AppAccessFailure(Exception):
            code = "app_review_pending"

        self.assertEqual(_error_readiness(AppAccessFailure()), "UNKNOWN")

    async def test_graph_permission_403_is_not_misclassified_as_invalid_token(self):
        error = MetaGraphClient._classify_error(
            403,
            {"error": {"code": 200, "message": "Permissions error: ads_read is required."}},
            path="act_123/insights",
        )
        self.assertEqual(error.code, "missing_permission")
        self.assertEqual(_error_readiness(error), "PERMISSION_DENIED")


if __name__ == "__main__":
    unittest.main()
