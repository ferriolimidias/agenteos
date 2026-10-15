import unittest
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import uuid

from app.services.campaign_central_dispatch_service import campaign_results_analytics


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def rate(numerator: int, denominator: int) -> float:
    return round((int(numerator) / int(denominator)) * 100, 2) if int(denominator or 0) else 0


class CampaignResultsAnalyticsContractTest(unittest.TestCase):
    def test_historical_as_of_query_rejects_live_timeline(self):
        with self.assertRaisesRegex(ValueError, "do not include the live timeline"):
            asyncio.run(campaign_results_analytics(
                AsyncMock(), empresa_id=uuid.uuid4(),
                observed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            ))

    def test_results_accepted_includes_delivered_and_read_contract(self):
        source = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("or status in ('sent', 'delivered', 'read')", source)
        self.assertIn("or event_delivered", source)
        self.assertIn("or event_read", source)
        self.assertIn("delivered_stage_at is not null", source)
        self.assertIn("or status in ('delivered', 'read')", source)

    def test_results_are_not_terminal_status_buckets(self):
        source = read("app/services/campaign_central_dispatch_service.py")
        analytics_source = source.split("async def campaign_results_analytics", 1)[1].split("async def campaign_results(", 1)[0]
        self.assertNotIn("select(CampaignRecipient.status, func.count())", analytics_source)
        self.assertIn("old_status_counts", analytics_source)
        self.assertIn("is_meta_accepted", analytics_source)
        self.assertIn("is_delivered", analytics_source)
        self.assertIn("is_read", analytics_source)

    def test_results_retry_is_not_final_failure(self):
        source = read("app/services/campaign_central_dispatch_service.py")
        failed_block = source.split(") as is_failed", 1)[0].rsplit("(", 1)[-1]
        self.assertNotIn("'retry'", failed_block)
        self.assertNotIn("'cancelled'", failed_block)
        self.assertNotIn("'stopped'", failed_block)
        self.assertIn("status in ('queued', 'locked', 'retry', 'processing') as is_pending", source)

    def test_results_filter_and_rates_contract(self):
        source = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("r.campanha_id = :campaign_id", source)
        self.assertIn("r.conexao_id = :conexao_id", source)
        self.assertIn("coalesce(r.sent_at, r.created_at) >= :period_start", source)
        self.assertIn("def _rate(numerator: int, denominator: int) -> float:", source)
        self.assertEqual(rate(913, 1000), 91.3)
        self.assertEqual(rate(103, 913), 11.28)
        self.assertEqual(rate(0, 0), 0)

    def test_results_replies_are_inbound_after_campaign_outbound(self):
        source = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("coalesce(mh.from_me, false) = false", source)
        self.assertIn("mh.channel_conversation_id = c.conversation_id", source)
        self.assertIn("mh.criado_em, now()) > coalesce(c.accepted_at, c.sent_at, c.created_at)", source)

    def test_frontend_uses_analytics_metrics_not_old_sent_bucket(self):
        source = read("frontend/src/pages/tenant/Campanhas.jsx")
        results_block = source.split('{["results", "costs", "conversions"].includes(activeTab) ? (', 1)[1]
        self.assertIn('label="Aceitas pela Meta"', results_block)
        self.assertIn("resultMetrics.meta_accepted", results_block)
        self.assertNotIn('label="Enviados"', results_block)
        self.assertNotIn('countValue(recipientCounts, ["sent"])', results_block)
        self.assertIn("ResultsLineChart", results_block)
        self.assertIn("ResultsFunnel", results_block)


if __name__ == "__main__":
    unittest.main()
