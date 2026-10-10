import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.meta.conversion_credentials import ResolvedMetaCredential
from app.services.meta.waba_dataset import (
    BusinessMessagingDatasetResolution,
    DatasetApiError,
    WabaDatasetService,
    ensure_business_messaging_dataset,
    is_generic_dataset_name,
    persist_dataset_for_tenant,
    proposed_business_messaging_dataset_name,
    verify_existing_business_messaging_dataset,
)
from db.models import Conexao, TrafficMetaConfig


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
    def json(self):
        return self._body


class ReadOnlyGraphTransport:
    def __init__(self, *, dataset_rows=None, phone_rows=None):
        self.calls = []
        self.dataset_rows = dataset_rows if dataset_rows is not None else [{"id": "dataset-1", "name": "unknown Event Data"}]
        self.phone_rows = phone_rows if phone_rows is not None else [{"id": "phone-1"}]

    async def get(self, url, *, headers, params):
        self.calls.append(("GET", url, params, headers))
        if url.endswith("/waba-1"):
            return Response(200, {"id": "waba-1", "name": "Tenant WABA", "owner_business_info": {"id": "business-1"}})
        if url.endswith("/waba-1/phone_numbers"):
            return Response(200, {"data": self.phone_rows})
        if url.endswith("/waba-1/dataset"):
            return Response(200, {"data": self.dataset_rows})
        return Response(404, {"error": {"code": 100}})


class Transport:
    def __init__(self, *, get_response=None, post_response=None):
        self.get_response = get_response
        self.post_response = post_response
        self.calls = []
    async def get(self, url, *, headers, params):
        self.calls.append(("GET", url, headers, params))
        return self.get_response
    async def post(self, url, *, headers):
        self.calls.append(("POST", url, headers, None))
        return self.post_response


class Result:
    def __init__(self, value):
        self.value = value
    def scalars(self):
        return self
    def first(self):
        return self.value
    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, *values):
        self.values = list(values)
        self.statements = []
        self.commits = 0
    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.values.pop(0))
    async def commit(self):
        self.commits += 1


