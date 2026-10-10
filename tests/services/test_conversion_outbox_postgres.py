"""PostgreSQL-only claim/FK/lease integration tests for the P1C3 schema."""
from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.meta.conversion_outbox import (
    claim_shadow_outbox,
    ensure_shadow_outbox_intent,
    record_shadow_simulation,
    reconcile_missing_shadow_outbox,
)
from db.models import CanonicalCommercialEvent, CanonicalMetaConversionProjection
from sqlalchemy import select

TEST_DATABASE_URL = os.getenv("CANONICAL_OUTBOX_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "isolated CANONICAL_OUTBOX_TEST_DATABASE_URL is required")
class CanonicalConversionOutboxPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(TEST_DATABASE_URL)
        if parsed.get_backend_name() != "postgresql" or parsed.database != "p1c3_outbox_test":
            raise RuntimeError("refusing outbox integration test outside isolated p1c3_outbox_test PostgreSQL")
        database_url = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.connect() as connection:
            self.tenant_a, self.tenant_b = (await connection.execute(
                text("SELECT id FROM empresas ORDER BY id LIMIT 2")
            )).scalars().all()
        if not self.tenant_a or not self.tenant_b:
            self.skipTest("isolated fixture database needs two tenant rows")
        self.created_event_ids: list[uuid.UUID] = []
        self.created_outbox_ids: list[uuid.UUID] = []

    async def asyncTearDown(self):
        if not hasattr(self, "engine"):
            return
        async with self.engine.begin() as connection:
            for row_id in self.created_outbox_ids:
                await connection.execute(text("DELETE FROM canonical_conversion_outbox WHERE id=:id"), {"id": row_id})
            for row_id in self.created_event_ids:
                await connection.execute(text("DELETE FROM commercial_events WHERE id=:id"), {"id": row_id})
        await self.engine.dispose()

    async def _insert_intent(
        self, *, tenant_id=None, historical=False, insert_outbox=True, insert_projection=True,
    ):
        tenant_id = tenant_id or self.tenant_a
        event_id, projection_id, outbox_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        event_key = f"p1c3-test:{event_id}"
        canonical_id = uuid.uuid4().hex + uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                INSERT INTO commercial_events (
                    id,empresa_id,event_name,event_category,source_type,occurred_at,observed_at,
                    is_backfilled,external_dispatch_policy,attribution_snapshot,evidence_json,metadata_json,idempotency_key
                ) VALUES (
                    :id,:tenant,'LeadSubmitted','lead','p1c3_test',:now,:now,
                    :historical,:dispatch_policy,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,:event_key
                )
            """), {
                "id": event_id, "tenant": tenant_id, "now": now,
                "historical": historical,
                "dispatch_policy": "historical_no_send" if historical else "disabled",
                "event_key": event_key,
            })
            if insert_projection:
                await connection.execute(text("""
                    INSERT INTO commercial_meta_conversion_projections (
                        id,empresa_id,commercial_event_id,projection_version,canonical_event_id,event_name,
                        mode,projection_status,comparison_classification,legacy_reference_classification,
                        delivery_state,comparison_json,safe_summary_json
                    ) VALUES (
                        :projection,:tenant,:event,1,:canonical,'LeadSubmitted','shadow','valid',
                        'NO_LEGACY_REFERENCE','UNLINKED','SHADOW_ONLY','{}'::jsonb,'{}'::jsonb
                    )
                """), {
                    "projection": projection_id, "tenant": tenant_id, "event": event_id,
                    "canonical": canonical_id,
                })
            if insert_outbox:
                await connection.execute(text("""
                    INSERT INTO canonical_conversion_outbox (
                        id,empresa_id,commercial_event_id,projection_id,event_name,event_id,
                        idempotency_key,payload_snapshot,status,dispatch_mode,historical_no_send
                    ) VALUES (
                        :outbox,:tenant,:event,:projection,'LeadSubmitted',:canonical,
                        :idem,'{}'::jsonb,:status,'shadow',:historical
                    )
                """), {
                    "outbox": outbox_id, "tenant": tenant_id, "event": event_id,
                    "projection": projection_id, "canonical": canonical_id,
                    "idem": f"canonical:{canonical_id}",
                    "status": "HISTORICAL_BLOCKED" if historical else "SHADOW",
                    "historical": historical,
                })
        self.created_event_ids.append(event_id)
        if insert_outbox:
            self.created_outbox_ids.append(outbox_id)
        return outbox_id, event_id

    async def test_projection_rebuild_creates_one_durable_intent(self):
        _unused_outbox_id, event_id = await self._insert_intent(insert_outbox=False)
        async with self.sessions() as session:
            event = await session.scalar(select(CanonicalCommercialEvent).where(CanonicalCommercialEvent.id == event_id))
            projection = await session.scalar(select(CanonicalMetaConversionProjection).where(
                CanonicalMetaConversionProjection.commercial_event_id == event_id,
            ))
            self.assertIsNotNone(event)
            self.assertIsNotNone(projection)
            await ensure_shadow_outbox_intent(session, event=event, projection=projection)
            stable_event_id = projection.canonical_event_id
            await ensure_shadow_outbox_intent(session, event=event, projection=projection)
            await session.commit()
        async with self.engine.connect() as connection:
            rows = (await connection.execute(text("""
                SELECT event_id,status,historical_no_send,payload_snapshot
                FROM canonical_conversion_outbox WHERE commercial_event_id=:event
            """), {"event": event_id})).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_id, stable_event_id)
        self.assertEqual(rows[0].status, "SHADOW")
        self.assertFalse(rows[0].historical_no_send)
        self.assertNotIn("ctwa_clid", rows[0].payload_snapshot)

    async def test_reconciler_recovers_missing_projection_and_outbox_idempotently(self):
        _unused_outbox_id, event_id = await self._insert_intent(
            insert_outbox=False, insert_projection=False,
        )
        async with self.sessions() as session:
            recovered = await reconcile_missing_shadow_outbox(
                session, empresa_id=self.tenant_a, limit=10,
            )
            self.assertEqual(recovered, 1)
            await session.commit()
        async with self.sessions() as session:
            recovered_again = await reconcile_missing_shadow_outbox(
                session, empresa_id=self.tenant_a, limit=10,
            )
            self.assertEqual(recovered_again, 0)
            await session.commit()
        async with self.engine.connect() as connection:
            counts = (await connection.execute(text("""
                SELECT
                  (SELECT count(*) FROM commercial_meta_conversion_projections WHERE commercial_event_id=:event),
                  (SELECT count(*) FROM canonical_conversion_outbox WHERE commercial_event_id=:event)
            """), {"event": event_id})).one()
        self.assertEqual(tuple(counts), (1, 1))

    async def test_reconciler_isolates_event_failure_and_continues_batch(self):
        _first_outbox, first_event_id = await self._insert_intent(
            insert_outbox=False, insert_projection=False,
        )
        _second_outbox, second_event_id = await self._insert_intent(
            insert_outbox=False, insert_projection=False,
        )
        from app.services.meta import canonical_conversion_shadow

        original_projector = canonical_conversion_shadow.project_commercial_event

        async def fail_first_then_project(session, *, empresa_id, commercial_event_id, persist):
            result = await original_projector(
                session, empresa_id=empresa_id,
                commercial_event_id=commercial_event_id, persist=persist,
            )
            if commercial_event_id == first_event_id:
                # Force rollback after the projector has inserted both rows.
                raise RuntimeError("synthetic isolated recovery failure")
            return result

        async with self.sessions() as session:
            with patch.object(
                canonical_conversion_shadow, "project_commercial_event", fail_first_then_project,
            ):
                recovered = await reconcile_missing_shadow_outbox(
                    session, empresa_id=self.tenant_a, limit=10,
                )
            self.assertEqual(recovered, 1)
            await session.commit()

        async with self.engine.connect() as connection:
            first_counts = (await connection.execute(text("""
                SELECT
                  (SELECT count(*) FROM commercial_meta_conversion_projections WHERE commercial_event_id=:event),
                  (SELECT count(*) FROM canonical_conversion_outbox WHERE commercial_event_id=:event)
            """), {"event": first_event_id})).one()
            second_counts = (await connection.execute(text("""
                SELECT
                  (SELECT count(*) FROM commercial_meta_conversion_projections WHERE commercial_event_id=:event),
                  (SELECT count(*) FROM canonical_conversion_outbox WHERE commercial_event_id=:event)
            """), {"event": second_event_id})).one()
        self.assertEqual(tuple(first_counts), (0, 0))
        self.assertEqual(tuple(second_counts), (1, 1))

        # The failed fact remains eligible for a later recovery round.
        async with self.sessions() as session:
            recovered_after_retry = await reconcile_missing_shadow_outbox(
                session, empresa_id=self.tenant_a, limit=10,
            )
            self.assertEqual(recovered_after_retry, 1)
            await session.commit()

    async def test_simulated_retry_backoff_is_durable_and_event_id_stable(self):
        outbox_id, _ = await self._insert_intent()
        start = datetime.now(timezone.utc)
        async with self.sessions() as session:
            first = await claim_shadow_outbox(
                session, lease_owner="retry-first", limit=1, lease_seconds=10, now=start,
            )
        self.assertEqual(len(first), 1)
        stable_event_id = first[0]["event_id"]
        async with self.sessions() as session:
            await record_shadow_simulation(
                session, outbox_id=outbox_id, lease_owner="retry-first",
                simulated_error_class="TimeoutError", simulated_retryable=True,
                retry_delay_seconds=15, now=start + timedelta(seconds=1),
            )
            await session.commit()
        async with self.sessions() as session:
            early = await claim_shadow_outbox(
                session, lease_owner="too-early", limit=1, now=start + timedelta(seconds=10),
            )
        self.assertEqual(early, [])
        async with self.sessions() as session:
            retried = await claim_shadow_outbox(
                session, lease_owner="retry-second", limit=1, lease_seconds=10,
                now=start + timedelta(seconds=20),
            )
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["event_id"], stable_event_id)
        self.assertEqual(retried[0]["attempt_number"], 2)
        async with self.engine.connect() as connection:
            outbox = (await connection.execute(text(
                "SELECT status,event_id,attempt_count FROM canonical_conversion_outbox WHERE id=:id"
            ), {"id": outbox_id})).one()
            attempts = (await connection.execute(text(
                "SELECT count(*), bool_and(provider_ack_status='NOT_ATTEMPTED') FROM canonical_conversion_delivery_attempts WHERE outbox_id=:id"
            ), {"id": outbox_id})).one()
        self.assertEqual((outbox.status, outbox.event_id, outbox.attempt_count),
                         ("CLAIMED", stable_event_id, 2))
        self.assertEqual((attempts[0], attempts[1]), (1, True))

    async def test_concurrent_claim_and_expired_lease_recovery(self):
        outbox_id, _ = await self._insert_intent()
        start = datetime.now(timezone.utc)

        async def claim(owner, now):
            async with self.sessions() as session:
                return await claim_shadow_outbox(
                    session, lease_owner=owner, limit=1, lease_seconds=10, now=now,
                )

        first, second = await asyncio.gather(
            claim("instance-one", start), claim("instance-two", start),
        )
        winners = [rows for rows in (first, second) if rows]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0][0]["outbox_id"], str(outbox_id))
        self.assertFalse(winners[0][0]["external_call_permitted"])

        async with self.sessions() as session:
            reclaimed = await claim_shadow_outbox(
                session,
                lease_owner="recovery-instance",
                limit=1,
                lease_seconds=10,
                now=start + timedelta(seconds=11),
            )
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0]["outbox_id"], str(outbox_id))
        self.assertEqual(reclaimed[0]["attempt_number"], 2)
        async with self.sessions() as session:
            await record_shadow_simulation(
                session,
                outbox_id=outbox_id,
                lease_owner="recovery-instance",
                now=start + timedelta(seconds=12),
            )
            await session.commit()
        async with self.engine.connect() as connection:
            status = (await connection.execute(text(
                "SELECT status,attempt_count FROM canonical_conversion_outbox WHERE id=:id"
            ), {"id": outbox_id})).one()
            attempt = (await connection.execute(text(
                "SELECT attempt_kind,status,provider_ack_status FROM canonical_conversion_delivery_attempts WHERE outbox_id=:id"
            ), {"id": outbox_id})).one()
        self.assertEqual((status.status, status.attempt_count), ("SHADOW", 2))
        self.assertEqual((attempt.attempt_kind, attempt.status, attempt.provider_ack_status),
                         ("SHADOW_SIMULATION", "SIMULATED", "NOT_ATTEMPTED"))

    async def test_tenant_composite_foreign_keys_and_history_trigger(self):
        other_outbox, _ = await self._insert_intent(tenant_id=self.tenant_b)
        _own_outbox, own_event = await self._insert_intent(tenant_id=self.tenant_a)
        async with self.engine.connect() as connection:
            transaction = await connection.begin()
            row = (await connection.execute(text(
                "SELECT projection_id,event_id,idempotency_key FROM canonical_conversion_outbox WHERE id=:id"
            ), {"id": other_outbox})).one()
            with self.assertRaises(Exception):
                await connection.execute(text("""
                    INSERT INTO canonical_conversion_outbox (
                        id,empresa_id,commercial_event_id,projection_id,event_name,event_id,
                        idempotency_key,payload_snapshot,status,dispatch_mode,historical_no_send
                    ) VALUES (
                        :id,:tenant,:event,:projection,'LeadSubmitted',:canonical,
                        :idem,'{}'::jsonb,'SHADOW','shadow',false
                    )
                """), {
                    "id": uuid.uuid4(), "tenant": self.tenant_a, "event": own_event,
                    "projection": row.projection_id, "canonical": row.event_id,
                    "idem": f"cross-tenant:{uuid.uuid4()}",
                })
            await transaction.rollback()
        # The failed statement is rolled back; check historical guard separately.
        historical_id, _ = await self._insert_intent(historical=True)
        async with self.engine.connect() as connection:
            transaction = await connection.begin()
            with self.assertRaises(Exception):
                await connection.execute(text(
                    "UPDATE canonical_conversion_outbox SET historical_no_send=false,status='SHADOW' WHERE id=:id"
                ), {"id": historical_id})
            await transaction.rollback()

    async def test_historical_intent_is_not_claimable(self):
        outbox_id, _ = await self._insert_intent(historical=True)
        async with self.sessions() as session:
            claimed = await claim_shadow_outbox(
                session,
                lease_owner="history-check",
                limit=10,
                now=datetime.now(timezone.utc) + timedelta(days=1),
            )
        self.assertNotIn(str(outbox_id), {item["outbox_id"] for item in claimed})


if __name__ == "__main__":
    unittest.main()
