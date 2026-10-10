import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routers.leoft_internal import ReviewCreate, approve, create_review, get_review, router
from app.services.leoft_postgres_central import canonical_checksum, _review_current_page, _review_total_pages
from db.database import get_db


SENSITIVE_RESPONSE_KEYS = {
    "password_hash",
    "temporary_password_masked",
    "access_token_hash",
    "access_token_masked",
    "login_identifier",
    "student_email",
    "student_whatsapp",
    "app_url",
    "access_token",
    "app_link",
    "app_link_masked",
}


def _review_row(*, status="approved", plan_id=None, version_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        review_public_id="8-tWczOxMlNb",
        plan_id=plan_id or uuid.uuid4(),
        version_id=version_id or uuid.uuid4(),
        status=status,
        current_page=47,
        total_pages=48,
        optimistic_lock=0,
        telegram_message_id="46",
        approved_at=datetime(2026, 7, 29, 12, 0, 0) if status == "approved" else None,
        approved_by=None,
    )


def _plan_row(plan_id, *, status="approved"):
    return SimpleNamespace(id=plan_id, status=status)


def _access_row(*, status="active"):
    return SimpleNamespace(status=status)


def _assert_sanitized(payload):
    text = json.dumps(payload, default=str)
    for key in SENSITIVE_RESPONSE_KEYS:
        assert key not in payload
        assert key not in text
    assert "aluno@example" not in text
    assert "+55" not in text
    assert "raw-secret" not in text

def test_checksum_is_deterministic_for_object_order():
    assert canonical_checksum({"b": 2, "a": 1}) == canonical_checksum({"a": 1, "b": 2})

def test_checksum_changes_when_content_changes():
    assert canonical_checksum({"a": 1}) != canonical_checksum({"a": 2})


def test_review_create_accepts_hermes_public_id_and_numeric_message_id():
    body = ReviewCreate(
        plan_id=uuid.uuid4(),
        idempotency_key="idem-review-46",
        review_public_id="8-tWczOxMlNb",
        telegram_chat_id="-100123",
        telegram_message_id=46,
        actor_user_id=123,
        current_page=47,
        max_page_seen=47,
        total_pages=48,
        total_workouts=48,
    )

    dumped = body.model_dump()
    assert dumped["telegram_message_id"] == "46"
    assert dumped["actor_user_id"] == "123"
    assert dumped["current_page"] == 47
    assert dumped["total_pages"] == 48


def test_review_page_count_prefers_hermes_total_pages_then_total_workouts():
    plan = {"months": [{"weeks": [{"workouts": [{}, {}]}]}]}

    assert _review_total_pages(plan, {"total_pages": 48, "total_workouts": 47}) == 48
    assert _review_total_pages(plan, {"total_workouts": 48}) == 48
    assert _review_current_page({"current_page": 47, "max_page_seen": 47}, 48) == 47


@pytest.mark.asyncio
async def test_create_review_returns_existing_remote_review_without_approval():
    plan_id = uuid.uuid4()
    version_id = uuid.uuid4()
    plan = SimpleNamespace(id=plan_id, status="pending_approval", plan_json={"months": []})
    review = SimpleNamespace(
        review_public_id="8-tWczOxMlNb",
        plan_id=plan_id,
        version_id=version_id,
        status="reviewing",
        current_page=47,
        total_pages=48,
        optimistic_lock=0,
        telegram_message_id="46",
    )
    db = AsyncMock()
    db.get.return_value = plan

    body = ReviewCreate(
        plan_id=plan_id,
        idempotency_key="idem-review-46",
        review_public_id="8-tWczOxMlNb",
        telegram_message_id="46",
        current_page=47,
        max_page_seen=47,
        total_pages=48,
        total_workouts=48,
    )

    with patch("app.api.routers.leoft_internal.normalize_plan", new=AsyncMock(return_value={"version_id": str(version_id)})), patch(
        "app.api.routers.leoft_internal.create_or_get_review", new=AsyncMock(return_value=review)
    ), patch("app.api.routers.leoft_internal.approve_training_plan_in_session", new=AsyncMock()) as approve:
        response = await create_review(body, auth=("idem-review-46", "hash"), db=db)

    assert response["review_public_id"] == "8-tWczOxMlNb"
    assert response["telegram_message_id"] == "46"
    assert response["current_page"] == 47
    assert response["total_pages"] == 48
    assert response["status"] == "reviewing"
    approve.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_review_internal_error_is_json_and_rolls_back():
    plan_id = uuid.uuid4()
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=plan_id, status="pending_approval", plan_json={"months": []})
    body = ReviewCreate(plan_id=plan_id, idempotency_key="idem-review-46", review_public_id="8-tWczOxMlNb")

    with patch("app.api.routers.leoft_internal.normalize_plan", new=AsyncMock(side_effect=RuntimeError("raw failure with private payload"))):
        response = await create_review(body, auth=("idem-review-46", "hash"), db=db)

    assert response.status_code == 500
    assert response.body
    assert b"review_create_internal_error" in response.body
    assert b"private payload" not in response.body
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_approved_review_returns_plan_and_access_state():
    review = _review_row(status="approved")
    db = AsyncMock()
    db.scalar.side_effect = [review, _access_row()]
    db.get.return_value = _plan_row(review.plan_id, status="approved")

    response = await get_review("8-tWczOxMlNb", auth=("idem", "hash"), db=db)

    assert response["status"] == "approved"
    assert response["review_status"] == "approved"
    assert response["plan_status"] == "approved"
    assert response["student_access_exists"] is True
    assert response["student_access_status"] == "active"
    assert response["telegram_message_id"] == "46"
    _assert_sanitized(response)


