"""Tenant-scoped persistence operations for LeoFT generation.

The repository never commits. Callers own transaction boundaries.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.leoft_models import (
    LeoFTAnamnesisSnapshot,
    LeoFTGenerationAttempt,
    LeoFTGenerationCheckpoint,
    LeoFTGenerationJob,
    LeoFTGenerationSessionTask,
    LeoFTPaymentEvent,
)
from db.models import CRMLead


class LeoFTGenerationRepository:
    """All reads are constrained to the injected tenant."""

    def __init__(self, session: AsyncSession, *, company_id: UUID) -> None:
        self.session = session
        self.company_id = UUID(str(company_id))

    async def flush(self) -> None:
        await self.session.flush()

    def add(self, entity: object) -> None:
        self.session.add(entity)

    async def get_lead(self, student_id: UUID, *, lock: bool = False) -> CRMLead | None:
        stmt = select(CRMLead).where(
            CRMLead.id == student_id, CRMLead.empresa_id == self.company_id
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def get_payment_event(
        self, event_id: UUID, *, lock: bool = False
    ) -> LeoFTPaymentEvent | None:
        stmt = select(LeoFTPaymentEvent).where(
            LeoFTPaymentEvent.event_id == event_id,
            LeoFTPaymentEvent.company_id == self.company_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def find_payment_event_by_idempotency(
        self, key: str, *, lock: bool = False
    ) -> LeoFTPaymentEvent | None:
        stmt = select(LeoFTPaymentEvent).where(
            LeoFTPaymentEvent.company_id == self.company_id,
            LeoFTPaymentEvent.idempotency_key == key,
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def find_payment_event_by_provider(
        self, provider: str, provider_event_id: str
    ) -> LeoFTPaymentEvent | None:
        return await self.session.scalar(
            select(LeoFTPaymentEvent).where(
                LeoFTPaymentEvent.company_id == self.company_id,
                LeoFTPaymentEvent.provider == provider,
                LeoFTPaymentEvent.provider_event_id == provider_event_id,
            )
        )

    async def find_purchase_payment(
        self, *, product_id: str, order_id: str, lock: bool = False
    ) -> LeoFTPaymentEvent | None:
        stmt = (
            select(LeoFTPaymentEvent)
            .where(
                LeoFTPaymentEvent.company_id == self.company_id,
                LeoFTPaymentEvent.product_id == product_id,
                LeoFTPaymentEvent.order_id == order_id,
            )
            .order_by(LeoFTPaymentEvent.received_at.desc())
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def find_snapshot(
        self, *, student_id: UUID, source_hash: str
    ) -> LeoFTAnamnesisSnapshot | None:
        return await self.session.scalar(
            select(LeoFTAnamnesisSnapshot).where(
                LeoFTAnamnesisSnapshot.company_id == self.company_id,
                LeoFTAnamnesisSnapshot.student_id == student_id,
                LeoFTAnamnesisSnapshot.source_hash == source_hash,
            )
        )

    async def get_snapshot(self, snapshot_id: UUID) -> LeoFTAnamnesisSnapshot | None:
        return await self.session.scalar(
            select(LeoFTAnamnesisSnapshot).where(
                LeoFTAnamnesisSnapshot.snapshot_id == snapshot_id,
                LeoFTAnamnesisSnapshot.company_id == self.company_id,
            )
        )

    async def find_job_by_idempotency(
        self, key: str, *, lock: bool = False
    ) -> LeoFTGenerationJob | None:
        stmt = select(LeoFTGenerationJob).where(
            LeoFTGenerationJob.company_id == self.company_id,
            LeoFTGenerationJob.idempotency_key == key,
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def find_job_for_payment(
        self, payment_event_id: UUID, *, lock: bool = False
    ) -> LeoFTGenerationJob | None:
        stmt = select(LeoFTGenerationJob).where(
            LeoFTGenerationJob.company_id == self.company_id,
            LeoFTGenerationJob.payment_event_id == payment_event_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def find_job_for_purchase(
        self, *, product_id: str, order_id: str, lock: bool = False
    ) -> LeoFTGenerationJob | None:
        stmt = select(LeoFTGenerationJob).where(
            LeoFTGenerationJob.company_id == self.company_id,
            LeoFTGenerationJob.product_id == product_id,
            LeoFTGenerationJob.order_id == order_id,
            LeoFTGenerationJob.status != "cancelled",
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def get_job(
        self, job_id: UUID, *, lock: bool = False
    ) -> LeoFTGenerationJob | None:
        stmt = select(LeoFTGenerationJob).where(
            LeoFTGenerationJob.generation_job_id == job_id,
            LeoFTGenerationJob.company_id == self.company_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    def _task_query(self) -> Select[tuple[LeoFTGenerationSessionTask]]:
        return (
            select(LeoFTGenerationSessionTask)
            .join(
                LeoFTGenerationJob,
                LeoFTGenerationJob.generation_job_id
                == LeoFTGenerationSessionTask.generation_job_id,
            )
            .where(
                LeoFTGenerationJob.company_id == self.company_id,
                LeoFTGenerationSessionTask.company_id == self.company_id,
            )
        )

    async def get_task(
        self, task_id: UUID, *, lock: bool = False
    ) -> LeoFTGenerationSessionTask | None:
        stmt = self._task_query().where(
            LeoFTGenerationSessionTask.session_task_id == task_id
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def list_tasks(
        self, job_id: UUID, *, lock: bool = False
    ) -> list[LeoFTGenerationSessionTask]:
        stmt = (
            self._task_query()
            .where(LeoFTGenerationSessionTask.generation_job_id == job_id)
            .order_by(LeoFTGenerationSessionTask.sequence.asc())
        )
        if lock:
            stmt = stmt.with_for_update()
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def next_acquirable_task(
        self, job_id: UUID, *, now: Any
    ) -> LeoFTGenerationSessionTask | None:
        stmt = (
            self._task_query()
            .where(
                LeoFTGenerationSessionTask.generation_job_id == job_id,
                LeoFTGenerationSessionTask.status.in_(
                    ["pending", "generating", "validating", "repairing"]
                ),
                or_(
                    LeoFTGenerationSessionTask.lease_owner.is_(None),
                    LeoFTGenerationSessionTask.lease_expires_at.is_(None),
                    LeoFTGenerationSessionTask.lease_expires_at <= now,
                ),
            )
            .order_by(LeoFTGenerationSessionTask.sequence.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return await self.session.scalar(stmt)

    async def find_attempt_by_idempotency(
        self, job_id: UUID, key: str
    ) -> LeoFTGenerationAttempt | None:
        return await self.session.scalar(
            select(LeoFTGenerationAttempt)
            .join(
                LeoFTGenerationJob,
                LeoFTGenerationJob.generation_job_id
                == LeoFTGenerationAttempt.generation_job_id,
            )
            .where(
                LeoFTGenerationJob.company_id == self.company_id,
                LeoFTGenerationAttempt.company_id == self.company_id,
                LeoFTGenerationAttempt.generation_job_id == job_id,
                LeoFTGenerationAttempt.idempotency_key == key,
            )
        )

    async def list_attempts(self, job_id: UUID) -> list[LeoFTGenerationAttempt]:
        result = await self.session.scalars(
            select(LeoFTGenerationAttempt)
            .join(
                LeoFTGenerationJob,
                LeoFTGenerationJob.generation_job_id
                == LeoFTGenerationAttempt.generation_job_id,
            )
            .where(
                LeoFTGenerationJob.company_id == self.company_id,
                LeoFTGenerationAttempt.company_id == self.company_id,
                LeoFTGenerationAttempt.generation_job_id == job_id,
            )
            .order_by(LeoFTGenerationAttempt.created_at.asc())
        )
        return list(result.all())

    async def latest_checkpoint(
        self, job_id: UUID, *, lock: bool = False
    ) -> LeoFTGenerationCheckpoint | None:
        stmt = (
            select(LeoFTGenerationCheckpoint)
            .join(
                LeoFTGenerationJob,
                LeoFTGenerationJob.generation_job_id
                == LeoFTGenerationCheckpoint.generation_job_id,
            )
            .where(
                LeoFTGenerationJob.company_id == self.company_id,
                LeoFTGenerationCheckpoint.company_id == self.company_id,
                LeoFTGenerationCheckpoint.generation_job_id == job_id,
            )
            .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)
