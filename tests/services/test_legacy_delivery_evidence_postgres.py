"""Real PostgreSQL evidence/identity tests; every external provider is mocked."""
import asyncio
import os
import unittest
import uuid
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import httpx

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.services.meta import legacy_delivery_evidence as evidence
from db.models import LegacyConversionDeliveryEvidence, Conexao, TipoConexao

URL = os.getenv("LEGACY_DELIVERY_EVIDENCE_TEST_DATABASE_URL", "")

@unittest.skipUnless(URL, "isolated PostgreSQL URL required")
class LegacyDeliveryEvidencePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(URL)
        if parsed.get_backend_name() != "postgresql" or parsed.database != "p1c3_outbox_test":
            raise RuntimeError("refusing test outside isolated p1c3_outbox_test")
        self.engine = create_async_engine(URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.factory_patch = patch.object(evidence, "SESSION_FACTORY", self.sessions)
        self.factory_patch.start()
        async with self.engine.connect() as conn:
            self.tenant_a, self.tenant_b = (await conn.execute(text("SELECT id FROM empresas ORDER BY id LIMIT 2"))).scalars().all()
        self.facts, self.legacy, self.attempts = [], [], []
        self.connection_ids = [uuid.uuid4(),uuid.uuid4()]
        async with self.engine.begin() as connection:
            # The isolated P1A fixture has a skeletal connection table. Extend only this test DB.
            for definition in ("tipo TEXT", "provider TEXT", "waba_id TEXT", "metadata_json JSONB"):
                await connection.execute(text("ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS " + definition))
            for tenant,identifier,dataset,waba in ((self.tenant_a,self.connection_ids[0],"123","456"),(self.tenant_b,self.connection_ids[1],"987","654")):
                await connection.execute(text("""INSERT INTO conexoes
                    (id,empresa_id,tipo,provider,waba_id,metadata_json)
                    VALUES (:id,:tenant,'META','meta_official',:waba,jsonb_build_object('dataset_id',CAST(:dataset AS text)))"""),
                    {"id":identifier,"tenant":tenant,"waba":waba,"dataset":dataset})

    async def asyncTearDown(self):
        self.factory_patch.stop()
        async with self.engine.begin() as conn:
            for item in self.attempts:
                await conn.execute(text("DELETE FROM legacy_conversion_delivery_evidence WHERE id=:id"), {"id": item})
            for item in self.facts:
                await conn.execute(text("DELETE FROM conversion_delivery_identities WHERE commercial_event_id=:id"), {"id": item})
                await conn.execute(text("DELETE FROM commercial_events WHERE id=:id"), {"id": item})
            for item in self.legacy:
                await conn.execute(text("DELETE FROM meta_conversion_events WHERE id=:id"), {"id": item})
            for identifier in self.connection_ids:
                await conn.execute(text("DELETE FROM conexoes WHERE id=:id"),{"id":identifier})
        await self.engine.dispose()

    async def fact(self, tenant=None):
        item = uuid.uuid4()
        async with self.engine.begin() as conn:
            await conn.execute(text("""INSERT INTO commercial_events
             (id,empresa_id,event_name,event_category,source_type,source_id,occurred_at,observed_at,idempotency_key)
             VALUES (:id,:tenant,'LeadSubmitted','lead','p1c4_test',:source,now(),now(),:key)"""),
             {"id": item, "tenant": tenant or self.tenant_a, "source": str(item), "key": "p1c4-test:"+str(item)})
        self.facts.append(item)
        return item

    async def start(self, **extras):
        item = await evidence.start_delivery_attempt(empresa_id=self.tenant_a,dataset_id="123",waba_id="456",
            event_name="LeadSubmitted",external_event_id="a"*64,transport_path=evidence.TRANSPORT_PATHS[0],**extras)
        self.assertIsNotNone(item)
        self.attempts.append(item)
        return item

    async def read(self, item):
        async with self.sessions() as session:
            return await session.get(LegacyConversionDeliveryEvidence,item)

    async def test_prepared_and_attempted_committed_before_mock_provider(self):
        item = await self.start()
        self.assertEqual((await self.read(item)).request_outcome,"LOCAL_PREPARED")
        self.assertTrue(await evidence.mark_delivery_attempted(item))
        # Independent reader simulates provider boundary: request intent already durable.
        row = await self.read(item)
        self.assertEqual(row.request_outcome,"REQUEST_ATTEMPTED")
        self.assertTrue(row.delivery_uncertainty)
        self.assertTrue(await evidence.finish_delivery_attempt(item,http_status=200,provider_response={"events_received":1,"access_token":"private"}))
        row = await self.read(item)
        self.assertEqual(row.request_outcome,"PROVIDER_ACCEPTED")
        self.assertFalse(row.delivery_uncertainty)
        self.assertNotIn("private",str(row.provider_response))
        self.assertFalse(await evidence.finish_delivery_attempt(item,http_status=400))

    async def test_timeout_reject_and_retry_keep_external_identity(self):
        first = await self.start()
        await evidence.mark_delivery_attempted(first)
        await evidence.finish_delivery_attempt(first,error_phase="after_request")
        self.assertEqual((await self.read(first)).request_outcome,"DELIVERY_UNKNOWN")
        second = await self.start()
        await evidence.mark_delivery_attempted(second)
        await evidence.finish_delivery_attempt(second,http_status=429,provider_response={"error":{"code":4}})
        self.assertEqual((await self.read(second)).request_outcome,"PROVIDER_REJECTED")
        self.assertEqual((await self.read(first)).external_event_id_hash,(await self.read(second)).external_event_id_hash)

    async def test_pre_send_failure_not_sent_and_crash_uncertain(self):
        before = await self.start()
        await evidence.finish_delivery_attempt(before,error_phase="before_request")
        self.assertEqual((await self.read(before)).request_outcome,"NOT_SENT")
        crashed = await self.start()
        await evidence.mark_delivery_attempted(crashed)
        status = None
        async with self.sessions() as session:
            status = await evidence.legacy_delivery_evidence_status(session,self.tenant_a)
        self.assertGreaterEqual(status["legacy_unknown_delivery"],1)
        self.assertEqual((await self.read(crashed)).request_outcome,"REQUEST_ATTEMPTED")

    async def test_cross_tenant_context_drops_references(self):
        foreign = await self.fact(self.tenant_b)
        with evidence.legacy_delivery_context(evidence.LegacyDeliveryContext(empresa_id=self.tenant_b,commercial_event_id=foreign,source_id="private")):
            item = await self.start()
        row = await self.read(item)
        self.assertIsNone(row.commercial_event_id)
        self.assertIsNone(row.source_id_hash)
        self.assertEqual(row.identity_classification,"UNLINKED")
        async with self.sessions() as session:
            status_b = await evidence.legacy_delivery_evidence_status(session,self.tenant_b)
        self.assertEqual(status_b["legacy_delivery_attempts"],0)

    async def test_atomic_competing_ownership_single_winner(self):
        fact = await self.fact()
        async def claim(owner):
            async with self.sessions() as session:
                return await evidence.claim_shared_delivery_identity(session,empresa_id=self.tenant_a,
                    commercial_event_id=fact,dataset_id="123",waba_id="456",event_name="LeadSubmitted",
                    external_event_id="a"*64,owner=owner,shadow_validation=True)
        results = await asyncio.gather(claim("legacy"),claim("canonical"))
        self.assertEqual(sum(row["claimed"] for row in results),1)
        self.assertTrue(all(not row["external_call_permitted"] for row in results))
        self.assertEqual(results[0]["owner"],results[1]["owner"])

    async def test_foreign_fact_and_live_claim_denied(self):
        fact = await self.fact(self.tenant_b)
        async with self.sessions() as session:
            with self.assertRaises(ValueError):
                await evidence.claim_shared_delivery_identity(session,empresa_id=self.tenant_a,
                    commercial_event_id=fact,dataset_id="123",waba_id="456",event_name="LeadSubmitted",
                    external_event_id="a"*64,owner="canonical",shadow_validation=True)
            with self.assertRaises(ValueError):
                await evidence.claim_shared_delivery_identity(session,empresa_id=self.tenant_b,
                    commercial_event_id=fact,dataset_id="123",waba_id="456",event_name="LeadSubmitted",
                    external_event_id="a"*64,owner="canonical",shadow_validation=False)

    async def test_batch_shares_correlation_without_duplicate_identity(self):
        correlation = uuid.uuid4()
        first = await self.start(request_correlation_id=correlation)
        second = await evidence.start_delivery_attempt(empresa_id=self.tenant_a,dataset_id="123",waba_id="456",
            event_name="LeadSubmitted",external_event_id="b"*64,transport_path=evidence.TRANSPORT_PATHS[0],request_correlation_id=correlation)
        self.assertIsNotNone(second)
        self.attempts.append(second)
        self.assertEqual((await self.read(first)).request_correlation_id,(await self.read(second)).request_correlation_id)

    async def test_exact_producer_identity_and_probable_are_separate(self):
        fact = await self.fact()
        legacy_id = uuid.uuid4()
        async with self.engine.begin() as conn:
            lead = (await conn.execute(text("SELECT id FROM crm_leads WHERE empresa_id=:tenant LIMIT 1"), {"tenant":self.tenant_a})).scalar()
            if lead is None:
                self.skipTest("isolated tenant needs lead fixture")
            await conn.execute(text("UPDATE commercial_events SET lead_id=:lead WHERE id=:fact"), {"lead":lead,"fact":fact})
            await conn.execute(text("""INSERT INTO meta_conversion_events
                (id,empresa_id,lead_id,event_name,event_id,status,source)
                VALUES (:id,:tenant,:lead,'LeadSubmitted',:event,'pending','tag')"""),
                {"id":legacy_id,"tenant":self.tenant_a,"lead":lead,"event":"a"*64})
        self.legacy.append(legacy_id)
        # Explicit producer proof permits pre-request identity while late trigger fields are uncommitted.
        context = evidence.LegacyDeliveryContext(empresa_id=self.tenant_a,legacy_event_id=legacy_id,
            commercial_event_id=fact,canonical_source_id=str(fact))
        with evidence.legacy_delivery_context(context):
            exact = await self.start()
        row = await self.read(exact)
        self.assertEqual(row.identity_classification,"EXACT_LINKED")
        self.assertEqual(row.commercial_event_id,fact)
        # Same tenant, lead and event name without actual business source evidence remain unlinked.
        other_fact = await self.fact()
        async with self.engine.begin() as conn:
            await conn.execute(text("UPDATE commercial_events SET lead_id=:lead WHERE id=:fact"), {"lead":lead,"fact":other_fact})
        context = evidence.LegacyDeliveryContext(empresa_id=self.tenant_a,legacy_event_id=legacy_id,
            commercial_event_id=other_fact)
        with evidence.legacy_delivery_context(context):
            probable = await self.start()
        self.assertEqual((await self.read(probable)).identity_classification,"UNLINKED")
        self.assertIsNone((await self.read(probable)).commercial_event_id)

    async def test_unknown_ownership_cannot_release_or_change_identity(self):
        from sqlalchemy.exc import DBAPIError
        fact = await self.fact()
        async with self.sessions() as session:
            await evidence.claim_shared_delivery_identity(session,empresa_id=self.tenant_a,
                commercial_event_id=fact,dataset_id="123",waba_id="456",event_name="LeadSubmitted",
                external_event_id="a"*64,owner="legacy",shadow_validation=True)
        async with self.engine.connect() as conn:
            transaction = await conn.begin()
            await conn.execute(text("UPDATE conversion_delivery_identities SET delivery_state='DELIVERY_UNKNOWN' WHERE commercial_event_id=:id"), {"id":fact})
            for query in (
                "UPDATE conversion_delivery_identities SET delivery_state='RESERVED' WHERE commercial_event_id=:id",
                "UPDATE conversion_delivery_identities SET owner='canonical' WHERE commercial_event_id=:id",
                "DELETE FROM conversion_delivery_identities WHERE commercial_event_id=:id",
            ):
                nested = await conn.begin_nested()
                with self.assertRaises(DBAPIError):
                    await conn.execute(text(query),{"id":fact})
                await nested.rollback()
            await transaction.rollback()

    async def test_evidence_composite_fk_rejects_other_tenant(self):
        from sqlalchemy.exc import IntegrityError
        fact = await self.fact(self.tenant_b)
        item = await self.start()
        async with self.sessions() as session:
            row = await session.get(LegacyConversionDeliveryEvidence,item)
            row.commercial_event_id = fact
            with self.assertRaises(IntegrityError):
                await session.commit()
            await session.rollback()

    async def test_missing_attempt_marker_cannot_turn_actual_response_into_not_sent(self):
        item = await self.start()
        # Simulate failed pre-POST marker write; response still proves an attempted request.
        await evidence.finish_delivery_attempt(item,http_status=200,provider_response={"events_received":1})
        self.assertEqual((await self.read(item)).request_outcome,"PROVIDER_ACCEPTED")
        unknown = await self.start()
        await evidence.finish_delivery_attempt(unknown,error_phase="after_request")
        self.assertEqual((await self.read(unknown)).request_outcome,"DELIVERY_UNKNOWN")

    async def test_same_tenant_fact_foreign_dataset_waba_is_blocked(self):
        fact = await self.fact()
        async with self.sessions() as session:
            for dataset,waba in (("987","654"),("123","654"),("987","456")):
                with self.assertRaises(ValueError):
                    await evidence.claim_shared_delivery_identity(session,empresa_id=self.tenant_a,
                        commercial_event_id=fact,dataset_id=dataset,waba_id=waba,event_name="LeadSubmitted",
                        external_event_id="a"*64,owner="canonical",shadow_validation=True)

    async def test_both_real_transports_persist_boundary_and_outcomes_in_postgres(self):
        from app.services import ads_integration_service as sender
        from app.services.meta.conversion_credentials import ResolvedMetaCredential

        credential = ResolvedMetaCredential("tenant_conversion_token", "mock-token", str(self.tenant_a),
            "456", "123", "v25.0")
        empresa = SimpleNamespace(id=self.tenant_a, meta_pixel_id="123", meta_access_token="mock-token")
        for path in ("enviar_payload_meta_business_messaging", "enviar_payload_meta_capi"):
            for scenario, expected in (
                ("accepted", "PROVIDER_ACCEPTED"), ("rejected", "PROVIDER_REJECTED"),
                ("timeout", "DELIVERY_UNKNOWN"), ("before_request", "NOT_SENT"),
                ("crash_after_accept", "REQUEST_ATTEMPTED"),
            ):
                with self.subTest(path=path, scenario=scenario):
                    external = uuid.uuid4().hex * 2
                    payload = {"data": [{"event_name": "LeadSubmitted", "event_id": external}]}
                    status = 400 if scenario == "rejected" else 200
                    body = {"error": {"code": 100}} if status == 400 else {"events_received": 1}
                    async def rows():
                        async with self.sessions() as session:
                            return list((await session.execute(select(LegacyConversionDeliveryEvidence).where(
                                LegacyConversionDeliveryEvidence.empresa_id == self.tenant_a,
                                LegacyConversionDeliveryEvidence.external_event_id_safe == external,
                            ))).scalars().all())
                    async def post(url, **kwargs):
                        persisted = await rows()  # Independent connection at the actual sender boundary.
                        self.assertEqual(len(persisted), 1)
                        row = persisted[0]
                        self.assertEqual(row.request_outcome, "REQUEST_ATTEMPTED")
                        self.assertTrue(row.delivery_uncertainty)
                        self.assertIsNone(row.attempt_finished_at)
                        self.assertEqual(row.transport_path, "ads_integration_service." + path)
                        self.assertEqual(kwargs["json"], payload)
                        self.assertTrue(url.endswith("/123/events"))
                        if scenario == "timeout":
                            raise httpx.ReadTimeout("mock provider response lost")
                        return SimpleNamespace(status_code=status, json=lambda: body)
                    client = MagicMock()
                    client.post = AsyncMock(side_effect=post)
                    manager = MagicMock()
                    manager.__aenter__ = AsyncMock(return_value=client)
                    manager.__aexit__ = AsyncMock(return_value=False)
                    if scenario == "before_request":
                        manager.__aenter__.side_effect = RuntimeError("mock client open failure")
                    async def invoke():
                        if path.endswith("business_messaging"):
                            return await sender.enviar_payload_meta_business_messaging(credential, payload)
                        return await sender.enviar_payload_meta_capi(empresa, payload)
                    try:
                        with patch.object(sender.httpx, "AsyncClient", return_value=manager):
                            if scenario == "crash_after_accept":
                                # Request/prepare persist for real; final local acknowledgment cannot commit.
                                with patch.object(evidence, "finish_delivery_attempt", AsyncMock(side_effect=RuntimeError("mock crash before local ack"))):
                                    self.assertEqual(await invoke(), (status, body))
                            elif scenario in {"timeout", "before_request"}:
                                with self.assertRaises(httpx.ReadTimeout if scenario == "timeout" else RuntimeError):
                                    await invoke()
                            else:
                                self.assertEqual(await invoke(), (status, body))
                        persisted = await rows()
                        self.assertEqual(len(persisted), 1)
                        row = persisted[0]
                        self.assertEqual(row.request_outcome, expected)
                        self.assertEqual(row.delivery_uncertainty, scenario in {"timeout", "crash_after_accept"})
                        self.assertEqual(row.attempt_finished_at is None, scenario == "crash_after_accept")
                        self.assertEqual(client.post.await_count, 0 if scenario == "before_request" else 1)
                        if path.endswith("meta_capi"):
                            self.assertIsNone(row.waba_id_hash)
                        else:
                            self.assertIsNotNone(row.waba_id_hash)
                    finally:
                        self.attempts.extend(row.id for row in await rows())

    async def test_real_retry_persists_two_requests_with_stable_identity_in_postgres(self):
        from app.services import ads_integration_service as sender
        from app.services.meta.conversion_credentials import ResolvedMetaCredential

        credential = ResolvedMetaCredential("tenant_conversion_token", "mock-token", str(self.tenant_a),
            "456", "123", "v25.0")
        external = uuid.uuid4().hex * 2
        payload = {"data": [{"event_name": "LeadSubmitted", "event_id": external}]}
        requests = []
        async def post(url, **kwargs):
            requests.append((url, kwargs["json"]["data"][0]["event_id"]))
            if len(requests) == 1:
                raise httpx.ReadTimeout("mock first response lost")
            return SimpleNamespace(status_code=200, json=lambda: {"events_received": 1})
        client = MagicMock()
        client.post = AsyncMock(side_effect=post)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        try:
            with patch.object(sender.httpx, "AsyncClient", return_value=manager):
                result = await sender.enviar_payload_meta_business_messaging_with_retry(
                    credential, payload, event_id=external, event_name="LeadSubmitted")
            self.assertEqual(result[0], 200)
            self.assertEqual(result[2], 2)
            self.assertEqual(requests[0], requests[1])
            async with self.sessions() as session:
                rows = list((await session.execute(select(LegacyConversionDeliveryEvidence).where(
                    LegacyConversionDeliveryEvidence.empresa_id == self.tenant_a,
                    LegacyConversionDeliveryEvidence.external_event_id_safe == external,
                ).order_by(LegacyConversionDeliveryEvidence.attempt_started_at))).scalars().all())
            self.assertEqual(len(rows), 2)
            self.assertEqual([row.request_outcome for row in rows], ["DELIVERY_UNKNOWN", "PROVIDER_ACCEPTED"])
            self.assertEqual(rows[0].external_event_id_hash, rows[1].external_event_id_hash)
            self.assertNotEqual(rows[0].request_correlation_id, rows[1].request_correlation_id)
        finally:
            async with self.sessions() as session:
                ids = (await session.execute(select(LegacyConversionDeliveryEvidence.id).where(
                    LegacyConversionDeliveryEvidence.empresa_id == self.tenant_a,
                    LegacyConversionDeliveryEvidence.external_event_id_safe == external,
                ))).scalars().all()
            self.attempts.extend(ids)
