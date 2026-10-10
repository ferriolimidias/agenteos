import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.meta.business_messaging_conversion_engine import (
    BusinessMessagingConversionError,
    BusinessMessagingTrigger,
    ConversionDomainEvent,
    DEFAULT_ALLOWED_TRIGGER_SOURCES,
    emit_conversion_domain_event,
    process_business_messaging_conversion_trigger,
    stable_business_messaging_event_id,
    validate_event_trigger_contract,
)
from app.services.meta.conversion_credentials import ResolvedMetaCredential
from db.models import BusinessMessagingConversionMapping, CRMLead, Empresa


class Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class Session:
    def __init__(self, *values):
        self.values = list(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.values.pop(0))


class BusinessMessagingConversionEngineTests(unittest.IsolatedAsyncioTestCase):
    def _objects(self, *, event_name="LeadSubmitted", trigger_type="tag_applied", ctwa=True, allowed_sources=None):
        empresa_id = uuid.uuid4()
        lead_id = uuid.uuid4()
        empresa = Empresa(id=empresa_id, nome_empresa="Tenant", meta_capi_ativo=True)
        lead = CRMLead(
            id=lead_id,
            empresa_id=empresa_id,
            nome_contato="Lead",
            telefone_contato="5511999999999",
            dados_adicionais={"meta_attribution": {"last_touch": {"ctwa_clid": "raw-ctwa-never-return"}}} if ctwa else {},
        )
        mapping = BusinessMessagingConversionMapping(
            id=uuid.uuid4(),
            empresa_id=empresa_id,
            trigger_type=trigger_type,
            trigger_ref="trigger-ref",
            event_name=event_name,
            ativo=True,
            send_mode="dry_run",
            metadata_json={"allowed_sources": allowed_sources} if allowed_sources is not None else {},
        )
        return empresa, lead, mapping

    def test_purchase_accepts_commercial_triggers_before_source_policy(self):
        validate_event_trigger_contract("Purchase", "payment_confirmed")
        validate_event_trigger_contract("Purchase", "tag_applied")
        validate_event_trigger_contract("Purchase", "order_created")

    def test_stable_event_id_reuses_same_key_for_retry(self):
        first = stable_business_messaging_event_id(
            empresa_id="empresa",
            lead_id="lead",
            trigger_type="payment_confirmed",
            trigger_ref="payment",
            source_event_id="provider-event",
            event_name="Purchase",
        )
        second = stable_business_messaging_event_id(
            empresa_id="empresa",
            lead_id="lead",
            trigger_type="payment_confirmed",
            trigger_ref="payment",
            source_event_id="provider-event",
            event_name="Purchase",
        )
        self.assertEqual(first, second)

    async def test_dry_run_uses_tenant_dataset_credential_and_audits_without_http(self):
        empresa, lead, mapping = self._objects()
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)) as resolve, \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record, \
             patch("app.services.meta.business_messaging_conversion_engine.enviar_payload_meta_business_messaging", AsyncMock()) as send:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1", event_time=1700001234),
            )
        self.assertEqual(result["status"], "dry_run")
        resolve.assert_awaited_once()
        send.assert_not_awaited()
        kwargs = record.await_args.kwargs
        self.assertEqual(kwargs["trigger_type"], "tag_applied")
        self.assertEqual(kwargs["trigger_ref"], "trigger-ref")
        self.assertEqual(kwargs["trigger_source"], "system")
        self.assertEqual(kwargs["source"], "system")
        self.assertEqual(kwargs["trigger_source_event_id"], "source-event-1")
        self.assertTrue(kwargs["ctwa_present"])
        self.assertEqual(kwargs["payload"]["data"][0]["event_time"], 1700001234)
        self.assertNotIn("raw-ctwa-never-return", str(result))

    async def test_test_event_mode_includes_configured_test_event_code_without_live_post(self):
        empresa, lead, mapping = self._objects()
        mapping.send_mode = "test_event"
        mapping.metadata_json = {"allowed_sources": ["system"], "test_event_code": "TEST123"}
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record, \
             patch("app.services.meta.business_messaging_conversion_engine.enviar_payload_meta_business_messaging", AsyncMock()) as send:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1"),
            )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(record.await_args.kwargs["test_event_code"], "TEST123")
        self.assertEqual(record.await_args.kwargs["payload"]["test_event_code"], "TEST123")
        send.assert_not_awaited()

    async def test_organic_qualifiedlead_creates_commercial_event_without_meta_delivery(self):
        empresa, lead, mapping = self._objects(ctwa=False)
        mapping.event_name = "QualifiedLead"
        mapping.trigger_type = "crm_stage_changed"
        session = Session(empresa, lead, mapping)
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock()) as resolve, \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "crm_stage_changed", "trigger-ref", "source-event-1"),
            )
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["reason"], "no_ctwa_attribution")
        self.assertEqual(result["commercial_event_status"], "confirmed")
        self.assertEqual(result["meta_delivery_status"], "not_applicable")
        resolve.assert_not_awaited()
        self.assertEqual(record.await_args.kwargs["status"], "not_applicable")
        self.assertEqual(record.await_args.kwargs["error_message"], "no_ctwa_attribution")
        self.assertFalse(record.await_args.kwargs["ctwa_present"])

    async def test_organic_ai_purchase_is_commercially_allowed_but_not_delivered_to_meta(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="tag_applied", ctwa=False, allowed_sources=["ai"])
        session = Session(empresa, lead, mapping)
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock()) as resolve, \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1", trigger_source="ai"),
            )
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["reason"], "no_ctwa_attribution")
        self.assertEqual(result["commercial_event_status"], "confirmed")
        self.assertEqual(result["meta_delivery_status"], "not_applicable")
        resolve.assert_not_awaited()
        self.assertEqual(record.await_args.kwargs["trigger_source"], "ai")
        self.assertEqual(record.await_args.kwargs["source"], "ai")
        self.assertFalse(record.await_args.kwargs["ctwa_present"])

    async def test_purchase_by_ai_tag_mapping_is_allowed_when_mapping_allows_ai(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="tag_applied", allowed_sources=["ai"])
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1", trigger_source="ai"),
            )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(record.await_args.kwargs["trigger_source"], "ai")
        self.assertEqual(record.await_args.kwargs["source"], "ai")

    async def test_purchase_by_ai_tag_mapping_is_blocked_when_mapping_denies_ai(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="tag_applied", allowed_sources=["manual"])
        session = Session(empresa, lead, mapping)
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock()) as resolve, \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1", trigger_source="ai"),
            )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "trigger_source_not_allowed")
        resolve.assert_not_awaited()
        self.assertEqual(record.await_args.kwargs["trigger_source"], "ai")

    async def test_purchase_by_manual_tag_mapping_is_allowed_when_mapping_allows_manual(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="tag_applied", allowed_sources=["manual"])
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "tag_applied", "trigger-ref", "source-event-1", trigger_source="manual"),
            )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(record.await_args.kwargs["trigger_source"], "manual")

    async def test_purchase_via_integration_and_system_remains_supported(self):
        for source in ("integration", "system"):
            empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="payment_confirmed", allowed_sources=[source])
            session = Session(empresa, lead, mapping)
            credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
            with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
                 patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()):
                result = await process_business_messaging_conversion_trigger(
                    session,
                    BusinessMessagingTrigger(empresa.id, lead.id, "payment_confirmed", "trigger-ref", f"source-event-{source}", trigger_source=source),
                )
            self.assertEqual(result["status"], "dry_run")

    async def test_generic_payment_domain_event_builds_purchase_value_from_payload(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="payment_confirmed", allowed_sources=["integration"])
        mapping.trigger_ref = "payment_confirmed"
        mapping.default_value = 999
        mapping.currency = "BRL"
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await emit_conversion_domain_event(
                session,
                ConversionDomainEvent(
                    empresa_id=empresa.id,
                    event_type="payment_confirmed",
                    lead_id=lead.id,
                    trigger_ref="payment_confirmed",
                    source_event_id="stripe:pay_123",
                    trigger_source="integration",
                    event_time=1700002222,
                    payload={
                        "provider": "stripe",
                        "external_payment_id": "pay_123",
                        "amount": "123.45",
                        "currency": "USD",
                        "order_id": "order-123",
                        "metadata": {"must_not_leak": "x"},
                    },
                ),
            )
        self.assertEqual(result["status"], "dry_run")
        custom_data = record.await_args.kwargs["payload"]["data"][0]["custom_data"]
        self.assertEqual(custom_data["value"], 123.45)
        self.assertEqual(custom_data["currency"], "USD")
        self.assertEqual(custom_data["order_id"], "order-123")
        self.assertNotIn("metadata", custom_data)

    async def test_generic_checkout_domain_event_uses_system_event_mapping(self):
        empresa, lead, mapping = self._objects(event_name="InitiateCheckout", trigger_type="system_event", allowed_sources=["system"])
        mapping.trigger_ref = "checkout_created"
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await emit_conversion_domain_event(
                session,
                ConversionDomainEvent(
                    empresa_id=empresa.id,
                    event_type="checkout_created",
                    lead_id=lead.id,
                    source_event_id="checkout:pref_123",
                    event_time=1700003333,
                    payload={"provider": "asaas", "amount": 50, "currency": "BRL"},
                ),
            )
        self.assertEqual(result["status"], "dry_run")
        kwargs = record.await_args.kwargs
        self.assertEqual(kwargs["trigger_type"], "system_event")
        self.assertEqual(kwargs["trigger_ref"], "checkout_created")
        self.assertEqual(kwargs["payload"]["data"][0]["custom_data"]["value"], 50.0)

    async def test_new_generic_tenant_flow_has_no_leoft_dependency_in_engine(self):
        empresa, lead, mapping = self._objects(event_name="LeadSubmitted", trigger_type="system_event", allowed_sources=["system"])
        mapping.trigger_ref = "ctwa_lead_created"
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-example", "dataset-example", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()) as record:
            result = await emit_conversion_domain_event(
                session,
                ConversionDomainEvent(
                    empresa_id=empresa.id,
                    event_type="ctwa_lead_created",
                    lead_id=lead.id,
                    source_event_id="wamid.example",
                    event_time=1700004444,
                    payload={"provider": "meta_official", "conversation_id": "conv-example"},
                ),
            )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(record.await_args.kwargs["payload"]["data"][0]["user_data"]["whatsapp_business_account_id"], "waba-example")
        with open("app/services/meta/business_messaging_conversion_engine.py", encoding="utf-8") as handle:
            engine_source = handle.read().lower()
        self.assertNotIn("leoft", engine_source)
        self.assertNotIn("leonardo", engine_source)

    async def test_source_policy_is_tenant_scoped_for_same_tag_name(self):
        empresa_a, lead_a, mapping_a = self._objects(event_name="Purchase", trigger_type="tag_applied", allowed_sources=["ai"])
        empresa_b, lead_b, mapping_b = self._objects(event_name="Purchase", trigger_type="tag_applied", allowed_sources=["manual"])
        mapping_b.trigger_ref = mapping_a.trigger_ref
        credential_a = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa_a.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential_a)), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()):
            allowed = await process_business_messaging_conversion_trigger(
                Session(empresa_a, lead_a, mapping_a),
                BusinessMessagingTrigger(empresa_a.id, lead_a.id, "tag_applied", mapping_a.trigger_ref, "source-event-1", trigger_source="ai"),
            )
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock()), \
             patch("app.services.meta.business_messaging_conversion_engine.registrar_evento_meta_capi", AsyncMock()):
            denied = await process_business_messaging_conversion_trigger(
                Session(empresa_b, lead_b, mapping_b),
                BusinessMessagingTrigger(empresa_b.id, lead_b.id, "tag_applied", mapping_b.trigger_ref, "source-event-1", trigger_source="ai"),
            )
        self.assertEqual(allowed["status"], "dry_run")
        self.assertEqual(denied["reason"], "trigger_source_not_allowed")

    async def test_live_duplicate_reuses_reservation_key_without_second_send(self):
        empresa, lead, mapping = self._objects(event_name="Purchase", trigger_type="payment_confirmed", allowed_sources=["integration"])
        mapping.send_mode = "live"
        session = Session(empresa, lead, mapping)
        credential = ResolvedMetaCredential("tenant_conversion_token", "secret", str(empresa.id), "waba-a", "dataset-a", "v25.0")
        with patch("app.services.meta.business_messaging_conversion_engine.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversion_engine.reservar_evento_meta_capi_live", AsyncMock(return_value=(None, "duplicate_sent"))) as reserve, \
             patch("app.services.meta.business_messaging_conversion_engine.enviar_payload_meta_business_messaging", AsyncMock()) as send:
            result = await process_business_messaging_conversion_trigger(
                session,
                BusinessMessagingTrigger(empresa.id, lead.id, "payment_confirmed", "payment", "payment-provider-event", trigger_source="integration", send_real=True),
            )
        self.assertEqual(result["reason"], "duplicate_sent")
        self.assertTrue(reserve.await_args.kwargs["reservation_key"].startswith("business_messaging:"))
        self.assertEqual(reserve.await_args.kwargs["trigger_source"], "integration")
        send.assert_not_awaited()

    def test_default_mapping_sources_are_conservative_but_not_global_block(self):
        self.assertEqual(DEFAULT_ALLOWED_TRIGGER_SOURCES, {"manual", "integration", "system"})

    def test_trigger_model_declares_required_types(self):
        from app.services.meta.business_messaging_conversion_engine import ALLOWED_TRIGGER_SOURCES, ALLOWED_TRIGGER_TYPES, HIGH_QUALITY_EVENT_CONTRACT

        self.assertTrue({"tag_applied", "crm_stage_changed", "order_created", "payment_confirmed", "system_event"}.issubset(ALLOWED_TRIGGER_TYPES))
        self.assertIn("tag_applied", HIGH_QUALITY_EVENT_CONTRACT["Purchase"])
        self.assertIn("payment_confirmed", HIGH_QUALITY_EVENT_CONTRACT["Purchase"])
        self.assertTrue({"ai", "manual", "automation", "integration", "system"}.issubset(ALLOWED_TRIGGER_SOURCES))
