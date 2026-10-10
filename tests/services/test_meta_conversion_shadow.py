from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from app.services.commercial_events import (
    attribution_touch_idempotency_key,
    canonical_dispatch_decision,
    managed_tag_idempotency_key,
)
from app.services.meta.canonical_conversion_shadow import (
    ExternalConversionDispatcher,
    _compare_payloads,
    _eligibility_classification,
    _legacy_delivery_state,
    _reason_categories,
    canonical_conversion_event_id,
    shadow_status,
)
from scripts.check_schema_compatibility import find_missing_schema_items


class MetaConversionShadowTests(unittest.IsolatedAsyncioTestCase):
    def _event(self, **overrides):
        values = {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000011"),
            "empresa_id": uuid.UUID("00000000-0000-0000-0000-000000000022"),
            "idempotency_key": "same-business-transition-v1",
            "event_name": "LeadSubmitted",
            "occurred_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_canonical_event_id_is_stable_across_retries_and_sources(self):
        event = self._event()
        self.assertEqual(canonical_conversion_event_id(event), canonical_conversion_event_id(event))
        self.assertEqual(len(canonical_conversion_event_id(event)), 64)
        self.assertEqual(
            canonical_conversion_event_id(event),
            canonical_conversion_event_id(self._event(id=uuid.uuid4())),
        )
        self.assertNotEqual(canonical_conversion_event_id(event), canonical_conversion_event_id(self._event(empresa_id=uuid.uuid4())))

    def test_schema_compatibility_contract_requires_projection_tables_and_constraints(self):
        missing = find_missing_schema_items({}, set())
        self.assertIn("table:commercial_meta_event_policies", missing)
        self.assertIn("table:commercial_meta_conversion_projections", missing)
        self.assertIn("constraint:ck_commercial_meta_projection_status", missing)

    def test_multiple_invalidation_reasons_are_preserved_and_config_precedes_attribution(self):
        fields = ["dataset_id", "ctwa_clid"]
        self.assertEqual(_reason_categories(fields), ["MISSING_CTWA_CLID", "MISSING_DATASET"])
        self.assertEqual(
            _eligibility_classification(projection_status="invalid", missing_fields=fields, unsupported=False),
            "CONFIGURATION_BLOCKED",
        )

    def test_missing_ctwa_is_ineligible_without_becoming_zero_or_config_error(self):
        fields = ["ctwa_clid"]
        self.assertEqual(_reason_categories(fields), ["MISSING_CTWA_CLID"])
        self.assertEqual(
            _eligibility_classification(projection_status="invalid", missing_fields=fields, unsupported=False),
            "INELIGIBLE_NO_ATTRIBUTION",
        )

    def test_purchase_without_verified_payment_is_ineligible(self):
        fields = ["verified_payment_evidence"]
        self.assertEqual(_reason_categories(fields), ["MISSING_BUSINESS_EVIDENCE"])
        self.assertEqual(
            _eligibility_classification(projection_status="invalid", missing_fields=fields, unsupported=False),
            "INELIGIBLE_NO_BUSINESS_EVIDENCE",
        )

    def test_supported_event_does_not_require_value_or_currency_without_verified_revenue(self):
        from app.services.meta.dataset_events import build_business_messaging_event, validate_business_messaging_event_payload

        credential = SimpleNamespace(
            waba_id="waba-1", dataset_id="dataset-1", access_token="never-serialized",
        )
        payload = build_business_messaging_event(
            event_name="LeadSubmitted", event_id="stable-event-id", credential=credential,
            ctwa_clid="ctwa-id", custom_data={}, event_time=1791504000,
        )
        validate_business_messaging_event_payload(payload)
        custom = payload["data"][0].get("custom_data") or {}
        self.assertNotIn("value", custom)
        self.assertNotIn("currency", custom)

    def test_monitor_manual_and_retry_observations_share_canonical_identity(self):
        shared = {
            "empresa_id": uuid.uuid4(), "lead_id": uuid.uuid4(), "tag_id": uuid.uuid4(),
            "event_name": "QualifiedLead", "funnel_stage_id": "qualified", "funnel_version": "v1",
        }
        manual_key = managed_tag_idempotency_key(**shared)
        monitor_key = managed_tag_idempotency_key(**shared)
        self.assertEqual(manual_key, monitor_key)
        touch_args = {"empresa_id": shared["empresa_id"], "event_name": "Purchase", "touch_id": uuid.uuid4()}
        self.assertEqual(attribution_touch_idempotency_key(**touch_args), attribution_touch_idempotency_key(**touch_args))

    async def test_shadow_dispatcher_never_attempts_external_send(self):
        dispatcher = ExternalConversionDispatcher()
        with patch("app.services.ads_integration_service.enviar_payload_meta_business_messaging_with_retry", new_callable=AsyncMock) as legacy_sender:
            for mode in ("disabled", "shadow", "live"):
                result = await dispatcher.dispatch(mode=mode)
                self.assertFalse(result["attempted"])
            legacy_sender.assert_not_awaited()
        historical = await dispatcher.dispatch(mode="live", historical_no_send=True)
        self.assertFalse(historical["attempted"])
        self.assertEqual(historical["reason"], "historical_no_send")

    async def test_exact_legacy_delivery_blocks_replay_but_probable_match_is_not_proof(self):
        dispatcher = ExternalConversionDispatcher()
        exact = await dispatcher.dispatch(
            mode="live", legacy_reference_classification="EXACT_LINKED",
            legacy_delivery_state="LEGACY_ACCEPTED",
        )
        probable = await dispatcher.dispatch(
            mode="live", legacy_reference_classification="PROBABLE_ONLY",
            legacy_delivery_state="UNKNOWN_DELIVERY",
        )
        self.assertFalse(exact["attempted"])
        self.assertEqual(exact["reason"], "exact_legacy_delivery_proof")
        self.assertFalse(probable["attempted"])
        self.assertEqual(probable["reason"], "p1c1_live_dispatch_disabled")

    def test_backfill_and_live_policy_are_hard_blocked_by_legacy_gate_too(self):
        event = SimpleNamespace(is_backfilled=True, external_dispatch_policy="historical_no_send")
        decision = canonical_dispatch_decision(event, mode="live")
        self.assertFalse(decision["dispatch"])
        self.assertEqual(decision["reason"], "historical_no_send")

    def test_probable_legacy_reference_is_not_delivery_proof(self):
        self.assertEqual(_legacy_delivery_state(None), "UNKNOWN_DELIVERY")
        event = SimpleNamespace(status="failed", response_preview={})
        self.assertEqual(_legacy_delivery_state(event), "LEGACY_FAILED")

    def test_exact_legacy_comparison_records_event_id_mismatch_without_claiming_delivery_quality(self):
        legacy = SimpleNamespace(
            event_name="LeadSubmitted",
            event_id="legacy-event-id",
            empresa_id=uuid.UUID("00000000-0000-0000-0000-000000000022"),
            conversion_context="business_messaging_whatsapp",
            dataset_id_masked="***4321",
            payload_preview={"data": [{
                "event_name": "LeadSubmitted", "event_id": "legacy-event-id", "event_time": 1791504000,
                "action_source": "business_messaging", "messaging_channel": "whatsapp", "user_data": {
                    "whatsapp_business_account_id": "1234...8765", "ctwa_clid": "cl***id",
                }, "custom_data": {},
            }]},
        )
        item = {
            "event_name": "LeadSubmitted", "event_id": "canonical-event-id", "event_time": 1791504000,
            "action_source": "business_messaging", "messaging_channel": "whatsapp",
            "user_data": {"whatsapp_business_account_id": "12345678765", "ctwa_clid": "click-id"},
            "custom_data": {},
        }
        classification, diff = _compare_payloads(
            event_name="LeadSubmitted", canonical_event_id="canonical-event-id", canonical_item=item,
            legacy=legacy, dataset_id_masked="99994321", tenant_id=legacy.empresa_id, unsupported=False,
            missing_fields=[], missing_attribution=False,
        )
        self.assertEqual(classification, "EVENT_ID_MISMATCH")
        self.assertFalse(diff["field_matches"]["event_id"])
        self.assertTrue(diff["field_matches"]["dataset"])

        legacy.event_id = "canonical-event-id"
        legacy.payload_preview["data"][0]["event_id"] = "canonical-event-id"
        legacy.payload_preview["data"][0]["custom_data"] = {"value": 5, "currency": "BRL"}
        item["custom_data"] = {"value": 5.0, "currency": "BRL"}
        classification, diff = _compare_payloads(
            event_name="LeadSubmitted", canonical_event_id="canonical-event-id", canonical_item=item,
            legacy=legacy, dataset_id_masked="99994321", tenant_id=legacy.empresa_id,
            unsupported=False, missing_fields=[], missing_attribution=False,
        )
        self.assertEqual(classification, "SEMANTICALLY_EQUIVALENT")
        self.assertTrue(diff["field_matches"]["ctwa_masked_match"])
        self.assertNotIn("click-id", str(diff))

    async def test_shadow_status_queries_are_scoped_to_requested_tenant(self):
        tenant_id = uuid.uuid4()

        class EmptyResult:
            def all(self):
                return []

        class ReadOnlySession:
            def __init__(self):
                self.statements = []

            async def scalar(self, statement):
                self.statements.append(statement)
                return None

            async def execute(self, statement):
                self.statements.append(statement)
                return EmptyResult()

        session = ReadOnlySession()
        result = await shadow_status(session, empresa_id=tenant_id)
        self.assertEqual(result["canonical_event_count"], 0)
        self.assertTrue(result["tenant_scoped"])
        self.assertGreaterEqual(len(session.statements), 5)
        for statement in session.statements:
            compiled = statement.compile(dialect=postgresql.dialect())
            self.assertIn("empresa_id", str(compiled))
            self.assertIn(tenant_id, compiled.params.values())


if __name__ == "__main__":
    unittest.main()
