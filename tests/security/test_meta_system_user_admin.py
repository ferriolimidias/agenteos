import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.api.routers.auth import require_super_admin
from app.api.routers.meta_system_user import (
    _credential_status,
    _permission_capabilities,
    _request_token,
    router,
    SystemUserTokenRequest,
    save_system_user_credential,
    validate_company_crm_dataset_readiness,
)


class MetaSystemUserAdminSecurityTests(unittest.TestCase):
    def test_all_routes_require_super_admin(self):
        self.assertGreater(len(router.routes), 0)
        for route in router.routes:
            self.assertTrue(
                any(dependency.call is require_super_admin for dependency in route.dependant.dependencies),
                msg=f"Super Admin dependency missing from {route.path}",
            )

    def test_invalid_token_shape_error_does_not_echo_secret(self):
        secret = "private-token"
        with self.assertRaises(Exception) as caught:
            _request_token(SystemUserTokenRequest(token=secret))
        self.assertNotIn(secret, str(caught.exception))

    def test_status_does_not_return_encrypted_or_plaintext_credential(self):
        row = type("Credential", (), {
            "validated_at": None,
            "encrypted_access_token": "configured-secret-value",
            "token_hint": "***1234",
            "system_user_id": "sys-user-1",
            "system_user_name": "Ferrioli System User",
            "app_id": None,
            "app_name": None,
            "granted_permissions": ["ads_read"],
            "permissions_status": "verified",
            "expires_at": None,
            "expiry_status": "unknown",
            "permissions_error_code": None,
        })()
        with patch("app.api.routers.meta_system_user._encryption_available", return_value=True):
            response = _credential_status(row)
        self.assertEqual(response["token_masked"], "***1234")
        self.assertNotIn("encrypted_access_token", response)
        self.assertNotIn("ciphertext-that-must-not-be-returned", str(response))

    def test_capability_scope_is_unknown_when_permission_query_failed(self):
        capabilities = _permission_capabilities(["ads_management"], status="unknown")
        self.assertEqual(set(capabilities.values()), {"unknown"})

    def test_ad_management_also_allows_ads_read_but_not_page_read(self):
        capabilities = _permission_capabilities(["ads_management"], status="verified")
        self.assertEqual(capabilities["ads_read"], "granted")
        self.assertEqual(capabilities["ads_management"], "granted")
        self.assertEqual(capabilities["pages_read_engagement"], "missing")

    def test_pydantic_validation_hides_credential_input(self):
        secret = "very-private-meta-token-value-12345"
        with self.assertRaises(Exception) as caught:
            SystemUserTokenRequest(token=[secret])
        self.assertNotIn(secret, str(caught.exception))


class MetaSystemUserCredentialSaveTests(unittest.IsolatedAsyncioTestCase):
    SAME_TOKEN = "synthetic-system-user-token-" + ("s" * 40)
    REPLACEMENT_TOKEN = "synthetic-system-user-token-" + ("r" * 40)

    def _verified(self):
        return {
            "system_user_id": "system-user-1", "system_user_name": "System User",
            "app_id": "app-1", "app_name": "AgentOS App", "granted_permissions": ["ads_read"],
            "permissions_status": "verified", "permissions_error_code": None,
            "validated_at": datetime.now(timezone.utc), "expires_at": None,
            "expiry_status": "unknown",
        }

    async def test_resaving_same_credential_keeps_all_asset_links_valid(self):
        previous = SimpleNamespace(
            id=1, encrypted_access_token="old-cipher", token_hint="***1234",
            granted_permissions=[], permissions_status="verified", validated_at=datetime.now(timezone.utc),
        )
        db = SimpleNamespace(
            scalar=AsyncMock(return_value=previous), execute=AsyncMock(), commit=AsyncMock(),
            add=unittest.mock.Mock(),
        )
        actor = SimpleNamespace(id=None)
        with patch("app.api.routers.meta_system_user._encryption_available", return_value=True), \
             patch("app.api.routers.meta_system_user.validate_system_user_token", new=AsyncMock(return_value=self._verified())), \
             patch("app.api.routers.meta_system_user.encrypt_meta_secret", return_value="new-cipher"), \
            patch("app.api.routers.meta_system_user.decrypt_meta_secret", return_value=self.SAME_TOKEN):
            result = await save_system_user_credential(SystemUserTokenRequest(token=self.SAME_TOKEN), db, actor)
        self.assertFalse(result["existing_asset_links_invalidated"])
        db.execute.assert_not_awaited()
        db.commit.assert_awaited_once()
        self.assertNotIn(self.SAME_TOKEN, str(result))

    async def test_new_credential_invalidates_old_asset_links_only_after_validation(self):
        previous = SimpleNamespace(
            id=1, encrypted_access_token="old-cipher", token_hint="***1234",
            granted_permissions=[], permissions_status="verified", validated_at=datetime.now(timezone.utc),
        )
        db = SimpleNamespace(
            scalar=AsyncMock(return_value=previous), execute=AsyncMock(), commit=AsyncMock(),
            add=unittest.mock.Mock(),
        )
        actor = SimpleNamespace(id=None)
        with patch("app.api.routers.meta_system_user._encryption_available", return_value=True), \
             patch("app.api.routers.meta_system_user.validate_system_user_token", new=AsyncMock(return_value=self._verified())), \
             patch("app.api.routers.meta_system_user.encrypt_meta_secret", return_value="new-cipher"), \
            patch("app.api.routers.meta_system_user.decrypt_meta_secret", return_value="previous-system-user-token-" + ("p" * 40)):
            result = await save_system_user_credential(SystemUserTokenRequest(token=self.REPLACEMENT_TOKEN), db, actor)
        self.assertTrue(result["existing_asset_links_invalidated"])
        self.assertEqual(db.execute.await_count, 3)
        db.commit.assert_awaited_once()

    async def test_invalid_replacement_does_not_write_or_destroy_previous_value(self):
        previous = SimpleNamespace(id=1, encrypted_access_token="old-cipher")
        db = SimpleNamespace(
            scalar=AsyncMock(return_value=previous), execute=AsyncMock(), commit=AsyncMock(),
            add=unittest.mock.Mock(),
        )
        actor = SimpleNamespace(id=None)
        with patch("app.api.routers.meta_system_user._encryption_available", return_value=True), \
             patch("app.api.routers.meta_system_user.validate_system_user_token", new=AsyncMock(side_effect=ValueError("invalid"))), \
             patch("app.api.routers.meta_system_user.encrypt_meta_secret") as encrypt:
            with self.assertRaises(HTTPException):
                await save_system_user_credential(SystemUserTokenRequest(token=self.REPLACEMENT_TOKEN), db, actor)
        db.scalar.assert_not_awaited()
        encrypt.assert_not_called()
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()
        self.assertEqual(previous.encrypted_access_token, "old-cipher")


