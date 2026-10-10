import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.services.ads_integration_service import (
    enviar_payload_meta_business_messaging_with_retry,
    payload_preview_seguro,
)
from app.services.meta.business_messaging_conversion_engine import (
    BusinessMessagingTrigger,
    process_business_messaging_conversion_trigger,
)
from app.services.meta.conversion_credentials import ResolvedMetaCredential
from app.services.meta.dataset_events import (
    MetaConversionPayloadValidationError,
    validate_business_messaging_event_payload,
)
from db.models import BusinessMessagingConversionMapping, CRMLead, Empresa


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _Session:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, statement):
        return _Result(self.values.pop(0))


def _payload(event_name="LeadSubmitted", *, currency=None, value=None):
    custom_data = {}
    if currency is not None:
        custom_data["currency"] = currency
    if value is not None:
        custom_data["value"] = value
    item = {
        "event_name": event_name,
        "event_time": 1700000000,
        "event_id": "event-stable",
        "action_source": "business_messaging",
        "messaging_channel": "whatsapp",
        "user_data": {"whatsapp_business_account_id": "waba-1", "ctwa_clid": "ctwa-1"},
    }
    if custom_data:
        item["custom_data"] = custom_data
    return {"data": [item]}


class CapiPreflightTests(unittest.TestCase):
    def test_order_created_without_currency_is_blocked(self):
        with self.assertRaises(MetaConversionPayloadValidationError) as ctx:
            validate_business_messaging_event_payload(_payload("OrderCreated"))
        self.assertEqual(ctx.exception.reason_code, "META_CONVERSION_CURRENCY_REQUIRED")

    def test_order_created_with_currency_passes(self):
        validate_business_messaging_event_payload(_payload("OrderCreated", currency="BRL", value=10))

    def test_value_requires_currency(self):
        with self.assertRaises(MetaConversionPayloadValidationError) as ctx:
            validate_business_messaging_event_payload(_payload("Purchase", value=10))
        self.assertEqual(ctx.exception.reason_code, "META_CONVERSION_VALUE_CURRENCY_REQUIRED")

    def test_active_business_messaging_contract_is_preserved(self):
        for name in ("Purchase", "InitiateCheckout", "QualifiedLead", "LeadSubmitted"):
            validate_business_messaging_event_payload(_payload(name))

    def test_payload_preview_masks_sensitive_user_data(self):
        preview = payload_preview_seguro({
            "data": [{"event_name": "LeadSubmitted", "user_data": {
                "ctwa_clid": "ctwa-secret",
                "whatsapp_business_account_id": "3655278474623087",
                "ph": "5511999999999",
                "em": "person@example.com",
            }}],
        })
        rendered = str(preview)
        self.assertNotIn("ctwa-secret", rendered)
        self.assertNotIn("5511999999999", rendered)
        self.assertNotIn("person@example.com", rendered)


class CapiRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.credential = ResolvedMetaCredential("tenant_conversion_token", "secret", "tenant", "waba", "dataset", "v25.0")

    async def _send(self, responses):
        with patch("app.services.ads_integration_service.enviar_payload_meta_business_messaging", new=AsyncMock(side_effect=responses)), \
             patch("app.services.ads_integration_service.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await enviar_payload_meta_business_messaging_with_retry(
                self.credential,
                _payload(),
                event_id="same-event-id",
                event_name="LeadSubmitted",
            )
        return result, sleep

    async def test_timeout_then_success_preserves_event_id_and_attempt_count(self):
        seen = []

        async def fake_send(credential, payload):
            seen.append(payload["data"][0]["event_id"])
            return (500, {"error": {"is_transient": True}}) if len(seen) == 1 else (200, {"events_received": 1})

        with patch("app.services.ads_integration_service.enviar_payload_meta_business_messaging", new=fake_send), \
             patch("app.services.ads_integration_service.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await enviar_payload_meta_business_messaging_with_retry(
                self.credential, _payload(), event_id="same-event-id", event_name="LeadSubmitted"
            )
        self.assertEqual(result[0], 200)
        self.assertEqual(result[2], 2)
        self.assertEqual(seen, ["event-stable", "event-stable"])
        self.assertEqual(sleep.await_count, 1)

    async def test_500_then_success_retries_once(self):
        result, _ = await self._send([(500, {}), (200, {"events_received": 1})])
        self.assertEqual(result[0], 200)
        self.assertEqual(result[2], 2)

    async def test_400_is_not_retried(self):
        result, sleep = await self._send([(400, {"error": {"code": 100, "error_subcode": 2804081, "is_transient": False}})])
        self.assertEqual(result[0], 400)
        self.assertEqual(result[2], 1)
        sleep.assert_not_awaited()

    async def test_429_retries(self):
        result, _ = await self._send([(429, {}), (200, {"events_received": 1})])
        self.assertEqual(result[0], 200)
        self.assertEqual(result[2], 2)

    async def test_maximum_three_attempts(self):
        result, sleep = await self._send([(500, {}), (502, {}), (503, {})])
        self.assertEqual(result[0], 503)
        self.assertEqual(result[2], 3)
        self.assertEqual(sleep.await_count, 2)


class CapiEnginePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_created_missing_currency_is_persisted_blocked_without_post(self):
        empresa_id, lead_id = uuid.uuid4(), uuid.uuid4()
        empresa = Empresa(id=empresa_id, nome_empresa="Tenant", meta_capi_ativo=True)
        lead = CRMLead(id=lead_id, empresa_id=empresa_id, nome_contato="Lead", telefone_contato="5511999999999", dados_adicionais={"meta_attribution": {"last_touch": {"ctwa_clid": "ctwa-1"}}})
        mapping = BusinessMessagingConversionMapping(
            id=uuid.uuid4(), empresa_id=empresa_id, trigger_type="order_created", trigger_ref="order", event_name="OrderCreated", ativo=True, send_mode="dry_run", currency=None,
        )
        mapping.default_value = 10
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa_id), "waba", "dataset", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", new=AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", new=AsyncMock()) as record, \
             patch("app.services.meta.business_messaging_conversion_engine.enviar_payload_meta_business_messaging_with_retry", new=AsyncMock()) as post:
            result = await process_business_messaging_conversion_trigger(
                _Session(empresa, lead, mapping),
                BusinessMessagingTrigger(empresa_id, lead_id, "order_created", "order", "order-event"),
            )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "META_CONVERSION_CURRENCY_REQUIRED")
        self.assertEqual(record.await_args.kwargs["status"], "blocked")
        self.assertEqual(record.await_args.kwargs["attempts"], 0)
        post.assert_not_awaited()
