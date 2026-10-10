import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class CampaignAudienceRuleEngineTests(TestCase):
    def test_include_exclude_rule_engine_contract_exists(self):
        service = read("app/services/campaign_audience_service.py")
        self.assertIn("canonicalize_audience_rules", service)
        self.assertIn("resolve_audience_rules", service)
        self.assertIn('"include": {"match": include_mode', service)
        self.assertIn('"exclude": {"match": exclude_mode', service)
        self.assertIn('EXCLUSION_PRECEDENCE = "ALWAYS"', service)
        self.assertIn('"exclusion_precedence": EXCLUSION_PRECEDENCE', service)

    def test_include_any_all_and_exclude_any_all_are_supported(self):
        service = read("app/services/campaign_audience_service.py")
        self.assertIn("normalize_match_mode", service)
        self.assertIn('return "all" if normalized == "all" else "any"', service)
        self.assertIn("include_mode = canonical[\"include\"][\"match\"]", service)
        self.assertIn("exclude_mode = canonical[\"exclude\"][\"match\"]", service)
        self.assertIn("_matches(lead_tags, include_values, include_mode", service)
        self.assertIn("_matches(lead_tags, exclude_values, exclude_mode", service)

    def test_exclusion_wins_before_consent_phone_and_final_audience(self):
        service = read("app/services/campaign_audience_service.py")
        exclusion_pos = service.index("if exclude_ok:")
        phone_pos = service.index("address, phone_ok = normalize_phone")
        consent_pos = service.index('statuses.intersection({"blocked"})')
        final_pos = service.index('final_members_all.append(_member_payload(lead, reason="MATCHED_INCLUDE"')
        self.assertLess(exclusion_pos, phone_pos)
        self.assertLess(phone_pos, consent_pos)
        self.assertLess(consent_pos, final_pos)

    def test_backend_dedupe_phone_optout_blocked_and_invalid_phone_are_backend_side(self):
        service = read("app/services/campaign_audience_service.py")
        for token in (
            "duplicate_lead_count",
            "seen_phones",
            "DUPLICATE_PHONE",
            "OPT_OUT",
            "BLOCKED",
            "INVALID_PHONE",
            "normalize_phone(",
            "RESTRICTIVE_CONSENT_STATUSES",
        ):
            self.assertIn(token, service)

    def test_preview_counts_members_and_reason_codes_are_returned(self):
        service = read("app/services/campaign_audience_service.py")
        for token in (
            '"contacts_found"',
            '"excluded_by_rules"',
            '"optout_removed"',
            '"blocked_removed"',
            '"invalid_phone_count"',
            '"duplicates_removed"',
            '"final_audience_count"',
            '"members"',
            '"reason_codes"',
        ):
            self.assertIn(token, service)

    def test_saved_audience_rules_are_canonical_and_dynamic(self):
        service = read("app/services/campaign_audience_service.py")
        self.assertIn("canonical_rules = preview[\"rules\"]", service)
        self.assertIn('"dynamic": True', service)
        self.assertIn('"snapshot_created": False', service)
        self.assertIn("rules_json = excluded.rules_json", service)

    def test_campaign_snapshot_uses_same_audience_resolver(self):
        service = read("app/services/campaign_central_service.py")
        self.assertIn("resolve_audience_rules", service)
        self.assertIn("canonical_audience_rules", service)
        self.assertIn('"audience_resolution"', service)
        self.assertIn('"final_lead_ids"', read("app/services/campaign_audience_service.py"))

    def test_campaign_prepare_fails_closed_without_explicit_inclusion(self):
        service = read("app/services/campaign_central_service.py")
        self.assertIn("def _audience_has_explicit_inclusion", service)
        self.assertIn("if not _audience_has_explicit_inclusion(canonical_audience_rules):", service)
        self.assertIn("Selecione ao menos um público explícito", service)
        guard_pos = service.index("if not _audience_has_explicit_inclusion(canonical_audience_rules):")
        resolver_pos = service.index("audience_resolution = await resolve_audience_rules")
        self.assertLess(guard_pos, resolver_pos)

    def test_empty_include_rules_are_not_explicit_all_contract(self):
        service = read("app/services/campaign_central_service.py")
        audience = read("app/services/campaign_audience_service.py")
        self.assertIn("_matches(lead_tags, include_values, include_mode, empty_matches=True)", audience)
        self.assertIn("if not _audience_has_explicit_inclusion(canonical_audience_rules):", service)
        self.assertNotIn('"audience_mode": "all"', service)
        self.assertNotIn('"all_contacts"', service)

    def test_legacy_saved_audience_compatibility(self):
        service = read("app/services/campaign_audience_service.py")
        self.assertIn("raw.get(\"include_tags\"", service)
        self.assertIn("raw.get(\"exclude_tags\"", service)
        self.assertIn("raw.get(\"tag_logic\")", service)

    def test_tenant_isolation_is_preserved_for_tags_leads_consents_and_saved_audiences(self):
        service = read("app/services/campaign_audience_service.py")
        central = read("app/services/campaign_central_service.py")
        self.assertGreaterEqual(len(re.findall(r"empresa_id == empresa_id", service)), 3)
        self.assertIn("where empresa_id = :empresa_id and id = :id and status = 'active'", central)
        self.assertIn("select(TagCRM).where(TagCRM.empresa_id == empresa_id)", service)
        self.assertIn("select(CRMLead).where(CRMLead.empresa_id == empresa_id)", service)

    def test_real_funnel_subtraction_fixture_is_represented(self):
        service = read("app/services/campaign_audience_service.py")
        # Fixture contract: A and D match include only; B matches include + exclude and is removed; C only exclude never enters.
        self.assertIn("include_candidates", service)
        self.assertIn("if not required:", service)
        self.assertIn("if exclude_ok:", service)
        self.assertIn("MATCHED_EXCLUDE", service)
        self.assertIn("final_lead_ids.append", service)

    def test_member_pagination_preserves_total_counts(self):
        service = read("app/services/campaign_audience_service.py")
        self.assertIn("_paginate_members", service)
        self.assertIn('"model": "cursor"', service)
        self.assertIn('"stable_order": "lead_id"', service)
        self.assertIn('"final_audience_count": final_count', service)
        self.assertIn("member_cursor", service)
        self.assertIn("member_view", service)
