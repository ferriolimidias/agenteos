from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app.services.meta.conversion_outbox import (
    CANONICAL_LIVE_ENABLED,
    canonical_dedup_decision,
    canonical_outbox_idempotency_key,
    classify_external_delivery_outcome,
    claim_shadow_outbox,
    ensure_shadow_outbox_intent,
    future_external_delivery_permitted,
    _safe_error_class,
    _is_duplicate_risk,
)


class ConversionOutboxTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_outbox_identity_is_stable(self):
        event_id = "a" * 64
        self.assertEqual(canonical_outbox_idempotency_key(event_id), canonical_outbox_idempotency_key(event_id))
        self.assertEqual(canonical_outbox_idempotency_key(event_id), f"canonical:{event_id}")

    def test_shared_dedup_does_not_promote_probable_or_unknown_to_delivery(self):
        self.assertEqual(canonical_dedup_decision(
            historical_no_send=False, projection_valid=True,
            legacy_reference_classification="EXACT_LINKED", legacy_delivery_state="LEGACY_ACCEPTED",
        ), "EXACT_LEGACY_DELIVERY")
        self.assertEqual(canonical_dedup_decision(
            historical_no_send=False, projection_valid=True,
            legacy_reference_classification="PROBABLE_ONLY", legacy_delivery_state="UNKNOWN_DELIVERY",
        ), "POSSIBLE_LEGACY_DUPLICATE")
        self.assertEqual(canonical_dedup_decision(
            historical_no_send=False, projection_valid=True,
            legacy_reference_classification="EXACT_LINKED", legacy_delivery_state="UNKNOWN_DELIVERY",
        ), "UNKNOWN_LEGACY_DELIVERY")
        self.assertEqual(canonical_dedup_decision(
            historical_no_send=True, projection_valid=True,
            legacy_reference_classification="UNLINKED", legacy_delivery_state="UNKNOWN_DELIVERY",
        ), "HISTORICAL_NO_SEND")
        self.assertEqual(canonical_dedup_decision(
            historical_no_send=False, projection_valid=True,
            legacy_reference_classification="UNLINKED", legacy_delivery_state="UNKNOWN_DELIVERY",
        ), "NO_LEGACY_REFERENCE")

    def test_duplicate_risk_uses_legacy_projection_evidence_when_no_snapshot_decision_exists(self):
        self.assertTrue(_is_duplicate_risk(
            stored_decision=None,
            legacy_reference_classification="PROBABLE_ONLY",
            legacy_delivery_state="UNKNOWN_DELIVERY",
        ))
        self.assertFalse(_is_duplicate_risk(
            stored_decision=None,
            legacy_reference_classification="UNLINKED",
            legacy_delivery_state="UNKNOWN_DELIVERY",
        ))
        self.assertFalse(_is_duplicate_risk(
            stored_decision=None,
            legacy_reference_classification="UNLINKED",
            legacy_delivery_state="NOT_APPLICABLE",
        ))

    def test_delivery_uncertainty_classifier_is_conservative(self):
        self.assertEqual(classify_external_delivery_outcome(phase="before_external_call"), "RETRY")
        self.assertEqual(classify_external_delivery_outcome(phase="timeout_after_external_call"), "UNKNOWN_DELIVERY")
        self.assertEqual(classify_external_delivery_outcome(
            phase="http_response", http_status=200, valid_provider_ack=True,
        ), "DELIVERED")
        self.assertEqual(classify_external_delivery_outcome(
            phase="http_response", http_status=200, valid_provider_ack=False,
        ), "UNKNOWN_DELIVERY")
        self.assertEqual(classify_external_delivery_outcome(
            phase="http_response", http_status=429,
        ), "RETRY")
        self.assertEqual(classify_external_delivery_outcome(
            phase="http_response", http_status=400,
        ), "FAILED_PERMANENT")

    async def test_historical_outbox_intent_is_hard_blocked_and_payload_redacted(self):
        tenant_id, event_id, projection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        event = SimpleNamespace(
            id=event_id, empresa_id=tenant_id, event_name="LeadSubmitted", value=None,
            currency=None, revenue_verified=False, is_backfilled=True,
            external_dispatch_policy="historical_no_send",
        )
        projection = SimpleNamespace(
            id=projection_id, canonical_event_id="c" * 64, event_name="LeadSubmitted",
            event_time=1_791_504_000, action_source="business_messaging", payload_hash="b" * 64,
            dataset_id_masked="***1234", waba_id_masked="***4321", ctwa_present=True,
            projection_status="valid", legacy_reference_classification="PROBABLE_ONLY",
            comparison_json={"legacy_delivery_state": "UNKNOWN_DELIVERY"}, safe_summary_json={},
            mode="shadow", last_projected_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )

        class FakeResult:
            rowcount = 1

        class FakeSession:
            statement = None

            async def execute(self, statement):
                self.statement = statement
                return FakeResult()

        session = FakeSession()
        inserted = await ensure_shadow_outbox_intent(session, event=event, projection=projection)
        self.assertTrue(inserted)
        compiled = session.statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["status"], "HISTORICAL_BLOCKED")
        self.assertTrue(compiled.params["historical_no_send"])
        payloads = [value for value in compiled.params.values() if isinstance(value, dict)]
        self.assertTrue(payloads)
        for payload in payloads:
            self.assertNotIn("ctwa_clid", payload)
            self.assertNotIn("user_data", payload)

    async def test_duplicate_or_uncertain_legacy_projection_is_blocked_from_shadow_claims(self):
        tenant_id, event_id, projection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        event = SimpleNamespace(
            id=event_id, empresa_id=tenant_id, event_name="LeadSubmitted", value=None,
            currency=None, revenue_verified=False, is_backfilled=False,
            external_dispatch_policy="disabled",
        )
        projection = SimpleNamespace(
            id=projection_id, canonical_event_id="d" * 64, event_name="LeadSubmitted",
            event_time=1_791_504_000, action_source="business_messaging", payload_hash="c" * 64,
            dataset_id_masked="***1234", waba_id_masked="***4321", ctwa_present=True,
            projection_status="valid", legacy_reference_classification="PROBABLE_ONLY",
            comparison_json={"legacy_delivery_state": "UNKNOWN_DELIVERY"}, safe_summary_json={},
            mode="shadow", last_projected_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )

        class FakeResult:
            rowcount = 1

        class FakeSession:
            statement = None

            async def execute(self, statement):
                self.statement = statement
                return FakeResult()

        session = FakeSession()
        await ensure_shadow_outbox_intent(session, event=event, projection=projection)
        compiled = session.statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["status"], "BLOCKED")
        payloads = [value for value in compiled.params.values() if isinstance(value, dict)]
        self.assertTrue(any(item.get("dedup_decision") == "POSSIBLE_LEGACY_DUPLICATE" for item in payloads))

    async def test_claim_commits_skip_locked_shadow_only_lease(self):
        class FakeResult:
            def all(self):
                return []

        class FakeSession:
            statement = None
            committed = False

            async def execute(self, statement):
                self.statement = statement
                return FakeResult()

            async def commit(self):
                self.committed = True

        session = FakeSession()
        claimed = await claim_shadow_outbox(
            session, lease_owner="worker-test", now=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        compiled = session.statement.compile(dialect=postgresql.dialect())
        sql = str(compiled).upper()
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("SKIP LOCKED", sql)
        self.assertIn("HISTORICAL_NO_SEND", sql)
        self.assertIn("DISPATCH_MODE", sql)
        self.assertIn("LEGACY_REFERENCE_CLASSIFICATION", sql)
        self.assertTrue(session.committed)
        self.assertEqual(claimed, [])

    def test_new_live_dispatch_stays_disabled(self):
        self.assertFalse(CANONICAL_LIVE_ENABLED)
        self.assertFalse(future_external_delivery_permitted(
            dedup_decision="SAFE_SHADOW_ONLY", historical_no_send=False,
            tenant_enabled=True, event_enabled=True,
        ))
        self.assertFalse(future_external_delivery_permitted(
            dedup_decision="POSSIBLE_LEGACY_DUPLICATE", historical_no_send=False,
            tenant_enabled=True, event_enabled=True,
        ))
        with patch("app.services.meta.conversion_outbox.CANONICAL_LIVE_ENABLED", True):
            self.assertTrue(future_external_delivery_permitted(
                dedup_decision="SAFE_SHADOW_ONLY", historical_no_send=False,
                tenant_enabled=True, event_enabled=True,
            ))
            for unsafe_decision in (
                "EXACT_LEGACY_DELIVERY", "POSSIBLE_LEGACY_DUPLICATE",
                "UNKNOWN_LEGACY_DELIVERY", "NO_LEGACY_REFERENCE",
            ):
                self.assertFalse(future_external_delivery_permitted(
                    dedup_decision=unsafe_decision, historical_no_send=False,
                    tenant_enabled=True, event_enabled=True,
                ))

    def test_error_metadata_only_keeps_allowlisted_class_names(self):
        self.assertEqual(_safe_error_class("sqlalchemy.exc.IntegrityError"), "IntegrityError")
        self.assertEqual(_safe_error_class("tokenValue123"), "simulated_error")
        self.assertIsNone(_safe_error_class(None))


if __name__ == "__main__":
    unittest.main()
