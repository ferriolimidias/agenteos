import os
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services.meta.conversion_credentials import EMBEDDED_SIGNUP_TENANT, MetaCredentialResolutionError
from app.services.meta.graph_client import MetaGraphError
from app.services.meta.marketing_api import MetaMarketingApiClient, graph_post_data, sanitize_graph_error
from app.services.meta.marketing_credentials import (
    ADMIN_MARKETING_SOURCE,
    MetaMarketingCapability,
    MetaMarketingCredentialResolver,
    marketing_admin_status,
)
from app.services.meta.settings import meta_settings
from app.services.meta.traffic_campaign_builder import (
    ADSET_BILLING_EVENT,
    ADSET_BID_STRATEGY,
    ADSET_DESTINATION_TYPE,
    ADSET_OPTIMIZATION_GOAL,
    CAMPAIGN_ADSET_BUDGET_SHARING_ENABLED,
    CAMPAIGN_OBJECTIVE,
    FORCED_PAUSED_STATUS,
    brl_to_meta_cents,
    build_adset_payload,
    build_brazil_targeting,
    build_campaign_payload,
    build_radius_targeting,
    build_ad_payload,
    build_creative_payload,
    generated_object_name,
)
from app.api.routers.traffic_meta import _digits, _ensure_resume_item_names, _is_active_meta_whatsapp_connection, _is_golden_whatsapp_binding, _resume_item_write_plan, _resume_parent_write_plan, _validate_page_whatsapp_compatibility
from db.models import TipoConexao


class FakeConnection:
    id = uuid.uuid4()
    credenciais = {
        "encrypted_access_token": "encrypted",
        "encrypted_marketing_access_token": "marketing-encrypted",
        "marketing_granted_permissions": ["ads_read", "ads_management", "business_management"],
    }
    metadata_json = {
        "meta_business_portfolio_id": "1234567890",
        "marketing_business_id": "business-1",
        "marketing_ad_account_id": "123",
    }


class FakeTrafficConnection:
    def __init__(self, *, tipo=TipoConexao.META, channel="whatsapp", provider="meta_official", status="connected"):
        self.id = uuid.uuid4()
        self.tipo = tipo
        self.channel = channel
        self.provider = provider
        self.status = status
        self.phone_number_id = "phone-1"
        self.waba_id = "waba-1"
        self.display_number = "+55 11 99999-1234"
        self.metadata_json = {}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.gets = []

    async def get(self, url, *, params=None, headers=None):
        self.gets.append({"url": url, "params": params, "headers": headers})
        return self.response

    async def post(self, url, *, data=None, json=None, headers=None, files=None):
        self.posts.append({"url": url, "data": data, "json": json, "headers": headers, "files": files})
        return self.response


