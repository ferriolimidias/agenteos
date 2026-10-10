import unittest
import uuid
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch
from app.services.meta.legacy_delivery_reconciliation import delivery_state_for_identity, reconcile_legacy_delivery_links


def result(rows):
    return Obj(scalars=lambda: Obj(all=lambda: rows))


class LegacyDeliveryReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_destination_and_full_identity_scoped_query(self):
        tenant, fact, legacy = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        session = Obj(execute=AsyncMock(return_value=result([Obj(request_outcome='PROVIDER_ACCEPTED', delivery_uncertainty=False)])))
        state = await delivery_state_for_identity(session,event=Obj(id=fact,empresa_id=tenant),legacy=Obj(id=legacy,event_id='a'*64),credential=Obj(dataset_id='123',waba_id='456'))
        self.assertEqual(state,'LEGACY_ACCEPTED')
        query = session.execute.call_args.args[0]
        from sqlalchemy.dialects import postgresql
        sql=str(query.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
        for field in ('empresa_id','commercial_event_id','legacy_event_id','external_event_id_hash','dataset_id_hash','waba_id_hash','EXACT_LINKED'):
            self.assertIn(field,sql)

    async def test_unknown_and_missing_evidence_never_delivered(self):
        for rows in ([],[Obj(request_outcome='REQUEST_ATTEMPTED',delivery_uncertainty=True)], [Obj(request_outcome='DELIVERY_UNKNOWN',delivery_uncertainty=True)]):
            session=Obj(execute=AsyncMock(return_value=result(rows)))
            state=await delivery_state_for_identity(session,event=Obj(id=uuid.uuid4(),empresa_id=uuid.uuid4()),legacy=Obj(id=uuid.uuid4(),event_id='a'*64),credential=Obj(dataset_id='123',waba_id='456'))
            self.assertEqual(state,'UNKNOWN_DELIVERY')

    async def test_repair_queries_unresolved_ids_and_all_conflicting_evidence(self):
        tenant, legacy, fact, other=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        rows=[Obj(legacy_event_id=legacy,commercial_event_id=fact),Obj(legacy_event_id=legacy,commercial_event_id=other)]
        session=Obj(execute=AsyncMock(side_effect=[result([legacy]),result(rows)]))
        with patch('app.services.commercial_events.link_legacy_meta_event_exact',new_callable=AsyncMock) as link:
            self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=tenant,limit=1),0)
            link.assert_not_awaited()
        from sqlalchemy.dialects import postgresql
        queries=[str(call.args[0].compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True})) for call in session.execute.call_args_list]
        self.assertIn('NOT (EXISTS',queries[0]);self.assertIn('HAVING count(distinct',queries[0]);self.assertIn('LIMIT 1',queries[0]);self.assertNotIn('LIMIT',queries[1])

    async def test_probable_without_explicit_evidence_not_promoted(self):
        session=Obj(execute=AsyncMock(return_value=result([])))
        self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=uuid.uuid4()),0)
