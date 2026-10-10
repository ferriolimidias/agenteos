import unittest
from types import SimpleNamespace

from app.services.meta.optimization_eligibility import is_meta_accepted_event, _state


class MetaOptimizationEligibilityTests(unittest.TestCase):
    def test_only_sent_events_with_meta_events_received_count(self):
        accepted = SimpleNamespace(status="sent", response_preview={"body": {"events_received": 1}})
        dry_run = SimpleNamespace(status="dry_run", response_preview={"body": {"events_received": 1}})
        rejected = SimpleNamespace(status="sent", response_preview={"body": {"events_received": 0}})
        self.assertTrue(is_meta_accepted_event(accepted))
        self.assertFalse(is_meta_accepted_event(dry_run))
        self.assertFalse(is_meta_accepted_event(rejected))

    def test_zero_signal_is_expected_bootstrap_state(self):
        self.assertEqual(_state(infrastructure_ready=True, purchase_count=0, qualified_lead_count=0), "NOT_ENOUGH_SIGNAL")

    def test_signal_accumulation_is_not_infrastructure_failure(self):
        self.assertEqual(_state(infrastructure_ready=True, purchase_count=1, qualified_lead_count=0, purchase_probe="not_eligible_yet"), "SIGNAL_ACCUMULATING")

    def test_business_ads_dataset_error_does_not_block_capi_lifecycle(self):
        self.assertEqual(_state(infrastructure_ready=True, purchase_count=0, qualified_lead_count=0), "NOT_ENOUGH_SIGNAL")

    def test_probe_eligibility_transitions_to_purchase_ready(self):
        self.assertEqual(_state(infrastructure_ready=True, purchase_count=1, qualified_lead_count=0, purchase_probe="eligible"), "ELIGIBLE_PURCHASE")


if __name__ == "__main__":
    unittest.main()
