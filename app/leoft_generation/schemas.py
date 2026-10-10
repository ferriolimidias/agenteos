"""Strict boundary and result schemas for LeoFT plan generation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    CANONICAL_PRODUCT_ID,
    TRAINING_PLAN_SCHEMA_VERSION,
    EligibilityStatus,
    Environment,
    GenerationAttemptStatus,
    PaymentEventType,
    PaymentStatus,
)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class PaymentEventCreate(StrictSchema):
    event_id: UUID
    event_type: PaymentEventType = PaymentEventType.CONFIRMED
    occurred_at: datetime
    received_at: datetime
    company_id: UUID
    contact_id: UUID
    student_id: UUID
    product_id: str = CANONICAL_PRODUCT_ID
    order_id: str = Field(min_length=1, max_length=255)
    payment_id: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=80)
    provider_event_id: str | None = Field(default=None, min_length=1, max_length=255)
    amount: Decimal = Field(gt=Decimal("0"), max_digits=18, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    payment_status: PaymentStatus = PaymentStatus.CONFIRMED
    environment: Environment
    correlation_id: UUID
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reverses_event_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _occurred_utc = field_validator("occurred_at")(_utc_aware)
    _received_utc = field_validator("received_at")(_utc_aware)

    @field_validator("order_id", "payment_id", "provider", "product_id")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("provider_event_id")
    @classmethod
    def strip_optional_nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> "PaymentEventCreate":
        if self.received_at < self.occurred_at:
            raise ValueError("received_at must not precede occurred_at")
        expected = {
            PaymentEventType.CONFIRMED: PaymentStatus.CONFIRMED,
            PaymentEventType.CANCELLED: PaymentStatus.CANCELLED,
            PaymentEventType.REFUNDED: PaymentStatus.REFUNDED,
        }[self.event_type]
        if self.payment_status != expected:
            raise ValueError("event_type and payment_status disagree")
        if (
            self.event_type == PaymentEventType.CONFIRMED
            and self.reverses_event_id is not None
        ):
            raise ValueError("confirmed event cannot reverse another event")
        if (
            self.event_type != PaymentEventType.CONFIRMED
            and self.reverses_event_id is None
        ):
            raise ValueError("cancellation/refund must reference the confirmed event")
        return self


class E2EAllowlist(StrictSchema):
    company_ids: frozenset[UUID] = frozenset()
    student_ids: frozenset[UUID] = frozenset()
    contact_ids: frozenset[UUID] = frozenset()
    product_ids: frozenset[str] = frozenset()

    def permits(
        self, *, company_id: UUID, student_id: UUID, contact_id: UUID, product_id: str
    ) -> bool:
        return bool(
            self.company_ids
            and self.student_ids
            and self.contact_ids
            and self.product_ids
            and company_id in self.company_ids
            and student_id in self.student_ids
            and contact_id in self.contact_ids
            and product_id in self.product_ids
        )


class GenerationPolicy(StrictSchema):
    environment: Environment
    active_product_ids: frozenset[str] = frozenset({CANONICAL_PRODUCT_ID})
    product_amounts: dict[str, Decimal] = Field(
        default_factory=lambda: {CANONICAL_PRODUCT_ID: Decimal("97")}
    )
    product_currencies: dict[str, str] = Field(
        default_factory=lambda: {CANONICAL_PRODUCT_ID: "BRL"}
    )
    requested_duration_weeks: int = Field(default=4, ge=1, le=52)
    sessions_per_week: int = Field(default=3, ge=1, le=14)
    maximum_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=120, ge=1, le=3600)
    e2e_allowlist: E2EAllowlist | None = None

    @model_validator(mode="after")
    def require_e2e_allowlist(self) -> "GenerationPolicy":
        if self.environment == Environment.E2E and self.e2e_allowlist is None:
            raise ValueError("e2e requires an explicit allowlist")
        return self


class AnamnesisSnapshotData(StrictSchema):
    anamnesis_id: str = Field(min_length=1, max_length=255)
    anamnesis_version: str = Field(min_length=1, max_length=80)
    goal: str | None = None
    experience_level: str | None = None
    age: int | None = Field(default=None, ge=1, le=120)
    biological_data: dict[str, Any] = Field(default_factory=dict)
    relevant_measurements: dict[str, Any] = Field(default_factory=dict)
    weekly_frequency: int | None = Field(default=None, ge=1, le=14)
    available_session_duration: int | None = Field(default=None, ge=1, le=1440)
    available_equipment: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    injuries: list[str] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    restrictions: list[str] = Field(default_factory=list)
    product_rules: dict[str, Any] = Field(default_factory=dict)
    requested_duration_weeks: int = Field(ge=1, le=52)
    sessions_per_week: int = Field(ge=1, le=14)
    additional_answers: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "leoft.anamnesis-snapshot.v1"
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class EligibilityResult(StrictSchema):
    status: EligibilityStatus
    generation_job_id: UUID | None = None
    anamnesis_snapshot_id: UUID | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")


class PurchaseKey(StrictSchema):
    company_id: UUID
    student_id: UUID
    contact_id: UUID
    product_id: str = CANONICAL_PRODUCT_ID
    order_id: str = Field(min_length=1, max_length=255)
    environment: Environment


class SessionBlueprint(StrictSchema):
    week_number: int = Field(ge=1, le=52)
    session_number: int = Field(ge=1, le=14)
    objective: str = Field(min_length=1, max_length=500)
    phase_number: int = Field(default=1, ge=1)
    sequence: int = Field(ge=1)
    session_code: str | None = Field(default=None, pattern=r"^W\d{2}S\d{2}$")

    @model_validator(mode="after")
    def check_code(self) -> "SessionBlueprint":
        expected = f"W{self.week_number:02d}S{self.session_number:02d}"
        if self.session_code is not None and self.session_code != expected:
            raise ValueError("session_code must be deterministic")
        return self


class TrainingBlueprint(StrictSchema):
    schema_version: str = TRAINING_PLAN_SCHEMA_VERSION
    sessions: list[SessionBlueprint] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_coordinates(self) -> "TrainingBlueprint":
        coordinates = [
            (item.week_number, item.session_number) for item in self.sessions
        ]
        codes = [
            item.session_code
            for item in self.sessions
            if item.session_code is not None
        ]
        sequences = [item.sequence for item in self.sessions]
        if len(coordinates) != len(set(coordinates)) or len(codes) != len(set(codes)):
            raise ValueError("blueprint sessions must be unique")
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise ValueError("session sequence must be contiguous from 1")
        return self


class AttemptCreate(StrictSchema):
    stage: str = Field(min_length=1, max_length=80)
    attempt_type: str = Field(min_length=1, max_length=50)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    model_version: str | None = Field(default=None, max_length=160)
    provider_request_id: str | None = Field(default=None, max_length=255)
    prompt_version: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=1, max_length=120)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=255)
    correlation_id: UUID
    started_at: datetime

    _started_utc = field_validator("started_at")(_utc_aware)


class AttemptCompletion(StrictSchema):
    status: GenerationAttemptStatus
    response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), max_digits=18, decimal_places=8
    )
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    latency_ms: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=120)
    error_summary: str | None = Field(default=None, max_length=1000)
    validation_result: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    repair_instructions: list[dict[str, Any]] = Field(default_factory=list)
    finished_at: datetime | None = None

    @field_validator("finished_at")
    @classmethod
    def finished_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_aware(value) if value is not None else None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AttemptCompletion":
        if self.status == GenerationAttemptStatus.STARTED:
            if self.finished_at is not None:
                raise ValueError("started attempt cannot have finished_at")
            return self
        if self.finished_at is None:
            raise ValueError("terminal attempt requires finished_at")
        if (
            self.status == GenerationAttemptStatus.SUCCEEDED
            and self.response_hash is None
        ):
            raise ValueError("succeeded attempt requires response_hash")
        if self.status != GenerationAttemptStatus.SUCCEEDED and not self.error_code:
            raise ValueError("unsuccessful attempt requires error_code")
        return self


_PII_KEY_PATTERN = re.compile(
    r"(^|_)(name|nome|email|e_mail|phone|telefone|whatsapp|instagram|token|password|senha|cpf|document|documento|address|endereco|user_agent|fbp|fbc|fbclid|gclid|utm)(_|$)",
    re.IGNORECASE,
)


def contains_pii_key(key: object) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower()
    return bool(_PII_KEY_PATTERN.search(normalized))