class TrafficMetaFoundationTests(unittest.IsolatedAsyncioTestCase):
    def _admin_env(self, tenant="tenant", **values):
        base = {
            "META_MARKETING_GRAPH_VERSION": "v26.0",
            "META_MARKETING_ADMIN_ACCESS_TOKEN": "marketing-admin-token",
        }
        base.update(values)
        return patch.dict(os.environ, base, clear=False)

    async def test_marketing_graph_version_is_v26(self):
        with patch.dict(os.environ, {"META_MARKETING_GRAPH_VERSION": "v26.0"}, clear=False):
            self.assertEqual(meta_settings().marketing_graph_api_version, "v26.0")

    async def test_traffic_meta_reads_never_use_global_admin_fallback(self):
        from app.api.routers.traffic_meta import _client

        class Resolved:
            access_token = "tenant-read-token"
            graph_version = "v26.0"
            read_only = True

            def sanitized_metadata(self):
                return {"credential_source": "embedded_signup_tenant"}

        with patch.object(MetaMarketingCredentialResolver, "resolve", new=AsyncMock(return_value=Resolved())) as resolve:
            await _client(object(), uuid.uuid4(), MetaMarketingCapability.ADS_READ)
        self.assertFalse(resolve.await_args.kwargs["allow_admin_fallback"])

    async def test_resolver_uses_tenant_marketing_token_when_capability_granted(self):
        resolver = MetaMarketingCredentialResolver()
        config = type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})()
        with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=FakeConnection())):
            with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                with patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="tenant-token"):
                    credential = await resolver.resolve(object(), "tenant-a", "ads_read")
        self.assertEqual(credential.credential_source, "tenant_marketing_oauth")
        self.assertEqual(credential.capability, MetaMarketingCapability.ADS_READ)
        self.assertNotIn("tenant-token", str(credential.sanitized_metadata()))

    async def test_admin_marketing_credential_cannot_be_used_for_writes(self):
        resolver = MetaMarketingCredentialResolver()
        connection = FakeConnection()
        connection.credenciais = {"encrypted_access_token": "encrypted", "granted_permissions": ["ads_read"]}
        config = type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})()
        with self._admin_env("tenant-a"), patch.object(resolver, "_embedded_connection", AsyncMock(return_value=connection)):
            with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                with self.assertRaisesRegex(MetaCredentialResolutionError, "TENANT_MARKETING_CREDENTIAL_UNAVAILABLE"):
                    await resolver.resolve(object(), "tenant-a", "ads_management", ad_account_id="act_123")

    async def test_missing_capability_without_admin_is_safe_error(self):
        resolver = MetaMarketingCredentialResolver()
        connection = FakeConnection()
        connection.credenciais = {"encrypted_access_token": "encrypted", "granted_permissions": ["ads_read"]}
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": ""}, clear=False):
            with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=connection)):
                with patch.object(resolver, "_tenant_config", AsyncMock(return_value=type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})())):
                    with self.assertRaisesRegex(MetaCredentialResolutionError, "MARKETING_CREDENTIAL_UNAVAILABLE"):
                        await resolver.resolve(object(), "tenant-a", "ads_management")

    async def test_missing_admin_token_is_safe_error_and_status_has_no_token(self):
        resolver = MetaMarketingCredentialResolver()
        with self._admin_env("tenant-a", META_MARKETING_ADMIN_ACCESS_TOKEN=""):
            with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=None)):
                with patch.object(resolver, "_tenant_config", AsyncMock(return_value=type("Config", (), {"ad_account_id": "123", "business_id": "business-a"})())):
                    with self.assertRaisesRegex(MetaCredentialResolutionError, "MARKETING_CREDENTIAL_UNAVAILABLE"):
                        await resolver.resolve(object(), "tenant-a", "ads_read", ad_account_id="123", allow_admin_fallback=True)
            status = marketing_admin_status("tenant-a")
        self.assertFalse(status["admin_token_configured"])
        self.assertNotIn("marketing-admin-token", str(status))

    async def test_admin_fallback_is_global_for_new_tenants_and_assets(self):
        resolver = MetaMarketingCredentialResolver()
        config = type("Config", (), {"ad_account_id": "999", "business_id": "business-a"})()
        empty_result = type("Result", (), {
            "scalar_one_or_none": lambda self: None,
            "scalars": lambda self: type("Scalars", (), {"all": lambda self: []})(),
            "first": lambda self: None,
        })()
        session = type("Session", (), {
            "scalar": AsyncMock(side_effect=[None, None]),
            "execute": AsyncMock(return_value=empty_result),
        })()
        with self._admin_env("tenant-a", META_MARKETING_ADMIN_TEST_TENANT_IDS="different", META_MARKETING_ADMIN_AD_ACCOUNT_IDS="111"):
            with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=None)):
                with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                    with self.assertRaisesRegex(MetaCredentialResolutionError, "ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED"):
                        await resolver.resolve(session, "new-tenant", "ads_read", ad_account_id="act_999", allow_admin_fallback=True)

    async def test_read_only_client_uses_v26_and_blocks_post(self):
        transport = FakeTransport(FakeResponse(200, {"id": "me"}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        body = await client.get_me()
        self.assertEqual(body["id"], "me")
        self.assertIn("https://graph.facebook.com/v26.0/me", transport.gets[0]["url"])
        with self.assertRaisesRegex(RuntimeError, "META_MARKETING_API_READ_ONLY"):
            await client.post("campaigns")
        with self.assertRaisesRegex(RuntimeError, "META_MARKETING_API_WRITE_BLOCKED"):
            await client.patch("campaigns")
        with self.assertRaisesRegex(RuntimeError, "META_MARKETING_API_WRITE_BLOCKED"):
            await client.delete("campaigns")
        self.assertEqual(transport.posts, [])

    async def test_campaign_and_adset_create_are_flag_gated_and_force_paused(self):
        transport = FakeTransport(FakeResponse(200, {"id": "created-id"}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "false"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "META_MARKETING_WRITE_DISABLED"):
                await client.create_campaign("act_111", {"name": "Teste", "status": "ACTIVE"})
        with patch.dict(
            os.environ,
            {
                "META_MARKETING_WRITE_ENABLED": "true",
                "META_MARKETING_CAMPAIGN_CREATE_ENABLED": "true",
                "META_MARKETING_ADSET_CREATE_ENABLED": "true",
            },
            clear=False,
        ):
            await client.create_campaign("111", {"name": "Teste", "status": "ACTIVE"})
            await client.create_adset("act_111", {"name": "AdSet", "status": "ACTIVE"})
        self.assertEqual(transport.posts[0]["url"], "https://graph.facebook.com/v26.0/act_111/campaigns")
        self.assertEqual(transport.posts[0]["data"]["status"], "PAUSED")
        self.assertEqual(transport.posts[1]["url"], "https://graph.facebook.com/v26.0/act_111/adsets")
        self.assertEqual(transport.posts[1]["data"]["status"], "PAUSED")

    async def test_campaign_and_adset_posts_encode_graph_complex_fields(self):
        transport = FakeTransport(FakeResponse(200, {"id": "created-id"}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        with patch.dict(
            os.environ,
            {
                "META_MARKETING_WRITE_ENABLED": "true",
                "META_MARKETING_CAMPAIGN_CREATE_ENABLED": "true",
                "META_MARKETING_ADSET_CREATE_ENABLED": "true",
            },
            clear=False,
        ):
            await client.create_campaign("111", {"name": "Teste", "special_ad_categories": []})
            await client.create_adset("111", {"name": "AdSet", "targeting": {"geo_locations": {"countries": ["BR"]}}})
        self.assertEqual(transport.posts[0]["data"]["special_ad_categories"], "[]")
        self.assertEqual(transport.posts[1]["data"]["targeting"], '{"geo_locations":{"countries":["BR"]}}')

    async def test_full_delivery_writes_are_flag_gated_and_paused(self):
        transport = FakeTransport(FakeResponse(200, {"id": "created-id"}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_CREATIVE_CREATE_ENABLED": "false", "META_MARKETING_AD_CREATE_ENABLED": "false"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "CREATIVE_CREATE_DISABLED"):
                await client.create_creative("111", {"name": "Creative"})
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_CREATIVE_CREATE_ENABLED": "true", "META_MARKETING_AD_CREATE_ENABLED": "true"}, clear=False):
            await client.create_creative("111", {"name": "Creative", "object_story_spec": {"page_id": "p1"}})
            await client.create_ad("111", {"name": "Ad", "status": "ACTIVE", "adset_id": "s1", "creative": {"creative_id": "c1"}})
        self.assertEqual(transport.posts[0]["data"]["object_story_spec"], '{"page_id":"p1"}')
        self.assertEqual(transport.posts[1]["data"]["status"], "PAUSED")

    async def test_ambiguous_graph_write_is_never_retried(self):
        transport = FakeTransport(FakeResponse(500, {"error": {"code": 1, "type": "OAuthException", "message": "An unknown error has occurred."}}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport, max_attempts=2)
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_CREATIVE_CREATE_ENABLED": "true"}, clear=False):
            with self.assertRaises(MetaGraphError):
                await client.create_creative("111", {"name": "Creative"})
        self.assertEqual(len(transport.posts), 1)

    async def test_creative_reconciliation_read_uses_deterministic_names(self):
        transport = FakeTransport(FakeResponse(200, {"data": [{"id": "creative-1", "name": "creative-stable"}]}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        creatives = await client.list_creatives("111")
        self.assertEqual(creatives[0]["name"], "creative-stable")
        self.assertTrue(transport.gets[0]["url"].endswith("/act_111/adcreatives"))

    async def test_custom_conversions_are_read_from_the_tenant_ad_account(self):
        transport = FakeTransport(FakeResponse(200, {"data": [{"id": "cc-1", "event_source_id": "dataset-1", "action_source_type": "business_messaging", "custom_event_type": "PURCHASE"}]}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        conversions = await client.list_custom_conversions("act_111")
        self.assertEqual(conversions[0]["event_source_id"], "dataset-1")
        self.assertEqual(conversions[0]["action_source_type"], "business_messaging")
        self.assertTrue(transport.gets[0]["url"].endswith("/act_111/customconversions"))

    async def test_custom_conversion_creation_is_explicitly_disabled_by_default(self):
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=FakeTransport(FakeResponse(200, {"id": "cc-1"})))
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_CUSTOM_CONVERSION_CREATE_ENABLED": "false"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "CUSTOM_CONVERSION_CREATE_DISABLED"):
                await client.create_custom_conversion("111", {"name": "Purchase"})

    async def test_business_asset_mutations_are_disabled_by_default(self):
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=FakeTransport(FakeResponse(200, {"id": "ds-1"})))
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_ADS_DATASET_CREATE_ENABLED": "false"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "ADS_DATASET_CREATE_ENABLED_DISABLED"):
                await client.create_business_ads_dataset("business-1", {"name": "Dataset"})
        with patch.dict(os.environ, {"META_MARKETING_WRITE_ENABLED": "true", "META_MARKETING_BUSINESS_ASSET_WRITE_ENABLED": "false"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "BUSINESS_ASSET_WRITE_ENABLED_DISABLED"):
                await client.claim_business_custom_conversion("business-1", "cc-1")

    async def test_tenant_campaign_mutation_does_not_fallback_to_admin_token(self):
        from app.services.meta.marketing_credentials import MetaMarketingCapability, MetaMarketingCredentialResolver
        resolver = MetaMarketingCredentialResolver()
        config = type("Config", (), {"ad_account_id": "123", "business_id": "business-1"})()
        empty_result = type("Result", (), {
            "scalar_one_or_none": lambda self: None,
            "scalars": lambda self: type("Scalars", (), {"all": lambda self: []})(),
            "first": lambda self: None,
        })()
        session = type("Session", (), {
            "scalar": AsyncMock(side_effect=[None, None]),
            "execute": AsyncMock(return_value=empty_result),
        })()
        with patch.dict(os.environ, {"META_MARKETING_ADMIN_ACCESS_TOKEN": "admin-token"}, clear=False):
            with patch.object(resolver, "_embedded_connection", AsyncMock(return_value=None)):
                with patch.object(resolver, "_tenant_config", AsyncMock(return_value=config)):
                    with self.assertRaisesRegex(Exception, "ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED"):
                        await resolver.resolve(session, "tenant-a", MetaMarketingCapability.ADS_MANAGEMENT, allow_admin_fallback=True)

    def test_generated_names_are_normalized_and_distinct_for_multiple_ads(self):
        company_id = "a7384cb4-98b2-4750-94bc-3b329d35156d"
        campaign = generated_object_name("Á Empresa, Principal!", company_id, "CAMPAIGN", 1, today=__import__("datetime").date(2026, 8, 13))
        creative_1 = generated_object_name("Á Empresa, Principal!", company_id, "CREATIVE", 1, ad_sequence=1, today=__import__("datetime").date(2026, 8, 13))
        creative_2 = generated_object_name("Á Empresa, Principal!", company_id, "CREATIVE", 1, ad_sequence=2, today=__import__("datetime").date(2026, 8, 13))
        self.assertEqual(campaign, "AOS | A-EMPRESA-PRINCIPAL | A7384CB4 | CAMPAIGN | 20260813 | 01")
        self.assertNotEqual(creative_1, creative_2)
        self.assertNotIn("Á", creative_1)

    def test_five_draft_items_receive_stable_resume_names(self):
        company_id = uuid.UUID("a7384cb4-98b2-4750-94bc-3b329d35156d")
        states = [
            _ensure_resume_item_names(
                state={"sequence": index, "status": "pending"},
                company_name="Leonardo Personal",
                empresa_id=company_id,
                campaign_sequence=8,
                item_sequence=index,
            )
            for index in range(1, 6)
        ]
        self.assertEqual(len({item["creative_name"] for item in states}), 5)
        self.assertEqual(len({item["ad_name"] for item in states}), 5)
        self.assertTrue(all(item["status"] == "pending" for item in states))
        self.assertEqual(_resume_parent_write_plan(meta_campaign_id="campaign-1", meta_adset_id="adset-1"), {"campaign": False, "adset": False})
        self.assertTrue(all(_resume_item_write_plan(item) == {"media": True, "creative": True, "ad": True} for item in states))
        confirmed = {**states[0], "media_reference": "hash-1", "creative_id": "creative-1", "ad_id": "ad-1"}
        self.assertEqual(_resume_item_write_plan(confirmed), {"media": False, "creative": False, "ad": False})

    def test_binding_comparison_uses_phone_digits(self):
        self.assertEqual(_digits("+55 54 99999-5742"), _digits("5554999995742"))
        self.assertTrue(
            _is_golden_whatsapp_binding(
                {"page_id": "page-1", "smart_pse_enabled": False, "whatsapp_phone_number": "5554999995742"},
                page_id="page-1",
                whatsapp_phone_number="+55 54 99999-5742",
            )
        )

    def test_creative_and_ad_builders_keep_ctwa_contract(self):
        creative = build_creative_payload(name="Creative", page_id="page-1", primary_text="Mensagem", headline="Título", description="Descrição", phone_number_id="phone-1", media_type="image", media_reference="hash-1", instagram_account_id="ig-1")
        ad = build_ad_payload(name="Ad", adset_id="adset-1", creative_id="creative-1")
        self.assertEqual(creative["object_story_spec"]["page_id"], "page-1")
        self.assertEqual(creative["object_story_spec"]["instagram_user_id"], "ig-1")
        self.assertEqual(creative["object_story_spec"]["link_data"]["image_hash"], "hash-1")
        self.assertEqual(creative["object_story_spec"]["link_data"]["call_to_action"]["type"], "WHATSAPP_MESSAGE")
        self.assertEqual(creative["object_story_spec"]["link_data"]["call_to_action"]["value"], {"app_destination": "WHATSAPP"})
        self.assertNotIn("whatsapp_phone_number", str(creative["object_story_spec"]["link_data"]["call_to_action"]))
        self.assertEqual(ad["status"], "PAUSED")
        self.assertEqual(ad["creative"]["creative_id"], "creative-1")

    def test_graph_post_data_preserves_empty_arrays_for_meta(self):
        data = graph_post_data({"special_ad_categories": [], "promoted_object": {"page_id": "1"}, "name": "Teste"})
        self.assertEqual(data["special_ad_categories"], "[]")
        self.assertEqual(data["promoted_object"], '{"page_id":"1"}')
        self.assertEqual(data["name"], "Teste")

    def test_campaign_payload_contract_is_paused_outcome_leads(self):
        payload = build_campaign_payload("Campanha Agosto")
        self.assertEqual(payload["name"], "Campanha Agosto")
        self.assertEqual(payload["objective"], CAMPAIGN_OBJECTIVE)
        self.assertEqual(payload["objective"], "OUTCOME_LEADS")
        self.assertEqual(payload["buying_type"], "AUCTION")
        self.assertEqual(payload["status"], FORCED_PAUSED_STATUS)
        self.assertEqual(payload["status"], "PAUSED")
        self.assertEqual(payload["special_ad_categories"], [])
        self.assertEqual(payload["is_adset_budget_sharing_enabled"], CAMPAIGN_ADSET_BUDGET_SHARING_ENABLED)
        self.assertIs(payload["is_adset_budget_sharing_enabled"], False)

    def test_adset_payload_contract_uses_ctwa_real_contract(self):
        targeting = build_brazil_targeting()
        payload = build_adset_payload(
            name="Campanha Agosto",
            campaign_id="123",
            daily_budget_cents=3000,
            page_id="page-1",
            phone_number="+55 11 99999-1234",
            targeting=targeting,
        )
        self.assertEqual(payload["status"], "PAUSED")
        self.assertEqual(payload["optimization_goal"], ADSET_OPTIMIZATION_GOAL)
        self.assertEqual(payload["optimization_goal"], "CONVERSATIONS")
        self.assertEqual(payload["billing_event"], ADSET_BILLING_EVENT)
        self.assertEqual(payload["billing_event"], "IMPRESSIONS")
        self.assertEqual(payload["bid_strategy"], ADSET_BID_STRATEGY)
        self.assertEqual(payload["bid_strategy"], "LOWEST_COST_WITHOUT_CAP")
        self.assertNotIn("bid_amount", payload)
        self.assertNotIn("bid_constraints", payload)
        self.assertEqual(payload["destination_type"], ADSET_DESTINATION_TYPE)
        self.assertEqual(payload["destination_type"], "WHATSAPP")
        self.assertEqual(payload["daily_budget"], "3000")
        self.assertEqual(payload["promoted_object"]["page_id"], "page-1")
        self.assertEqual(payload["promoted_object"]["whatsapp_phone_number"], "+55 11 99999-1234")
        self.assertFalse(payload["promoted_object"]["smart_pse_enabled"])
        self.assertEqual(payload["attribution_spec"][0], {"event_type": "CLICK_THROUGH", "window_days": 1})

    def test_brazil_radius_advantage_and_budget_contracts(self):
        self.assertEqual(brl_to_meta_cents("R$ 30,00"), 3000)
        brazil = build_brazil_targeting()
        self.assertEqual(brazil["geo_locations"]["countries"], ["BR"])
        self.assertEqual(brazil["geo_locations"]["location_types"], ["home", "recent"])
        self.assertEqual(brazil["targeting_automation"]["advantage_audience"], 1)
        radius = build_radius_targeting("-29.157582", "-51.196133", "16")
        location = radius["geo_locations"]["custom_locations"][0]
        self.assertEqual(location["country"], "BR")
        self.assertEqual(location["distance_unit"], "kilometer")
        self.assertEqual(location["radius"], 16.0)
        self.assertEqual(radius["targeting_automation"]["advantage_audience"], 1)

    async def test_marketing_filters_are_encoded_for_graph(self):
        transport = FakeTransport(FakeResponse(200, {"data": []}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        await client.list_adsets("act_111", campaign_id="campaign-1")
        filtering = transport.gets[0]["params"]["filtering"]
        self.assertEqual(json.loads(filtering)[0]["field"], "campaign.id")
        self.assertIn("ARCHIVED", json.loads(transport.gets[0]["params"]["effective_status"]))
        await client.list_ads("act_111", campaign_id="campaign-1")
        self.assertIn("ARCHIVED", json.loads(transport.gets[1]["params"]["effective_status"]))

    async def test_adset_child_read_is_available_for_post_write_reconciliation(self):
        transport = FakeTransport(FakeResponse(200, {"data": [{"id": "ad-1", "status": "PAUSED"}]}))
        client = MetaMarketingApiClient("token-value", graph_version="v26.0", transport=transport)
        ads = await client.list_ads_for_adset("adset-1")
        self.assertEqual(ads[0]["id"], "ad-1")
        self.assertTrue(transport.gets[0]["url"].endswith("/adset-1/ads"))
        self.assertNotIn("effective_status", transport.gets[0]["params"])

    async def test_graph_errors_are_sanitized(self):
        error = MetaGraphError(
            "invalid_auth",
            "Bearer secret-token failed",
            status_code=401,
            meta_code="190",
            meta_error_type="OAuthException",
            error_user_title="Erro de Meta",
            error_user_msg="Mensagem segura",
            is_transient=False,
            error_data={"blame_field_specs": [["special_ad_categories"]], "details": "Campo inválido"},
            fbtrace_id="trace-safe",
            graph_path="me/adaccounts",
        )
        safe = sanitize_graph_error(error)
        self.assertEqual(safe["code"], "invalid_auth")
        self.assertEqual(safe["meta_error_type"], "OAuthException")
        self.assertEqual(safe["error_user_title"], "Erro de Meta")
        self.assertEqual(safe["error_user_msg"], "Mensagem segura")
        self.assertEqual(safe["is_transient"], False)
        self.assertEqual(safe["blame_field_specs"], [["special_ad_categories"]])
        self.assertEqual(safe["error_details"], "Campo inválido")
        self.assertIn("trace-safe", str(safe))
        self.assertNotIn("secret-token", str(safe))

    def test_routes_and_models_keep_tenant_binding_contracts(self):
        route_source = Path("app/api/routers/traffic_meta.py").read_text(encoding="utf-8")
        model_source = Path("db/models.py").read_text(encoding="utf-8")
        baseline_source = Path("protected_baselines/baselines.json").read_text(encoding="utf-8")
        self.assertIn("require_tenant_access", route_source)
        self.assertIn("TrafficMetaConfig.empresa_id == empresa_id", route_source)
        self.assertIn("TrafficMetaConfig.ad_account_id == normalized_ad_account_id", route_source)
        self.assertIn("ad_account_validation_id", route_source)
        self.assertIn("Valide a conta de anúncios antes de salvar", route_source)
        self.assertIn("await client.validate_ad_account(normalized_ad_account_id)", route_source)
        self.assertIn("_ensure_ad_account_binding_admin", route_source)
        self.assertIn("Acesso administrativo necessário para vincular conta de anúncios", route_source)
        self.assertIn('if "business_id" in fields', route_source)
        self.assertIn('if "page_id" in fields', route_source)
        self.assertIn('"whatsapp_connection_id" in fields', route_source)
        self.assertIn("not config.whatsapp_connection_id", route_source)
        self.assertIn("Conexao.empresa_id == empresa_id", route_source)
        self.assertIn("fk_traffic_meta_config_empresa_whatsapp_connection", model_source)
        self.assertIn("fk_traffic_meta_creation_empresa_whatsapp_connection", model_source)
        self.assertIn("whatsapp_connection_id = Column(UUID", model_source)
        self.assertIn("ad_account_id = Column(String", model_source)
        self.assertIn("uq_traffic_meta_configs_ad_account_id", model_source)
        self.assertIn("uq_traffic_meta_configs_empresa_id_id", model_source)
        self.assertIn("class TrafficMetaCampaignCreation", model_source)
        self.assertIn("uq_traffic_meta_campaign_creations_idempotency", model_source)
        self.assertIn("TrafficMetaCampaignCreateRequest", route_source)
        self.assertNotIn("ad_account_id: str | None = None", route_source.split("class TrafficMetaCampaignCreateRequest", 1)[1].split("def _settings_flags", 1)[0])
        self.assertIn("MetaMarketingCapability.ADS_MANAGEMENT", route_source)
        self.assertIn("ad_account_id_for_write = _current_configured_ad_account_id(config)", route_source)
        self.assertIn("await client.validate_ad_account(ad_account_id_for_write)", route_source)
        self.assertIn("await client.create_campaign(ad_account_id_for_write, campaign_payload)", route_source)
        self.assertIn("await client.create_adset(ad_account_id_for_write, adset_payload)", route_source)
        self.assertIn("_ensure_meta_paused_read", route_source)
        self.assertIn("ad_created_pending_verification", route_source)
        self.assertIn("list_ads_for_adset", route_source)
        self.assertIn("golden_binding", route_source)
        self.assertIn("_append_write_audit", route_source)
        self.assertIn('"success": True', route_source)
        self.assertIn('"success": False', route_source)
        self.assertIn("raise HTTPException(status_code=502", route_source)
        self.assertIn("campaign_http_form", route_source)
        self.assertIn("adset_http_form", route_source)
        self.assertIn("graph_error_blame_field_specs", route_source)
        self.assertIn("graph_error_details", route_source)
        self.assertIn("_resolve_campaign_whatsapp_for_write", route_source)
        self.assertIn("Selecione qual WhatsApp Oficial será usado nesta campanha", route_source)
        self.assertIn("_validate_page_access", route_source)
        self.assertIn("_validate_whatsapp_asset_for_ads", route_source)
        self.assertIn("_validate_page_whatsapp_compatibility", route_source)
        self.assertIn("business_id=asset_snapshot.get", route_source)
        self.assertIn("instagram_account_id=asset_snapshot.get", route_source)
        self.assertIn("whatsapp_phone_number=asset_snapshot.get", route_source)
        self.assertIn("dataset_id=asset_snapshot.get", route_source)
        self.assertIn("partial_error", route_source)
        self.assertIn("creation_idempotency_key", route_source)
        self.assertIn('router.post("/campaigns/full")', route_source)
        self.assertIn("resume_creation_id", route_source)
        self.assertIn("Somente estruturas em estado parcial podem ser retomadas", route_source)
        self.assertIn("A conta configurada mudou; a estrutura parcial não pode ser retomada", route_source)
        self.assertIn("if not row.meta_campaign_id:", route_source)
        self.assertIn("if not row.meta_adset_id:", route_source)
        self.assertIn(".with_for_update()", route_source)
        self.assertIn("_is_golden_whatsapp_binding", route_source)
        self.assertIn("existing_creatives_by_name", route_source)
        self.assertIn("creative_created_pending_verification", route_source)
        self.assertNotIn("except Exception:\n            existing_ads_by_name = {}", route_source)
        self.assertIn("MEDIA_PROCESSING_PENDING", route_source)
        self.assertIn("marketing_creative_create_enabled", route_source)
        self.assertIn("marketing_ad_create_enabled", route_source)
        self.assertIn('router.post("/ad-copy/generate")', route_source)
        ad_copy_route = route_source.split('router.post("/ad-copy/generate")', 1)[1].split("def _phone_number_id_from_connection", 1)[0]
        self.assertIn("generate_ad_copy", ad_copy_route)
        self.assertIn("CAMPAIGN_OBJECTIVE", ad_copy_route)
        self.assertIn("_ensure_ad_account_binding_admin", ad_copy_route)
        self.assertNotIn("create_campaign", ad_copy_route)
        self.assertNotIn("create_adset", ad_copy_route)
        self.assertNotIn("upload_image", ad_copy_route)
        self.assertNotIn("upload_video", ad_copy_route)
        self.assertNotIn("create_creative", ad_copy_route)
        self.assertNotIn("create_ad(", ad_copy_route)
        self.assertIn("traffic_meta_config", baseline_source)
        self.assertIn("marketing_api_read", baseline_source)
        self.assertIn("admin_fallback_not_tenant_allowlist_bound", baseline_source)

    def test_frontend_requires_visual_ad_account_validation(self):
        frontend_source = Path("frontend/src/pages/tenant/TrafficMeta.jsx").read_text(encoding="utf-8")
        self.assertIn("ID da conta de anúncios Meta", frontend_source)
        self.assertIn("ID da conta de anúncios", frontend_source)
        self.assertIn("Validar conta", frontend_source)
        self.assertIn("Salvar conta de anúncios", frontend_source)
        self.assertIn("Ativos Meta da empresa", frontend_source)
        self.assertIn("normalizeAdAccountId", frontend_source)
        self.assertIn("/traffic/meta/adaccounts/${normalized}", frontend_source)
        self.assertIn("ad_account_validation_id", frontend_source)
        self.assertIn("Valide a conta de anúncios Meta antes de salvar", frontend_source)
        self.assertIn("getActiveEmpresaId", frontend_source)
        self.assertIn("Nova campanha", frontend_source)
        self.assertIn("Criar campanha e anúncios", frontend_source)
        self.assertIn("Selecionar imagens", frontend_source)
        self.assertIn("multiple", frontend_source)
        self.assertIn("/traffic/meta/ad-copy/analyze-image", frontend_source)
        self.assertIn("analysis_status", frontend_source)
        self.assertIn("Adicionar imagens", frontend_source)
        self.assertIn("fingerprintFile", frontend_source)
        self.assertIn("loadCampaignDraft", frontend_source)
        self.assertIn("resume_creation_id", frontend_source)
        self.assertIn("Progresso por imagem", frontend_source)
        self.assertIn("Texto principal", frontend_source)
        self.assertIn("Título", frontend_source)
        self.assertIn("Descrição", frontend_source)
        self.assertIn("Confirme a criação pausada", frontend_source)
        self.assertIn("Brasil inteiro", frontend_source)
        self.assertIn("Região por raio", frontend_source)
        self.assertIn("Página Meta", frontend_source)
        self.assertIn("WhatsApp da campanha", frontend_source)
        self.assertIn("Salvar ativos Meta", frontend_source)
        self.assertIn("Credencial de conversões", frontend_source)
        self.assertIn("não solicita token manual redundante", frontend_source)
        self.assertIn("compatibleWhatsapps.length === 1", frontend_source)
        self.assertIn("Selecione o WhatsApp compatível", frontend_source)
        self.assertIn("whatsapp_connection_id", frontend_source)
        self.assertIn("Advantage+ / automático", frontend_source)
        self.assertIn("/traffic/meta/campaigns/full", frontend_source)
        self.assertIn("Carregar dados reais", frontend_source)
        self.assertIn("Dados reais da campanha", frontend_source)
        self.assertIn("Conjuntos de anúncios", frontend_source)
        self.assertIn("Criativos", frontend_source)
        self.assertIn("setAdsets(response.data?.adsets || [])", frontend_source)
        self.assertIn("setAds(response.data?.ads || [])", frontend_source)
        self.assertIn("response.data?.success", frontend_source)
        self.assertIn('creation?.status === "created"', frontend_source)
        self.assertIn("setCreatedStructure(null)", frontend_source)
        self.assertIn("A campanha não foi criada", frontend_source)
        self.assertIn("Empresa não identificada no contexto atual", frontend_source)
        self.assertNotIn("Promise.all([", frontend_source)
        self.assertNotIn(">Business ID<", frontend_source)
        self.assertNotIn(">Page ID<", frontend_source)
        self.assertNotIn(">WhatsApp Connection ID<", frontend_source)
        self.assertNotIn("Salvar vínculo", frontend_source)
        self.assertNotIn("setForm({ ...form, ad_account_id: adAccounts[0]", frontend_source)
        self.assertNotIn("Configurar token", frontend_source)

    async def test_campaign_whatsapp_connection_eligibility_contract(self):
        self.assertTrue(_is_active_meta_whatsapp_connection(FakeTrafficConnection()))
        self.assertTrue(_is_active_meta_whatsapp_connection(FakeTrafficConnection(status="ativo")))
        self.assertFalse(_is_active_meta_whatsapp_connection(FakeTrafficConnection(channel="instagram")))
        self.assertFalse(_is_active_meta_whatsapp_connection(FakeTrafficConnection(provider="instagram")))
        self.assertFalse(_is_active_meta_whatsapp_connection(FakeTrafficConnection(status="disconnected")))
        self.assertFalse(_is_active_meta_whatsapp_connection(FakeTrafficConnection(tipo=TipoConexao.EVOLUTION)))

    def test_campaign_conversion_contract_is_the_campaign_builder_bridge(self):
        route_source = Path("app/api/routers/traffic_meta.py").read_text(encoding="utf-8")
        self.assertIn("def _campaign_conversion_contract", route_source)
        self.assertIn('"campaign_conversion_contract"', route_source)
        self.assertIn('"infrastructure_ready"', route_source)
        self.assertIn('"optimization_state"', route_source)
        self.assertIn('"eligible_goals"', route_source)
        self.assertIn('"accepted_signal_counts"', route_source)
        self.assertIn('"meta_probe"', route_source)
        self.assertIn("get_meta_optimization_eligibility", route_source)
        self.assertIn("conversions_status(session, empresa_id)", route_source)

    def test_campaign_request_uses_one_server_owned_strategy_not_customer_selected_goals(self):
        route_source = Path("app/api/routers/traffic_meta.py").read_text(encoding="utf-8")
        request_source = route_source.split("class TrafficMetaCampaignCreateRequest", 1)[1].split("class TrafficMetaFullCampaignCreateRequest", 1)[0]
        full_request_source = route_source.split("class TrafficMetaFullCampaignCreateRequest", 1)[1].split("class TrafficMetaAdCopyGenerateRequest", 1)[0]
        for request in (request_source, full_request_source):
            self.assertNotIn("business_goal:", request)
            self.assertNotIn("conversion_goal_key:", request)
        self.assertNotIn("_requested_business_goal(payload)", route_source)
        self.assertIn('"strategy": "agentos_ctwa_sales_v1"', route_source)
        self.assertIn('"crm_dataset_ctwa_optimization": {', route_source)
        self.assertIn('"state": "unverified"', route_source)

    def test_campaign_creation_uses_eligibility_and_blocks_without_silent_conversations_fallback(self):
        route_source = Path("app/api/routers/traffic_meta.py").read_text(encoding="utf-8")
        full_route = route_source.split('router.post("/campaigns/full")', 1)[1].split('router.post("/campaign-adsets")', 1)[0]
        parent_route = route_source.split('router.post("/campaign-adsets")', 1)[1].split('router.get("/adaccounts")', 1)[0]
        for source in (full_route, parent_route):
            self.assertIn("_campaign_conversion_readiness", source)
            self.assertIn('if not readiness.get("ready")', source)
            self.assertIn('optimization_goal=readiness["best_available_conversion_optimization"]', source)
            self.assertNotIn("optimization_goal=ADSET_OPTIMIZATION_GOAL", source)
            self.assertNotIn('optimization_goal="CONVERSATIONS"', source)

    def test_campaign_creation_validate_only_precedes_real_campaign_and_adset_writes(self):
        route_source = Path("app/api/routers/traffic_meta.py").read_text(encoding="utf-8")
        full_route = route_source.split('router.post("/campaigns/full")', 1)[1].split('router.post("/campaign-adsets")', 1)[0]
        parent_route = route_source.split('router.post("/campaign-adsets")', 1)[1].split('router.get("/adaccounts")', 1)[0]
        for source in (full_route, parent_route):
            self.assertIn("_validate_meta_write_payload", source)
            self.assertLess(source.index('object_type="campaign"'), source.index("await client.create_campaign(ad_account_id_for_write, campaign_payload)"))
            self.assertLess(source.index('object_type="adset"'), source.index("await client.create_adset("))
        self.assertIn("validate_only=True", route_source)
        self.assertIn("META_VALIDATE_ONLY_FAILED", route_source)

    async def test_page_whatsapp_compatibility_requires_meta_readback_match(self):
        class Client:
            async def get_whatsapp_phone_number(self, phone_number_id):
                return {"id": phone_number_id, "display_phone_number": "+55 11 99999-1234"}

            async def list_adsets(self, ad_account_id):
                return [
                    {
                        "id": "adset-ok",
                        "effective_status": "PAUSED",
                        "promoted_object": {
                            "page_id": "page-ok",
                            "whatsapp_phone_number": "+55 11 99999-1234",
                        },
                    }
                ]

        proof = await _validate_page_whatsapp_compatibility(Client(), "act-1", FakeTrafficConnection(), "page-ok")
        self.assertEqual(proof["source"], "meta_adset_readback")
        with self.assertRaises(HTTPException):
            await _validate_page_whatsapp_compatibility(Client(), "act-1", FakeTrafficConnection(), "page-wrong")


if __name__ == "__main__":
    unittest.main()
