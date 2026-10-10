"""Strict HTTP contracts for the Hermes -> AgentOS generation API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.leoft_generation.enums import GenerationAttemptStatus
from app.leoft_generation.schemas import TrainingBlueprint


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BlueprintPutRequest(ApiSchema):
    target_version_id: UUID
    expected_row_version: int = Field(ge=0)
    expected_checkpoint_version: int = Field(ge=0)
    blueprint_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    blueprint: TrainingBlueprint
    schema_version: Literal["leoft.training-plan.v2"]
    model_provider: str = Field(min_length=1, max_length=120)
    model_name: str = Field(min_length=1, max_length=180)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=8)
    correlation_id: UUID


class TaskInitializeRequest(ApiSchema):
    expected_row_version: int = Field(ge=0)


class LeaseRequest(ApiSchema):
    lease_owner: str = Field(min_length=1, max_length=160)


class LeaseMutationRequest(LeaseRequest):
    expected_row_version: int = Field(ge=0)


class AttemptPostRequest(ApiSchema):
    expected_task_row_version: int = Field(ge=0)
    attempt_type: Literal["blueprint", "generation", "validation", "repair"]
    stage: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=1, max_length=120)
    status: GenerationAttemptStatus
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=180)
    model_version: str | None = Field(default=None, max_length=180)
    provider_request_id: str | None = Field(default=None, max_length=255)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(
        default=Decimal(0), ge=0, max_digits=18, decimal_places=8
    )
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    latency_ms: int = Field(default=0, ge=0)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    validation_result: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    repair_instructions: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=120)
    error_summary: str | None = Field(default=None, max_length=1000)
    started_at: datetime
    finished_at: datetime | None = None
    correlation_id: UUID

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def status_fields_match(self) -> AttemptPostRequest:
        if self.status == GenerationAttemptStatus.STARTED:
            if self.finished_at is not None:
                raise ValueError("started attempt cannot be finished")
        elif self.finished_at is None:
            raise ValueError("terminal attempt requires finished_at")
        if (
            self.status == GenerationAttemptStatus.SUCCEEDED
            and self.response_hash is None
        ):
            raise ValueError("succeeded attempt requires response_hash")
        if (
            self.status
            not in {GenerationAttemptStatus.STARTED, GenerationAttemptStatus.SUCCEEDED}
            and not self.error_code
        ):
            raise ValueError("failed attempt requires error_code")
        return self


class ExercisePrescription(ApiSchema):
    external_exercise_id: str = Field(min_length=1, max_length=160)
    order: int = Field(ge=1)
    catalog_id: str = Field(min_length=1, max_length=255)
    media_id: str = Field(min_length=1, max_length=255)
    catalog_version: str = Field(min_length=1, max_length=120)
    prescription_mode: Literal["repetitions", "duration", "interval"]
    sets: int | None = Field(default=None, ge=1)
    repetitions: str | None = Field(default=None, min_length=1, max_length=80)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int = Field(default=0, ge=0)
    intensity: str | None = Field(default=None, max_length=160)
    tempo: str | None = Field(default=None, max_length=80)
    load_guidance: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)
    progression_rule: dict[str, Any] | None = None
    substitutions: list[dict[str, str]] = Field(default_factory=list)
    selection_reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def one_prescription_mode(self) -> ExercisePrescription:
        if self.prescription_mode == "repetitions":
            if (
                not self.sets
                or not self.repetitions
                or self.duration_seconds is not None
            ):
                raise ValueError(
                    "repetitions mode requires sets/repetitions and no duration"
                )
        elif self.prescription_mode == "duration":
            if not self.duration_seconds or self.repetitions is not None:
                raise ValueError("duration mode requires duration and no repetitions")
        elif (
            self.repetitions is not None
            or self.duration_seconds is not None
            or not self.progression_rule
        ):
            raise ValueError("interval mode requires progression_rule only")
        for item in self.substitutions:
            if set(item) != {"catalog_id", "media_id"} or not all(
                str(value).strip() for value in item.values()
            ):
                raise ValueError("substitutions must contain catalog_id/media_id pairs")
        return self


class SessionPutRequest(ApiSchema):
    target_version_id: UUID
    week_number: int = Field(ge=1, le=52)
    session_number: int = Field(ge=1, le=14)
    session_code: str = Field(pattern=r"^W\d{2}S\d{2}$")
    sequence: int = Field(ge=1)
    expected_row_version: int = Field(ge=0)
    lease_owner: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=1000)
    estimated_duration_minutes: int = Field(ge=1, le=1440)
    day_label: str | None = Field(default=None, max_length=120)
    session_type: str | None = Field(default=None, max_length=120)
    warmup: str | None = Field(default=None, max_length=4000)
    cooldown: str | None = Field(default=None, max_length=4000)
    notes: dict[str, Any]
    week_objective: str | None = Field(default=None, max_length=1000)
    progression_notes: str | None = Field(default=None, max_length=2000)
    exercises: list[ExercisePrescription] = Field(min_length=1)
    validation: dict[str, Any]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempt_id: UUID
    correlation_id: UUID

    @model_validator(mode="after")
    def exercise_order_is_contiguous(self) -> SessionPutRequest:
        orders = [item.order for item in self.exercises]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("exercise order must be contiguous from 1")
        if len({item.external_exercise_id for item in self.exercises}) != len(
            self.exercises
        ):
            raise ValueError("external_exercise_id must be unique")
        return self


class TaskFailureRequest(LeaseMutationRequest):
    error_code: str = Field(min_length=1, max_length=120)
    error_summary: str = Field(min_length=1, max_length=1000)
    retryable: bool
    attempt_id: UUID
    correlation_id: UUID


class TaskRetryRequest(ApiSchema):
    expected_row_version: int = Field(ge=0)
    lease_owner: str | None = Field(default=None, max_length=160)


class CheckpointPostRequest(ApiSchema):
    current_session_task_id: UUID | None = None
    stage: str | None = Field(default=None, max_length=160)
    blueprint_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    last_attempt_id: UUID | None = None


class CompleteGenerationRequest(ApiSchema):
    expected_row_version: int = Field(ge=0)
    expected_blueprint_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=8)
    correlation_id: UUID


class SubmitReviewRequest(ApiSchema):
    expected_job_row_version: int = Field(ge=0)
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class JobResponse(ApiSchema):
    generation_job_id: UUID
    company_id: UUID
    product_id: str
    status: str
    current_stage: str | None
    row_version: int
    target_version_id: UUID | None
    snapshot_hash: str
    snapshot: dict[str, Any]
    schema_version: str
    correlation_id: UUID
    blueprint: dict[str, Any]
    task_summary: dict[str, int]
    latest_checkpoint: dict[str, Any] | None
    limits: dict[str, Any]


class TaskResponse(ApiSchema):
    session_task_id: UUID
    generation_job_id: UUID
    company_id: UUID
    target_version_id: UUID
    phase_number: int
    week_number: int
    session_number: int
    session_code: str
    objective: str
    sequence: int
    status: str
    attempt_count: int
    maximum_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    row_version: int


class AttemptResponse(ApiSchema):
    attempt_id: UUID
    attempt_number: int
    status: str
    task_row_version: int


class SessionResponse(ApiSchema):
    session_id: UUID
    session_task_id: UUID
    content_hash: str
    task_row_version: int
    exercise_count: int


class CheckpointResponse(ApiSchema):
    checkpoint_id: UUID
    checkpoint_version: int
    state_hash: str
    stage: str


class GenerationCompletionResponse(ApiSchema):
    generation_job_id: UUID
    version_id: UUID
    content_hash: str
    status: str
    row_version: int


class MutationResponse(ApiSchema):
    status: str
    row_version: int
