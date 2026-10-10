from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.services.leoft_security import hash_token, mask_token
from app.services.leoft_training_approval_service import approve_training_plan_in_session
from db.models import LeoFTTrainingPlan


APPROVAL_TOKEN_PREFIX = "leoft_approve"


def build_approval_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def mask_approval_url(url: str) -> str:
    text = str(url or "")
    if "token=" not in text:
        return text
    prefix, token = text.split("token=", 1)
    return prefix + "token=" + mask_token(token)


async def issue_training_approval_token(session, plan: LeoFTTrainingPlan, *, base_url: str) -> dict[str, Any]:
    if plan.status != "pending_approval":
        raise ValueError("Token de aprovação só pode ser gerado para treino pending_approval.")
    parsed_base_url = build_approval_base_url(base_url)
    if not parsed_base_url:
        raise ValueError("LEOFT_APPROVAL_BASE_URL ausente.")
    secret = secrets.token_urlsafe(32)
    raw_token = f"{APPROVAL_TOKEN_PREFIX}.{plan.id}.{secret}"
    token_hash = hash_token(raw_token)
    payload = dict(plan.telegram_payload or {})
    payload["approval_token_hash"] = token_hash
    payload["approval_token_masked"] = mask_token(raw_token)
    payload["approval_token_issued_at"] = datetime.utcnow().isoformat()
    payload["approval_base_url"] = parsed_base_url
    plan.telegram_payload = payload
    flag_modified(plan, "telegram_payload")
    await session.flush()
    approval_url = f"{parsed_base_url}/api/public/leoft-training/approve?token={raw_token}"
    return {
        "approval_url": approval_url,
        "approval_url_masked": mask_approval_url(approval_url),
        "approval_token_masked": payload["approval_token_masked"],
    }


async def approve_training_plan_by_token(session, raw_token: str, *, approved_by: str = "telegram") -> dict[str, Any]:
    plan_id = _plan_id_from_token(raw_token)
    plan = await session.get(LeoFTTrainingPlan, plan_id)
    if plan is None:
        raise ValueError("approval_token_invalid")
    payload = dict(plan.telegram_payload or {})
    expected_hash = str(payload.get("approval_token_hash") or "")
    if not expected_hash or expected_hash != hash_token(raw_token):
        raise ValueError("approval_token_invalid")
    if plan.status == "approved":
        access = await _existing_or_create_access(session, plan)
        await session.commit()
        return {"plan_id": str(plan.id), "status": "approved", "idempotent": True, "app_access": access}
    approval = await approve_training_plan_in_session(
        session,
        plan,
        approved_by=approved_by,
        notes="Aprovado via Telegram LeoFT.",
    )
    payload = dict(plan.telegram_payload or {})
    payload["approval_token_used_at"] = datetime.utcnow().isoformat()
    plan.telegram_payload = payload
    flag_modified(plan, "telegram_payload")
    await session.commit()
    approval["idempotent"] = False
    return approval


async def _existing_or_create_access(session, plan: LeoFTTrainingPlan) -> dict[str, Any]:
    from app.services.leoft_student_app_access_service import create_or_get_student_access_for_approved_plan

    return await create_or_get_student_access_for_approved_plan(session, plan)


def _plan_id_from_token(raw_token: str) -> uuid.UUID:
    parts = str(raw_token or "").split(".")
    if len(parts) != 3 or parts[0] != APPROVAL_TOKEN_PREFIX or not parts[2]:
        raise ValueError("approval_token_invalid")
    try:
        return uuid.UUID(parts[1])
    except ValueError as exc:
        raise ValueError("approval_token_invalid") from exc
