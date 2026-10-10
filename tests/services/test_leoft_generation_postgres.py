from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.attributes import flag_modified

from app.leoft_generation.enums import (
    CANONICAL_PRODUCT_ID,
    EligibilityStatus,
    Environment,
    GenerationAttemptStatus,
    GenerationTaskStatus,
    PaymentEventType,
    PaymentStatus,
)
from app.leoft_generation.errors import (
    DomainValidationError,
    IdempotencyConflictError,
    LeaseConflictError,
)
from app.leoft_generation.repository import LeoFTGenerationRepository
from app.leoft_generation.schemas import (
    AttemptCompletion,
    AttemptCreate,
    E2EAllowlist,
    GenerationPolicy,
    PaymentEventCreate,
    PurchaseKey,
    TrainingBlueprint,
)
from app.leoft_generation.services import (
    LeoFTGenerationService,
    payment_event_idempotency_key,
)
from db.database import AsyncSessionLocal, engine
from db.leoft_models import (
    LeoFTAnamnesisSnapshot,
    LeoFTGenerationAttempt,
    LeoFTGenerationCheckpoint,
    LeoFTGenerationJob,
    LeoFTPaymentEvent,
    LeoFTTrainingPlan,
    LeoFTTrainingPlanVersion,
)
from db.models import CRMLead, Empresa


if not os.getenv("LEOFT_S3_TEST_DATABASE_URL"):
    raise unittest.SkipTest("requires an isolated PostgreSQL database")


def _anamnesis(goal: str = "condicionamento") -> dict:
    return {
        "submitted": True,
        "submitted_at": "2026-08-05T12:00:00Z",
        "versao_form": "synthetic_v1",
        "respostas": {
            "personalData": {"age": "35", "gender": "other", "name": "Synthetic"},
            "trainingProfile": {
                "mainGoal": goal,
                "currentLevel": "beginner",
                "daysPerWeek": "3",
                "timePerSession": "45",
                "equipment": ["bodyweight"],
                "limitations": [],
            },
        },
    }


class LeoFTGenerationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.company_id = uuid4()
        self.student_id = uuid4()
        self.now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        self.session = AsyncSessionLocal()
        self.session.add(Empresa(id=self.company_id, nome_empresa="Synthetic tenant"))
        self.session.add(
            CRMLead(
                id=self.student_id,
                empresa_id=self.company_id,
                nome_contato="Synthetic student",
                tags=[],
                dados_adicionais={"anamneses": {"leoft": _anamnesis()}},
                status_atendimento="aberto",
            )
        )
        await self.session.commit()
        allowlist = E2EAllowlist(
            company_ids={self.company_id},
            student_ids={self.student_id},
            contact_ids={self.student_id},
            product_ids={CANONICAL_PRODUCT_ID},
        )
        self.policy = GenerationPolicy(
            environment=Environment.E2E, e2e_allowlist=allowlist
        )
        self.repository = LeoFTGenerationRepository(
            self.session, company_id=self.company_id
        )
        self.service = LeoFTGenerationService(
            self.repository,
            policy=self.policy,
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await engine.dispose()

    def _payment(
        self,
        *,
        order_id: str = "order-1",
        event_type: PaymentEventType = PaymentEventType.CONFIRMED,
        status: PaymentStatus = PaymentStatus.CONFIRMED,
        event_id: UUID | None = None,
        provider_event_id: str | None = None,
        raw_hash: str = "a" * 64,
        received_at: datetime | None = None,
        reverses_event_id: UUID | None = None,
    ) -> PaymentEventCreate:
        provider_id = provider_event_id or f"provider-{uuid4()}"
        payment_id = f"payment-{order_id}"
        return PaymentEventCreate(
            event_id=event_id or uuid4(),
            event_type=event_type,
            occurred_at=self.now,
            received_at=received_at or self.now,
            company_id=self.company_id,
            contact_id=self.student_id,
            student_id=self.student_id,
            product_id=CANONICAL_PRODUCT_ID,
            order_id=order_id,
            payment_id=payment_id,
            provider="controlled_e2e",
            provider_event_id=provider_id,
            amount=Decimal("97"),
            currency="BRL",
            payment_status=status,
            environment=Environment.E2E,
            correlation_id=uuid4(),
            idempotency_key=payment_event_idempotency_key(
                provider="controlled_e2e",
                provider_event_id=provider_id,
                payment_id=payment_id,
                status=status.value,
            ),
            raw_event_hash=raw_hash,
            reverses_event_id=reverses_event_id,
            metadata={"adapter_version": "synthetic"},
        )

    async def test_payment_snapshot_job_tasks_attempt_checkpoint_and_tenant_isolation(
        self,
    ) -> None:
        payment_data = self._payment()
        payment = await self.service.register_payment_event_idempotent(payment_data)
        duplicate = await self.service.register_payment_event_idempotent(payment_data)
        self.assertEqual(payment.id, duplicate.id)

        purchase = PurchaseKey(
            company_id=self.company_id,
            student_id=self.student_id,
            contact_id=self.student_id,
            product_id=CANONICAL_PRODUCT_ID,
            order_id="order-1",
            environment=Environment.E2E,
        )
        eligible = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase,
            trigger="payment",
            correlation_id=payment_data.correlation_id,
        )
        self.assertEqual(eligible.status, EligibilityStatus.ELIGIBLE)
        repeated = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase,
            trigger="administrative_retry",
            correlation_id=payment_data.correlation_id,
        )
        self.assertEqual(repeated.status, EligibilityStatus.ALREADY_REQUESTED)

        second_payment = self._payment(
            order_id="order-1",
            event_id=uuid4(),
            provider_event_id=f"provider-{uuid4()}",
            raw_hash="e" * 64,
            received_at=self.now + timedelta(seconds=1),
        )
        await self.service.register_payment_event_idempotent(second_payment)
        same_purchase = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase,
            trigger="duplicate_purchase_event",
            correlation_id=second_payment.correlation_id,
        )
        self.assertEqual(same_purchase.status, EligibilityStatus.ALREADY_REQUESTED)
        await self.session.commit()

        self.assertEqual(
            await self.session.scalar(select(func.count(LeoFTPaymentEvent.id))), 2
        )
        self.assertEqual(
            await self.session.scalar(
                select(func.count(LeoFTGenerationJob.generation_job_id))
            ),
            1,
        )
        self.assertEqual(
            await self.session.scalar(select(func.count(LeoFTTrainingPlan.id))), 1
        )
        self.assertEqual(
            await self.session.scalar(select(func.count(LeoFTTrainingPlanVersion.id))),
            1,
        )

        snapshot = await self.service.capture_anamnesis_snapshot(
            student_id=self.student_id
        )
        same_snapshot = await self.service.capture_anamnesis_snapshot(
            student_id=self.student_id
        )
        self.assertEqual(snapshot.snapshot_id, same_snapshot.snapshot_id)
        lead = await self.repository.get_lead(self.student_id, lock=True)
        lead.dados_adicionais["anamneses"]["leoft"] = _anamnesis("forca")
        flag_modified(lead, "dados_adicionais")
        changed_snapshot = await self.service.capture_anamnesis_snapshot(
            student_id=self.student_id
        )
        self.assertNotEqual(snapshot.snapshot_id, changed_snapshot.snapshot_id)

        blueprint = TrainingBlueprint(
            sessions=[
                {
                    "week_number": week,
                    "session_number": session_number,
                    "phase_number": 1,
                    "objective": f"synthetic-{week}-{session_number}",
                    "sequence": (week - 1) * 3 + session_number,
                }
                for week in range(1, 5)
                for session_number in range(1, 4)
            ]
        )
        tasks = await self.service.create_tasks_from_blueprint(
            job_id=eligible.generation_job_id,
            blueprint=blueprint,
        )
        tasks_again = await self.service.create_tasks_from_blueprint(
            job_id=eligible.generation_job_id,
            blueprint=blueprint,
        )
        self.assertEqual(len(tasks), 12)
        self.assertEqual(
            [task.session_task_id for task in tasks],
            [task.session_task_id for task in tasks_again],
        )

        first = await self.service.acquire_next_task(
            job_id=eligible.generation_job_id, lease_owner="worker-a"
        )
        with self.assertRaises(LeaseConflictError):
            await self.service.acquire_task_lease(
                task_id=first.session_task_id, lease_owner="worker-b"
            )
        self.now += timedelta(seconds=self.policy.lease_seconds + 1)
        first = await self.service.acquire_task_lease(
            task_id=first.session_task_id, lease_owner="worker-b"
        )
        first = await self.service.transition_task(
            task_id=first.session_task_id,
            lease_owner="worker-b",
            target=GenerationTaskStatus.VALIDATING,
        )

        attempt = await self.service.append_attempt(
            job_id=eligible.generation_job_id,
            task_id=first.session_task_id,
            request=AttemptCreate(
                stage="session_generation",
                attempt_type="generation",
                provider="deepseek",
                model="synthetic-no-call",
                prompt_version="v1",
                catalog_version="synthetic-v1",
                request_hash="b" * 64,
                idempotency_key="attempt-1",
                correlation_id=payment_data.correlation_id,
                started_at=self.now,
            ),
            completion=AttemptCompletion(
                status=GenerationAttemptStatus.SUCCEEDED,
                response_hash="c" * 64,
                input_tokens=10,
                output_tokens=20,
                estimated_cost=Decimal("0.001"),
                validation_result={"valid": True},
                finished_at=self.now + timedelta(seconds=1),
            ),
        )
        duplicate_attempt = await self.service.append_attempt(
            job_id=eligible.generation_job_id,
            task_id=first.session_task_id,
            request=AttemptCreate(
                stage="session_generation",
                attempt_type="generation",
                provider="deepseek",
                model="synthetic-no-call",
                prompt_version="v1",
                catalog_version="synthetic-v1",
                request_hash="b" * 64,
                idempotency_key="attempt-1",
                correlation_id=payment_data.correlation_id,
                started_at=self.now,
            ),
            completion=AttemptCompletion(
                status=GenerationAttemptStatus.SUCCEEDED,
                response_hash="c" * 64,
                input_tokens=10,
                output_tokens=20,
                estimated_cost=Decimal("0.001"),
                validation_result={"valid": True},
                finished_at=self.now + timedelta(seconds=1),
            ),
        )
        self.assertEqual(attempt.attempt_id, duplicate_attempt.attempt_id)
        job_attempt = await self.service.append_attempt(
            job_id=eligible.generation_job_id,
            task_id=None,
            request=AttemptCreate(
                stage="blueprint",
                attempt_type="blueprint",
                provider="deepseek",
                model="synthetic-no-call",
                prompt_version="v1",
                catalog_version="synthetic-v1",
                request_hash="1" * 64,
                idempotency_key="job-attempt-1",
                correlation_id=payment_data.correlation_id,
                started_at=self.now,
            ),
            completion=AttemptCompletion(
                status=GenerationAttemptStatus.SUCCEEDED,
                response_hash="2" * 64,
                finished_at=self.now + timedelta(seconds=1),
            ),
        )
        with self.assertRaises(DBAPIError):
            async with self.session.begin_nested():
                self.session.add(
                    LeoFTGenerationAttempt(
                        attempt_id=uuid4(),
                        company_id=self.company_id,
                        generation_job_id=eligible.generation_job_id,
                        session_task_id=None,
                        target_version_id=job_attempt.target_version_id,
                        attempt_number=job_attempt.attempt_number,
                        attempt_type="blueprint",
                        stage="blueprint",
                        prompt_version="v1",
                        catalog_version="synthetic-v1",
                        status=GenerationAttemptStatus.SUCCEEDED.value,
                        provider="deepseek",
                        model="synthetic-no-call",
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_cost=Decimal("0"),
                        currency="USD",
                        latency_ms=0,
                        request_hash="3" * 64,
                        response_hash="4" * 64,
                        started_at=self.now,
                        completed_at=self.now,
                        created_at=self.now,
                        correlation_id=payment_data.correlation_id,
                        idempotency_key="job-attempt-duplicate-number",
                    )
                )
                await self.session.flush()
        first = await self.service.complete_task(
            task_id=first.session_task_id, lease_owner="worker-b"
        )
        self.assertEqual(first.status, GenerationTaskStatus.COMPLETED.value)
        checkpoint_1 = await self.service.create_generation_checkpoint(
            job_id=eligible.generation_job_id,
            current_session_task_id=first.session_task_id,
            stage="generating",
            blueprint_hash="d" * 64,
            last_attempt_id=attempt.attempt_id,
        )
        checkpoint_2 = await self.service.capture_checkpoint(
            job_id=eligible.generation_job_id,
            stage="generating",
            blueprint_hash="d" * 64,
            last_attempt_id=attempt.attempt_id,
        )
        self.assertEqual(
            (checkpoint_1.checkpoint_version, checkpoint_2.checkpoint_version), (1, 2)
        )
        await self.session.commit()

        for table, id_column, row_id in (
            (
                LeoFTAnamnesisSnapshot,
                LeoFTAnamnesisSnapshot.snapshot_id,
                snapshot.snapshot_id,
            ),
            (
                LeoFTGenerationAttempt,
                LeoFTGenerationAttempt.attempt_id,
                attempt.attempt_id,
            ),
            (
                LeoFTGenerationCheckpoint,
                LeoFTGenerationCheckpoint.checkpoint_id,
                checkpoint_1.checkpoint_id,
            ),
        ):
            with self.assertRaises(DBAPIError):
                async with self.session.begin_nested():
                    await self.session.execute(
                        update(table)
                        .where(id_column == row_id)
                        .values(created_at=self.now)
                    )
                    await self.session.flush()

        other_company = uuid4()
        other_student = uuid4()
        self.session.add(
            Empresa(id=other_company, nome_empresa="Other synthetic tenant")
        )
        self.session.add(
            CRMLead(
                id=other_student,
                empresa_id=other_company,
                nome_contato="Other synthetic student",
                tags=[],
                dados_adicionais={},
                status_atendimento="aberto",
            )
        )
        await self.session.commit()
        other_repository = LeoFTGenerationRepository(
            self.session, company_id=other_company
        )
        self.assertIsNone(await other_repository.get_job(eligible.generation_job_id))
        self.assertIsNone(await other_repository.get_task(tasks[1].session_task_id))

        other_policy = GenerationPolicy(
            environment=Environment.E2E,
            e2e_allowlist=E2EAllowlist(
                company_ids={other_company},
                student_ids={other_student},
                contact_ids={other_student},
                product_ids={CANONICAL_PRODUCT_ID},
            ),
        )
        other_service = LeoFTGenerationService(
            other_repository, policy=other_policy, clock=lambda: self.now
        )
        other_payment = payment_data.model_copy(
            update={
                "event_id": uuid4(),
                "company_id": other_company,
                "contact_id": other_student,
                "student_id": other_student,
                "correlation_id": uuid4(),
            }
        )
        await other_service.register_payment_event_idempotent(other_payment)
        cross_tenant_reversal = other_payment.model_copy(
            update={
                "event_id": uuid4(),
                "event_type": PaymentEventType.CANCELLED,
                "payment_status": PaymentStatus.CANCELLED,
                "provider_event_id": f"provider-{uuid4()}",
                "reverses_event_id": payment_data.event_id,
                "received_at": self.now + timedelta(seconds=1),
            }
        )
        cross_tenant_reversal = cross_tenant_reversal.model_copy(
            update={
                "idempotency_key": payment_event_idempotency_key(
                    provider=cross_tenant_reversal.provider,
                    provider_event_id=cross_tenant_reversal.provider_event_id,
                    payment_id=cross_tenant_reversal.payment_id,
                    status=PaymentStatus.CANCELLED.value,
                )
            }
        )
        with self.assertRaises(DomainValidationError):
            await other_service.register_payment_event_idempotent(cross_tenant_reversal)

    async def test_waiting_blocked_cancelled_and_duplicate_conflict(self) -> None:
        lead = await self.repository.get_lead(self.student_id, lock=True)
        lead.dados_adicionais = {}
        flag_modified(lead, "dados_adicionais")
        payment_data = self._payment(order_id="order-wait")
        await self.service.register_payment_event_idempotent(payment_data)
        purchase = PurchaseKey(
            company_id=self.company_id,
            student_id=self.student_id,
            contact_id=self.student_id,
            order_id="order-wait",
            environment=Environment.E2E,
        )
        waiting = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase,
            trigger="payment",
            correlation_id=payment_data.correlation_id,
        )
        self.assertEqual(waiting.status, EligibilityStatus.WAITING_FOR_ANAMNESIS)

        lead.dados_adicionais = {"anamneses": {"leoft": _anamnesis()}}
        flag_modified(lead, "dados_adicionais")
        no_payment = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase.model_copy(update={"order_id": "missing-order"}),
            trigger="anamnesis",
            correlation_id=uuid4(),
        )
        self.assertEqual(no_payment.status, EligibilityStatus.WAITING_FOR_PAYMENT)
        blocked = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase.model_copy(update={"product_id": "not-leoft"}),
            trigger="retry",
            correlation_id=uuid4(),
        )
        self.assertEqual(blocked.status, EligibilityStatus.BLOCKED)

        with self.assertRaises(IdempotencyConflictError):
            await self.service.register_payment_event_idempotent(
                payment_data.model_copy(update={"raw_event_hash": "f" * 64})
            )

        cancellation = self._payment(
            order_id="order-wait",
            event_type=PaymentEventType.CANCELLED,
            status=PaymentStatus.CANCELLED,
            received_at=self.now + timedelta(seconds=1),
            reverses_event_id=payment_data.event_id,
        )
        await self.service.register_payment_event_idempotent(cancellation)
        cancelled = await self.service.evaluate_training_generation_eligibility(
            purchase=purchase,
            trigger="cancellation",
            correlation_id=cancellation.correlation_id,
        )
        self.assertEqual(cancelled.status, EligibilityStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
