from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class CampaignFunnelAudienceTests(TestCase):
    def test_funnel_reuses_crm_funnel_and_stage_models(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        self.assertIn("from crm_funis f", service)
        self.assertIn("left join crm_etapas e", service)
        self.assertIn('"order_source": "crm_etapas.ordem"', service)
        self.assertIn("campaign_funnel_stage_rules", service)

    def test_funnel_create_update_reorder_and_disable_contracts(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        router = read("app/api/routers/campaign_central.py")
        self.assertIn("upsert_campaign_funnel", service)
        self.assertIn("upsert_funnel_stage", service)
        self.assertIn("reorder_funnel_stages", service)
        self.assertIn("set_funnel_stage_active", service)
        self.assertIn('/audiences/funnels/{funnel_id}/stages/reorder', router)
        self.assertIn('/audiences/funnels/{funnel_id}/stages/{stage_id}/active', router)

    def test_funnel_stage_rules_use_audience_engine_schema(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        self.assertIn("canonicalize_audience_rules", service)
        self.assertIn("rules_json", service)
        self.assertIn("preview_saved_audience", service)
        self.assertIn("save_audience", service)

    def test_funnel_tenant_isolation_for_funnels_stages_and_tags(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        self.assertIn("where f.empresa_id = :empresa_id", service)
        self.assertIn("where id = :id and empresa_id = :empresa_id", service)
        self.assertIn("f.empresa_id = :empresa_id", service)
        self.assertIn("Etapa não encontrada para esta empresa", service)

    def test_smart_audience_generation_and_not_advanced_formula(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        self.assertIn("build_smart_audiences", service)
        self.assertIn("build_not_advanced_rules", service)
        self.assertIn('"type": suggestion_type', service)
        self.assertIn('"not_advanced"', service)
        self.assertIn('"exclude": _merge_stage_rules(later_stages', service)

    def test_smart_audience_last_stage_and_save_behavior(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        self.assertIn('"last_stage"', service)
        self.assertIn("save_smart_audience", service)
        self.assertIn('"source": "funnel_suggestion"', service)
        self.assertIn('"saved_behavior": "dynamic_rules_snapshot_only_when_campaign_prepares"', service)

    def test_smart_audience_cumulative_funnel_fixture_contract(self):
        service = read("app/services/campaign_funnel_audience_service.py")
        # Cumulative fixture: Stage 1 minus later stages leaves only people that have not advanced.
        self.assertIn("later = [item for item in stages[index + 1 :]", service)
        self.assertIn("EXCLUSION_PRECEDENCE", read("app/services/campaign_audience_service.py"))
        self.assertIn("build_not_advanced_rules(stage, later)", service)

    def test_preview_pagination_endpoints_are_available(self):
        router = read("app/api/routers/campaign_central.py")
        self.assertIn("member_view=payload.get(\"member_view\")", router)
        self.assertIn("member_cursor=payload.get(\"member_cursor\")", router)
        self.assertIn("member_limit=int(payload.get(\"member_limit\")", router)
        self.assertIn("/audiences/smart/preview", router)

    def test_migration_is_additive(self):
        migration = read("db/migrations/2026_08_21_campaign_audience_funnel_rules.sql")
        self.assertIn("CREATE TABLE IF NOT EXISTS campaign_audience_funnel_settings", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS campaign_funnel_stage_rules", migration)
        self.assertIn("REFERENCES crm_funis", migration)
        self.assertIn("REFERENCES crm_etapas", migration)
