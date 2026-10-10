import asyncio
import unittest
import uuid
from unittest.mock import patch

from app.services.meta import legacy_delivery_evidence as evidence


class LegacyDeliveryEvidenceUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_response_never_preserves_pii_or_secrets(self):
        response = {"events_received": 1, "access_token": "private", "messages": ["private"],
                    "error": {"code": 190, "message": "private", "error_subcode": 463, "fbtrace_id": "private"}}
        safe = evidence.sanitized_provider_response(response)
        self.assertEqual(safe["code"], 190)
        self.assertEqual(safe["error_subcode"], 463)
        self.assertNotIn("private", str(safe))
        self.assertNotIn("messages", safe)

    def test_ack_reject_malformed_timeout_pre_send(self):
        cases = [
            (True, 200, {"events_received": 1}, None, ("PROVIDER_ACCEPTED", False)),
            (True, 400, {"error": {"code": 10}}, None, ("PROVIDER_REJECTED", False)),
            (True, 200, {}, None, ("DELIVERY_UNKNOWN", True)),
            (True, None, None, "after_request", ("DELIVERY_UNKNOWN", True)),
            (False, None, None, "before_request", ("NOT_SENT", False)),
        ]
        for attempted, status, response, phase, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(evidence.delivery_outcome(attempted=attempted, http_status=status,
                    provider_response=response, error_phase=phase), expected)

    def test_context_is_restored_and_shared_identity_is_stable(self):
        tenant, fact = uuid.uuid4(), uuid.uuid4()
        context = evidence.LegacyDeliveryContext(empresa_id=tenant)
        self.assertIsNone(evidence._CONTEXT.get())
        with evidence.legacy_delivery_context(context):
            self.assertEqual(evidence._CONTEXT.get(), context)
        self.assertIsNone(evidence._CONTEXT.get())
        kwargs = dict(empresa_id=tenant,business_fact_id=fact,dataset_id="123",waba_id="456",event_name="LeadSubmitted")
        self.assertEqual(evidence.shared_delivery_identity_key(**kwargs), evidence.shared_delivery_identity_key(**kwargs))
        self.assertNotEqual(evidence.shared_delivery_identity_key(**kwargs), evidence.shared_delivery_identity_key(**(kwargs|{"empresa_id": uuid.uuid4()})))

    async def test_telemetry_timeout_is_bounded_and_safe(self):
        async def slow():
            await asyncio.sleep(0.1)
        with patch.object(evidence, "TELEMETRY_TIMEOUT_SECONDS", 0.001):
            self.assertIsNone(await evidence._bounded(slow, None))

    async def test_cancellation_propagates(self):
        async def canceled():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await evidence._bounded(canceled, None)

    def test_arbitrary_external_id_is_hashed_only(self):
        self.assertIsNone(evidence.safe_external_event_id("phone:private"))
        self.assertEqual(evidence.safe_external_event_id("a"*64), "a"*64)
        identifier = str(uuid.uuid4())
        self.assertEqual(evidence.safe_external_event_id(identifier), identifier)
