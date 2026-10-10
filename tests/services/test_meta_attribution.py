import unittest
import uuid
from types import SimpleNamespace

from app.services.ads_integration_service import attribution_preview_seguro, montar_payload_meta_capi, payload_preview_seguro
from app.services.meta_attribution import apply_meta_attribution, dados_adicionais_for_public_response, extract_meta_referral
from db.models import CRMLead, Empresa, TagCRM


def _lead():
    return CRMLead(id=uuid.uuid4(), empresa_id=uuid.uuid4(), nome_contato="Lead", telefone_contato="5511999999999", dados_adicionais={})


class MetaAttributionTests(unittest.IsolatedAsyncioTestCase):
    def test_referral_keeps_only_documented_present_fields_without_normalizing_ctwa_clid(self):
        raw = {"ctwa_clid": "CtWa-Exact Value", "source_id": "123", "body": "ad", "ignored": "no"}
        self.assertEqual(extract_meta_referral(raw), {"ctwa_clid": "CtWa-Exact Value", "source_id": "123", "body": "ad"})


    def test_first_touch_is_immutable_and_new_ctwa_updates_last_touch(self):
        lead = _lead()
        apply_meta_attribution(lead, {"ctwa_clid": "first", "source_id": "one"}, captured_at="2026-07-23T00:00:00")
        apply_meta_attribution(lead, {"ctwa_clid": "second", "source_id": "two"}, captured_at="2026-07-23T01:00:00")
        attribution = lead.dados_adicionais["meta_attribution"]
        self.assertEqual(attribution["first_touch"]["ctwa_clid"], "first")
        self.assertEqual(attribution["last_touch"]["ctwa_clid"], "second")

    def test_inbound_without_referral_does_not_erase_known_attribution(self):
        lead = _lead()
        apply_meta_attribution(lead, {"ctwa_clid": "known-click", "source_id": "known-ad"}, captured_at="2026-09-01T00:00:00Z")
        before = dict(lead.dados_adicionais["meta_attribution"])
        self.assertFalse(apply_meta_attribution(lead, None, captured_at="2026-09-02T00:00:00Z"))
        self.assertEqual(lead.dados_adicionais["meta_attribution"], before)


    def test_same_ctwa_is_idempotent_and_public_data_never_contains_it(self):
        lead = _lead()
        apply_meta_attribution(lead, {"ctwa_clid": "secret-value"}, captured_at="a")
        apply_meta_attribution(lead, {"ctwa_clid": "secret-value"}, captured_at="b")
        attribution = lead.dados_adicionais["meta_attribution"]
        self.assertEqual(attribution["first_touch"]["ctwa_clid"], "secret-value")
        self.assertEqual(attribution["last_touch"]["last_seen_at"], "b")
        self.assertNotIn("meta_attribution", dados_adicionais_for_public_response(lead.dados_adicionais))


    async def test_dry_run_payload_recovers_attribution_without_exposing_raw_value_in_preview(self):
        lead = _lead()
        apply_meta_attribution(lead, {"ctwa_clid": "raw-ctwa-identifier"}, captured_at="a")
        empresa = Empresa(id=lead.empresa_id, nome_empresa="test")
        tag = TagCRM(id=uuid.uuid4(), empresa_id=lead.empresa_id, nome="tag")
        config = SimpleNamespace(event_name="Lead", currency="BRL")
        payload = await montar_payload_meta_capi(lead, tag, empresa, config, event_id="event-a")
        item = payload["data"][0]
        self.assertEqual(item["action_source"], "chat")
        self.assertNotIn("ctwa_clid", item)
        self.assertEqual(attribution_preview_seguro(lead), {"messaging_channel": "whatsapp", "ctwa_clid": "raw-...fier"})
        self.assertNotIn("raw-ctwa-identifier", str(payload_preview_seguro(payload)))
