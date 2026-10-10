from decimal import Decimal
import unittest
from unittest.mock import patch

from app.services.commercial_analytics import _cost_metrics, _ratio, _sum_known
from app.services.meta.marketing_api import MetaMarketingApiClient


class CommercialAnalyticsMetricTests(unittest.TestCase):
    def test_ads_rates_and_funnel_costs_use_explicit_denominators(self):
        metrics = _cost_metrics(
            spend=Decimal("100"), spend_state="known", impressions=1000, impressions_state="known",
            clicks=50, clicks_state="known", conversations=20, leads=10, qualified=5,
            checkouts=4, purchases=2, currency="BRL",
        )
        self.assertEqual(metrics["ctr"]["value"], 5.0)
        self.assertEqual(metrics["cpc"]["value"], 2.0)
        self.assertEqual(metrics["cpm"]["value"], 100.0)
        self.assertEqual(metrics["cost_per_conversation"]["value"], 5.0)
        self.assertEqual(metrics["cpl"]["value"], 10.0)
        self.assertEqual(metrics["cpql"]["value"], 20.0)
        self.assertEqual(metrics["cost_per_checkout"]["value"], 25.0)
        self.assertEqual(metrics["cpa"]["value"], 50.0)
        self.assertTrue(all(metrics[key]["currency"] == "BRL" for key in ("cpc", "cpm", "cpl", "cpql", "cpa")))

    def test_unknown_is_not_zero_and_zero_denominator_is_not_applicable(self):
        self.assertEqual(_ratio(None, 0), {"value": None, "state": "unknown"})
        self.assertEqual(_ratio(Decimal("1"), 0), {"value": None, "state": "not_applicable"})
        total, state = _sum_known([])
        self.assertIsNone(total)
        self.assertEqual(state, "unknown")
        total, state = _sum_known([Decimal("0"), None])
        self.assertEqual(total, Decimal("0"))
        self.assertEqual(state, "partial")

    def test_roas_formula_is_net_revenue_over_spend(self):
        self.assertEqual(_ratio(Decimal("360"), Decimal("120"))["value"], 3.0)


if __name__ == "__main__":
    unittest.main()


class MetaInsightsReadContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_tenant_account_insights_use_daily_graph_get_scope(self):
        captured = {}

        class FakeGraph:
            async def _list_paginated(self, path, *, fields, limit, extra_params, page_counter=None, max_pages=100):
                captured.update(path=path, fields=fields, limit=limit, params=extra_params, max_pages=max_pages)
                return []

        client = MetaMarketingApiClient("tenant-scoped-token")
        with patch.object(MetaMarketingApiClient, "_client", return_value=FakeGraph()):
            result = await client.get_insights(
                "act_123", level="ad", since="2026-09-01", until="2026-09-30",
                time_increment=1, account_scope=True,
            )
        self.assertEqual(result, [])
        self.assertEqual(captured["path"], "act_123/insights")
        self.assertEqual(captured["params"]["level"], "ad")
        self.assertEqual(captured["params"]["time_increment"], 1)
        self.assertIn('"since":"2026-09-01"', captured["params"]["time_range"])

    async def test_existing_object_scoped_insights_path_stays_compatible(self):
        captured = {}

        class FakeGraph:
            async def _list_paginated(self, path, *, fields, limit, extra_params, page_counter=None, max_pages=100):
                captured.update(path=path, params=extra_params, max_pages=max_pages)
                return []

        client = MetaMarketingApiClient("tenant-scoped-token")
        with patch.object(MetaMarketingApiClient, "_client", return_value=FakeGraph()):
            await client.get_insights("campaign-42", level="campaign", date_preset="last_30d")
        self.assertEqual(captured["path"], "campaign-42/insights")
        self.assertEqual(captured["params"]["date_preset"], "last_30d")
