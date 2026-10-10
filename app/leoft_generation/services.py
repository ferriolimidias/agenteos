"""Application services for the first LeoFT generation vertical slice."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.services.leoft_generation_dispatch_client import (
    GenerationDispatchError,
    GenerationStartPayload,
    HermesGenerationDispatchClient,
)
from db.leoft_models import (
    LeoFTAnamnesisSnapshot,
    LeoFTGenerationAttempt,
    LeoFTGenerationCheckpoint,
    LeoFTGenerationJob,
    LeoFTGenerationSessionTask,
    LeoFTPaymentEvent,
    LeoFTTrainingPlan,
    LeoFTTrainingPlanVersion,
)

from .enums import (
    CANONICAL_PRODUCT_ID,
    TRAINING_PLAN_SCHEMA_VERSION,
    EligibilityStatus,
    Environment,
    GenerationAttemptStatus,
    GenerationJobStatus,
    GenerationTaskStatus,
    PaymentEventType,
    PaymentProcessingStatus,
    PaymentStatus,
)
from .errors import (
    AllowlistDeniedError,
    AttemptsExhaustedError,
    DomainValidationError,
    IdempotencyConflictError,
    LeaseConflictError,
    ResourceNotFoundError,
)
from .repository import LeoFTGenerationRepository
from .schemas import (
    AnamnesisSnapshotData,
    AttemptCompletion,
    AttemptCreate,
    EligibilityResult,
    GenerationPolicy,
    PaymentEventCreate,
    PurchaseKey,
    TrainingBlueprint,
    contains_pii_key,
)
from .state_machine import validate_job_transition, validate_task_transition

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def payment_event_idempotency_key(
    *, provider: str, provider_event_id: str | None, payment_id: str, status: str
) -> str:
    return canonical_sha256(
        {
            "payment_id": payment_id,
            "provider": provider,
            "provider_event_id": provider_event_id,
            "status": status,
        }
    )


def sanitize_pii(value: Any) -> Any:
    """Recursively drop PII/tracking/secret-bearing keys without guessing values."""
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_pii(item)
            for key, item in value.items()
            if not contains_pii_key(key)
        }
    if isinstance(value, list):
        return [sanitize_pii(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_pii(item) for item in value]
    if value is None or isinstance(value, (str, int, bool, Decimal)):
        return value
    if isinstance(value, float):
        return str(value)
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in items if str(item).strip()]


def normalize_anamnesis(
    leoft_payload: Mapping[str, Any], policy: GenerationPolicy
) -> AnamnesisSnapshotData:
    """Turn the legacy lead JSONB submission into a minimal, deterministic snapshot."""
    sanitized_raw = sanitize_pii(dict(leoft_payload))
    if not isinstance(sanitized_raw, dict):
        raise DomainValidationError("invalid anamnesis payload")
    sanitized = cast(dict[str, Any], sanitized_raw)
    answers_value = sanitized.get("respostas")
    answers: dict[str, Any] = (
        cast(dict[str, Any], answers_value)
        if isinstance(answers_value, dict)
        else sanitized
    )
    profile_value = answers.get("trainingProfile")
    profile: dict[str, Any] = (
        cast(dict[str, Any], profile_value) if isinstance(profile_value, dict) else {}
    )
    personal_value = answers.get("personalData")
    personal: dict[str, Any] = (
        cast(dict[str, Any], personal_value) if isinstance(personal_value, dict) else {}
    )
    status_value = answers.get("status")
    status: dict[str, Any] = (
        cast(dict[str, Any], status_value) if isinstance(status_value, dict) else {}
    )
    submitted = bool(sanitized.get("submitted")) or bool(
        status.get("anamneseCompleted")
    )
    if not submitted:
        raise DomainValidationError("anamnesis is not completed")

    version = str(
        sanitized.get("versao_form") or sanitized.get("version") or "unknown"
    ).strip()
    anamnesis_id = str(
        sanitized.get("anamnesis_id") or sanitized.get("id") or "leoft-anamnesis"
    )
    known_profile = {
        "mainGoal",
        "currentLevel",
        "daysPerWeek",
        "timePerSession",
        "equipment",
        "limitations",
        "limitationsDetails",
        "specialCare",
        "specialCareDetails",
        "gender",
        "height",
        "weight",
        "trainingType",
        "trainingPlace",
        "goalPriority",
        "equipmentOther",
    }
    additional_profile = {
        key: value for key, value in profile.items() if key not in known_profile
    }
    additional_answers = {"leoft": additional_profile} if additional_profile else {}
    gender = personal.get("gender") or profile.get("gender")
    height = personal.get("height") or profile.get("height")
    weight = personal.get("weight") or profile.get("weight")
    special_care = _as_list(
        profile.get("specialCareDetails") or profile.get("specialCare")
    )
    limitations = _as_list(profile.get("limitations"))
    details = _as_list(profile.get("limitationsDetails"))

    normalized_content: dict[str, Any] = dict(
        anamnesis_id=anamnesis_id,
        anamnesis_version=version,
        goal=str(profile.get("mainGoal") or "").strip() or None,
        experience_level=str(profile.get("currentLevel") or "").strip() or None,
        age=_as_int(personal.get("age") or profile.get("age")),
        biological_data={"gender": gender} if gender else {},
        relevant_measurements={
            key: value
            for key, value in {"height": height, "weight": weight}.items()
            if value not in (None, "")
        },
        weekly_frequency=_as_int(profile.get("daysPerWeek")),
        available_session_duration=_as_int(profile.get("timePerSession")),
        available_equipment=_as_list(profile.get("equipment"))
        + _as_list(profile.get("equipmentOther")),
        limitations=limitations + details,
        injuries=[],
        preferences={
            key: profile[key]
            for key in ("trainingType", "trainingPlace", "goalPriority")
            if profile.get(key) not in (None, "")
        },
        restrictions=special_care,
        product_rules={"product_id": CANONICAL_PRODUCT_ID},
        requested_duration_weeks=policy.requested_duration_weeks,
        sessions_per_week=policy.sessions_per_week,
        additional_answers=additional_answers,
    )
    normalized_content["schema_version"] = "leoft.anamnesis-snapshot.v1"
    return AnamnesisSnapshotData(
        **normalized_content,
        source_hash=canonical_sha256(normalized_content),
    )


@dataclass(frozen=True)
class DispatchResult:
    status: str
    generation_job_id: UUID
    retryable: bool = False
    code: str | None = None
    response_status: int | None = None
    redispatched: bool = False


BLOCKING_DISPATCH_ERROR_CODES = frozenset(
    {
        "generation_dispatch_unauthorized",
        "generation_dispatch_forbidden",
        "generation_dispatch_idempotency_conflict",
        "generation_dispatch_contract_error",
        "generation_dispatch_http_error",
        "generation_dispatch_invalid_response",
        "generation_dispatch_unconfigured",
        "generation_dispatch_https_required",
    }
)


def generation_callback_base_url() -> str:
    value = str(
        os.getenv("AGENTOS_GENERATION_CALLBACK_BASE_URL")
        or os.getenv("LEOFT_GENERATION_CALLBACK_BASE_URL")
        or os.getenv("LEOFT_APPROVAL_BASE_URL")
        or os.getenv("AGENTOS_PUBLIC_BASE_URL")
        or ""
    ).strip()
    if not value:
        raise DomainValidationError("generation callback base URL is not configured")
    return value.rstrip("/")


def generation_redispatch_stale_after() -> timedelta:
    seconds = int(os.getenv("LEOFT_GENERATION_REDISPATCH_STALE_SECONDS", "600"))
    return timedelta(seconds=max(60, min(seconds, 86400)))


def generation_redispatch_cooldown() -> timedelta:
    seconds = int(os.getenv("LEOFT_GENERATION_REDISPATCH_COOLDOWN_SECONDS", "300"))
    return timedelta(seconds=max(60, min(seconds, 3600)))


class LeoFTGenerationService:
    def __init__(
        self,
        repository: LeoFTGenerationRepository,
        *,
        policy: GenerationPolicy,
        clock: Clock = utc_now,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.clock = clock

    def _validate_scope(
        self,
        *,
        company_id: UUID,
        student_id: UUID,
        contact_id: UUID,
        product_id: str,
        environment: Environment,
    ) -> None:
        if (
            company_id != self.repository.company_id
            or environment != self.policy.environment
        ):
            raise AllowlistDeniedError()
        if product_id not in self.policy.active_product_ids:
            raise AllowlistDeniedError()
        if environment == Environment.E2E:
            allowlist = self.policy.e2e_allowlist
            if allowlist is None or not allowlist.permits(
                company_id=company_id,
                student_id=student_id,
                contact_id=contact_id,
                product_id=product_id,
            ):
                raise AllowlistDeniedError()

    async def register_payment_event_idempotent(
        self, data: PaymentEventCreate
    ) -> LeoFTPaymentEvent:
        self._validate_scope(
            company_id=data.company_id,
            student_id=data.student_id,
            contact_id=data.contact_id,
            product_id=data.product_id,
            environment=data.environment,
        )
        if data.contact_id != data.student_id:
            raise DomainValidationError(
                "v1 requires contact_id and student_id to identify the same CRMLead"
            )
        expected_key = payment_event_idempotency_key(
            provider=data.provider,
            provider_event_id=data.provider_event_id,
            payment_id=data.payment_id,
            status=data.payment_status.value,
        )
        if data.idempotency_key != expected_key:
            raise DomainValidationError("payment idempotency key is not canonical")
        lead = await self.repository.get_lead(data.student_id, lock=True)
        if lead is None:
            raise ResourceNotFoundError("student does not belong to tenant")
        same_id = await self.repository.get_payment_event(data.event_id, lock=True)
        if same_id is not None:
            if (
                same_id.raw_event_hash != data.raw_event_hash
                or same_id.idempotency_key != data.idempotency_key
            ):
                raise IdempotencyConflictError()
            return same_id
        existing = await self.repository.find_payment_event_by_idempotency(
            data.idempotency_key, lock=True
        )
        if existing is not None:
            if existing.raw_event_hash != data.raw_event_hash:
                raise IdempotencyConflictError()
            return existing
        provider_duplicate = (
            await self.repository.find_payment_event_by_provider(
                data.provider, data.provider_event_id
            )
            if data.provider_event_id is not None
            else None
        )
        if provider_duplicate is not None:
            if provider_duplicate.raw_event_hash != data.raw_event_hash:
                raise IdempotencyConflictError()
            return provider_duplicate
        if data.reverses_event_id is not None:
            reversed_event = await self.repository.get_payment_event(
                data.reverses_event_id, lock=True
            )
            if reversed_event is None:
                raise DomainValidationError(
                    "reversed payment event does not belong to tenant"
                )
            same_purchase = (
                reversed_event.event_type == PaymentEventType.CONFIRMED.value
                and reversed_event.payment_status == PaymentStatus.CONFIRMED.value
                and reversed_event.contact_id == data.contact_id
                and reversed_event.student_id == data.student_id
                and reversed_event.product_id == data.product_id
                and reversed_event.order_id == data.order_id
                and reversed_event.payment_id == data.payment_id
                and reversed_event.provider == data.provider
                and reversed_event.environment == data.environment.value
                and reversed_event.currency == data.currency
                and Decimal(reversed_event.amount) == Decimal(data.amount)
            )
            if not same_purchase:
                raise DomainValidationError(
                    "reversal must reference the same confirmed tenant purchase"
                )
        expected_amount = self.policy.product_amounts.get(data.product_id)
        expected_currency = self.policy.product_currencies.get(data.product_id)
        if (
            expected_amount is None
            or Decimal(data.amount) != Decimal(expected_amount)
            or data.currency != expected_currency
        ):
            raise DomainValidationError(
                "payment does not match server-side product rules"
            )
        event = LeoFTPaymentEvent(
            **data.model_dump(mode="python", exclude={"metadata"})
        )
        event.event_metadata = sanitize_pii(data.metadata)
        event.processing_status = PaymentProcessingStatus.RECEIVED.value
        try:
            async with self.repository.session.begin_nested():
                self.repository.add(event)
                await self.repository.flush()
        except IntegrityError as exc:
            winner = await self.repository.find_payment_event_by_idempotency(
                data.idempotency_key
            )
            if winner is None and data.provider_event_id is not None:
                winner = await self.repository.find_payment_event_by_provider(
                    data.provider, data.provider_event_id
                )
            if winner is None or winner.raw_event_hash != data.raw_event_hash:
                raise IdempotencyConflictError() from exc
            return winner
        return event

    async def capture_anamnesis_snapshot(
        self, *, student_id: UUID
    ) -> LeoFTAnamnesisSnapshot:
        lead = await self.repository.get_lead(student_id, lock=True)
        if lead is None:
            raise ResourceNotFoundError("student does not belong to tenant")
        root: dict[str, Any] = (
            cast(dict[str, Any], lead.dados_adicionais)
            if isinstance(lead.dados_adicionais, dict)
            else {}
        )
        anamneses_value = root.get("anamneses")
        anamneses: dict[str, Any] = (
            cast(dict[str, Any], anamneses_value)
            if isinstance(anamneses_value, dict)
            else {}
        )
        payload = anamneses.get("leoft")
        if not isinstance(payload, dict):
            raise DomainValidationError("LeoFT anamnesis is absent")
        source_payload = dict(payload)
        source_payload.setdefault("anamnesis_id", f"leoft-anamnese:{student_id}")
        normalized = normalize_anamnesis(source_payload, self.policy)
        existing = await self.repository.find_snapshot(
            student_id=student_id,
            source_hash=normalized.source_hash,
        )
        if existing is not None:
            return existing
        now = self.clock()
        snapshot = LeoFTAnamnesisSnapshot(
            snapshot_id=uuid.uuid4(),
            company_id=self.repository.company_id,
            student_id=student_id,
            captured_at=now,
            created_at=now,
            **normalized.model_dump(mode="python"),
        )
        try:
            async with self.repository.session.begin_nested():
                self.repository.add(snapshot)
                await self.repository.flush()
        except IntegrityError as exc:
            winner = await self.repository.find_snapshot(
                student_id=student_id,
                source_hash=normalized.source_hash,
            )
            if winner is None:
                raise IdempotencyConflictError(
                    "snapshot conflict without tenant winner"
                ) from exc
            return winner
        return snapshot

    async def evaluate_training_generation_eligibility(
        self, *, purchase: PurchaseKey, trigger: str, correlation_id: UUID
    ) -> EligibilityResult:
        del (
            trigger
        )  # trigger is audit context for the future adapter; it does not alter inputs.
        try:
            self._validate_scope(
                company_id=purchase.company_id,
                student_id=purchase.student_id,
                contact_id=purchase.contact_id,
                product_id=purchase.product_id,
                environment=purchase.environment,
            )
        except AllowlistDeniedError:
            return EligibilityResult(
                status=EligibilityStatus.BLOCKED, reason_code="e2e_allowlist_denied"
            )
        lead = await self.repository.get_lead(purchase.student_id, lock=True)
        if lead is None or purchase.contact_id != purchase.student_id:
            return EligibilityResult(
                status=EligibilityStatus.BLOCKED, reason_code="tenant_student_mismatch"
            )
        payment = await self.repository.find_purchase_payment(
            product_id=purchase.product_id, order_id=purchase.order_id, lock=True
        )
        if payment is None:
            try:
                await self.capture_anamnesis_snapshot(student_id=purchase.student_id)
            except DomainValidationError:
                return EligibilityResult(status=EligibilityStatus.WAITING_FOR_PAYMENT)
            return EligibilityResult(status=EligibilityStatus.WAITING_FOR_PAYMENT)
        if payment.payment_status in {
            PaymentStatus.CANCELLED.value,
            PaymentStatus.REFUNDED.value,
        }:
            payment.processing_status = PaymentProcessingStatus.CANCELLED.value
            payment.processed_at = self.clock()
            return EligibilityResult(status=EligibilityStatus.CANCELLED)
        if payment.payment_status != PaymentStatus.CONFIRMED.value:
            payment.processing_status = PaymentProcessingStatus.IGNORED.value
            payment.processed_at = self.clock()
            return EligibilityResult(
                status=EligibilityStatus.BLOCKED, reason_code="payment_not_confirmed"
            )
        existing = await self.repository.find_job_for_purchase(
            product_id=payment.product_id, order_id=payment.order_id, lock=True
        )
        if existing is not None:
            payment.processing_status = PaymentProcessingStatus.JOB_CREATED.value
            payment.processed_at = self.clock()
            return EligibilityResult(
                status=EligibilityStatus.ALREADY_REQUESTED,
                generation_job_id=existing.generation_job_id,
                anamnesis_snapshot_id=existing.anamnesis_snapshot_id,
            )
        try:
            snapshot = await self.capture_anamnesis_snapshot(
                student_id=purchase.student_id
            )
        except DomainValidationError:
            payment.processing_status = (
                PaymentProcessingStatus.WAITING_FOR_ANAMNESIS.value
            )
            return EligibilityResult(status=EligibilityStatus.WAITING_FOR_ANAMNESIS)
        payment.processing_status = PaymentProcessingStatus.ELIGIBLE.value
        job = await self.create_generation_job_idempotent(
            payment_event=payment, snapshot=snapshot, correlation_id=correlation_id
        )
        payment.processing_status = PaymentProcessingStatus.JOB_CREATED.value
        payment.processed_at = self.clock()
        return EligibilityResult(
            status=EligibilityStatus.ELIGIBLE,
            generation_job_id=job.generation_job_id,
            anamnesis_snapshot_id=snapshot.snapshot_id,
        )

    async def create_generation_job_idempotent(
        self,
        *,
        payment_event: LeoFTPaymentEvent,
        snapshot: LeoFTAnamnesisSnapshot,
        correlation_id: UUID,
    ) -> LeoFTGenerationJob:
        if (
            payment_event.company_id != self.repository.company_id
            or snapshot.company_id != self.repository.company_id
        ):
            raise AllowlistDeniedError()
        if payment_event.student_id != snapshot.student_id:
            raise DomainValidationError("payment and snapshot student mismatch")
        existing_purchase = await self.repository.find_job_for_purchase(
            product_id=payment_event.product_id,
            order_id=payment_event.order_id,
            lock=True,
        )
        if existing_purchase is not None:
            return existing_purchase
        key = canonical_sha256(
            {
                "company_id": self.repository.company_id,
                "product_id": payment_event.product_id,
                "payment_event_id": payment_event.id,
                "snapshot_hash": snapshot.source_hash,
                "schema_version": TRAINING_PLAN_SCHEMA_VERSION,
            }
        )
        existing = await self.repository.find_job_by_idempotency(key, lock=True)
        if existing is not None:
            if (
                existing.payment_event_id != payment_event.id
                or existing.anamnesis_snapshot_id != snapshot.snapshot_id
            ):
                raise IdempotencyConflictError()
            return existing

        now = self.clock()
        legacy_now = now.astimezone(timezone.utc).replace(tzinfo=None)
        plan_id, version_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        empty_hash = canonical_sha256({})
        plan = LeoFTTrainingPlan(
            id=plan_id,
            empresa_id=self.repository.company_id,
            lead_id=payment_event.student_id,
            product_id=payment_event.product_id,
            generation_job_id=job_id,
            current_version_id=version_id,
            payment_reference=payment_event.payment_id,
            payment_status=payment_event.payment_status,
            payment_provider=payment_event.provider,
            status="generating",
            plan_json={},
            created_at=legacy_now,
            updated_at=legacy_now,
        )
        version = LeoFTTrainingPlanVersion(
            id=version_id,
            plan_id=plan_id,
            version_number=1,
            schema_version=TRAINING_PLAN_SCHEMA_VERSION,
            source="leoft_generation",
            source_snapshot_id=snapshot.snapshot_id,
            blueprint={},
            status="building",
            snapshot_json={},
            checksum=empty_hash,
            created_at=legacy_now,
            updated_at=now,
            created_by="leoft_generation",
        )
        job = LeoFTGenerationJob(
            generation_job_id=job_id,
            company_id=self.repository.company_id,
            student_id=payment_event.student_id,
            product_id=payment_event.product_id,
            order_id=payment_event.order_id,
            payment_event_id=payment_event.id,
            anamnesis_snapshot_id=snapshot.snapshot_id,
            plan_id=plan_id,
            target_version_id=version_id,
            status=GenerationJobStatus.PENDING.value,
            current_stage="created",
            requested_duration_weeks=snapshot.requested_duration_weeks,
            sessions_per_week=snapshot.sessions_per_week,
            correlation_id=correlation_id,
            idempotency_key=key,
            maximum_attempts=self.policy.maximum_attempts,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self.repository.session.begin_nested():
                self.repository.add(plan)
                self.repository.add(version)
                self.repository.add(job)
                await self.repository.flush()
        except IntegrityError as exc:
            winner = await self.repository.find_job_for_purchase(
                product_id=payment_event.product_id,
                order_id=payment_event.order_id,
            )
            if winner is None:
                raise IdempotencyConflictError(
                    "generation job conflict without tenant winner"
                ) from exc
            return winner
        return job

    async def build_generation_start_payload(
        self,
        *,
        job: LeoFTGenerationJob,
        callback_base_url: str | None = None,
    ) -> GenerationStartPayload:
        if job.company_id != self.repository.company_id:
            raise AllowlistDeniedError()
        if job.target_version_id is None:
            raise ResourceNotFoundError("generation target version not found")
        snapshot = await self.repository.get_snapshot(job.anamnesis_snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("generation snapshot not found")
        version = await self.repository.session.scalar(
            select(LeoFTTrainingPlanVersion).where(
                LeoFTTrainingPlanVersion.id == job.target_version_id,
                LeoFTTrainingPlanVersion.plan_id == job.plan_id,
            )
        )
        if version is None:
            raise ResourceNotFoundError("generation target version not found")
        return GenerationStartPayload(
            generation_job_id=job.generation_job_id,
            company_id=job.company_id,
            target_version_id=job.target_version_id,
            schema_version=str(version.schema_version or TRAINING_PLAN_SCHEMA_VERSION),
            correlation_id=job.correlation_id,
            callback_base_url=callback_base_url or generation_callback_base_url(),
            requested_duration_weeks=int(job.requested_duration_weeks),
            sessions_per_week=int(job.sessions_per_week),
            snapshot_hash=str(snapshot.source_hash),
            idempotency_key=str(job.idempotency_key),
        )

    async def dispatch_generation_job_to_hermes(
        self,
        *,
        job_id: UUID,
        callback_base_url: str | None = None,
        client: HermesGenerationDispatchClient | None = None,
    ) -> DispatchResult:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        if job.status == GenerationJobStatus.DISPATCHING.value:
            return DispatchResult(
                status="accepted",
                generation_job_id=job.generation_job_id,
                code="generation_dispatch_already_accepted",
                response_status=202,
            )
        if job.status != GenerationJobStatus.PENDING.value:
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_dispatch_job_not_pending",
            )
        if job.last_error_code in BLOCKING_DISPATCH_ERROR_CODES:
            return DispatchResult(
                status="blocked",
                generation_job_id=job.generation_job_id,
                code=str(job.last_error_code),
            )

        payload = await self.build_generation_start_payload(
            job=job, callback_base_url=callback_base_url
        )
        now = self.clock()
        dispatch_client = client or HermesGenerationDispatchClient()
        try:
            response = await dispatch_client.start_job(payload)
        except GenerationDispatchError as exc:
            job.last_error_code = exc.code[:120]
            job.last_error_summary = sanitize_pii(
                f"hermes dispatch failed retryable={exc.retryable} status={exc.status_code}"
            )[:1000]
            job.row_version = int(job.row_version or 0) + 1
            job.updated_at = self.clock()
            await self.repository.flush()
            return DispatchResult(
                status="retryable_error" if exc.retryable else "blocked",
                generation_job_id=job.generation_job_id,
                retryable=exc.retryable,
                code=exc.code,
                response_status=exc.status_code,
            )

        job.status = validate_job_transition(
            job.status, GenerationJobStatus.DISPATCHING
        ).value
        job.current_stage = "dispatch_accepted"
        job.started_at = job.started_at or now
        job.last_dispatch_attempt_at = now
        job.dispatch_attempt_count = int(getattr(job, "dispatch_attempt_count", 0) or 0) + 1
        job.hermes_request_id = str(
            response.get("request_id")
            or response.get("dispatch_id")
            or response.get("generation_job_id")
            or job.generation_job_id
        )[:255]
        job.last_error_code = None
        job.last_error_summary = None
        job.row_version = int(job.row_version or 0) + 1
        job.updated_at = now
        await self.repository.flush()
        return DispatchResult(
            status="accepted",
            generation_job_id=job.generation_job_id,
            response_status=202,
        )

    async def redispatch_generation_job_to_hermes(
        self,
        *,
        job_id: UUID,
        reason: str,
        callback_base_url: str | None = None,
        client: HermesGenerationDispatchClient | None = None,
        stale_after: timedelta | None = None,
        cooldown: timedelta | None = None,
    ) -> DispatchResult:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        if job.company_id != self.repository.company_id:
            raise AllowlistDeniedError()
        if job.status != GenerationJobStatus.DISPATCHING.value:
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_job_not_dispatching",
            )
        if job.current_stage != "dispatch_accepted":
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_stage_not_dispatch_accepted",
            )
        if job.status in {
            GenerationJobStatus.AWAITING_REVIEW.value,
            GenerationJobStatus.APPROVED.value,
            GenerationJobStatus.PUBLISHED.value,
            GenerationJobStatus.FAILED.value,
            GenerationJobStatus.CANCELLED.value,
        }:
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_terminal_or_review_state",
            )
        version = await self.repository.session.scalar(
            select(LeoFTTrainingPlanVersion).where(
                LeoFTTrainingPlanVersion.id == job.target_version_id,
                LeoFTTrainingPlanVersion.plan_id == job.plan_id,
            )
        )
        if version is None or str(version.status or "") != "building":
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_invalid_target_version",
            )
        if bool(version.blueprint):
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_blueprint_present",
            )
        if await self.repository.list_tasks(job_id, lock=True):
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_tasks_already_initialized",
            )
        snapshot = await self.repository.get_snapshot(job.anamnesis_snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("generation snapshot not found")

        now = self.clock()
        stale_after = stale_after or generation_redispatch_stale_after()
        cooldown = cooldown or generation_redispatch_cooldown()
        last_attempt = (
            getattr(job, "last_dispatch_attempt_at", None)
            or job.started_at
            or job.updated_at
            or job.created_at
        )
        if last_attempt is None or now - last_attempt < stale_after:
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_not_stale",
            )
        last_redispatch = getattr(job, "last_dispatch_attempt_at", None)
        if (
            last_redispatch is not None
            and last_redispatch != job.started_at
            and now - last_redispatch < cooldown
        ):
            return DispatchResult(
                status="skipped",
                generation_job_id=job.generation_job_id,
                code="generation_redispatch_cooldown_active",
            )

        payload = await self.build_generation_start_payload(
            job=job, callback_base_url=callback_base_url
        )
        dispatch_client = client or HermesGenerationDispatchClient()
        job.last_dispatch_attempt_at = now
        job.dispatch_attempt_count = int(getattr(job, "dispatch_attempt_count", 0) or 0) + 1
        job.last_redispatch_reason = sanitize_pii(reason)[:255]
        job.row_version = int(job.row_version or 0) + 1
        job.updated_at = now
        await self.repository.flush()
        try:
            response = await dispatch_client.start_job(payload)
        except GenerationDispatchError as exc:
            job.last_error_code = exc.code[:120]
            job.last_error_summary = sanitize_pii(
                f"hermes redispatch failed retryable={exc.retryable} status={exc.status_code}"
            )[:1000]
            job.row_version = int(job.row_version or 0) + 1
            job.updated_at = self.clock()
            await self.repository.flush()
            return DispatchResult(
                status="retryable_error" if exc.retryable else "blocked",
                generation_job_id=job.generation_job_id,
                retryable=exc.retryable,
                code=exc.code,
                response_status=exc.status_code,
                redispatched=True,
            )

        job.status = GenerationJobStatus.DISPATCHING.value
        job.current_stage = "dispatch_accepted"
        job.hermes_request_id = str(
            response.get("request_id")
            or response.get("dispatch_id")
            or response.get("generation_job_id")
            or job.generation_job_id
        )[:255]
        job.last_error_code = None
        job.last_error_summary = None
        job.row_version = int(job.row_version or 0) + 1
        job.updated_at = self.clock()
        await self.repository.flush()
        return DispatchResult(
            status="accepted",
            generation_job_id=job.generation_job_id,
            response_status=202,
            redispatched=True,
        )

    async def transition_job(
        self,
        *,
        job_id: UUID,
        target: GenerationJobStatus,
        current_stage: str | None = None,
    ) -> LeoFTGenerationJob:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        target = validate_job_transition(job.status, target)
        now = self.clock()
        job.status = target.value
        job.current_stage = current_stage or target.value
        job.row_version = int(job.row_version or 0) + 1
        job.updated_at = now
        if target == GenerationJobStatus.DISPATCHING and job.started_at is None:
            job.started_at = now
        elif target == GenerationJobStatus.PUBLISHED:
            job.completed_at = now
        elif target == GenerationJobStatus.FAILED:
            job.failed_at = now
        return job

    async def create_tasks_from_blueprint(
        self, *, job_id: UUID, blueprint: TrainingBlueprint
    ) -> list[LeoFTGenerationSessionTask]:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        existing = await self.repository.list_tasks(job_id, lock=True)
        expected = [
            (item.session_code, item.objective, item.sequence)
            for item in blueprint.sessions
        ]
        if existing:
            actual = [
                (item.session_code, item.objective, item.sequence) for item in existing
            ]
            if actual != expected:
                raise IdempotencyConflictError("blueprint differs from persisted tasks")
            return existing
        now = self.clock()
        tasks = [
            LeoFTGenerationSessionTask(
                session_task_id=uuid.uuid4(),
                company_id=self.repository.company_id,
                generation_job_id=job.generation_job_id,
                target_version_id=job.target_version_id,
                phase_number=item.phase_number,
                week_number=item.week_number,
                session_number=item.session_number,
                session_code=item.session_code,
                objective=item.objective,
                sequence=item.sequence,
                status=GenerationTaskStatus.PENDING.value,
                maximum_attempts=self.policy.maximum_attempts,
                created_at=now,
                updated_at=now,
            )
            for item in blueprint.sessions
        ]
        try:
            async with self.repository.session.begin_nested():
                for task in tasks:
                    self.repository.add(task)
                await self.repository.flush()
        except IntegrityError as exc:
            winner = await self.repository.list_tasks(job_id, lock=True)
            actual = [
                (item.session_code, item.objective, item.sequence) for item in winner
            ]
            if actual != expected:
                raise IdempotencyConflictError(
                    "task conflict without matching blueprint"
                ) from exc
            return winner
        return tasks

    async def acquire_next_task(
        self, *, job_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask | None:
        owner = lease_owner.strip()
        if not owner:
            raise DomainValidationError("lease_owner is required")
        job = await self.repository.get_job(job_id)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        now = self.clock()
        task = await self.repository.next_acquirable_task(job_id, now=now)
        if task is None:
            return None
        if task.status == GenerationTaskStatus.PENDING.value:
            task.status = validate_task_transition(
                task.status, GenerationTaskStatus.GENERATING
            ).value
            task.started_at = task.started_at or now
        task.lease_owner = owner
        task.lease_expires_at = now + timedelta(seconds=self.policy.lease_seconds)
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = now
        return task

    async def next_task(
        self, *, job_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask | None:
        return await self.acquire_next_task(job_id=job_id, lease_owner=lease_owner)

    async def acquire_task_lease(
        self, *, task_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask:
        owner = lease_owner.strip()
        if not owner:
            raise DomainValidationError("lease_owner is required")
        task = await self.repository.get_task(task_id, lock=True)
        if task is None:
            raise ResourceNotFoundError("generation task not found")
        now = self.clock()
        if task.status in {
            GenerationTaskStatus.COMPLETED.value,
            GenerationTaskStatus.FAILED.value,
            GenerationTaskStatus.CANCELLED.value,
        }:
            raise LeaseConflictError("terminal task cannot be acquired")
        if (
            task.lease_owner
            and task.lease_owner != owner
            and task.lease_expires_at
            and task.lease_expires_at > now
        ):
            raise LeaseConflictError()
        if task.status == GenerationTaskStatus.PENDING.value:
            task.status = validate_task_transition(
                task.status, GenerationTaskStatus.GENERATING
            ).value
            task.started_at = task.started_at or now
        task.lease_owner = owner
        task.lease_expires_at = now + timedelta(seconds=self.policy.lease_seconds)
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = now
        return task

    async def renew_task_lease(
        self, *, task_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask:
        task = await self._owned_task(task_id, lease_owner, require_unexpired=True)
        now = self.clock()
        task.lease_expires_at = now + timedelta(seconds=self.policy.lease_seconds)
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = now
        return task

    async def transition_task(
        self, *, task_id: UUID, lease_owner: str, target: GenerationTaskStatus
    ) -> LeoFTGenerationSessionTask:
        task = await self._owned_task(task_id, lease_owner, require_unexpired=True)
        task.status = validate_task_transition(task.status, target).value
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = self.clock()
        return task

    async def release_task_lease(
        self, *, task_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask:
        task = await self._owned_task(task_id, lease_owner, require_unexpired=False)
        task.lease_owner = None
        task.lease_expires_at = None
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = self.clock()
        return task

    async def complete_task(
        self, *, task_id: UUID, lease_owner: str
    ) -> LeoFTGenerationSessionTask:
        task = await self._owned_task(task_id, lease_owner, require_unexpired=True)
        task.status = validate_task_transition(
            task.status, GenerationTaskStatus.COMPLETED
        ).value
        now = self.clock()
        task.completed_at = now
        task.lease_owner = None
        task.lease_expires_at = None
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = now
        return task

    async def fail_task(
        self,
        *,
        task_id: UUID,
        lease_owner: str,
        error_summary: str,
        error_code: str = "generation_failed",
    ) -> LeoFTGenerationSessionTask:
        task = await self._owned_task(task_id, lease_owner, require_unexpired=True)
        target = GenerationTaskStatus.FAILED
        task.status = validate_task_transition(task.status, target).value
        now = self.clock()
        task.last_error_code = error_code[:120]
        task.last_error_summary = sanitize_pii(error_summary)[:1000]
        task.failed_at = now if target == GenerationTaskStatus.FAILED else None
        task.lease_owner = None
        task.lease_expires_at = None
        task.row_version = int(task.row_version or 0) + 1
        task.updated_at = now
        return task

    async def _owned_task(
        self, task_id: UUID, lease_owner: str, *, require_unexpired: bool
    ) -> LeoFTGenerationSessionTask:
        task = await self.repository.get_task(task_id, lock=True)
        if task is None:
            raise ResourceNotFoundError("generation task not found")
        now = self.clock()
        if task.lease_owner != lease_owner or (
            require_unexpired
            and (task.lease_expires_at is None or task.lease_expires_at <= now)
        ):
            raise LeaseConflictError()
        return task

    async def append_attempt(
        self,
        *,
        job_id: UUID,
        task_id: UUID | None,
        request: AttemptCreate,
        completion: AttemptCompletion,
    ) -> LeoFTGenerationAttempt:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        existing = await self.repository.find_attempt_by_idempotency(
            job_id, request.idempotency_key
        )
        if existing is not None:
            if (
                existing.request_hash != request.request_hash
                or existing.response_hash != completion.response_hash
            ):
                raise IdempotencyConflictError()
            return existing
        task = await self.repository.get_task(task_id, lock=True) if task_id else None
        if task_id and task is None:
            raise ResourceNotFoundError("generation task not found")
        number = int(task.attempt_count if task else job.attempt_count or 0) + 1
        maximum = int(task.maximum_attempts if task else job.maximum_attempts or 0)
        if number > maximum:
            raise AttemptsExhaustedError()
        attempt = LeoFTGenerationAttempt(
            attempt_id=uuid.uuid4(),
            company_id=self.repository.company_id,
            generation_job_id=job_id,
            session_task_id=task_id,
            target_version_id=job.target_version_id,
            attempt_number=number,
            total_tokens=completion.input_tokens + completion.output_tokens,
            completed_at=completion.finished_at
            if completion.status == GenerationAttemptStatus.SUCCEEDED
            else None,
            failed_at=completion.finished_at
            if completion.status
            not in {GenerationAttemptStatus.SUCCEEDED, GenerationAttemptStatus.STARTED}
            else None,
            created_at=self.clock(),
            **request.model_dump(mode="python"),
            validation_result=sanitize_pii(completion.validation_result),
            errors=sanitize_pii(completion.errors),
            repair_instructions=sanitize_pii(completion.repair_instructions),
            **completion.model_dump(
                mode="python",
                exclude={
                    "finished_at",
                    "errors",
                    "validation_result",
                    "repair_instructions",
                },
            ),
        )
        self.repository.add(attempt)
        if task is None:
            job.attempt_count = int(job.attempt_count or 0) + 1
        job.input_tokens = int(job.input_tokens or 0) + completion.input_tokens
        job.output_tokens = int(job.output_tokens or 0) + completion.output_tokens
        job.estimated_cost = (
            Decimal(job.estimated_cost or 0) + completion.estimated_cost
        )
        if task is not None:
            task.attempt_count = number
            task.selected_provider = request.provider
            task.selected_model = request.model
            task.input_tokens = int(task.input_tokens or 0) + completion.input_tokens
            task.output_tokens = int(task.output_tokens or 0) + completion.output_tokens
            task.estimated_cost = (
                Decimal(task.estimated_cost or 0) + completion.estimated_cost
            )
        await self.repository.flush()
        return attempt

    async def create_generation_checkpoint(
        self,
        *,
        job_id: UUID,
        current_session_task_id: UUID | None = None,
        current_task_id: UUID | None = None,
        stage: str | None = None,
        blueprint_hash: str | None = None,
        last_attempt_id: UUID | None = None,
    ) -> LeoFTGenerationCheckpoint:
        job = await self.repository.get_job(job_id, lock=True)
        if job is None:
            raise ResourceNotFoundError("generation job not found")
        if current_task_id is not None:
            if (
                current_session_task_id is not None
                and current_session_task_id != current_task_id
            ):
                raise DomainValidationError("conflicting current task identifiers")
            current_session_task_id = current_task_id
        latest = await self.repository.latest_checkpoint(job_id, lock=True)
        checkpoint_version = int(latest.checkpoint_version if latest else 0) + 1
        tasks = await self.repository.list_tasks(job_id)
        completed = [
            str(task.session_task_id)
            for task in tasks
            if task.status == GenerationTaskStatus.COMPLETED.value
        ]
        failed = [
            str(task.session_task_id)
            for task in tasks
            if task.status == GenerationTaskStatus.FAILED.value
        ]
        pending = [
            str(task.session_task_id)
            for task in tasks
            if task.status
            not in {
                GenerationTaskStatus.COMPLETED.value,
                GenerationTaskStatus.FAILED.value,
            }
        ]
        now = self.clock()
        snapshot = await self.repository.get_snapshot(job.anamnesis_snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("generation snapshot not found")
        stage = stage or job.current_stage or job.status
        state = {
            "job_status": job.status,
            "stage": stage,
            "current_session_task_id": current_session_task_id,
            "completed_task_ids": completed,
            "pending_task_ids": pending,
            "failed_task_ids": failed,
            "blueprint_hash": blueprint_hash or canonical_sha256({}),
            "snapshot_hash": snapshot.source_hash,
            "last_attempt_id": last_attempt_id,
        }
        checkpoint = LeoFTGenerationCheckpoint(
            checkpoint_id=uuid.uuid4(),
            company_id=self.repository.company_id,
            generation_job_id=job_id,
            checkpoint_version=checkpoint_version,
            target_version_id=job.target_version_id,
            job_status=job.status,
            stage=stage,
            current_session_task_id=current_session_task_id,
            completed_task_ids=completed,
            pending_task_ids=pending,
            failed_task_ids=failed,
            blueprint_hash=blueprint_hash or canonical_sha256({}),
            snapshot_hash=snapshot.source_hash,
            state_hash=canonical_sha256(state),
            last_attempt_id=last_attempt_id,
            captured_at=now,
            correlation_id=job.correlation_id,
        )
        self.repository.add(checkpoint)
        await self.repository.flush()
        job.latest_checkpoint_id = checkpoint.checkpoint_id
        job.row_version = int(job.row_version or 0) + 1
        for task in tasks:
            task.checkpoint_version = checkpoint_version
        return checkpoint

    async def capture_checkpoint(self, **kwargs: Any) -> LeoFTGenerationCheckpoint:
        """Backward-compatible alias for the explicit domain operation name."""
        return await self.create_generation_checkpoint(**kwargs)
