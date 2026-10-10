from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.leoft_generation.enums import (
    CANONICAL_PRODUCT_ID,
    Environment,
    GenerationJobStatus,
    GenerationTaskStatus,
    PaymentEventType,
    PaymentStatus,
)
from app.leoft_generation.errors import (
    InvalidJobTransitionError,
    InvalidTaskTransitionError,
)
from app.leoft_generation.schemas import (
    E2EAllowlist,
    GenerationPolicy,
    PaymentEventCreate,
    TrainingBlueprint,
)
from app.leoft_generation.services import (
    canonical_sha256,
    normalize_anamnesis,
    sanitize_pii,
)
from app.leoft_generation.state_machine import (
    validate_job_transition,
    validate_task_transition,
)


def _policy() -> GenerationPolicy:
    company_id, student_id = uuid4(), uuid4()
    return GenerationPolicy(
        environment=Environment.E2E,
        e2e_allowlist=E2EAllowlist(
            company_ids={company_id},
            student_ids={student_id},
            contact_ids={student_id},
            product_ids={CANONICAL_PRODUCT_ID},
        ),
    )


def test_strict_payment_schema_rejects_extra_and_naive_timestamp() -> None:
    now = datetime.now(timezone.utc)
    base = {
        "event_id": uuid4(),
        "event_type": PaymentEventType.CONFIRMED,
        "occurred_at": now,
        "received_at": now,
        "company_id": uuid4(),
        "contact_id": uuid4(),
        "student_id": uuid4(),
        "order_id": "order-1",
        "payment_id": "payment-1",
        "provider": "controlled_e2e",
        "provider_event_id": "provider-event-1",
        "amount": Decimal("97.00"),
        "currency": "BRL",
        "payment_status": PaymentStatus.CONFIRMED,
        "environment": Environment.E2E,
        "correlation_id": uuid4(),
        "idempotency_key": "a" * 64,
        "raw_event_hash": "b" * 64,
    }
    PaymentEventCreate(**base)
    with pytest.raises(ValidationError):
        PaymentEventCreate(**base, raw_payload={"secret": True})
    with pytest.raises(ValidationError):
        PaymentEventCreate(**{**base, "occurred_at": datetime.now()})  # noqa: DTZ005


def test_e2e_policy_fails_closed_without_allowlist() -> None:
    with pytest.raises(ValidationError):
        GenerationPolicy(environment=Environment.E2E)


def test_exact_job_state_machine() -> None:
    assert (
        validate_job_transition("pending", "dispatching")
        == GenerationJobStatus.DISPATCHING
    )
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition("failed", "pending")
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition("pending", "planning")
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition("published", "cancelled")
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition("awaiting_review", "repairing")


def test_exact_task_state_machine_and_terminal_immutability() -> None:
    assert (
        validate_task_transition("pending", "generating")
        == GenerationTaskStatus.GENERATING
    )
    assert (
        validate_task_transition("validating", "completed")
        == GenerationTaskStatus.COMPLETED
    )
    assert (
        validate_task_transition("generating", "pending")
        == GenerationTaskStatus.PENDING
    )
    assert (
        validate_task_transition("validating", "pending")
        == GenerationTaskStatus.PENDING
    )
    assert (
        validate_task_transition("repairing", "pending") == GenerationTaskStatus.PENDING
    )
    with pytest.raises(InvalidTaskTransitionError):
        validate_task_transition("completed", "repairing")
    with pytest.raises(InvalidTaskTransitionError):
        validate_task_transition("failed", "pending")
    with pytest.raises(InvalidTaskTransitionError):
        validate_task_transition("generating", "repairing")
    with pytest.raises(InvalidTaskTransitionError):
        validate_task_transition("repairing", "validating")


def test_blueprint_codes_and_sequence_are_deterministic() -> None:
    blueprint = TrainingBlueprint(
        sessions=[
            {
                "week_number": 1,
                "session_number": 1,
                "objective": "Força",
                "sequence": 1,
            },
            {
                "week_number": 1,
                "session_number": 2,
                "objective": "Corrida",
                "sequence": 2,
            },
        ]
    )
    assert [item.session_code for item in blueprint.sessions] == [None, None]
    with pytest.raises(ValidationError):
        TrainingBlueprint(
            sessions=[
                {"week_number": 1, "session_number": 1, "objective": "X", "sequence": 2}
            ]
        )


def test_schema_v2_is_not_limited_to_four_weeks() -> None:
    blueprint = TrainingBlueprint(
        sessions=[
            {
                "week_number": week,
                "session_number": 1,
                "objective": f"Week {week}",
                "sequence": week,
            }
            for week in range(1, 9)
        ]
    )
    assert blueprint.sessions[-1].session_code is None


def test_anamnesis_normalization_drops_pii_and_is_canonical() -> None:
    payload = {
        "versao_form": "lovable_v1",
        "submitted": True,
        "token_ref": "must-not-survive",
        "metadata": {"user_agent": "secret-agent", "utm_source": "campaign"},
        "respostas": {
            "personalData": {
                "name": "Fake Person",
                "email": "fake@example.invalid",
                "whatsapp": "000000000",
                "age": "38",
                "gender": "homem",
            },
            "trainingProfile": {
                "mainGoal": "massa",
                "currentLevel": "intermediario",
                "height": "1,78",
                "weight": "78",
                "daysPerWeek": "4",
                "timePerSession": "45",
                "equipment": ["academia_full"],
                "limitations": ["nao"],
                "customAnswer": "ok",
            },
        },
    }
    first = normalize_anamnesis(payload, _policy())
    reordered = normalize_anamnesis(dict(reversed(list(payload.items()))), _policy())
    serialized = first.model_dump(mode="json")
    assert first.source_hash == reordered.source_hash
    assert first.age == 38
    assert first.requested_duration_weeks == 4
    assert first.sessions_per_week == 3
    assert "name" not in str(serialized).lower()
    assert "email" not in str(serialized).lower()
    assert "whatsapp" not in str(serialized).lower()
    assert "token" not in str(serialized).lower()


def test_sanitize_pii_recursive_and_hash_is_order_independent() -> None:
    clean = sanitize_pii({"safe": {"email": "x", "goal": "run"}, "phone": "1"})
    assert clean == {"safe": {"goal": "run"}}
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