@pytest.mark.asyncio
async def test_get_pending_review_without_access_returns_false_access_state():
    review = _review_row(status="pending")
    review.approved_at = None
    db = AsyncMock()
    db.scalar.side_effect = [review, None]
    db.get.return_value = _plan_row(review.plan_id, status="pending_approval")

    response = await get_review("8-tWczOxMlNb", auth=("idem", "hash"), db=db)

    assert response["status"] == "pending"
    assert response["review_status"] == "pending"
    assert response["plan_status"] == "pending_approval"
    assert response["student_access_exists"] is False
    assert response["student_access_status"] is None
    _assert_sanitized(response)


@pytest.mark.asyncio
async def test_get_missing_review_still_404():
    db = AsyncMock()
    db.scalar.return_value = None

    with pytest.raises(Exception) as excinfo:
        await get_review("missing-review", auth=("idem", "hash"), db=db)

    assert getattr(excinfo.value, "status_code", None) == 404
    assert getattr(excinfo.value, "detail", None) == "review_not_found"


@pytest.mark.asyncio
async def test_get_review_with_missing_linked_plan_returns_safe_json_detail():
    review = _review_row(status="approved")
    db = AsyncMock()
    db.scalar.return_value = review
    db.get.return_value = None

    with pytest.raises(Exception) as excinfo:
        await get_review("8-tWczOxMlNb", auth=("idem", "hash"), db=db)

    assert getattr(excinfo.value, "status_code", None) == 409
    assert getattr(excinfo.value, "detail", {})["code"] == "linked_plan_not_found"
    _assert_sanitized(getattr(excinfo.value, "detail", {}))


@pytest.mark.asyncio
async def test_approve_missing_review_still_404():
    db = AsyncMock()
    db.scalar.return_value = None

    with pytest.raises(Exception) as excinfo:
        await approve("missing-review", SimpleNamespace(actor="hermes", callback_id=None), auth=("idem", "hash"), db=db)

    assert getattr(excinfo.value, "status_code", None) == 404
    assert getattr(excinfo.value, "detail", None) == "review_not_found"


@pytest.mark.asyncio
async def test_approve_with_missing_linked_plan_returns_safe_json_detail():
    review = _review_row(status="awaiting_confirmation")
    db = AsyncMock()
    db.scalar.side_effect = [review, None]

    with pytest.raises(Exception) as excinfo:
        await approve("8-tWczOxMlNb", SimpleNamespace(actor="hermes", callback_id=None), auth=("idem", "hash"), db=db)

    assert getattr(excinfo.value, "status_code", None) == 409
    assert getattr(excinfo.value, "detail", {})["code"] == "linked_plan_not_found"
    _assert_sanitized(getattr(excinfo.value, "detail", {}))


@pytest.mark.asyncio
async def test_new_approval_returns_complete_sanitized_contract():
    review = _review_row(status="awaiting_confirmation")
    plan = _plan_row(review.plan_id, status="pending_approval")
    db = AsyncMock()
    db.add = Mock()
    db.scalar.side_effect = [review, plan, _access_row()]
    db.get.return_value = plan

    async def _approve(_db, loaded_plan, *, approved_by, notes=None):
        loaded_plan.status = "approved"
        return {
            "plan_id": str(loaded_plan.id),
            "status": "approved",
            "app_access": {
                "student_email": "aluno@example.test",
                "student_whatsapp": "+5500000000000",
                "temporary_password_masked": "raw-secret",
                "access_token": "raw-secret",
                "app_url": "https://app.example/?token=raw-secret",
                "status": "active",
            },
        }

    with patch("app.api.routers.leoft_internal.approve_training_plan_in_session", new=_approve):
        response = await approve("8-tWczOxMlNb", SimpleNamespace(actor="hermes", callback_id=None), auth=("idem", "hash"), db=db)

    assert response["status"] == "approved"
    assert response["review_status"] == "approved"
    assert response["plan_status"] == "approved"
    assert response["student_access_exists"] is True
    assert response["student_access_status"] == "active"
    assert response["idempotent"] is False
    _assert_sanitized(response)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotent_approval_returns_complete_contract_and_does_not_create_access():
    review = _review_row(status="approved")
    plan = _plan_row(review.plan_id, status="approved")
    db = AsyncMock()
    db.scalar.side_effect = [review, plan, _access_row(), _access_row()]
    db.get.return_value = plan

    with patch("app.api.routers.leoft_internal.approve_training_plan_in_session", new=AsyncMock()) as approve_service:
        response = await approve("8-tWczOxMlNb", SimpleNamespace(actor="hermes", callback_id=None), auth=("idem", "hash"), db=db)

    assert response["idempotent"] is True
    assert response["status"] == "approved"
    assert response["review_status"] == "approved"
    assert response["plan_status"] == "approved"
    assert response["student_access_exists"] is True
    approve_service.assert_not_called()
    db.commit.assert_not_called()
    _assert_sanitized(response)


