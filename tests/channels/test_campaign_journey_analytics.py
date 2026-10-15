from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.api.routers.auth import require_tenant_access
from app.api.routers.campaign_central import router as campaign_central_router
from app.services import campaign_journey_analytics as service


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class CampaignJourneyContractTests(unittest.TestCase):
    def test_filter_contract_normalizes_timezone_and_caps_campaigns(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        campaign_ids = [str(uuid.uuid4()) for _ in range(5)]
        parsed = service.filters(
            empresa_id=uuid.uuid4(), start=now - timedelta(days=30), end=now,
            observed_at=now, campaign_ids=campaign_ids + [campaign_ids[0]], now=now,
        )
        self.assertEqual(parsed["start"].tzinfo, timezone.utc)
        self.assertEqual(len(parsed["campaign_ids"]), 5)
        self.assertEqual(parsed["response_window_days"], 7)

    def test_filter_rejects_more_than_five_campaigns_and_future_observation(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            service.filters(empresa_id=uuid.uuid4(), campaign_ids=[str(uuid.uuid4()) for _ in range(6)], now=now)
        with self.assertRaises(ValueError):
            service.filters(empresa_id=uuid.uuid4(), observed_at=now + timedelta(days=1), now=now)
        with self.assertRaises(ValueError):
            service.filters(empresa_id=uuid.uuid4(), end=now + timedelta(hours=1), observed_at=now, now=now)

    def test_delivery_coverage_requires_matching_confirmation_at_each_stage(self):
        sent_only = service._delivery_rates(
            {"meta_accepted": 2, "delivered": 0, "read": 0}, sampled=2, capped=False,
            provider_delivery_observed=0, provider_read_observed=0,
        )
        self.assertEqual(sent_only["delivered"]["state"], "unknown")
        self.assertEqual(sent_only["read"]["state"], "not_applicable")

        delivered_only = service._delivery_rates(
            {"meta_accepted": 2, "delivered": 1, "read": 0}, sampled=2, capped=False,
            provider_delivery_observed=1, provider_read_observed=0,
        )
        self.assertEqual(delivered_only["delivered"]["state"], "partial")
        self.assertEqual(delivered_only["read"]["state"], "unknown")

    def test_zero_is_observed_when_source_exists_and_missing_source_is_unknown(self):
        observed_zero = service._metric(0, 4, state="observed", definition="zero com denominador")
        missing = service._metric(None, None, state="unknown", definition="fonte ausente")
        self.assertEqual(observed_zero["value"], 0)
        self.assertEqual(observed_zero["state"], "observed")
        self.assertIsNone(missing["value"])
        self.assertEqual(missing["state"], "unknown")

    def test_zero_denominator_distinguishes_not_applicable_from_missing_source(self):
        self.assertEqual(service._metric_state(sampled=0, capped=False, denominator=0), "not_applicable")
        self.assertEqual(service._metric_state(sampled=2, capped=False, denominator=0), "unknown")
        self.assertEqual(service._metric_state(sampled=2, capped=False, denominator=0, pending_window=1), "partial")
        self.assertEqual(service._metric_state(sampled=2, capped=False, denominator=1, incomplete=1), "partial")

    def test_empty_campaign_period_returns_unknown_coverage_without_fabricated_zeroes(self):
        tenant_id = uuid.uuid4()
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        with patch.object(service, "_selected_campaigns", new=AsyncMock(return_value=[])):
            output = __import__("asyncio").run(service.dashboard(
                AsyncMock(), empresa_id=tenant_id, start=now - timedelta(days=30), end=now,
                observed_at=now, now=now,
            ))
        self.assertEqual(output["campaigns"], [])
        self.assertEqual(output["coverage"]["state"], "unknown")
        self.assertEqual(output["coverage"]["sample_limit_per_campaign"], service.MAX_RECIPIENTS_PER_CAMPAIGN)

    def test_dashboard_preserves_partial_windows_unknown_stage_and_verified_zero(self):
        tenant_id, campaign_id = uuid.uuid4(), uuid.uuid4()
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        delivery = {"audience_total": 2, "meta_accepted": 2, "delivered": 1, "read": 0,
                    "failed": 0, "pending": 0, "replies": 0}
        outcome = {
            "campaign_id": campaign_id, "origin_segment": "unknown", "sampled_recipients": 2,
            "sampled_leads": 2, "attempted": 2, "replied": 0, "reactivated": 0,
            "campaign_reply_touches": 0, "campaign_sent_touches": 2,
            "stage_comparable": 0, "stage_advanced": 0, "verified_purchase": 0,
            "current_opt_out": 0, "provider_status_observed": 1,
            "reactivation_history_comparable": 0, "sent_recipients": 2,
            "response_unlinked": 0, "reactivation_pending": 0, "reactivation_unknown_history": 2,
            "stage_unknown_history": 2, "purchase_unlinked": 0,
            "response_matured": 1, "stage_matured": 0, "purchase_matured": 1,
            "response_denominator": 1, "response_pending": 1,
            "reactivation_denominator": 0, "reactivation_comparable": 0,
            "stage_denominator": 0, "stage_comparable_denominator": 0, "stage_pending": 0,
            "purchase_denominator": 1, "purchase_pending": 1,
            "failed": 0, "cancelled": 0, "stopped": 0, "blocked": 0,
            "pending": 0, "consent_blocked": 0, "allowlist_blocked": 0,
            "template_blocked": 0, "other_recorded_failure": 0,
        }
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[
            _Result([outcome]),
            _Result([{"selected_snapshot": 2, "snapshot_opt_out": 1,
                      "snapshot_blocked": 0, "snapshot_duplicates": 0}]),
        ])
        results = {
            "metrics": delivery,
            "segments": [{"segment": "unknown", **delivery}],
            "coverage": {"state": "observed", "sampled_recipients": 2, "sample_limit": 10000, "capped": False},
        }
        with patch.object(service, "_selected_campaigns", new=AsyncMock(return_value=[(campaign_id, "Campanha sintética")])):
            with patch.object(service, "load_funnel_configuration", new=AsyncMock(return_value=[])):
                with patch.object(service, "campaign_results_analytics", new=AsyncMock(return_value=results)):
                    output = __import__("asyncio").run(service.dashboard(
                        session, empresa_id=tenant_id, start=now - timedelta(days=30), end=now,
                        observed_at=now, now=now,
                    ))
        campaign = output["campaigns"][0]
        self.assertEqual(campaign["eligible_snapshot"]["value"], 2)
        self.assertEqual(campaign["eligible_snapshot"]["state"], "observed")
        self.assertEqual(campaign["response_after_send"]["state"], "partial")
        self.assertEqual(campaign["verified_purchase_after_send"]["value"], 0)
        self.assertEqual(campaign["verified_purchase_after_send"]["state"], "partial")
        self.assertEqual(campaign["stage_advance_after_send"]["state"], "unknown")
        self.assertEqual(campaign["opt_out"]["snapshot_exclusions"], 1)
        reply_step = next(step for step in campaign["funnel"] if step["key"] == "reply")
        self.assertEqual(reply_step["denominator"], 1)
        self.assertEqual(reply_step["value"], 0)

    def test_read_paths_are_tenant_scoped_and_no_write_or_dispatch_is_added(self):
        source = open("app/services/campaign_journey_analytics.py", encoding="utf-8").read()
        routes = open("app/api/routers/campaign_central.py", encoding="utf-8").read()
        self.assertIn("r.empresa_id = :tenant", source)
        self.assertIn("e.revenue_verified = true", source)
        self.assertIn("t.campaign_recipient_id = r.id", source)
        self.assertIn("LIMIT :recipient_limit", source)
        self.assertIn("LIMIT :sample_limit", source)
        self.assertIn("mh.criado_em <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'", source)
        self.assertIn("coalesce(r.sent_at, r.created_at) <= CAST(:observed_at AS timestamptz) AT TIME ZONE 'UTC'", source)
        self.assertIn("ev.conexao_id = r.conexao_id", source)
        self.assertIn("ev.channel = r.channel", source)
        self.assertIn("e.revenue_verified = true", source)
        self.assertIn('@router.get("/journey")', routes)
        self.assertIn('@router.get("/journey/recipients")', routes)
        journey_routes = [route for route in campaign_central_router.routes
                          if getattr(route, "path", "").endswith("/journey")
                          or getattr(route, "path", "").endswith("/journey/recipients")]
        self.assertEqual(len(journey_routes), 2)
        for route in journey_routes:
            self.assertEqual(route.methods, {"GET"})
            self.assertIn(require_tenant_access, [dependency.call for dependency in route.dependant.dependencies])
        self.assertNotIn("session.add(", source)
        self.assertNotIn("await session.commit", source)
        self.assertNotIn("enqueue_message(", source)
        self.assertNotIn("MetaWhatsAppProvider", source)
        self.assertIn("response_after_send", source)
        self.assertIn("verified_purchase_after_send", source)


if __name__ == "__main__":
    unittest.main()
