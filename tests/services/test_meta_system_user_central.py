import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.meta.graph_client import MetaGraphError
from app.services.meta.marketing_credentials import (
    MetaMarketingCapability,
    MetaMarketingCredentialResolver,
    SYSTEM_USER_MARKETING_SOURCE,
)
from app.services.meta.system_user_assets import (
    discover_system_user_assets,
    read_crm_dataset_metadata,
    revalidate_asset,
    system_user_graph,
    validate_asset_in_business,
    validate_system_user_token,
)


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.headers = {}

    def json(self):
        return self._body


class FakeGraphTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, *, params=None, headers=None):
        path = url.split("/v26.0/", 1)[-1]
        self.calls.append({"path": path, "params": params or {}, "headers": headers or {}})
        value = self.responses.get(path, {"data": []})
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return Response(value[0], value[1])
        return Response(200, value)

    async def post(self, *args, **kwargs):
        raise AssertionError("System User discovery must remain read-only")


class MetaSystemUserDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_crm_dataset_read_checks_only_allowlisted_fields_and_never_posts(self):
        transport = FakeGraphTransport({"123456": {
            "id": "123456", "name": "CRM Dataset", "is_crm": True, "is_unavailable": False,
            "access_token": "configured-secret-value",
        }})
        graph = system_user_graph("x" * 40, transport=transport)
        with patch("app.services.meta.system_user_assets.system_user_graph", return_value=graph):
            result = await read_crm_dataset_metadata("x" * 40, "123456")
        self.assertEqual(result["is_crm"], True)
        self.assertEqual(result["is_unavailable"], False)
        self.assertNotIn("access_token", str(result))
        self.assertEqual(transport.calls[0]["path"], "123456")
        self.assertEqual(transport.calls[0]["params"], {"fields": "id,name,is_crm,is_unavailable"})

    async def test_crm_dataset_read_rejects_non_numeric_id_before_request(self):
        transport = FakeGraphTransport({})
        with self.assertRaises(MetaGraphError):
            await read_crm_dataset_metadata("x" * 40, "other-business/dataset")
        self.assertEqual(transport.calls, [])

    async def test_manual_instagram_validation_uses_official_business_asset_edges(self):
        transport = FakeGraphTransport({
            "business-1": {"id": "business-1", "name": "BM One"},
            "business-1/owned_instagram_assets": {"data": [{
                "id": "instagram-business-asset-1", "ig_user_id": "instagram-user-1",
                "ig_username": "ferrioli.profile",
            }]},
            "business-1/client_instagram_assets": {"data": []},
        })
        asset = await validate_asset_in_business(
            "x" * 40, "instagram_account", "instagram-user-1", "business-1", transport=transport,
        )
        self.assertEqual(asset["external_id"], "instagram-user-1")
        self.assertEqual(asset["metadata"]["username"], "ferrioli.profile")
        self.assertIn("business-1/owned_instagram_assets", [call["path"] for call in transport.calls])
        self.assertNotIn("owned_instagram_accounts", [call["path"] for call in transport.calls])

    async def test_pagination_uses_cursor_only_and_reports_truncation(self):
        from app.services.meta.system_user_assets import _list_pages, system_user_graph

        class PagingTransport(FakeGraphTransport):
            async def get(self, url, *, params=None, headers=None):
                path = url.split("/v26.0/", 1)[-1]
                self.calls.append({"path": path, "params": params or {}, "headers": headers or {}})
                if params and params.get("after") == "cursor-1":
                    return Response(200, {"data": [{"id": "two"}]})
                return Response(200, {"data": [{"id": "one"}],
                                "paging": {"next": "https://untrusted.example/elsewhere?after=cursor-1"}})

        transport = PagingTransport({})
        rows, truncated = await _list_pages(system_user_graph("x" * 40, transport=transport), "edge", "id", 2)
        self.assertEqual([row["id"] for row in rows], ["one", "two"])
        self.assertFalse(truncated)
        self.assertEqual([call["path"] for call in transport.calls], ["edge", "edge"])
        transport.calls.clear()
        rows, truncated = await _list_pages(system_user_graph("x" * 40, transport=transport), "edge", "id", 1)
        self.assertEqual([row["id"] for row in rows], ["one"])
        self.assertTrue(truncated)

    async def test_validation_reports_identity_and_only_granted_permissions(self):
        transport = FakeGraphTransport({
            "me": {"id": "system-user-1", "name": "Ferrioli System User"},
            "me/permissions": {"data": [
                {"permission": "ads_read", "status": "granted"},
                {"permission": "ads_management", "status": "declined"},
            ]},
        })
        result = await validate_system_user_token("x" * 40, transport=transport)
        self.assertEqual(result["system_user_id"], "system-user-1")
        self.assertEqual(result["granted_permissions"], ["ads_read"])
        self.assertEqual(result["permissions_status"], "verified")
        self.assertTrue(all(call["headers"].get("Authorization") == f"Bearer {'x' * 40}" for call in transport.calls))

    async def test_validation_rejects_a_token_from_a_different_configured_app(self):
        transport = FakeGraphTransport({
            "me": {"id": "system-user-1", "name": "Ferrioli System User"},
            "me/permissions": {"data": []},
            "debug_token": {"data": {"is_valid": True, "app_id": "other-app", "application": "Other App"}},
        })
        with patch("app.services.meta.system_user_assets.meta_settings", return_value=SimpleNamespace(
            marketing_graph_api_version="v26.0", app_id="expected-app", app_secret="configured-secret-value",
        )):
            with self.assertRaises(MetaGraphError) as caught:
                await validate_system_user_token("x" * 40, transport=transport)
        self.assertEqual(caught.exception.code, "token_app_mismatch")

    async def test_validation_identifies_matching_configured_app(self):
        transport = FakeGraphTransport({
            "me": {"id": "system-user-1", "name": "Ferrioli System User"},
            "me/permissions": {"data": []},
            "debug_token": {"data": {"is_valid": True, "app_id": "expected-app", "application": "Ferrioli App", "expires_at": 0}},
        })
        with patch("app.services.meta.system_user_assets.meta_settings", return_value=SimpleNamespace(
            marketing_graph_api_version="v26.0", app_id="expected-app", app_secret="configured-secret-value",
        )):
            result = await validate_system_user_token("x" * 40, transport=transport)
        self.assertEqual(result["app_id"], "expected-app")
        self.assertEqual(result["app_name"], "Ferrioli App")

    async def test_permission_enumeration_failure_keeps_token_valid_but_unknown(self):
        transport = FakeGraphTransport({
            "me": {"id": "system-user-1", "name": "Ferrioli System User"},
            "me/permissions": (403, {"error": {"code": 200, "message": "Permissions error."}}),
        })
        result = await validate_system_user_token("x" * 40, transport=transport)
        self.assertEqual(result["permissions_status"], "unknown")
        self.assertEqual(result["permissions_error_code"], "missing_permission")
        self.assertEqual(result["granted_permissions"], [])

    async def test_discovery_keeps_only_whitelisted_metadata_and_finds_assets(self):
        transport = FakeGraphTransport({
            "me/businesses": {"data": [{"id": "business-1", "name": "Ferrioli BM"}]},
            "me/adaccounts": {"data": []},
            "me/accounts": {"data": []},
            "business-1/owned_ad_accounts": {"data": [{
                "id": "act_123", "account_id": "123", "name": "Ads", "account_status": 1,
                "currency": "BRL", "business": {"id": "business-1", "name": "Ferrioli BM"},
                "access_token": "must-not-be-stored",
            }]},
            "business-1/client_ad_accounts": {"data": []},
            "business-1/owned_pages": {"data": [{
                "id": "page-1", "name": "Page", "tasks": ["ADVERTISE"],
                "access_token": "must-not-be-stored",
                "instagram_business_account": {"id": "ig-1", "username": "ferrioli"},
            }]},
            "business-1/client_pages": {"data": []},
            "business-1/owned_instagram_assets": {"data": [{
                "id": "instagram-business-asset-1", "ig_user_id": "instagram-user-1",
                "ig_username": "ferrioli.profile",
            }]},
            "business-1/client_instagram_assets": {"data": []},
            "business-1/ads_dataset": {"data": [{"id": "dataset-1", "dataset_id": "dataset-1", "name": "CRM"}]},
            "business-1/owned_pixels": {"data": [{"id": "pixel-1", "name": "Pixel"}]},
            "business-1/client_pixels": {"data": []},
        })
        result = await discover_system_user_assets("x" * 40, transport=transport)
        indexed = {(row["asset_type"], row["external_id"]): row for row in result["assets"]}
        self.assertIn(("ad_account", "123"), indexed)
        self.assertIn(("page", "page-1"), indexed)
        self.assertIn(("instagram_account", "instagram-user-1"), indexed)
        self.assertEqual(indexed[("instagram_account", "instagram-user-1")]["metadata"]["username"], "ferrioli.profile")
        self.assertIn(("dataset", "dataset-1"), indexed)
        self.assertIn(("pixel", "pixel-1"), indexed)
        self.assertNotIn("access_token", str(result))
        self.assertNotIn("must-not-be-stored", str(result))
        self.assertTrue(result["complete"])

    async def test_shared_ad_account_retains_each_business_access_relationship(self):
        shared_account = {
            "id": "act_123", "account_id": "123", "name": "Shared Ads",
            "business": {"id": "owner-business", "name": "Owner"},
        }
        transport = FakeGraphTransport({
            "me/businesses": {"data": [
                {"id": "business-1", "name": "BM One"},
                {"id": "business-2", "name": "BM Two"},
            ]},
            "me/adaccounts": {"data": []},
            "me/accounts": {"data": []},
            "business-1/client_ad_accounts": {"data": [shared_account]},
            "business-2/owned_ad_accounts": {"data": [shared_account]},
        })
        result = await discover_system_user_assets("x" * 40, transport=transport)
        asset = next(row for row in result["assets"] if row["asset_type"] == "ad_account")
        self.assertEqual({item["business_id"] for item in asset["business_access"]}, {"business-1", "business-2"})
        self.assertEqual(asset["metadata"]["owner_business_id"], "owner-business")

    async def test_manual_id_validation_requires_the_asset_under_selected_business(self):
        transport = FakeGraphTransport({
            "business-1": {"id": "business-1", "name": "BM One"},
            "business-1/owned_ad_accounts": {"data": [{"id": "act_123", "account_id": "123", "name": "Ads"}]},
            "business-1/client_ad_accounts": {"data": []},
        })
        asset = await validate_asset_in_business("x" * 40, "ad_account", "act_123", "business-1", transport=transport)
        self.assertEqual(asset["external_id"], "123")
        self.assertEqual(asset["business_access"][0]["business_id"], "business-1")

        denied = FakeGraphTransport({
            "business-1": {"id": "business-1", "name": "BM One"},
            "business-1/owned_ad_accounts": {"data": []},
            "business-1/client_ad_accounts": {"data": []},
        })
        with self.assertRaises(MetaGraphError) as caught:
            await validate_asset_in_business("x" * 40, "ad_account", "123", "business-1", transport=denied)
        self.assertEqual(caught.exception.code, "asset_not_assigned_to_business")

    async def test_ad_account_revalidation_uses_graph_object_path(self):
        transport = FakeGraphTransport({"act_123": {"id": "act_123", "account_id": "123"}})
        result = await revalidate_asset("x" * 40, "ad_account", "123", transport=transport)
        self.assertEqual(result["account_id"], "123")
        self.assertEqual(transport.calls[0]["path"], "act_123")


