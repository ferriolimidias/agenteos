import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace

from app.api.routers.traffic_meta import _serialize_conversion_history_event


class TrafficMetaConversionsHistoryTests(unittest.TestCase):
    def test_history_serializer_does_not_expose_sensitive_fields(self):
        event = SimpleNamespace(
            id=uuid.uuid4(),
            lead_id=uuid.uuid4(),
            event_name="QualifiedLead",
            status="not_applicable",
            error_message="no_ctwa_attribution",
            trigger_type="crm_stage_changed",
            trigger_ref="stage-qualified",
            trigger_source="system",
            source="system",
            criado_em=datetime(2026, 8, 20, 12, 5),
            response_preview={"status_code": 200, "body": {"events_received": 1}},
            test_event_code="TEST123",
            payload_preview={"data": [{"user_data": {"ctwa_clid": "raw-ctwa"}}]},
            credential_source="tenant_conversion_token",
            waba_id_masked="***4417",
            dataset_id_masked="***8529",
            ctwa_present=False,
        )
        lead = SimpleNamespace(id=event.lead_id, nome_contato="Carlos")
        mapping = SimpleNamespace(trigger_type="crm_stage_changed", trigger_ref="stage-qualified", send_mode="live")

        serialized = _serialize_conversion_history_event(event, lead, mapping)

        self.assertEqual(serialized["event_name"], "QualifiedLead")
        self.assertEqual(serialized["lead"]["name"], "Carlos")
        self.assertEqual(serialized["mode"], "Live")
        self.assertEqual(serialized["status_label"], "Ignorado - sem atribuição CTWA")
        self.assertNotIn("TEST123", str(serialized))
        self.assertNotIn("raw-ctwa", str(serialized))
        self.assertNotIn("payload_preview", serialized)

    def test_payment_label_is_provider_agnostic(self):
        event = SimpleNamespace(
            id=uuid.uuid4(),
            lead_id=uuid.uuid4(),
            event_name="Purchase",
            status="sent",
            error_message=None,
            trigger_type="payment_confirmed",
            trigger_ref="fake_gateway_confirmed",
            trigger_source="integration",
            source="integration",
            criado_em=datetime(2026, 8, 20, 12, 1),
            response_preview={"status_code": 200},
            test_event_code=None,
            credential_source="tenant_conversion_token",
            waba_id_masked="***0001",
            dataset_id_masked="***0002",
            ctwa_present=True,
        )
        lead = SimpleNamespace(id=event.lead_id, nome_contato="Maria")
        mapping = SimpleNamespace(trigger_type="payment_confirmed", trigger_ref="fake_gateway_confirmed", send_mode="live")

        serialized = _serialize_conversion_history_event(event, lead, mapping)

        self.assertEqual(serialized["trigger"]["label"], "Pagamento confirmado")
        self.assertEqual(serialized["status_label"], "Enviado")
        self.assertEqual(serialized["meta"]["status_code"], 200)


if __name__ == "__main__":
    unittest.main()
