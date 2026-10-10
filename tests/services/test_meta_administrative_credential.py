import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.services.meta.conversion_credentials import (
    CONVERSIONS_PURPOSE, INSTAGRAM_PURPOSE, MetaCredentialResolutionError,
    MetaCredentialResolver, SYSTEM_USER_TEST, TENANT_CONNECTION, system_user_status,
)


class Connection:
    id = uuid.uuid4()
    waba_id = "waba-1"
    page_id = "page-1"
    instagram_business_account_id = "ig-1"
    metadata_json = {"dataset_id": "dataset-1"}
    credenciais = {}


class MetaAdministrativeCredentialTests(unittest.IsolatedAsyncioTestCase):
    def _env(self, tenant, **values):
        base = {"META_SYSTEM_USER_TEST_ENABLED": "true", "META_SYSTEM_USER_TEST_TENANT_IDS": tenant, "META_SYSTEM_USER_ACCESS_TOKEN": "central-test-token", "META_BUSINESS_MESSAGING_TEST_ENABLED": "false"}
        base.update(values)
        return patch.dict(os.environ, base, clear=False)

    async def test_same_system_source_supports_instagram_and_conversions(self):
        tenant = str(uuid.uuid4()); resolver = MetaCredentialResolver()
        with self._env(tenant), patch.object(resolver, "_connection", AsyncMock(return_value=Connection())):
            instagram = await resolver.resolve(object(), tenant, INSTAGRAM_PURPOSE, SYSTEM_USER_TEST)
            conversions = await resolver.resolve(object(), tenant, CONVERSIONS_PURPOSE, SYSTEM_USER_TEST)
        self.assertEqual(instagram.credential_source, conversions.credential_source)
        self.assertEqual(instagram.access_token, conversions.access_token)
        self.assertNotIn("central-test-token", str(instagram.sanitized_metadata()))

    async def test_system_source_has_no_fallback(self):
        tenant = str(uuid.uuid4()); resolver = MetaCredentialResolver()
        with self._env(tenant, META_SYSTEM_USER_TEST_ENABLED="false"):
            with self.assertRaisesRegex(MetaCredentialResolutionError, "CREDENTIAL_NOT_CONFIGURED"):
                await resolver.resolve(object(), tenant, INSTAGRAM_PURPOSE, SYSTEM_USER_TEST)

    async def test_manual_allowlist_is_deprecated_and_missing_token_is_sanitized(self):
        resolver = MetaCredentialResolver()
        with self._env("different"), patch.object(resolver, "_connection", AsyncMock(return_value=Connection())):
            credential = await resolver.resolve(object(), "tenant", INSTAGRAM_PURPOSE, SYSTEM_USER_TEST)
        self.assertEqual(credential.credential_source, SYSTEM_USER_TEST)
        with self._env("tenant", META_SYSTEM_USER_ACCESS_TOKEN=""), patch.object(resolver, "_connection", AsyncMock(return_value=Connection())):
            with self.assertRaisesRegex(MetaCredentialResolutionError, "CREDENTIAL_NOT_CONFIGURED"):
                await resolver.resolve(object(), "tenant", INSTAGRAM_PURPOSE, SYSTEM_USER_TEST)

    def test_public_status_never_returns_token(self):
        with self._env("tenant"):
            status = system_user_status("tenant")
        self.assertTrue(status["credential_configured"])
        self.assertTrue(status["tenant_allowlist_deprecated"])
        self.assertNotIn("central-test-token", str(status))

    async def test_tenant_connection_is_distinct(self):
        resolver = MetaCredentialResolver(); connection = Connection()
        connection.credenciais = {"encrypted_access_token": "bad"}
        with patch.object(resolver, "_connection", AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(MetaCredentialResolutionError, "CREDENTIAL_NOT_CONFIGURED"):
                await resolver.resolve(object(), "tenant", INSTAGRAM_PURPOSE, TENANT_CONNECTION)

    async def test_system_user_can_be_resolved_before_selecting_page(self):
        tenant = str(uuid.uuid4())
        resolver = MetaCredentialResolver()

        class ConnectionWithoutPage:
            id = uuid.uuid4()
            waba_id = ""
            page_id = None
            instagram_business_account_id = None
            metadata_json = {}
            credenciais = {}

        with self._env(tenant), patch.object(resolver, "_connection", AsyncMock(return_value=ConnectionWithoutPage())):
            credential = await resolver.resolve(object(), tenant, INSTAGRAM_PURPOSE, SYSTEM_USER_TEST)

        self.assertEqual(credential.credential_source, SYSTEM_USER_TEST)
        self.assertIsNone(credential.page_id)
        self.assertIsNone(credential.instagram_account_id)
