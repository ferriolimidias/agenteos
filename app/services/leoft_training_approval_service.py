from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.services.leoft_plan_contract import validate_leoft_plan_contract
from app.services.leoft_student_app_access_service import create_or_get_student_access_for_approved_plan
from db.database import AsyncSessionLocal
from db.models import LeoFTTrainingPlan

APPROVABLE_STATUSES = {"pending_approval", "revision_requested"}


async def approve_training_plan(plan_id, approved_by, notes=None) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        plan = await _load_plan(session, plan_id)
        result = await approve_training_plan_in_session(session, plan, approved_by=approved_by, notes=notes)
        await session.commit()
        return result


async def approve_training_plan_in_session(session, plan: LeoFTTrainingPlan, *, approved_by, notes=None) -> dict[str, Any]:
    if plan.status not in APPROVABLE_STATUSES:
        raise ValueError(f"Plano LeoFT não pode ser aprovado a partir de {plan.status}.")
    plan_json = dict(plan.plan_json or {})
    plan_json["status"] = "approved"
    validate_leoft_plan_contract(plan_json)
    now = datetime.utcnow()
    plan.status = "approved"
    plan.plan_json = plan_json
    plan.approved_at = now
    plan.approved_by = str(approved_by or "").strip()
    plan.approval_notes = str(notes or "").strip() or None
    plan.rejected_at = None
    plan.rejected_by = None
    plan.rejection_reason = None
    plan.revision_requested_at = None
    plan.revision_requested_by = None
    plan.revision_notes = None
    plan.updated_at = now
    flag_modified(plan, "plan_json")
    app_access = await create_or_get_student_access_for_approved_plan(session, plan)
    return {"plan_id": str(plan.id), "status": plan.status, "app_access": app_access}


async def reject_training_plan(plan_id, rejected_by, reason) -> dict[str, Any]:
    if not str(reason or "").strip():
        raise ValueError("Motivo de rejeição é obrigatório.")
    async with AsyncSessionLocal() as session:
        plan = await _load_plan(session, plan_id)
        now = datetime.utcnow()
        plan.status = "rejected"
        plan.rejected_at = now
        plan.rejected_by = str(rejected_by or "").strip() or None
        plan.rejection_reason = f"{str(rejected_by or '').strip()}: {str(reason).strip()}".strip(": ")
        plan.updated_at = now
        await session.commit()
        return {"plan_id": str(plan.id), "status": plan.status}


async def request_training_revision(plan_id, requested_by, notes) -> dict[str, Any]:
    if not str(notes or "").strip():
        raise ValueError("Notas de revisão são obrigatórias.")
    async with AsyncSessionLocal() as session:
        plan = await _load_plan(session, plan_id)
        now = datetime.utcnow()
        plan.status = "revision_requested"
        plan.revision_requested_at = now
        plan.revision_requested_by = str(requested_by or "").strip() or None
        plan.revision_notes = f"{str(requested_by or '').strip()}: {str(notes).strip()}".strip(": ")
        plan.updated_at = now
        await session.commit()
        return {"plan_id": str(plan.id), "status": plan.status}


async def _load_plan(session, plan_id) -> LeoFTTrainingPlan:
    try:
        parsed = uuid.UUID(str(plan_id))
    except ValueError as exc:
        raise ValueError("plan_id inválido.") from exc
    plan = await session.get(LeoFTTrainingPlan, parsed)
    if plan is None:
        raise ValueError("Plano LeoFT não encontrado.")
    return plan
