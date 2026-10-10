"""SQLAlchemy models for the LeoFT PostgreSQL runtime."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

from .database import Base


class LeoFTTrainingPlan(Base):
    __tablename__ = "leoft_training_plans"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id",
            "lead_id",
            "id",
            name="uq_leoft_training_plans_tenant_student_id",
        ),
        CheckConstraint(
            "status IN ('pending_approval','approved','rejected','revision_requested','draft','generating','awaiting_review','published','cancelled')",
            name="ck_leoft_training_plans_status",
        ),
        CheckConstraint(
            "product_id IS NULL OR status IN ('draft','generating','awaiting_review','approved','published','cancelled')",
            name="ck_leoft_plan_v2_status",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "lead_id", "generation_job_id"],
            [
                "leoft_generation_jobs.company_id",
                "leoft_generation_jobs.student_id",
                "leoft_generation_jobs.generation_job_id",
            ],
            name="fk_leoft_plans_tenant_generation_job",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["leoft_training_plan_versions.plan_id", "leoft_training_plan_versions.id"],
            name="fk_leoft_plans_current_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id = Column(
        UUID(as_uuid=True),
        ForeignKey("crm_leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id = Column(String, nullable=True)
    token_reference = Column(String, nullable=True)
    anamnese_token_hash = Column(String, nullable=True)
    anamnese_status = Column(String, nullable=True)
    payment_reference = Column(String, nullable=True, index=True)
    payment_status = Column(String, nullable=True)
    payment_mode = Column(String, nullable=True)
    payment_provider = Column(String, nullable=True)
    product_name = Column(String, nullable=False, default="Método Híbrido LeoFT")
    product_id = Column(String, nullable=True)
    generation_job_id = Column(UUID(as_uuid=True), nullable=True)
    current_version_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String, nullable=False, default="pending_approval", index=True)
    student_name = Column(String, nullable=True)
    student_email = Column(String, nullable=True)
    student_whatsapp = Column(String, nullable=True)
    plan_json = Column(JSONB, nullable=False, default=dict)
    hermes_payload = Column(JSONB, nullable=True)
    hermes_mode = Column(String, nullable=False, default="mock")
    telegram_status = Column(String, nullable=False, default="dry_run")
    telegram_payload = Column(JSONB, nullable=True)
    telegram_sent_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, nullable=True)
    approval_notes = Column(Text, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejected_by = Column(String, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    revision_requested_at = Column(DateTime, nullable=True)
    revision_requested_by = Column(String, nullable=True)
    revision_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTStudent(Base):
    __tablename__ = "leoft_students"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=True)
    whatsapp = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    crm_lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True, index=True)
    assessment_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LeoFTStudentAccess(Base):
    __tablename__ = "leoft_student_accesses"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_students.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    lead_id = Column(
        UUID(as_uuid=True),
        ForeignKey("crm_leads.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    student_email = Column(String, nullable=True)
    student_whatsapp = Column(String, nullable=True)
    login_identifier = Column(String, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    temporary_password_masked = Column(String, nullable=True)
    access_token_hash = Column(String, nullable=False, index=True)
    access_token_masked = Column(String, nullable=False)
    app_url = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    first_login_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class LeoFTTrainingPlanVersion(Base):
    __tablename__ = "leoft_training_plan_versions"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "version_number", name="uq_leoft_plan_version_number"
        ),
        UniqueConstraint("plan_id", "id", name="uq_leoft_plan_versions_plan_id_id"),
        Index("ix_leoft_plan_versions_checksum", "checksum"),
        CheckConstraint(
            "source_snapshot_id IS NULL OR status IN ('building','validating','awaiting_review','approved','rejected','revision_requested','published','superseded','cancelled')",
            name="ck_leoft_plan_versions_v2_status",
        ),
        CheckConstraint(
            "schema_version IS NULL OR btrim(schema_version) <> ''",
            name="ck_leoft_plan_versions_schema_version",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    schema_version = Column(String, nullable=True)
    source = Column(String, nullable=True)
    status = Column(String, nullable=True)
    snapshot_json = Column(JSONB, nullable=False)
    source_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_anamnesis_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=True,
    )
    blueprint = Column(JSONB, nullable=True)
    checksum = Column(String, nullable=False)
    content_hash = Column(String, nullable=True)
    generation_completed_at = Column(DateTime(timezone=True), nullable=True)
    submitted_for_review_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    created_by = Column(String, nullable=True)
    superseded_at = Column(DateTime, nullable=True)


class LeoFTTrainingWeek(Base):
    __tablename__ = "leoft_training_weeks"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "week_number", name="uq_leoft_week_version_number"
        ),
        CheckConstraint(
            "phase_number IS NULL OR (phase_number > 0 AND week_number > 0)",
            name="ck_leoft_weeks_v2_values",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('pending','generating','validating','completed','failed','cancelled')",
            name="ck_leoft_weeks_status",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    week_number = Column(Integer, nullable=False)
    phase_number = Column(Integer, nullable=True)
    objective = Column(Text, nullable=True)
    progression_notes = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    phase = Column(String)
    focus = Column(String)
    position = Column(Integer, nullable=False)
    source_json = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTTrainingSession(Base):
    __tablename__ = "leoft_training_sessions"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "external_session_id",
            name="uq_leoft_session_version_external_id",
        ),
        Index("ix_leoft_training_sessions_week_position", "week_id", "position"),
        CheckConstraint(
            "session_code IS NULL OR (session_code ~ '^W[0-9]{2}S[0-9]{2}$' AND sequence > 0 AND estimated_duration_minutes > 0)",
            name="ck_leoft_sessions_v2_values",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('pending','generating','validating','completed','failed','cancelled')",
            name="ck_leoft_sessions_v2_status",
        ),
        CheckConstraint(
            "estimated_duration_minutes IS NULL OR estimated_duration_minutes > 0",
            name="ck_leoft_sessions_estimated_duration",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    week_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_weeks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_session_id = Column(String, nullable=False)
    session_task_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "leoft_generation_session_tasks.session_task_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    session_code = Column(String, nullable=True)
    objective = Column(Text, nullable=True)
    sequence = Column(Integer, nullable=True)
    estimated_duration_minutes = Column(Integer, nullable=True)
    generation_status = Column("status", String, nullable=True)
    position = Column(Integer, nullable=False)
    day_label = Column(String)
    title = Column(String)
    session_type = Column(String)
    warmup = Column(Text)
    cooldown = Column(Text)
    estimated_duration = Column(String)
    notes = Column(JSONB)
    source_json = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTTrainingExercise(Base):
    __tablename__ = "leoft_training_exercises"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "position", name="uq_leoft_exercise_session_position"
        ),
        CheckConstraint("sets IS NULL OR sets > 0", name="ck_leoft_exercises_sets"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_leoft_exercises_duration_seconds",
        ),
        CheckConstraint(
            "rest_seconds IS NULL OR rest_seconds >= 0",
            name="ck_leoft_exercises_rest_seconds",
        ),
        CheckConstraint(
            'prescription_mode IS NULL OR ("order" > 0 AND rest_seconds >= 0 '
            "AND catalog_id IS NOT NULL AND media_id IS NOT NULL AND catalog_version IS NOT NULL "
            "AND association_validated_at IS NOT NULL AND ((prescription_mode = 'repetitions' "
            "AND sets > 0 AND repetitions IS NOT NULL AND duration_seconds IS NULL) OR "
            "(prescription_mode = 'duration' AND duration_seconds > 0 AND repetitions IS NULL "
            "AND (sets IS NULL OR sets > 0)) OR (prescription_mode = 'interval' "
            "AND progression_rule IS NOT NULL AND repetitions IS NULL AND duration_seconds IS NULL)))",
            name="ck_leoft_exercises_v2_prescription",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_exercise_id = Column(String, nullable=False)
    position = Column(Integer, nullable=False)
    catalog_id = Column(
        String,
        ForeignKey("leoft_catalog_entries.catalog_id", ondelete="RESTRICT"),
        nullable=True,
    )
    media_id = Column(
        String,
        ForeignKey("leoft_catalog_media.media_id", ondelete="RESTRICT"),
        nullable=True,
    )
    catalog_version = Column(
        String,
        ForeignKey("leoft_catalog_versions.version", ondelete="RESTRICT"),
        nullable=True,
    )
    exercise_order = Column("order", Integer, nullable=True)
    name = Column(String, nullable=False)
    muscle_group = Column(String)
    sets = Column(Integer)
    reps = Column(String)
    repetitions = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    rest_seconds = Column(Integer, nullable=True)
    duration = Column(String)
    rest = Column(String)
    intensity = Column(String)
    instructions = Column(Text)
    tempo = Column(String, nullable=True)
    load_guidance = Column(Text, nullable=True)
    exercise_notes = Column("notes", Text, nullable=True)
    progression_rule = Column(JSONB, nullable=True)
    substitutions = Column(JSONB, nullable=True)
    prescription_mode = Column(String, nullable=True)
    canonical_name = Column(Text, nullable=True)
    selection_reason = Column(Text, nullable=True)
    association_validated_at = Column(DateTime(timezone=True), nullable=True)
    gif_url = Column(Text)
    alternatives = Column(JSONB)
    caution_tags = Column(JSONB)
    source_json = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTPaymentEvent(Base):
    __tablename__ = "leoft_payment_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_leoft_payment_events_event_id"),
        UniqueConstraint(
            "company_id",
            "provider",
            "provider_event_id",
            name="uq_leoft_payment_events_provider_event",
        ),
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_leoft_payment_events_company_idempotency",
        ),
        UniqueConstraint("company_id", "id", name="uq_leoft_payment_events_company_id"),
        UniqueConstraint(
            "company_id",
            "event_id",
            name="uq_leoft_payment_events_company_event_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "contact_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_leoft_payment_events_tenant_contact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "student_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_leoft_payment_events_tenant_student",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reverses_event_id"],
            ["leoft_payment_events.company_id", "leoft_payment_events.event_id"],
            name="fk_leoft_payment_events_tenant_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_type IN ('leoft.payment_confirmed.v1', 'leoft.payment_cancelled.v1', 'leoft.payment_refunded.v1')",
            name="ck_leoft_payment_events_type",
        ),
        CheckConstraint(
            "payment_status IN ('confirmed', 'cancelled', 'refunded')",
            name="ck_leoft_payment_events_status",
        ),
        CheckConstraint(
            "environment IN ('e2e', 'staging', 'production')",
            name="ck_leoft_payment_events_environment",
        ),
        CheckConstraint(
            "processing_status IN ('received','waiting_for_anamnesis','eligible','job_created','ignored','cancelled','failed')",
            name="ck_leoft_payment_events_processing_status",
        ),
        CheckConstraint("amount > 0", name="ck_leoft_payment_events_amount"),
        CheckConstraint(
            "char_length(currency) = 3", name="ck_leoft_payment_events_currency"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    event_type = Column(String, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contact_id = Column(UUID(as_uuid=True), nullable=False)
    student_id = Column(UUID(as_uuid=True), nullable=False)
    product_id = Column(String, nullable=False)
    order_id = Column(String, nullable=False)
    payment_id = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    provider_event_id = Column(String, nullable=True)
    amount = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(3), nullable=False)
    payment_status = Column(String, nullable=False)
    environment = Column(String, nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    idempotency_key = Column(String, nullable=False)
    raw_event_hash = Column(String(64), nullable=False)
    processing_status = Column(
        String, nullable=False, default="received", server_default=sa_text("'received'")
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processing_error = Column(Text, nullable=True)
    event_metadata = Column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa_text("'{}'::jsonb"),
    )
    reverses_event_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )


class LeoFTAnamnesisSnapshot(Base):
    __tablename__ = "leoft_anamnesis_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "student_id",
            "source_hash",
            name="uq_leoft_anamnesis_snapshot_source",
        ),
        UniqueConstraint(
            "company_id", "snapshot_id", name="uq_leoft_anamnesis_snapshots_company_id"
        ),
        ForeignKeyConstraint(
            ["company_id", "student_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_leoft_anamnesis_snapshots_tenant_student",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "age IS NULL OR age BETWEEN 1 AND 130",
            name="ck_leoft_anamnesis_snapshots_age",
        ),
        CheckConstraint(
            "requested_duration_weeks BETWEEN 1 AND 52",
            name="ck_leoft_anamnesis_snapshots_duration",
        ),
        CheckConstraint(
            "sessions_per_week BETWEEN 1 AND 14",
            name="ck_leoft_anamnesis_snapshots_frequency",
        ),
        CheckConstraint(
            "btrim(schema_version) <> ''",
            name="ck_leoft_anamnesis_snapshots_schema_version",
        ),
    )

    snapshot_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_id = Column(UUID(as_uuid=True), nullable=False)
    anamnesis_id = Column(String, nullable=False)
    anamnesis_version = Column(String, nullable=False)
    source_hash = Column(String(64), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    goal = Column(Text, nullable=True)
    experience_level = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    biological_data = Column(JSONB, nullable=True)
    relevant_measurements = Column(JSONB, nullable=True)
    weekly_frequency = Column(Integer, nullable=True)
    available_session_duration = Column(Integer, nullable=True)
    available_equipment = Column(JSONB, nullable=True)
    limitations = Column(JSONB, nullable=True)
    injuries = Column(JSONB, nullable=True)
    preferences = Column(JSONB, nullable=True)
    restrictions = Column(JSONB, nullable=True)
    product_rules = Column(
        JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    requested_duration_weeks = Column(Integer, nullable=False)
    sessions_per_week = Column(Integer, nullable=False)
    additional_answers = Column(
        JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    schema_version = Column(String, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )


class LeoFTGenerationJob(Base):
    __tablename__ = "leoft_generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_leoft_generation_jobs_company_idempotency",
        ),
        UniqueConstraint(
            "company_id",
            "generation_job_id",
            name="uq_leoft_generation_jobs_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "payment_event_id",
            name="uq_leoft_generation_jobs_payment_event",
        ),
        UniqueConstraint(
            "company_id",
            "student_id",
            "generation_job_id",
            name="uq_leoft_generation_jobs_tenant_student_id",
        ),
        Index(
            "ux_leoft_generation_jobs_active_purchase",
            "company_id",
            "product_id",
            "order_id",
            unique=True,
            postgresql_where=sa_text("status <> 'cancelled'"),
        ),
        ForeignKeyConstraint(
            ["company_id", "student_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_leoft_generation_jobs_tenant_student",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payment_event_id"],
            ["leoft_payment_events.company_id", "leoft_payment_events.id"],
            name="fk_leoft_generation_jobs_tenant_payment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "anamnesis_snapshot_id"],
            [
                "leoft_anamnesis_snapshots.company_id",
                "leoft_anamnesis_snapshots.snapshot_id",
            ],
            name="fk_leoft_generation_jobs_tenant_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "student_id", "plan_id"],
            [
                "leoft_training_plans.empresa_id",
                "leoft_training_plans.lead_id",
                "leoft_training_plans.id",
            ],
            name="fk_leoft_generation_jobs_tenant_plan",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "status IN ('pending','dispatching','planning','generating','validating','repairing','awaiting_review','approved','publishing','published','failed','cancelled')",
            name="ck_leoft_generation_jobs_status",
        ),
        CheckConstraint(
            "requested_duration_weeks BETWEEN 1 AND 52",
            name="ck_leoft_generation_jobs_duration",
        ),
        CheckConstraint(
            "sessions_per_week BETWEEN 1 AND 14",
            name="ck_leoft_generation_jobs_frequency",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND maximum_attempts > 0 AND attempt_count <= maximum_attempts",
            name="ck_leoft_generation_jobs_attempts",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost >= 0",
            name="ck_leoft_generation_jobs_usage",
        ),
    )

    generation_job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_id = Column(UUID(as_uuid=True), nullable=False)
    product_id = Column(String, nullable=False)
    order_id = Column(String, nullable=False)
    payment_event_id = Column(UUID(as_uuid=True), nullable=False)
    anamnesis_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    plan_id = Column(UUID(as_uuid=True), nullable=True)
    target_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "leoft_training_plan_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    latest_checkpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "leoft_generation_checkpoints.checkpoint_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    status = Column(
        String, nullable=False, default="pending", server_default=sa_text("'pending'")
    )
    current_stage = Column(String, nullable=True)
    requested_duration_weeks = Column(Integer, nullable=False)
    sessions_per_week = Column(Integer, nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    idempotency_key = Column(String, nullable=False)
    hermes_request_id = Column(String, nullable=True)
    dispatch_attempt_count = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    last_dispatch_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_redispatch_reason = Column(String, nullable=True)
    model_provider = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    attempt_count = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    maximum_attempts = Column(
        Integer, nullable=False, default=3, server_default=sa_text("3")
    )
    input_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    output_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    estimated_cost = Column(
        Numeric(18, 8), nullable=False, default=0, server_default=sa_text("0")
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String, nullable=True)
    last_error_summary = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    row_version = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )


class LeoFTGenerationSessionTask(Base):
    __tablename__ = "leoft_generation_session_tasks"
    __table_args__ = (
        UniqueConstraint(
            "generation_job_id",
            "week_number",
            "session_number",
            name="uq_leoft_generation_tasks_job_week_session",
        ),
        UniqueConstraint(
            "generation_job_id",
            "sequence",
            name="uq_leoft_generation_tasks_job_sequence",
        ),
        UniqueConstraint(
            "company_id", "session_task_id", name="uq_leoft_generation_tasks_company_id"
        ),
        ForeignKeyConstraint(
            ["company_id", "generation_job_id"],
            [
                "leoft_generation_jobs.company_id",
                "leoft_generation_jobs.generation_job_id",
            ],
            name="fk_leoft_generation_tasks_tenant_job",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending','generating','validating','repairing','completed','failed','cancelled')",
            name="ck_leoft_generation_tasks_status",
        ),
        CheckConstraint(
            "phase_number > 0 AND week_number > 0 AND session_number > 0 AND sequence > 0",
            name="ck_leoft_generation_tasks_coordinates",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND maximum_attempts > 0 AND attempt_count <= maximum_attempts",
            name="ck_leoft_generation_tasks_attempts",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost >= 0",
            name="ck_leoft_generation_tasks_usage",
        ),
    )

    session_task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    generation_job_id = Column(UUID(as_uuid=True), nullable=False)
    target_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    phase_number = Column(Integer, nullable=False)
    week_number = Column(Integer, nullable=False)
    session_number = Column(Integer, nullable=False)
    session_code = Column(String, nullable=False)
    objective = Column(Text, nullable=False)
    sequence = Column(Integer, nullable=False)
    status = Column(
        String, nullable=False, default="pending", server_default=sa_text("'pending'")
    )
    attempt_count = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    maximum_attempts = Column(
        Integer, nullable=False, default=3, server_default=sa_text("3")
    )
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    checkpoint_version = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    selected_provider = Column(String, nullable=True)
    selected_model = Column(String, nullable=True)
    input_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    output_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    estimated_cost = Column(
        Numeric(18, 8), nullable=False, default=0, server_default=sa_text("0")
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String, nullable=True)
    last_error_summary = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    row_version = Column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )


class LeoFTGenerationAttempt(Base):
    __tablename__ = "leoft_generation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_leoft_generation_attempts_company_idempotency",
        ),
        UniqueConstraint(
            "generation_job_id",
            "session_task_id",
            "attempt_number",
            name="uq_leoft_generation_attempts_task_number",
        ),
        Index(
            "ux_leoft_generation_attempts_job_number",
            "generation_job_id",
            "attempt_number",
            unique=True,
            postgresql_where=sa_text("session_task_id IS NULL"),
        ),
        CheckConstraint(
            "attempt_number > 0", name="ck_leoft_generation_attempts_number"
        ),
        ForeignKeyConstraint(
            ["company_id", "generation_job_id"],
            [
                "leoft_generation_jobs.company_id",
                "leoft_generation_jobs.generation_job_id",
            ],
            name="fk_leoft_generation_attempts_tenant_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "session_task_id"],
            [
                "leoft_generation_session_tasks.company_id",
                "leoft_generation_session_tasks.session_task_id",
            ],
            name="fk_leoft_generation_attempts_tenant_task",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('started','succeeded','validation_failed','provider_failed','timed_out','cancelled')",
            name="ck_leoft_generation_attempts_status",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0 AND estimated_cost >= 0 AND latency_ms >= 0",
            name="ck_leoft_generation_attempts_usage",
        ),
    )

    attempt_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    generation_job_id = Column(UUID(as_uuid=True), nullable=False)
    session_task_id = Column(UUID(as_uuid=True), nullable=True)
    target_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_number = Column(Integer, nullable=False)
    attempt_type = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    prompt_version = Column(String, nullable=False)
    catalog_version = Column(String, nullable=False)
    status = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    model_version = Column(String, nullable=True)
    provider_request_id = Column(String, nullable=True)
    input_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    output_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    total_tokens = Column(
        BigInteger, nullable=False, default=0, server_default=sa_text("0")
    )
    estimated_cost = Column(
        Numeric(18, 8), nullable=False, default=0, server_default=sa_text("0")
    )
    currency = Column(String(3), nullable=False)
    latency_ms = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    request_hash = Column(String(64), nullable=False)
    response_hash = Column(String(64), nullable=True)
    validation_result = Column(JSONB, nullable=True)
    errors = Column(JSONB, nullable=True)
    repair_instructions = Column(JSONB, nullable=True)
    error_code = Column(String, nullable=True)
    error_summary = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    idempotency_key = Column(String, nullable=False)


class LeoFTGenerationCheckpoint(Base):
    __tablename__ = "leoft_generation_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "generation_job_id",
            "checkpoint_version",
            name="uq_leoft_generation_checkpoints_job_version",
        ),
        ForeignKeyConstraint(
            ["company_id", "generation_job_id"],
            [
                "leoft_generation_jobs.company_id",
                "leoft_generation_jobs.generation_job_id",
            ],
            name="fk_leoft_generation_checkpoints_tenant_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "current_session_task_id"],
            [
                "leoft_generation_session_tasks.company_id",
                "leoft_generation_session_tasks.session_task_id",
            ],
            name="fk_leoft_generation_checkpoints_tenant_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "checkpoint_version > 0", name="ck_leoft_generation_checkpoints_version"
        ),
    )

    checkpoint_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    generation_job_id = Column(UUID(as_uuid=True), nullable=False)
    checkpoint_version = Column(Integer, nullable=False)
    target_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_status = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    current_session_task_id = Column(UUID(as_uuid=True), nullable=True)
    completed_task_ids = Column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    pending_task_ids = Column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    failed_task_ids = Column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    blueprint_hash = Column(String(64), nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    state_hash = Column(String(64), nullable=False)
    last_attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_generation_attempts.attempt_id", ondelete="RESTRICT"),
        nullable=True,
    )
    captured_at = Column(DateTime(timezone=True), nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )


class LeoFTStudentExerciseProgress(Base):
    __tablename__ = "leoft_student_exercise_progress"
    __table_args__ = (
        UniqueConstraint(
            "access_id",
            "plan_id",
            "exercise_id",
            name="uq_leoft_student_exercise_progress",
        ),
        Index("ix_leoft_student_exercise_progress_access_plan", "access_id", "plan_id"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_student_accesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    exercise_id = Column(String, nullable=False)
    workout_id = Column(String, nullable=False)
    status = Column(
        String,
        nullable=False,
        default="completed",
        server_default=sa_text("'completed'"),
    )
    completed_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=sa_text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=sa_text("now()"),
    )


class LeoFTStudentWorkoutProgress(Base):
    __tablename__ = "leoft_student_workout_progress"
    __table_args__ = (
        UniqueConstraint(
            "access_id",
            "plan_id",
            "workout_id",
            name="uq_leoft_student_workout_progress",
        ),
        Index("ix_leoft_student_workout_progress_access_plan", "access_id", "plan_id"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empresas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_student_accesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workout_id = Column(String, nullable=False)
    completion_source = Column(
        String, nullable=False, default="manual", server_default=sa_text("'manual'")
    )
    status = Column(
        String,
        nullable=False,
        default="completed",
        server_default=sa_text("'completed'"),
    )
    completed_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=sa_text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=sa_text("now()"),
    )


class LeoFTTrainingReviewSession(Base):
    __tablename__ = "leoft_training_review_sessions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_public_id = Column(String, unique=True, nullable=False)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String, nullable=False)
    current_page = Column(Integer, nullable=False, default=0)
    total_pages = Column(Integer, nullable=False)
    telegram_chat_id = Column(String)
    telegram_message_id = Column(String)
    telegram_user_id = Column(String)
    reviewer_identifier = Column(String)
    idempotency_key = Column(String, unique=True)
    optimistic_lock = Column(Integer, nullable=False, default=0)
    confirmation_requested_at = Column(DateTime)
    approved_at = Column(DateTime)
    approved_by = Column(String)
    failed_at = Column(DateTime)
    failure_code = Column(String)
    failure_message_sanitized = Column(Text)
    last_callback_id_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    expires_at = Column(DateTime)


class LeoFTTrainingReviewEvent(Base):
    __tablename__ = "leoft_training_review_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_review_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String, nullable=False, index=True)
    page_before = Column(Integer)
    page_after = Column(Integer)
    actor_type = Column(String)
    actor_identifier = Column(String)
    callback_id_hash = Column(String)
    idempotency_key = Column(String)
    payload_sanitized = Column(JSONB)
    result_status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class LeoFTIntegrationEvent(Base):
    __tablename__ = "leoft_integration_events"
    __table_args__ = (
        UniqueConstraint(
            "integration", "idempotency_key", name="uq_leoft_integration_idempotency"
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction = Column(String, nullable=False)
    integration = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    external_event_id = Column(String)
    idempotency_key = Column(String, nullable=False)
    plan_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_training_plans.id", ondelete="SET NULL")
    )
    review_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_review_sessions.id", ondelete="SET NULL"),
    )
    request_hash = Column(String)
    request_payload_sanitized = Column(JSONB)
    response_status = Column(Integer)
    response_payload_sanitized = Column(JSONB)
    attempt_count = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False)
    next_retry_at = Column(DateTime)
    processed_at = Column(DateTime)
    last_error_code = Column(String)
    last_error_sanitized = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTProduct(Base):
    """Canonical product identity, independent from a sales offer."""

    __tablename__ = "leoft_products"
    __table_args__ = (
        UniqueConstraint("company_id", "product_id", name="uq_leoft_products_company_product"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id = Column(String, nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    product_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft")
    default_price = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    access_policy = Column(String, nullable=True)
    requires_anamnesis = Column(Boolean, nullable=False, default=False)
    requires_human_approval = Column(Boolean, nullable=False, default=False)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class LeoFTOffer(Base):
    """Canonical commercial offer; price lives here for future single-source use."""

    __tablename__ = "leoft_offers"
    __table_args__ = (
        UniqueConstraint("company_id", "offer_id", name="uq_leoft_offers_company_offer"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True)
    offer_id = Column(String, nullable=False)
    product_ref = Column(UUID(as_uuid=True), ForeignKey("leoft_products.id", ondelete="RESTRICT"), nullable=False)
    name = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="draft")
    price = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    checkout_provider = Column(String, nullable=True)
    checkout_reference = Column(String, nullable=True)
    cta = Column(Text, nullable=True)
    flow_reference = Column(Text, nullable=True)
    agent_reference = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class LeoFTPersonalizationRequest(Base):
    """Idempotent handoff from a paid personalized product to creation/review."""

    __tablename__ = "leoft_personalization_requests"
    __table_args__ = (
        UniqueConstraint("sale_fulfillment_id", name="uq_leoft_personalization_request_sale"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True)
    sale_fulfillment_id = Column(UUID(as_uuid=True), ForeignKey("leoft_sale_fulfillments.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(UUID(as_uuid=True), ForeignKey("leoft_students.id", ondelete="RESTRICT"), nullable=False, index=True)
    anamnesis_id = Column(String, nullable=True)
    analysis_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class LeoFTOfferFulfillment(Base):
    """Product-to-fulfillment mapping; templates are configured data, not code."""

    __tablename__ = "leoft_offer_fulfillments"
    __table_args__ = (
        UniqueConstraint("company_id", "product_id", name="uq_leoft_offer_fulfillment_product"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id = Column(String, nullable=False)
    offer_code = Column(String, nullable=False)
    product_type = Column(String, nullable=False, default="PERSONALIZED_PROGRAM")
    fulfillment_type = Column(String, nullable=False, default="PROGRAM_AND_APP")
    training_program_template_id = Column(UUID(as_uuid=True), nullable=True)
    canonical_offer_id = Column(String, nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class LeoFTSaleFulfillment(Base):
    """Idempotent SALE_CONFIRMED contract and its delivery state machine."""

    __tablename__ = "leoft_sale_fulfillments"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_leoft_sale_fulfillment_event"),
        UniqueConstraint("company_id", "payment_provider", "payment_id", name="uq_leoft_sale_fulfillment_payment"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    event_type = Column(String, nullable=False, default="SALE_CONFIRMED")
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    company_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True)
    sale_id = Column(String, nullable=False)
    order_id = Column(String, nullable=False)
    payment_id = Column(String, nullable=False)
    payment_provider = Column(String, nullable=False)
    payment_status = Column(String, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    product_id = Column(String, nullable=False)
    offer_code = Column(String, nullable=True)
    product_type = Column(String, nullable=False)
    fulfillment_type = Column(String, nullable=False)
    amount = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    lead_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    contact_id = Column(UUID(as_uuid=True), nullable=True)
    student_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    access_id = Column(UUID(as_uuid=True), nullable=True)
    anamnesis_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    campaign = Column(String, nullable=True)
    funnel = Column(String, nullable=True)
    training_program_template_id = Column(UUID(as_uuid=True), nullable=True)
    student_program_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String, nullable=False, default="received", index=True)
    current_stage = Column(String, nullable=False, default="received")
    post_sale_status = Column(String, nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String, nullable=True)
    last_error_sanitized = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    personalization_request_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class LeoFTCatalogEntry(Base):
    __tablename__ = "leoft_catalog_entries"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id = Column(String, nullable=False, unique=True)
    canonical_name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False, index=True)
    muscle_group = Column(Text)
    equipment = Column(Text)
    body_position = Column(Text)
    variation = Column(Text)
    laterality = Column(Text)
    grip = Column(Text)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=True, index=True)
    approval_status = Column(Text, nullable=False, index=True)
    confidence = Column(Numeric)
    source = Column(Text)
    source_metadata = Column(JSONB)
    description = Column(Text)
    instructions = Column(Text)
    modality = Column(Text)
    media_url = Column(Text)
    active = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    search_document = Column(TSVECTOR)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTManualWorkout(Base):
    __tablename__ = "leoft_manual_workouts"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="ck_leoft_manual_workouts_name"),
        Index("ix_leoft_manual_workouts_empresa_id", "empresa_id"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text)
    modality = Column(Text)
    active = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LeoFTManualWorkoutExercise(Base):
    __tablename__ = "leoft_manual_workout_exercises"
    __table_args__ = (
        UniqueConstraint("workout_id", "position", name="uq_leoft_manual_workout_exercise_position"),
        CheckConstraint("position > 0", name="ck_leoft_manual_workout_exercise_position"),
        CheckConstraint("sets IS NULL OR sets > 0", name="ck_leoft_manual_workout_exercise_sets"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="ck_leoft_manual_workout_exercise_duration"),
        CheckConstraint("rest_seconds IS NULL OR rest_seconds >= 0", name="ck_leoft_manual_workout_exercise_rest"),
        Index("ix_leoft_manual_workout_exercises_workout_position", "workout_id", "position"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workout_id = Column(UUID(as_uuid=True), ForeignKey("leoft_manual_workouts.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(UUID(as_uuid=True), ForeignKey("leoft_catalog_entries.id", ondelete="RESTRICT"), nullable=False)
    position = Column(Integer, nullable=False)
    sets = Column(Integer)
    reps = Column(Text)
    duration_seconds = Column(Integer)
    rest_seconds = Column(Integer)
    load = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LeoFTStudentWorkoutAssignment(Base):
    __tablename__ = "leoft_student_workout_assignments"
    __table_args__ = (
        UniqueConstraint("student_id", "workout_id", name="uq_leoft_student_workout_assignment_pair"),
        UniqueConstraint("student_id", "position", name="uq_leoft_student_workout_assignment_position"),
        CheckConstraint("position > 0", name="ck_leoft_student_workout_assignment_position"),
        CheckConstraint("status IN ('blocked', 'released')", name="ck_leoft_student_workout_assignment_status"),
        ForeignKeyConstraint(
            ["student_id", "empresa_id"],
            ["leoft_students.id", "leoft_students.empresa_id"],
            ondelete="CASCADE",
            name="fk_leoft_student_workout_assignments_student_tenant",
        ),
        ForeignKeyConstraint(
            ["workout_id", "empresa_id"],
            ["leoft_manual_workouts.id", "leoft_manual_workouts.empresa_id"],
            ondelete="CASCADE",
            name="fk_leoft_student_workout_assignments_workout_tenant",
        ),
        Index("ix_leoft_student_workout_assignments_tenant_student_position", "empresa_id", "student_id", "position"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(UUID(as_uuid=True), nullable=False)
    workout_id = Column(UUID(as_uuid=True), nullable=False)
    position = Column(Integer, nullable=False)
    status = Column(Text, nullable=False, default="blocked")
    released_at = Column(DateTime)
    blocked_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LeoFTCatalogAlias(Base):
    __tablename__ = "leoft_catalog_aliases"
    __table_args__ = (
        UniqueConstraint(
            "catalog_entry_id",
            "normalized_alias",
            name="uq_leoft_catalog_alias_entry_normalized",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = Column(Text, nullable=False)
    normalized_alias = Column(Text, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LeoFTCatalogMedia(Base):
    __tablename__ = "leoft_catalog_media"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id = Column(String, nullable=False, unique=True)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_type = Column(Text)
    format = Column(Text)
    mime_type = Column(Text)
    storage_provider = Column(Text)
    storage_path = Column(Text, nullable=False)
    thumbnail_path = Column(Text)
    sha256 = Column(String, nullable=False)
    file_size = Column(BigInteger)
    width = Column(Integer)
    height = Column(Integer)
    duration_ms = Column(Integer)
    media_metadata = Column("metadata", JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LeoFTCatalogSignature(Base):
    __tablename__ = "leoft_catalog_signatures"
    __table_args__ = (
        UniqueConstraint(
            "catalog_entry_id",
            "signature_version",
            name="uq_leoft_catalog_signature_entry_version",
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    signature_key = Column(Text, nullable=False)
    signature_version = Column(Text, nullable=False)
    normalized_name = Column(Text)
    muscle_group = Column(Text)
    equipment = Column(Text)
    body_position = Column(Text)
    variation = Column(Text)
    laterality = Column(Text)
    grip = Column(Text)
    signature_payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LeoFTCatalogVersion(Base):
    __tablename__ = "leoft_catalog_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String, nullable=False, unique=True)
    source_hash = Column(String, nullable=False)
    record_count = Column(Integer)
    media_count = Column(Integer)
    alias_count = Column(Integer)
    signature_count = Column(Integer)
    status = Column(String)
    version_metadata = Column("metadata", JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    activated_at = Column(DateTime)


class LeoFTCatalogImportRun(Base):
    __tablename__ = "leoft_catalog_import_runs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(String, nullable=False, unique=True)
    catalog_version_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_versions.id", ondelete="SET NULL")
    )
    source_filename = Column(Text)
    source_sha256 = Column(String)
    status = Column(String)
    counters = Column(JSONB)
    error_summary = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime)


class LeoFTMediaApproval(Base):
    __tablename__ = "leoft_media_approvals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signature_key = Column(Text, nullable=False)
    signature_version = Column(Text, nullable=False)
    catalog_entry_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_entries.id", ondelete="SET NULL")
    )
    media_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_media.id", ondelete="SET NULL")
    )
    approval_method = Column(Text, nullable=False)
    approved_by = Column(Text, nullable=False)
    confidence = Column(Numeric)
    revision_number = Column(Integer, nullable=False)
    supersedes_approval_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_media_approvals.id", ondelete="SET NULL")
    )
    idempotency_key = Column(String, nullable=False, unique=True)
    active = Column(Boolean, nullable=False)
    source_plan_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_training_plans.id", ondelete="SET NULL")
    )
    source_workout_id = Column(Text)
    source_exercise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("leoft_training_exercises.id", ondelete="SET NULL"),
    )
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LeoFTMediaCandidateRejection(Base):
    __tablename__ = "leoft_media_candidate_rejections"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signature_key = Column(Text, nullable=False)
    signature_version = Column(Text, nullable=False)
    catalog_entry_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_entries.id", ondelete="SET NULL")
    )
    media_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_media.id", ondelete="SET NULL")
    )
    rejected_by = Column(Text, nullable=False)
    reason = Column(Text)
    idempotency_key = Column(String, nullable=False, unique=True)
    active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    restored_at = Column(DateTime)


class LeoFTApprovalEvent(Base):
    __tablename__ = "leoft_approval_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(Text, nullable=False)
    signature_key = Column(Text)
    approval_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_media_approvals.id", ondelete="SET NULL")
    )
    previous_approval_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_media_approvals.id", ondelete="SET NULL")
    )
    catalog_entry_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_entries.id", ondelete="SET NULL")
    )
    media_id = Column(
        UUID(as_uuid=True), ForeignKey("leoft_catalog_media.id", ondelete="SET NULL")
    )
    actor_identifier = Column(Text)
    source = Column(Text)
    reason = Column(Text)
    payload = Column(JSONB, nullable=False)
    idempotency_key = Column(String, nullable=False, unique=True)
    event_version = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


__all__ = [name for name in globals() if name.startswith("LeoFT")]
