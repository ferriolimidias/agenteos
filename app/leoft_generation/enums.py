"""Typed values used by the LeoFT generation domain."""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    E2E = "e2e"
    STAGING = "staging"
    PRODUCTION = "production"


class PaymentEventType(StrEnum):
    CONFIRMED = "leoft.payment_confirmed.v1"
    CANCELLED = "leoft.payment_cancelled.v1"
    REFUNDED = "leoft.payment_refunded.v1"


class PaymentStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentProcessingStatus(StrEnum):
    RECEIVED = "received"
    WAITING_FOR_ANAMNESIS = "waiting_for_anamnesis"
    ELIGIBLE = "eligible"
    JOB_CREATED = "job_created"
    IGNORED = "ignored"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    WAITING_FOR_PAYMENT = "waiting_for_payment"
    WAITING_FOR_ANAMNESIS = "waiting_for_anamnesis"
    ALREADY_REQUESTED = "already_requested"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class GenerationJobStatus(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    PLANNING = "planning"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationTaskStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    VALIDATION_FAILED = "validation_failed"
    PROVIDER_FAILED = "provider_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class TrainingPlanStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class TrainingPlanVersionStatus(StrEnum):
    BUILDING = "building"
    VALIDATING = "validating"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


CANONICAL_PRODUCT_ID = "leoft_metodo_hibrido"
TRAINING_PLAN_SCHEMA_VERSION = "leoft.training-plan.v2"