class WabaDatasetServiceTests(unittest.IsolatedAsyncioTestCase):
    def credential(self):
        return ResolvedMetaCredential("system_user_test", "test-token", "tenant", "waba-123", "", "v25.0", purpose="conversions")

    async def test_post_directly_uses_waba_dataset_edge_without_get(self):
        transport = Transport(post_response=Response(200, {"id": "dataset-existing"}))
        result = await WabaDatasetService(self.credential(), transport=transport).get_or_create_dataset()
        self.assertEqual(result.dataset_id, "dataset-existing")
        method, url, headers, params = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/waba-123/dataset"))
        self.assertIsNone(params)
        self.assertNotIn("name", url)
        self.assertNotIn("description", url)
        self.assertEqual(len(transport.calls), 1)

    async def test_get_failure_is_not_a_precondition_for_direct_post(self):
        transport = Transport(get_response=Response(400, {"error": {"message": "unsupported"}}), post_response=Response(201, {"id": "dataset-new"}))
        service = WabaDatasetService(self.credential(), transport=transport)
        result = await service.get_or_create_dataset()
        self.assertEqual(result.dataset_id, "dataset-new")
        self.assertEqual([call[0] for call in transport.calls], ["POST"])

    async def test_waba_validation_is_read_only_and_uses_waba_not_phone(self):
        transport = Transport(get_response=Response(200, {"id": "waba-123", "name": "private"}))
        status = await WabaDatasetService(self.credential(), transport=transport).validate_waba()
        method, url, headers, params = transport.calls[0]
        self.assertEqual(status, 200)
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/waba-123"))
        self.assertEqual(params, {"fields": "id,name"})
        self.assertNotIn("test-token", url)
        self.assertIn("Authorization", headers)

    async def test_existing_and_new_dataset_responses_are_accepted(self):
        for status, dataset_id in ((200, "existing"), (201, "new")):
            with self.subTest(status=status):
                transport = Transport(post_response=Response(status, {"id": dataset_id}))
                self.assertEqual((await WabaDatasetService(self.credential(), transport=transport).get_or_create_dataset()).dataset_id, dataset_id)

    async def test_response_without_id_is_rejected(self):
        transport = Transport(post_response=Response(200, {}))
        with self.assertRaisesRegex(DatasetApiError, "missing dataset ID"):
            await WabaDatasetService(self.credential(), transport=transport).get_or_create_dataset()

    async def test_post_failure_is_not_retried(self):
        transport = Transport(post_response=Response(500, {"error": {"type": "OAuthException", "code": 1, "message": "temporary"}}))
        with self.assertRaises(DatasetApiError):
            await WabaDatasetService(self.credential(), transport=transport).get_or_create_dataset()
        self.assertEqual([call[0] for call in transport.calls], ["POST"])

    async def test_persist_dataset_only_updates_tenant_dataset_mapping(self):
        tenant = uuid.uuid4()
        connection = Conexao(id=uuid.uuid4(), empresa_id=tenant, waba_id="waba-123", phone_number_id="phone-123", metadata_json={"existing": "preserved"})
        config = TrafficMetaConfig(empresa_id=tenant, dataset_id="dataset-456", waba_id="waba-old", phone_number_id="phone-old")
        session = AsyncMock()
        with patch("app.services.meta.waba_dataset._official_connection_for_update", AsyncMock(return_value=connection)) as resolve, patch("app.services.meta.waba_dataset._traffic_config_for_update", AsyncMock(return_value=config)):
            await persist_dataset_for_tenant(session, tenant, expected_waba_id="waba-123", dataset_id="dataset-456", dataset_name="unknown Event Data", graph_verified_waba_id="waba-123")
        resolve.assert_awaited_once_with(session, tenant)
        session.commit.assert_awaited_once()
        self.assertEqual(connection.waba_id, "waba-123")
        self.assertEqual(connection.metadata_json, {"existing": "preserved", "dataset_id": "dataset-456", "dataset_name": "unknown Event Data"})
        self.assertEqual(config.dataset_id, "dataset-456")
        self.assertEqual(config.waba_id, "waba-123")
        self.assertEqual(config.phone_number_id, "phone-123")
        self.assertEqual(config.whatsapp_connection_id, connection.id)

    async def test_persist_dataset_requires_graph_verified_current_waba(self):
        session = AsyncMock()
        with self.assertRaisesRegex(ValueError, "dataset_graph_verification_required"):
            await persist_dataset_for_tenant(session, "tenant", expected_waba_id="waba-123", dataset_id="dataset-456")
        session.commit.assert_not_awaited()

    async def test_persist_dataset_rejects_waba_mapping_change(self):
        connection = Conexao(waba_id="another-waba", metadata_json={})
        session = AsyncMock()
        with patch("app.services.meta.waba_dataset._official_connection_for_update", AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(ValueError, "dataset_waba_mapping_changed"):
                await persist_dataset_for_tenant(session, "tenant", expected_waba_id="waba-123", dataset_id="dataset-456", graph_verified_waba_id="waba-123")
        session.commit.assert_not_awaited()

    async def test_graph_verified_mirror_dataset_recovers_current_waba_binding_read_only(self):
        tenant = uuid.uuid4()
        connection = Conexao(
            id=uuid.uuid4(), empresa_id=tenant, waba_id="waba-1", phone_number_id="phone-1",
            metadata_json={"meta_business_portfolio_id": "business-1"},
        )
        config = TrafficMetaConfig(empresa_id=tenant, dataset_id="dataset-1", waba_id="old-waba", phone_number_id="old-phone")
        session = AsyncMock()
        session.scalar.return_value = config
        transport = ReadOnlyGraphTransport()
        credential = ResolvedMetaCredential(
            "embedded_signup_tenant", "do-not-print", str(tenant), "waba-1", "", "v25.0",
            str(connection.id), "business-1", purpose="conversions",
        )
        debug = {"available": True, "data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}
        with patch("app.services.meta.business_messaging_conversions.official_whatsapp_connection", AsyncMock(return_value=connection)), \
             patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversions._debug_token", AsyncMock(return_value=debug)):
            result = await verify_existing_business_messaging_dataset(session, tenant, transport=transport)
        self.assertTrue(result["ready"])
        self.assertEqual(result["candidate_source"], "traffic_meta_mirror")
        self.assertTrue(result["mirror_waba_mismatch"])
        self.assertTrue(result["dataset_verified_by_waba_edge"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["graph_write_executed"])
        self.assertEqual([call[0] for call in transport.calls], ["GET", "GET", "GET"])
        self.assertNotIn("do-not-print", repr(result))

    async def test_graph_verified_candidate_is_the_only_value_handed_to_local_persistence(self):
        tenant = uuid.uuid4()
        connection = Conexao(
            id=uuid.uuid4(), empresa_id=tenant, waba_id="waba-1", phone_number_id="phone-1",
            metadata_json={"meta_business_portfolio_id": "business-1"},
        )
        config = TrafficMetaConfig(empresa_id=tenant, dataset_id="dataset-1", waba_id="stale-waba")
        session = AsyncMock()
        session.scalar.return_value = config
        transport = ReadOnlyGraphTransport()
        credential = ResolvedMetaCredential(
            "embedded_signup_tenant", "never-print", str(tenant), "waba-1", "", "v25.0",
            str(connection.id), "business-1", purpose="conversions",
        )
        debug = {"available": True, "data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}
        with patch("app.services.meta.business_messaging_conversions.official_whatsapp_connection", AsyncMock(return_value=connection)), \
             patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversions._debug_token", AsyncMock(return_value=debug)), \
             patch("app.services.meta.waba_dataset.persist_dataset_for_tenant", new_callable=AsyncMock) as persist:
            result = await verify_existing_business_messaging_dataset(session, tenant, transport=transport, persist=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["persisted"])
        persist.assert_awaited_once_with(
            session, tenant,
            expected_waba_id="waba-1", dataset_id="dataset-1",
            dataset_name="unknown Event Data", graph_verified_waba_id="waba-1",
        )
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    async def test_wrong_tenant_dataset_and_waba_are_never_persisted(self):
        tenant = uuid.uuid4()
        connection = Conexao(id=uuid.uuid4(), empresa_id=tenant, waba_id="waba-1", phone_number_id="phone-1", metadata_json={"meta_business_portfolio_id": "business-1"})
        config = TrafficMetaConfig(empresa_id=tenant, dataset_id="dataset-other", waba_id="old-waba")
        session = AsyncMock()
        session.scalar.return_value = config
        transport = ReadOnlyGraphTransport()
        credential = ResolvedMetaCredential("embedded_signup_tenant", "secret", str(tenant), "waba-1", "", "v25.0", str(connection.id), "business-1", purpose="conversions")
        debug = {"available": True, "data": {"is_valid": True, "scopes": ["whatsapp_business_management", "whatsapp_business_manage_events"]}}
        with patch("app.services.meta.business_messaging_conversions.official_whatsapp_connection", AsyncMock(return_value=connection)), \
             patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversions._debug_token", AsyncMock(return_value=debug)), \
             patch("app.services.meta.waba_dataset.persist_dataset_for_tenant", new_callable=AsyncMock) as persist:
            result = await verify_existing_business_messaging_dataset(session, tenant, transport=transport, persist=True)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "DATASET_NOT_VERIFIED_FOR_CURRENT_WABA")
        persist.assert_not_awaited()

        wrong_scope_credential = ResolvedMetaCredential(
            "embedded_signup_tenant", "secret", str(tenant), "another-waba", "", "v25.0",
            str(connection.id), "business-1", purpose="conversions",
        )
        with patch("app.services.meta.business_messaging_conversions.official_whatsapp_connection", AsyncMock(return_value=connection)), \
             patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=wrong_scope_credential)), \
             patch("app.services.meta.waba_dataset.persist_dataset_for_tenant", new_callable=AsyncMock) as persist:
            result = await verify_existing_business_messaging_dataset(session, tenant, transport=transport, persist=True)
        self.assertEqual(result["reason"], "CREDENTIAL_TENANT_OR_WABA_MISMATCH")
        persist.assert_not_awaited()

    async def test_invalid_credential_stops_before_asset_reads_or_persistence(self):
        tenant = uuid.uuid4()
        connection = Conexao(id=uuid.uuid4(), empresa_id=tenant, waba_id="waba-1", phone_number_id="phone-1", metadata_json={"meta_business_portfolio_id": "business-1"})
        config = TrafficMetaConfig(empresa_id=tenant, dataset_id="dataset-1", waba_id="waba-1")
        session = AsyncMock()
        session.scalar.return_value = config
        transport = ReadOnlyGraphTransport()
        credential = ResolvedMetaCredential("embedded_signup_tenant", "secret", str(tenant), "waba-1", "", "v25.0", str(connection.id), "business-1", purpose="conversions")
        debug = {"available": True, "data": {"is_valid": False, "scopes": ["whatsapp_business_management"]}}
        with patch("app.services.meta.business_messaging_conversions.official_whatsapp_connection", AsyncMock(return_value=connection)), \
             patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=credential)), \
             patch("app.services.meta.business_messaging_conversions._debug_token", AsyncMock(return_value=debug)), \
             patch("app.services.meta.waba_dataset.persist_dataset_for_tenant", new_callable=AsyncMock) as persist:
            result = await verify_existing_business_messaging_dataset(session, tenant, transport=transport, persist=True)
        self.assertEqual(result["reason"], "CREDENTIAL_INVALID_OR_UNVERIFIABLE")
        self.assertEqual(transport.calls, [])
        persist.assert_not_awaited()

    def test_token_is_not_in_service_representation(self):
        self.assertNotIn("test-token", repr(WabaDatasetService(self.credential())))

    def test_proposed_dataset_name_pattern_is_company_scoped(self):
        name = proposed_business_messaging_dataset_name(
            empresa_id="a7384cb4-98b2-4750-94bc-3b329d35156d",
            empresa_name="Leonardo Personal",
        )
        self.assertEqual(name, "AOS | LEONARDO-PERSONAL | A7384CB4 | EVENT DATA")
        self.assertLessEqual(len(name), 80)

    def test_generic_dataset_name_detection(self):
        self.assertTrue(is_generic_dataset_name("unknown Event Data"))
        self.assertTrue(is_generic_dataset_name(""))
        self.assertFalse(is_generic_dataset_name("AOS | LEONARDO-PERSONAL | A7384CB4 | EVENT DATA"))

    async def test_ensure_creates_once_when_dataset_is_missing_and_mirrors_traffic_config(self):
        empresa_id = uuid.UUID("a7384cb4-98b2-4750-94bc-3b329d35156d")
        connection = Conexao(id=uuid.uuid4(), empresa_id=empresa_id, waba_id="waba-123", metadata_json={})
        config = TrafficMetaConfig(id=uuid.uuid4(), empresa_id=empresa_id, dataset_id=None)
        session = Session(connection, config, "Leonardo Personal")
        transport = Transport(post_response=Response(200, {"id": "dataset-123"}))
        credential = ResolvedMetaCredential("tenant_conversion_token", "token", str(empresa_id), "waba-123", "", "v25.0", purpose="conversions")
        with patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock(return_value=credential)) as resolve:
            result = await ensure_business_messaging_dataset(session, empresa_id, transport=transport)
        resolve.assert_awaited_once()
        self.assertTrue(result.created)
        self.assertEqual(result.dataset_id, "dataset-123")
        self.assertEqual(connection.metadata_json["dataset_id"], "dataset-123")
        self.assertEqual(connection.metadata_json["dataset_name"], "AOS | LEONARDO-PERSONAL | A7384CB4 | EVENT DATA")
        self.assertEqual(config.dataset_id, "dataset-123")
        self.assertEqual(session.commits, 1)
        self.assertEqual([call[0] for call in transport.calls], ["POST"])

    async def test_ensure_existing_dataset_does_not_post_and_repairs_mirror_from_source(self):
        empresa_id = uuid.uuid4()
        connection = Conexao(id=uuid.uuid4(), empresa_id=empresa_id, waba_id="waba-123", metadata_json={"dataset_id": "source-dataset"})
        config = TrafficMetaConfig(id=uuid.uuid4(), empresa_id=empresa_id, dataset_id="stale-dataset")
        session = Session(connection, config, "Tenant")
        transport = Transport(post_response=Response(200, {"id": "new-dataset"}))
        with patch("app.services.meta.business_messaging_conversions.resolve_meta_conversion_credential", AsyncMock()) as resolve:
            result = await ensure_business_messaging_dataset(session, empresa_id, transport=transport)
        resolve.assert_not_awaited()
        self.assertFalse(result.created)
        self.assertEqual(result.dataset_id, "source-dataset")
        self.assertEqual(config.dataset_id, "source-dataset")
        self.assertEqual(transport.calls, [])
        self.assertEqual(session.commits, 1)

    async def test_ensure_missing_waba_fails_safely_without_post(self):
        empresa_id = uuid.uuid4()
        connection = Conexao(id=uuid.uuid4(), empresa_id=empresa_id, metadata_json={})
        session = Session(connection, None, "Tenant")
        transport = Transport(post_response=Response(200, {"id": "new-dataset"}))
        with self.assertRaisesRegex(ValueError, "official_waba_missing"):
            await ensure_business_messaging_dataset(session, empresa_id, transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertEqual(session.commits, 0)

    def test_ensure_uses_row_lock_for_idempotency(self):
        source = ensure_business_messaging_dataset.__globals__["_official_connection_for_update"].__code__.co_names
        self.assertIn("with_for_update", source)
