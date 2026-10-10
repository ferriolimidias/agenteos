"""Synthetic P1C.4 read model against PostgreSQL; no transport can be called."""
import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.meta import legacy_delivery_evidence as evidence
from app.services.meta.sender_operations import sender_operations, sender_attempt_detail
from db.models import LegacyConversionDeliveryEvidence

URL = os.getenv('LEGACY_DELIVERY_EVIDENCE_TEST_DATABASE_URL', '')


@unittest.skipUnless(URL, 'isolated PostgreSQL required')
class SenderOperationsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(URL)
        if parsed.get_backend_name() != 'postgresql' or parsed.database != 'p1c3_outbox_test':
            raise RuntimeError('refusing test outside isolated p1c3_outbox_test')
        self.engine = create_async_engine(URL.replace('postgresql://','postgresql+asyncpg://',1))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patcher = patch.object(evidence, 'SESSION_FACTORY', self.sessions)
        self.patcher.start()
        async with self.engine.connect() as conn:
            self.a, self.b = (await conn.execute(text('SELECT id FROM empresas ORDER BY id LIMIT 2'))).scalars().all()
        self.attempts, self.facts, self.legacy = [], [], []

    async def asyncTearDown(self):
        self.patcher.stop()
        async with self.engine.begin() as conn:
            for identifier in self.attempts:
                await conn.execute(text('DELETE FROM legacy_conversion_delivery_evidence WHERE id=:id'), {'id':identifier})
            for identifier in self.legacy:
                await conn.execute(text('DELETE FROM commercial_event_meta_links WHERE meta_event_id=:id'), {'id':identifier})
            for identifier in self.facts:
                await conn.execute(text('DELETE FROM commercial_events WHERE id=:id'), {'id':identifier})
            for identifier in self.legacy:
                await conn.execute(text('DELETE FROM meta_conversion_events WHERE id=:id'), {'id':identifier})
        await self.engine.dispose()

    async def start(self, tenant=None, external=None, dataset='123', waba='456', path=None):
        identifier = await evidence.start_delivery_attempt(empresa_id=tenant or self.a,
            dataset_id=dataset,waba_id=waba,event_name='LeadSubmitted',external_event_id=external or uuid.uuid4().hex*2,
            transport_path=path or evidence.TRANSPORT_PATHS[0])
        self.assertIsNotNone(identifier)
        self.attempts.append(identifier)
        return identifier

    async def overview(self, tenant=None, **filters):
        async with self.sessions() as session:
            # PostgreSQL enforces the GET no-write promise, including indirect helpers.
            await session.execute(text('SET TRANSACTION READ ONLY'))
            return await sender_operations(session, empresa_id=tenant or self.a, **filters)

    async def detail(self, identifier, tenant=None):
        async with self.sessions() as session:
            await session.execute(text('SET TRANSACTION READ ONLY'))
            return await sender_attempt_detail(session, empresa_id=tenant or self.a, attempt_id=identifier)

    async def test_no_event_observed_is_explicit_not_provider_failure(self):
        # A fresh identity has no evidence regardless of other fixture traffic.
        result = await self.overview(tenant=uuid.uuid4())
        summary = result['summary']
        self.assertEqual(summary['observation_status'], 'NO_EVENT_OBSERVED')
        self.assertEqual(summary['delivery_validation'], 'NÃO_VALIDADO')
        self.assertEqual(summary['coverage']['paths_instrumented'], 2)
        self.assertTrue(all(row['observation_status'] == 'NO_EVENT_OBSERVED' for row in summary['coverage']['paths']))
        self.assertFalse(summary['canonical_live_enabled'])
        self.assertFalse(summary['ownership_enforced'])

    async def test_timeout_retry_shared_identity_destination_and_summary_filter_independence(self):
        external = uuid.uuid4().hex*2
        first = await self.start(external=external)
        await evidence.mark_delivery_attempted(first)
        await evidence.finish_delivery_attempt(first,error_phase='after_request')
        second = await self.start(external=external)
        await evidence.mark_delivery_attempted(second)
        await evidence.finish_delivery_attempt(second,http_status=200,provider_response={'events_received':1})
        third = await self.start(external=external,dataset='other-destination')
        foreign = await self.start(tenant=self.b,external=external)
        detail = await self.detail(first)
        self.assertEqual(detail['delivery_status'],'unknown')
        self.assertTrue(detail['delivery_uncertainty'])
        self.assertEqual(detail['shadow_diagnostics']['same_identity_attempts'],2)
        self.assertEqual(detail['shadow_diagnostics']['accepted_attempts'],1)
        self.assertEqual(detail['shadow_diagnostics']['uncertain_attempts'],1)
        self.assertFalse(detail['shadow_diagnostics']['retry_authorized'])
        self.assertTrue(detail['shadow_diagnostics']['repeated_attempts_are_not_duplicate_proof'])
        full = await self.overview()
        filtered = await self.overview(outcome='unknown',transport='business_messaging')
        self.assertEqual(full['summary']['total_attempts'],filtered['summary']['total_attempts'])
        self.assertEqual(full['summary']['outcome_counts'],filtered['summary']['outcome_counts'])
        self.assertTrue(all(row['delivery_status']=='unknown' for row in filtered['items']))
        self.assertNotIn(str(foreign),str(full))
        self.assertEqual((await self.detail(third))['shadow_diagnostics']['same_identity_attempts'],1)

    async def test_tenant_detail_and_same_time_pagination_are_deterministic(self):
        identifiers = await asyncio.gather(*(self.start() for _ in range(3)))
        same_time = datetime.now(timezone.utc)
        async with self.engine.begin() as conn:
            for identifier in identifiers:
                await conn.execute(text('UPDATE legacy_conversion_delivery_evidence SET attempt_started_at=:at WHERE id=:id'), {'at':same_time,'id':identifier})
        self.assertIsNone(await self.detail(identifiers[0],tenant=self.b))
        first = await self.overview(start_at=same_time,end_at=same_time,limit=1)
        second = await self.overview(start_at=same_time,end_at=same_time,limit=1,offset=1)
        self.assertEqual(first['total'],3)
        self.assertNotEqual(first['items'][0]['id'],second['items'][0]['id'])
        self.assertEqual(first['items'][0]['id'],str(sorted(identifiers,reverse=True)[0]))

    async def test_incomplete_capi_identity_and_polluted_persisted_response(self):
        identifier = await self.start(waba=None,path=evidence.TRANSPORT_PATHS[1])
        async with self.sessions() as session:
            row = await session.get(LegacyConversionDeliveryEvidence,identifier)
            row.provider_response = {'events_received':1,'access_token':'private-client-secret','trace_hash':'private-client-secret'}
            row.source_type = 'private-client-secret'
            await session.commit()
        detail = await self.detail(identifier)
        self.assertEqual(detail['transport'],'capi')
        self.assertEqual(detail['provider_response'],{'events_received':1})
        self.assertNotIn('private-client-secret',str(detail))
        self.assertFalse(detail['shadow_diagnostics']['identity_complete'])
        self.assertIsNone(detail['shadow_diagnostics']['same_identity_attempts'])

    async def test_probable_link_remains_probable_and_history_is_policy_protected(self):
        fact, legacy = uuid.uuid4(), uuid.uuid4()
        self.facts.append(fact); self.legacy.append(legacy)
        before = await self.overview()
        async with self.engine.begin() as conn:
            await conn.execute(text("""INSERT INTO commercial_events
                (id,empresa_id,event_name,event_category,source_type,occurred_at,observed_at,idempotency_key,is_backfilled,external_dispatch_policy)
                VALUES (:id,:tenant,'LeadSubmitted','lead','tag',now(),now(),:key,false,'historical_no_send')"""),
                {'id':fact,'tenant':self.a,'key':'a01:'+str(fact)})
            await conn.execute(text("""INSERT INTO meta_conversion_events(id,empresa_id,event_name,event_id,status,source)
                VALUES (:id,:tenant,'LeadSubmitted',:external,'pending','tag')"""), {'id':legacy,'tenant':self.a,'external':'a'*64})
            await conn.execute(text("""INSERT INTO commercial_event_meta_links
                (id,empresa_id,meta_event_id,commercial_event_id,classification,match_reason)
                VALUES (:id,:tenant,:legacy,:fact,'LINKED_PROBABLE','test-only')"""),
                {'id':uuid.uuid4(),'tenant':self.a,'legacy':legacy,'fact':fact})
        identifier = await self.start(external='a'*64)
        async with self.sessions() as session:
            row = await session.get(LegacyConversionDeliveryEvidence,identifier)
            row.legacy_event_id = legacy
            await session.commit()
        detail = await self.detail(identifier)
        self.assertEqual(detail['related']['persisted_link']['classification'],'LINKED_PROBABLE')
        self.assertEqual(detail['related']['persisted_link']['commercial_event_id'],str(fact))
        self.assertIsNone(detail['related']['commercial_event_id'])
        self.assertEqual(detail['identity_classification'],'UNLINKED')
        self.assertEqual(detail['related']['outbox'],[])
        after = await self.overview()
        self.assertEqual(after['summary']['historical']['commercial_events'],before['summary']['historical']['commercial_events']+1)
        self.assertEqual(after['summary']['historical']['protected_commercial_events'],before['summary']['historical']['protected_commercial_events']+1)
        self.assertEqual(after['summary']['correspondence']['inferred_exact_links_created'],0)

    async def test_invalid_windows_rejected_and_mixed_timezone_safe(self):
        async with self.sessions() as session:
            with self.assertRaises(ValueError):
                await sender_operations(session,empresa_id=self.a,start_at=datetime(2026,1,2),end_at=datetime(2026,1,1,tzinfo=timezone.utc))
            with self.assertRaises(ValueError):
                await sender_operations(session,empresa_id=self.a,start_at=datetime(2020,1,1),end_at=datetime(2026,1,1))
        result = await self.overview(start_at=datetime(2026,1,1),end_at=datetime(2026,1,2,tzinfo=timezone.utc))
        self.assertEqual(result['total'],0)
