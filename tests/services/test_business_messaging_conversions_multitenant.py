import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.services.meta import business_messaging_conversions as svc
from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.credential_crypto import decrypt_meta_secret
from db.models import Conexao


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class Transport:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [])
        self.calls = []

    async def get(self, url, *, headers, params=None):
        self.calls.append(("GET", url, headers, params))
        status, body = self.statuses.pop(0)
        return Response(status, body)

    async def post(self, url, *, headers):
        self.calls.append(("POST", url, headers, None))
        return Response(200, {"id": "dataset-created"})


class Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def connection_for(empresa_id, *, token=None, waba="waba-a", dataset="dataset-a"):
    encrypted = svc.encrypt_meta_secret(token) if token else None
    return Conexao(
        id=uuid.uuid4(),
        empresa_id=empresa_id,
        waba_id=waba,
        metadata_json={"dataset_id": dataset} if dataset else {},
        credenciais={svc.CONVERSION_TOKEN_CREDENTIAL_KEY: encrypted} if encrypted else {},
    )


class BusinessMessagingConversionsMultitenantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"META_CREDENTIAL_ENCRYPTION_KEY": "x" * 40, "META_APP_ID": "app", "META_APP_SECRET": "secret"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_per_tenant_tokens_do_not_cross(self):
        empresa_a, empresa_b = uuid.uuid4(), uuid.uuid4()
        conn_a = connection_for(empresa_a, token="token-a", waba="waba-a", dataset="dataset-a")
        conn_b = connection_for(empresa_b, token="token-b", waba="waba-b", dataset="dataset-b")

        async def official(_session, empresa_id, **_kwargs):
            return conn_a if empresa_id == empresa_a else conn_b

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(side_effect=official)):
            cred_a = await svc.resolve_meta_conversion_credential(object(), empresa_a)
            cred_b = await svc.resolve_meta_conversion_credential(object(), empresa_b)

        self.assertEqual(cred_a.waba_id, "waba-a")
        self.assertEqual(cred_a.dataset_id, "dataset-a")
        self.assertEqual(cred_b.waba_id, "waba-b")
        self.assertEqual(cred_b.dataset_id, "dataset-b")
        self.assertNotEqual(cred_a.access_token, cred_b.access_token)

    async def test_save_valid_token_encrypts_and_never_returns_token(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, waba="waba-a", dataset="")
        session = Session()
        transport = Transport([
            (200, {"data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}),
            (200, {"id": "waba-a"}),
        ])

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(return_value=conn)):
            result = await svc.save_conversion_token(session, empresa_id, "secret-token", transport=transport)

        self.assertTrue(result.valid)
        self.assertEqual(session.commits, 1)
        encrypted = conn.credenciais[svc.CONVERSION_TOKEN_CREDENTIAL_KEY]
        self.assertNotIn("secret-token", encrypted)
        self.assertEqual(decrypt_meta_secret(encrypted), "secret-token")
        self.assertNotIn("secret-token", str(result.sanitized()))
        self.assertNotIn("secret-token", str(conn.metadata_json))

    async def test_invalid_token_is_not_saved_as_ready(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, waba="waba-a", dataset="")
        session = Session()
        transport = Transport([
            (200, {"data": {"is_valid": True, "scopes": ["whatsapp_business_management"]}}),
            (200, {"id": "waba-a"}),
        ])

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(return_value=conn)):
            result = await svc.save_conversion_token(session, empresa_id, "secret-token", transport=transport)

        self.assertFalse(result.valid)
        self.assertFalse(result.capability_ready)
        self.assertEqual(session.commits, 0)
        self.assertNotIn(svc.CONVERSION_TOKEN_CREDENTIAL_KEY, conn.credenciais)

    async def test_dataset_validation_uses_waba_dataset_edge_not_direct_dataset_get(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, waba="waba-a", dataset="dataset-a")
        transport = Transport([
            (200, {"data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}),
            (200, {"id": "waba-a"}),
            (200, {"data": [{"id": "dataset-a", "name": "unknown Event Data"}]}),
        ])

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(return_value=conn)):
            result = await svc.validate_conversion_token(object(), empresa_id, "secret-token", transport=transport)

        self.assertTrue(result.valid)
        self.assertTrue(result.dataset_valid)
        called_urls = [call[1] for call in transport.calls]
        self.assertTrue(any(url.endswith("/waba-a/dataset") for url in called_urls))
        self.assertFalse(any(url.endswith("/dataset-a") for url in called_urls))

    async def test_dataset_validation_rejects_waba_edge_without_saved_dataset(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, waba="waba-a", dataset="dataset-a")
        transport = Transport([
            (200, {"data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}),
            (200, {"id": "waba-a"}),
            (200, {"data": [{"id": "another-dataset", "name": "unknown Event Data"}]}),
        ])

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(return_value=conn)):
            result = await svc.validate_conversion_token(object(), empresa_id, "secret-token", transport=transport)

        self.assertTrue(result.valid)
        self.assertFalse(result.dataset_valid)

    async def test_missing_dataset_blocks_event_credential_but_setup_can_resolve_without_dataset(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, token="token-a", waba="waba-a", dataset="")
        with patch.object(svc, "official_whatsapp_connection", AsyncMock(return_value=conn)):
            with self.assertRaisesRegex(MetaCredentialResolutionError, "BLOCKED_DATASET_NOT_LINKED"):
                await svc.resolve_meta_conversion_credential(object(), empresa_id)
            credential = await svc.resolve_meta_conversion_credential(object(), empresa_id, require_dataset=False)
        self.assertEqual(credential.waba_id, "waba-a")
        self.assertEqual(credential.dataset_id, "")

    async def test_setup_orchestrator_reports_only_public_automation_steps(self):
        empresa_id = uuid.uuid4()
        with patch.object(svc, "ensure_business_messaging_dataset_for_conversions", AsyncMock(return_value={"already_configured": True, "created": False, "dataset_id_masked": "***8529"})) as ensure:
            result = await svc.setup_business_messaging_conversions(object(), empresa_id)
        ensure.assert_awaited_once()
        self.assertTrue(result["dataset_creation_automatic"])
        self.assertTrue(result["dataset_waba_binding_automatic"])
        self.assertFalse(result["public_messaging_source_binding_api_found"])
        self.assertFalse(result["messaging_source_binding_automatic"])
        self.assertFalse(result["facebook_page_binding_required"])
        self.assertFalse(result["manual_action_required"])

    async def test_setup_existing_dataset_validates_through_waba_dataset_edge(self):
        empresa_id = uuid.uuid4()
        conn = connection_for(empresa_id, token="token-a", waba="waba-a", dataset="dataset-a")
        session = Session()
        transport = Transport([
            (200, {"data": [{"id": "dataset-a", "name": "unknown Event Data"}]}),
        ])

        async def official(_session, _empresa_id, **_kwargs):
            return conn

        with patch.object(svc, "official_whatsapp_connection", AsyncMock(side_effect=official)), \
             patch.object(svc, "_official_connection_for_update", AsyncMock(return_value=conn)), \
             patch.object(svc, "_traffic_config_for_update", AsyncMock(return_value=None)), \
             patch.object(svc, "_empresa_name", AsyncMock(return_value="Tenant")):
            result = await svc.ensure_business_messaging_dataset_for_conversions(session, empresa_id, transport=transport)

        self.assertTrue(result["already_configured"])
        self.assertFalse(result["created"])
        self.assertEqual(session.commits, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(transport.calls[0][1].endswith("/waba-a/dataset"))

    async def test_debug_token_uses_app_access_token_without_empty_bearer_header(self):
        class DebugTransport:
            def __init__(self):
                self.headers = None
                self.params = None

            async def get(self, url, *, headers, params=None):
                self.headers = headers
                self.params = params
                return Response(200, {"data": {"is_valid": True, "scopes": []}})

        transport = DebugTransport()
        result = await svc._debug_token("tenant-token", transport=transport)
        self.assertTrue(result["available"])
        self.assertEqual(transport.headers, {})
        self.assertEqual(transport.params["input_token"], "tenant-token")
        self.assertIn("|", transport.params["access_token"])

    def test_public_messaging_source_binding_contract_is_not_private_endpoint(self):
        self.assertFalse(svc.PUBLIC_MESSAGING_SOURCE_BINDING_API_FOUND)
        self.assertIsNone(svc.PUBLIC_MESSAGING_SOURCE_BINDING_ENDPOINT)
        self.assertFalse(svc.FACEBOOK_PAGE_BINDING_REQUIRED_FOR_WHATSAPP_CAPI)

    def test_permanent_architecture_rule_documented(self):
        with open("docs/meta/multitenant_external_integrations.md", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("tenant-scoped por `empresa_id`", text)
        self.assertIn("não pode depender de IDs, tokens, nomes ou regras específicas de clientes", text)
