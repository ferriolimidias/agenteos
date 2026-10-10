from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers.leoft_internal import rotate_student_access_token


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, *, existing=None, review=None, plan=None, accesses=None):
        self.existing = existing
        self.review = review
        self.plan = plan
        self.accesses = accesses or []
        self.added = []
        self.committed = False

    async def scalar(self, statement):
        text = str(statement)
        if "leoft_integration_events" in text:
            return self.existing
        if "leoft_training_review_sessions" in text:
            return self.review
        return self.plan

    async def scalars(self, statement):
        return _ScalarRows(self.accesses)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


def _fixture(*, review_status="approved", plan_status="approved", revoked_at=None, accesses=1):
    plan_id = uuid4()
    review = SimpleNamespace(id=uuid4(), review_public_id="8-tWczOxMlNb", plan_id=plan_id, status=review_status)
    plan = SimpleNamespace(id=plan_id, status=plan_status, empresa_id=uuid4())
    rows = [SimpleNamespace(
        id=uuid4(), status="active", revoked_at=revoked_at, plan_id=plan_id,
        access_token_hash="old-hash", access_token_masked="leoft_...old", app_url="old",
        updated_at=None,
    ) for _ in range(accesses)]
    return review, plan, rows


@pytest.mark.asyncio
async def test_rotation_success_preserves_id_and_returns_real_token_only_in_response(monkeypatch):
    review, plan, accesses = _fixture()
    db = _DB(review=review, plan=plan, accesses=accesses)
    monkeypatch.setenv("LEOFT_STUDENT_APP_URL", "https://app.leoft.com.br/acesso")
    with patch("app.api.routers.leoft_internal._resolve_rotation_app_url", new=AsyncMock(return_value="https://app.leoft.com.br/acesso")):
        result = await rotate_student_access_token("8-tWczOxMlNb", SimpleNamespace(actor="telegram:admin", reason="administrative_reissue"), ("rotation-test", "request-hash"), db)
    assert result["ok"] is True
    assert result["access"]["status"] == "active"
    assert result["access"]["id"] == str(accesses[0].id)
    assert result["access"]["access_token"].startswith("leoft_app_")
    assert result["access"]["app_url"] == "https://app.leoft.com.br/acesso"
    assert db.committed is True
    assert len(accesses) == 1
    assert accesses[0].access_token_hash != "old-hash"


@pytest.mark.asyncio
@pytest.mark.parametrize(("review_status", "plan_status", "revoked_at", "accesses", "code"), [
    ("pending", "approved", None, 1, "review_not_approved"),
    ("approved", "pending_approval", None, 1, "plan_not_approved"),
    ("approved", "approved", datetime.utcnow(), 1, "student_access_revoked"),
    ("approved", "approved", None, 2, "multiple_active_student_accesses"),
])
async def test_rotation_rejects_incompatible_state(review_status, plan_status, revoked_at, accesses, code):
    review, plan, rows = _fixture(review_status=review_status, plan_status=plan_status, revoked_at=revoked_at, accesses=accesses)
    db = _DB(review=review, plan=plan, accesses=rows)
    with pytest.raises(HTTPException) as error:
        await rotate_student_access_token("8-tWczOxMlNb", SimpleNamespace(actor="admin", reason="administrative_reissue"), ("rotation-test", "request-hash"), db)
    assert error.value.detail == code
    assert db.committed is False


@pytest.mark.asyncio
async def test_rotation_missing_access_is_404():
    review, plan, _ = _fixture(accesses=0)
    with pytest.raises(HTTPException) as error:
        await rotate_student_access_token("8-tWczOxMlNb", SimpleNamespace(actor="admin", reason="administrative_reissue"), ("rotation-test", "request-hash"), _DB(review=review, plan=plan, accesses=[]))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_same_idempotency_key_does_not_rotate_again():
    review, plan, accesses = _fixture()
    db = _DB(existing=SimpleNamespace(), review=review, plan=plan, accesses=accesses)
    with pytest.raises(HTTPException) as error:
        await rotate_student_access_token("8-tWczOxMlNb", SimpleNamespace(actor="admin", reason="administrative_reissue"), ("rotation-test", "request-hash"), db)
    assert error.value.status_code == 409
    assert error.value.detail == "idempotency_key_already_processed"
    assert accesses[0].access_token_hash == "old-hash"

