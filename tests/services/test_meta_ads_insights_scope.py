import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from app.services.meta.ads_insights import sync_ads_insights


class MetaAdsInsightsScopeTests(unittest.IsolatedAsyncioTestCase):
    async def _run_sync(self, *, account_business="business-1", shared_accounts=None):
        tenant_id = uuid.uuid4()
        config = type("Config", (), {
            "empresa_id": tenant_id,
            "ad_account_id": "act_12345",
            "business_id": "business-1",
        })()
        session = MagicMock()
        session.scalar = AsyncMock(return_value=config)
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        credential = type("Credential", (), {
            "access_token": "opaque",
            "credential_source": "admin_managed_marketing_token",
            "graph_version": "v26.0",
            "read_only": True,
        })()
        client = MagicMock()
        client.validate_ad_account = AsyncMock(return_value={
            "id": "act_12345", "account_id": "12345", "currency": "USD",
            "business": {"id": account_business} if account_business else {},
        })
        client.list_business_client_ad_accounts = AsyncMock(return_value=shared_accounts or [])
        client.get_insights = AsyncMock(return_value=[])
        with patch("app.services.meta.ads_insights.MetaMarketingCredentialResolver.resolve", new=AsyncMock(return_value=credential)) as resolve:
            with patch("app.services.meta.ads_insights.MetaMarketingApiClient", return_value=client) as client_factory:
                result = await sync_ads_insights(
                    session,
                    empresa_id=tenant_id,
                    start_date=date(2026, 10, 1),
                    end_date=date(2026, 10, 3),
                    dry_run=False,
                )
        return result, resolve, client, client_factory, tenant_id

    async def test_insights_sync_uses_scoped_admin_read_credential(self):
        result, resolve, client, factory, tenant_id = await self._run_sync()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["rows_fetched"], 0)
        self.assertEqual(result["rows_upserted"], 0)
        self.assertEqual(resolve.await_args.args[1], tenant_id)
        self.assertTrue(resolve.await_args.kwargs["allow_admin_fallback"])
        self.assertEqual(resolve.await_args.kwargs["ad_account_id"], "act_12345")
        self.assertTrue(factory.call_args.kwargs["read_only"])
        self.assertEqual(client.get_insights.await_count, 3)
        for call in client.get_insights.await_args_list:
            self.assertEqual(call.args[0], "act_12345")
            self.assertTrue(call.kwargs["account_scope"])

    async def test_delegated_business_binding_is_accepted_only_with_exact_shared_account(self):
        result, _resolve, client, _factory, _tenant_id = await self._run_sync(
            account_business="another-business",
            shared_accounts=[{"id": "act_12345", "account_id": "12345"}],
        )
        self.assertEqual(result["status"], "complete")
        client.list_business_client_ad_accounts.assert_awaited_once_with("business-1")

    async def test_unverified_business_binding_blocks_insights_before_query(self):
        result, _resolve, client, _factory, _tenant_id = await self._run_sync(
            account_business="another-business",
            shared_accounts=[{"id": "act_99999", "account_id": "99999"}],
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "business_account_binding_unverified")
        client.get_insights.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