class MetaSystemUserResolverTests(unittest.IsolatedAsyncioTestCase):
    def _session(self, *, grant, credential, link_result):
        session = SimpleNamespace()
        # Central credential, central assignment, then resolver rechecks both
        # the assignment and credential at the use boundary.
        session.scalar = AsyncMock(side_effect=[credential, grant, credential])
        session.execute = AsyncMock(return_value=link_result)
        return session

    async def _resolve(self, *, grant, credential, link, capability):
        resolver = MetaMarketingCredentialResolver()
        config = SimpleNamespace(ad_account_id="123", business_id="business-1")
        link_result = SimpleNamespace(
            scalar_one_or_none=lambda: "asset-link" if link is not None else None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            first=lambda: (link, SimpleNamespace(
            business_id="business-1", access_state="verified",
            business_access_json=[{"business_id": "business-1"}])) if link is not None else None,
        )
        session = self._session(grant=grant, credential=credential, link_result=link_result)
        with patch.object(resolver, "_tenant_config", new=AsyncMock(return_value=config)), \
             patch.object(resolver, "_embedded_connection", new=AsyncMock(return_value=None)), \
             patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="decrypted-system-token"):
            return await resolver.resolve(session, "tenant-a", capability, ad_account_id="123", allow_admin_fallback=True)

    async def test_central_token_is_tenant_asset_scoped_and_read_only(self):
        grant = SimpleNamespace(business_id="business-1", ad_account_id="123", verified_at=True,
                                read_enabled=True, write_enabled=False)
        credential = SimpleNamespace(encrypted_access_token="encrypted", granted_permissions=["ads_read"],
                                     permissions_status="verified")
        link = SimpleNamespace(read_enabled=True, write_enabled=False)
        resolved = await self._resolve(grant=grant, credential=credential, link=link, capability=MetaMarketingCapability.ADS_READ)
        self.assertEqual(resolved.credential_source, SYSTEM_USER_MARKETING_SOURCE)
        self.assertEqual(resolved.access_token, "decrypted-system-token")
        self.assertTrue(resolved.read_only)
        self.assertNotIn("decrypted-system-token", str(resolved.sanitized_metadata()))


    async def test_business_surface_audit_uses_tenant_bound_central_credential_only(self):
        from app.services.meta.meta_tenant_provisioner import audit_business_meta_surfaces

        credential = SimpleNamespace(
            access_token="secret-not-returned", graph_version="v26.0", business_id="business-tenant-a",
            sanitized_metadata=lambda: {"credential_source": SYSTEM_USER_MARKETING_SOURCE},
        )

        class FakeClient:
            async def list_business_ads_datasets(self, business_id):
                return [{"id": f"dataset-for-{business_id}"}]

            async def list_business_offsite_signal_containers(self, business_id):
                return []

            async def list_business_event_source_groups(self, business_id):
                return []

        session = SimpleNamespace()
        with patch("app.services.meta.meta_tenant_provisioner.MetaMarketingCredentialResolver.resolve", new=AsyncMock(return_value=credential)) as resolve, \
             patch("app.services.meta.meta_tenant_provisioner.MetaMarketingApiClient", return_value=FakeClient()):
            result = await audit_business_meta_surfaces(
                session, "tenant-a", business_ids=["business-tenant-a", "business-tenant-b"],
            )
        resolve.assert_awaited_once_with(
            session, "tenant-a", MetaMarketingCapability.BUSINESS_MANAGEMENT,
            allow_admin_fallback=True,
        )
        self.assertEqual(set(result["businesses"]), {"business-tenant-a"})
        self.assertNotIn("business-tenant-b", str(result))

    async def test_ads_management_requires_explicit_tenant_write_and_meta_scope(self):
        grant = SimpleNamespace(business_id="business-1", ad_account_id="123", verified_at=True,
                                read_enabled=True, write_enabled=False)
        credential = SimpleNamespace(encrypted_access_token="encrypted", granted_permissions=["ads_management"],
                                     permissions_status="verified")
        link = SimpleNamespace(read_enabled=True, write_enabled=False)
        with self.assertRaisesRegex(ValueError, "ADMIN_MARKETING_WRITE_NOT_ENABLED"):
            await self._resolve(grant=grant, credential=credential, link=link, capability=MetaMarketingCapability.ADS_MANAGEMENT)

    async def test_ads_management_is_available_only_after_explicit_link_grant(self):
        grant = SimpleNamespace(business_id="business-1", ad_account_id="123", verified_at=True,
                                read_enabled=True, write_enabled=True)
        credential = SimpleNamespace(encrypted_access_token="encrypted", granted_permissions=["ads_management"],
                                     permissions_status="verified")
        link = SimpleNamespace(read_enabled=True, write_enabled=True)
        resolved = await self._resolve(grant=grant, credential=credential, link=link, capability=MetaMarketingCapability.ADS_MANAGEMENT)
        self.assertEqual(resolved.credential_source, SYSTEM_USER_MARKETING_SOURCE)
        self.assertFalse(resolved.read_only)

    async def test_account_without_company_asset_link_is_denied(self):
        grant = SimpleNamespace(business_id="business-1", ad_account_id="123", verified_at=True,
                                read_enabled=True, write_enabled=False)
        credential = SimpleNamespace(encrypted_access_token="encrypted", granted_permissions=["ads_read"],
                                     permissions_status="verified")
        with self.assertRaisesRegex(ValueError, "ADMIN_MARKETING_ASSET_NOT_LINKED"):
            await self._resolve(grant=grant, credential=credential, link=None, capability=MetaMarketingCapability.ADS_READ)

    async def test_central_credential_does_not_replace_unlinked_tenant_credential(self):
        resolver = MetaMarketingCredentialResolver()
        tenant_credential = SimpleNamespace(credential_source="tenant_marketing_oauth", access_token="tenant-token")
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=SimpleNamespace(id=1)),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
        )
        with patch.object(resolver, "_tenant_config", new=AsyncMock(return_value=SimpleNamespace(
            ad_account_id="123", business_id="business-1"))), \
             patch.object(resolver, "_embedded_connection", new=AsyncMock(return_value=SimpleNamespace(id="connection"))), \
             patch.object(resolver, "_legacy_marketing_connections", new=AsyncMock(return_value=[])), \
             patch.object(resolver, "_tenant_marketing_credential", return_value=tenant_credential):
            resolved = await resolver.resolve(
                session, "tenant-a", MetaMarketingCapability.ADS_READ,
                ad_account_id="123", allow_admin_fallback=True,
            )
        self.assertIs(resolved, tenant_credential)

    async def test_business_management_requires_business_asset_link_and_scope(self):
        resolver = MetaMarketingCredentialResolver()
        grant = SimpleNamespace(business_id="business-1", ad_account_id="123", verified_at=True,
                                read_enabled=True, write_enabled=False)
        credential = SimpleNamespace(encrypted_access_token="encrypted", granted_permissions=["business_management"],
                                     permissions_status="verified")
        ad_link = SimpleNamespace(read_enabled=True, write_enabled=False)
        business_link = SimpleNamespace(first=lambda: (SimpleNamespace(read_enabled=True), SimpleNamespace()))
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[credential, grant, credential]),
            execute=AsyncMock(side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: "asset-link"),
                SimpleNamespace(first=lambda: (ad_link, SimpleNamespace(
                business_id="business-1", access_state="verified",
                business_access_json=[{"business_id": "business-1"}]))),
                business_link,
            ]),
        )
        with patch.object(resolver, "_tenant_config", new=AsyncMock(return_value=SimpleNamespace(
            ad_account_id="123", business_id="business-1"))), \
             patch.object(resolver, "_embedded_connection", new=AsyncMock(return_value=None)), \
             patch("app.services.meta.marketing_credentials.decrypt_meta_secret", return_value="central-token"):
            resolved = await resolver.resolve(session, "tenant-a", MetaMarketingCapability.BUSINESS_MANAGEMENT,
                                              ad_account_id="123", allow_admin_fallback=True)
        self.assertEqual(resolved.credential_source, SYSTEM_USER_MARKETING_SOURCE)
        self.assertTrue(resolved.read_only)


class CentralPageScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_central_credential_sees_only_the_page_linked_to_company(self):
        from app.api.routers.traffic_meta import _tenant_pages

        class Client:
            async def list_business_pages(self, business_id):
                self.business_id = business_id
                return [{"id": "page-a"}, {"id": "page-b"}]

        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["page-a"]))))
        rows = await _tenant_pages(
            Client(),
            SimpleNamespace(empresa_id="tenant-a", business_id="business-1"),
            {"credential_source": SYSTEM_USER_MARKETING_SOURCE},
            session,
        )
        self.assertEqual(rows, [{"id": "page-a"}])

    async def test_central_page_scope_fails_closed_without_session(self):
        from app.api.routers.traffic_meta import _tenant_pages

        class Client:
            async def list_business_pages(self, business_id):
                return [{"id": "page-a"}]

        rows = await _tenant_pages(
            Client(), SimpleNamespace(empresa_id="tenant-a", business_id="business-1"),
            {"credential_source": SYSTEM_USER_MARKETING_SOURCE}, None,
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
