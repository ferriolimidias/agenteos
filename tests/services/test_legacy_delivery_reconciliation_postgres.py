"""Real PostgreSQL repair/destination tests; all provider calls remain mocked/absent."""
import os
import unittest
import uuid
from types import SimpleNamespace as Obj
from unittest.mock import patch, AsyncMock
from sqlalchemy import text, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services.meta import legacy_delivery_evidence as evidence
from app.services.meta.legacy_delivery_reconciliation import reconcile_legacy_delivery_links, delivery_state_for_identity
from db.models import CanonicalCommercialEvent, MetaConversionEvent

URL=os.getenv('LEGACY_DELIVERY_EVIDENCE_TEST_DATABASE_URL','')

@unittest.skipUnless(URL,'isolated PostgreSQL URL required')
class LegacyReconciliationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(URL).database!='p1c3_outbox_test':
            raise RuntimeError('isolated database only')
        self.engine=create_async_engine(URL.replace('postgresql://','postgresql+asyncpg://',1))
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.patcher=patch.object(evidence,'SESSION_FACTORY',self.sessions);self.patcher.start()
        async with self.engine.connect() as conn:
            self.tenant,self.lead=(await conn.execute(text('SELECT empresa_id,id FROM crm_leads LIMIT 1'))).one()
        self.facts=[];self.legacy=[];self.attempts=[]

    async def asyncTearDown(self):
        self.patcher.stop()
        async with self.engine.begin() as conn:
            for legacy in self.legacy:
                await conn.execute(text('DELETE FROM commercial_event_meta_links WHERE meta_event_id=:id'),{'id':legacy})
            for attempt in self.attempts:
                await conn.execute(text('DELETE FROM legacy_conversion_delivery_evidence WHERE id=:id'),{'id':attempt})
            for fact in self.facts:
                await conn.execute(text('DELETE FROM commercial_events WHERE id=:id'),{'id':fact})
            for legacy in self.legacy:
                await conn.execute(text('DELETE FROM meta_conversion_events WHERE id=:id'),{'id':legacy})
        await self.engine.dispose()

    async def seed(self, legacy=None):
        fact=uuid.uuid4();external=uuid.uuid4().hex*2
        async with self.engine.begin() as conn:
            await conn.execute(text("""INSERT INTO commercial_events (id,empresa_id,lead_id,event_name,event_category,source_type,source_id,occurred_at,observed_at,idempotency_key) VALUES (:id,:tenant,:lead,'LeadSubmitted','lead','p1c4_test',:source,now(),now(),:key)"""),{'id':fact,'tenant':self.tenant,'lead':self.lead,'source':str(fact),'key':'p1c4:'+str(fact)})
            if legacy is None:
                legacy=uuid.uuid4();self.legacy.append(legacy)
                await conn.execute(text("""INSERT INTO meta_conversion_events (id,empresa_id,lead_id,event_name,event_id,status,source) VALUES (:id,:tenant,:lead,'LeadSubmitted',:external,'pending','tag')"""),{'id':legacy,'tenant':self.tenant,'lead':self.lead,'external':external})
            else:
                external=(await conn.execute(text('SELECT event_id FROM meta_conversion_events WHERE id=:id'),{'id':legacy})).scalar()
        self.facts.append(fact)
        with evidence.legacy_delivery_context(evidence.LegacyDeliveryContext(empresa_id=self.tenant,legacy_event_id=legacy,commercial_event_id=fact,canonical_source_id=fact)):
            attempt=await evidence.start_delivery_attempt(empresa_id=self.tenant,dataset_id='123',waba_id='456',event_name='LeadSubmitted',external_event_id=external,transport_path=evidence.TRANSPORT_PATHS[0])
        self.assertIsNotNone(attempt);self.attempts.append(attempt)
        await evidence.mark_delivery_attempted(attempt)
        await evidence.finish_delivery_attempt(attempt,http_status=200,provider_response={'events_received':1})
        return fact,legacy

    async def test_repair_multiple_batches_and_destination_bound_acceptance(self):
        pairs=[await self.seed() for _ in range(3)]
        with patch('app.services.commercial_events._project_shadow_best_effort',new_callable=AsyncMock):
            async with self.sessions() as session:
                for _ in pairs:
                    self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=self.tenant,limit=1),1)
                    await session.commit()
                self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=self.tenant,limit=1),0)
                fact=await session.get(CanonicalCommercialEvent,pairs[0][0]);legacy=await session.get(MetaConversionEvent,pairs[0][1])
                self.assertEqual(await delivery_state_for_identity(session,event=fact,legacy=legacy,credential=Obj(dataset_id='123',waba_id='456')),'LEGACY_ACCEPTED')
                self.assertEqual(await delivery_state_for_identity(session,event=fact,legacy=legacy,credential=Obj(dataset_id='123',waba_id='other-tenant')),'UNKNOWN_DELIVERY')
                self.assertEqual(await delivery_state_for_identity(session,event=fact,legacy=legacy,credential=Obj(dataset_id='other-tenant',waba_id='456')),'UNKNOWN_DELIVERY')

    async def test_conflict_outside_candidate_limit_is_not_promoted(self):
        first=await self.seed();await self.seed(legacy=first[1]); recoverable=await self.seed()
        with patch('app.services.commercial_events._project_shadow_best_effort',new_callable=AsyncMock):
          async with self.sessions() as session:
            self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=self.tenant,limit=1),1)
            await session.commit()
            self.assertEqual(await reconcile_legacy_delivery_links(session,empresa_id=self.tenant,limit=1),0)
            count=(await session.execute(text('SELECT count(*) FROM commercial_event_meta_links WHERE meta_event_id=:id'),{'id':first[1]})).scalar()
            self.assertEqual(count,0)