@pytest.mark.asyncio
async def test_idempotent_approval_missing_access_is_safe_inconsistency(caplog):
    review = _review_row(status="approved")
    plan = _plan_row(review.plan_id, status="approved")
    db = AsyncMock()
    db.scalar.side_effect = [review, plan, None]

    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception) as excinfo:
            await approve("8-tWczOxMlNb", SimpleNamespace(actor="hermes", callback_id=None), auth=("idem", "hash"), db=db)

    detail = getattr(excinfo.value, "detail", {})
    assert getattr(excinfo.value, "status_code", None) == 409
    assert detail["code"] == "approved_state_missing_student_access"
    assert detail["student_access_exists"] is False
    assert "aluno@example" not in caplog.text
    assert "raw-secret" not in caplog.text
    _assert_sanitized(detail)


def test_hmac_valid_allows_get_review_contract(monkeypatch):
    secret = "local-test-secret"
    review = _review_row(status="approved")
    db = AsyncMock()
    db.scalar.side_effect = [review, _access_row()]
    db.get.return_value = _plan_row(review.plan_id, status="approved")

    app = FastAPI()
    app.include_router(router)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("HERMES_LEOFT_HMAC_SECRET", secret)
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), b"", hashlib.sha256).hexdigest()

    client = TestClient(app)
    response = client.get(
        "/api/internal/hermes/leoft/reviews/8-tWczOxMlNb",
        headers={
            "X-AgenteOS-Timestamp": timestamp,
            "X-AgenteOS-Signature": signature,
            "X-AgenteOS-Idempotency-Key": "idem-get-review",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_status"] == "approved"
    assert payload["student_access_exists"] is True
    _assert_sanitized(payload)


def test_hmac_invalid_still_401(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setenv("HERMES_LEOFT_HMAC_SECRET", "local-test-secret")

    client = TestClient(app)
    response = client.get(
        "/api/internal/hermes/leoft/reviews/8-tWczOxMlNb",
        headers={
            "X-AgenteOS-Timestamp": str(int(time.time())),
            "X-AgenteOS-Signature": "invalid",
            "X-AgenteOS-Idempotency-Key": "idem-get-review",
        },
    )

    assert response.status_code == 401


def test_hmac_valid_allows_idempotent_approve_contract(monkeypatch):
    secret = "local-test-secret"
    review = _review_row(status="approved")
    db = AsyncMock()
    db.scalar.side_effect = [review, _plan_row(review.plan_id, status="approved"), _access_row(), _access_row()]
    db.get.return_value = _plan_row(review.plan_id, status="approved")

    app = FastAPI()
    app.include_router(router)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("HERMES_LEOFT_HMAC_SECRET", secret)
    body = b'{"actor":"hermes"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    client = TestClient(app)
    response = client.post(
        "/api/internal/hermes/leoft/reviews/8-tWczOxMlNb/approve",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-AgenteOS-Timestamp": timestamp,
            "X-AgenteOS-Signature": signature,
            "X-AgenteOS-Idempotency-Key": "idem-approve-review",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["idempotent"] is True
    assert payload["review_status"] == "approved"
    assert payload["plan_status"] == "approved"
    assert payload["student_access_exists"] is True
    _assert_sanitized(payload)


def test_hmac_invalid_blocks_approve_before_side_effects(monkeypatch):
    db = AsyncMock()
    app = FastAPI()
    app.include_router(router)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("HERMES_LEOFT_HMAC_SECRET", "local-test-secret")

    client = TestClient(app)
    response = client.post(
        "/api/internal/hermes/leoft/reviews/8-tWczOxMlNb/approve",
        json={"actor": "hermes"},
        headers={
            "X-AgenteOS-Timestamp": str(int(time.time())),
            "X-AgenteOS-Signature": "invalid",
            "X-AgenteOS-Idempotency-Key": "idem-approve-review",
        },
    )

    assert response.status_code == 401
    db.scalar.assert_not_called()