class CrmDatasetReadinessTests(unittest.IsolatedAsyncioTestCase):
    COMPANY_ID = "11111111-1111-4111-8111-111111111111"
    OTHER_COMPANY_ID = "22222222-2222-4222-8222-222222222222"

    def _assets(self):
        business = SimpleNamespace(
            asset_type="business", external_id="business-1", access_state="verified",
            business_access_json=[], metadata_json={}, name="Business one",
        )
        dataset = SimpleNamespace(
            asset_type="dataset", external_id="123456", access_state="verified",
            business_access_json=[{"business_id": "business-1"}], metadata_json={}, name="CRM",
            last_verified_at=None,
        )
        return [
            (dataset, SimpleNamespace(read_enabled=True)),
            (business, SimpleNamespace(read_enabled=True)),
        ], dataset

    async def test_validates_only_the_tenant_linked_dataset_and_keeps_capi_unknown(self):
        rows, dataset = self._assets()
        db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(id=self.COMPANY_ID)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
            commit=AsyncMock(), add=unittest.mock.Mock(),
        )
        credential = SimpleNamespace(
            validated_at=datetime.now(timezone.utc), permissions_status="unknown",
            granted_permissions=[], encrypted_access_token="ciphertext",
        )
        actor = SimpleNamespace(id=None)
        with patch("app.api.routers.meta_system_user._stored_credential", new=AsyncMock(return_value=credential)), \
             patch("app.api.routers.meta_system_user._decrypt", return_value="synthetic-central-token"), \
             patch("app.api.routers.meta_system_user.read_crm_dataset_metadata", new=AsyncMock(return_value={
                 "id": "123456", "name": "CRM Dataset", "is_crm": True, "is_unavailable": False,
             })) as read_dataset, \
             patch("app.api.routers.meta_system_user._audit", new=AsyncMock()):
            result = await validate_company_crm_dataset_readiness(self.COMPANY_ID, db, actor)
        read_dataset.assert_awaited_once_with("synthetic-central-token", "123456")
        self.assertEqual(result["crm_dataset_state"], "observed")
        self.assertEqual(result["asset_read_state"], "observed")
        self.assertEqual(result["capi_delivery_state"], "unknown")
        self.assertFalse(result["live_sender_enabled"])
        self.assertFalse(result["external_write_performed"])
        self.assertEqual(result["system_user_permissions_state"], "unknown")
        self.assertEqual(dataset.metadata_json["is_crm"], True)
        db.commit.assert_awaited_once()

    async def test_cross_tenant_or_unlinked_dataset_cannot_be_probed(self):
        rows, _ = self._assets()
        # The query is tenant-scoped; an asset linked only to another company
        # yields no rows for this company.
        db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(id=self.COMPANY_ID)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
            commit=AsyncMock(), add=unittest.mock.Mock(),
        )
        with patch("app.api.routers.meta_system_user._stored_credential", new=AsyncMock()) as credential_lookup, \
             patch("app.api.routers.meta_system_user.read_crm_dataset_metadata", new=AsyncMock()) as read_dataset:
            with self.assertRaises(HTTPException) as caught:
                await validate_company_crm_dataset_readiness(self.COMPANY_ID, db, SimpleNamespace(id=None))
        self.assertEqual(caught.exception.status_code, 409)
        credential_lookup.assert_not_awaited()
        read_dataset.assert_not_awaited()

    async def test_non_crm_dataset_is_not_reported_as_capi_ready(self):
        rows, _ = self._assets()
        db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(id=self.COMPANY_ID)),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
            commit=AsyncMock(), add=unittest.mock.Mock(),
        )
        credential = SimpleNamespace(
            validated_at=datetime.now(timezone.utc), permissions_status="verified",
            granted_permissions=["ads_read"], encrypted_access_token="ciphertext",
        )
        with patch("app.api.routers.meta_system_user._stored_credential", new=AsyncMock(return_value=credential)), \
             patch("app.api.routers.meta_system_user._decrypt", return_value="synthetic-central-token"), \
             patch("app.api.routers.meta_system_user.read_crm_dataset_metadata", new=AsyncMock(return_value={
                 "id": "123456", "name": "Other Dataset", "is_crm": False, "is_unavailable": False,
             })), \
             patch("app.api.routers.meta_system_user._audit", new=AsyncMock()):
            result = await validate_company_crm_dataset_readiness(self.COMPANY_ID, db, SimpleNamespace(id=None))
        self.assertEqual(result["crm_dataset_state"], "not_applicable")
        self.assertEqual(result["capi_delivery_state"], "unknown")
        self.assertFalse(result["live_sender_enabled"])


if __name__ == "__main__":
    unittest.main()
