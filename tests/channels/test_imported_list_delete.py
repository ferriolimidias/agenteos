import inspect
import unittest
import uuid

from app.api.routers import campaign_central
from app.services import campaign_audience_service


class Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class Session:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return Result(self.row)


class ImportedListDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_imported_list_deletes_only_tenant_scoped_batch(self):
        empresa_id = uuid.uuid4()
        import_id = uuid.uuid4()
        session = Session((import_id,))
        self.assertTrue(await campaign_audience_service.delete_import_batch(session, empresa_id=empresa_id, import_id=import_id))
        sql, params = session.calls[0]
        self.assertIn("delete from contact_import_batches", sql.lower())
        self.assertIn("id = :import_id and empresa_id = :empresa_id", sql.lower())
        self.assertEqual(params, {"import_id": import_id, "empresa_id": empresa_id})

    async def test_delete_imported_list_not_found_is_safe(self):
        self.assertFalse(await campaign_audience_service.delete_import_batch(Session(None), empresa_id=uuid.uuid4(), import_id=uuid.uuid4()))

    def test_delete_contract_preserves_related_data_and_existing_tags(self):
        source = inspect.getsource(campaign_audience_service.delete_import_batch)
        self.assertNotIn("crm_leads", source)
        self.assertNotIn("channel_conversations", source)
        self.assertNotIn("mensagens_historico", source)
        self.assertNotIn("communication_consents", source)
        self.assertNotIn("tags_crm", source)
        self.assertIn("empresa_id", source)
        router_source = inspect.getsource(campaign_central.delete_import_batch_endpoint)
        self.assertIn("Lista importada não encontrada", router_source)

    def test_delete_route_is_tenant_scoped_and_commits_only_after_match(self):
        source = inspect.getsource(campaign_central.delete_import_batch_endpoint)
        self.assertIn("empresa_uuid = _uuid_or_400", source)
        self.assertIn("import_uuid = _uuid_or_400", source)
        self.assertIn("await db.commit()", source)
        self.assertIn("delete_import_batch", source)

    def test_import_delete_does_not_change_snapshot_or_reimport_contract(self):
        source = inspect.getsource(campaign_audience_service.delete_import_batch)
        self.assertNotIn("campaign_preparation_snapshots", source)
        import_source = inspect.getsource(campaign_audience_service.import_contacts_csv)
        self.assertIn("idempotency_key", import_source)
        self.assertIn("on conflict (empresa_id, idempotency_key) do nothing", import_source)


if __name__ == "__main__":
    unittest.main()
