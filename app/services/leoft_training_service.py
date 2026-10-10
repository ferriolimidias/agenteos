from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.services.leoft_plan_contract import validate_leoft_plan_contract
from db.models import LeoFTTrainingPlan


ALLOWED_TELEGRAM_STATUSES = {"dry_run", "sent", "failed", "disabled"}


def _required_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or not isinstance(plan.get("months"), list) or len(plan["months"]) < 3:
        raise ValueError("Plano LeoFT inválido: months[1..3] obrigatório.")


async def create_pending_training_plan(session, **data: Any) -> LeoFTTrainingPlan:
    plan_json = dict(data["plan_json"])
    plan_json = validate_leoft_plan_contract(plan_json)
    _required_plan(plan_json)
    existing = await session.scalar(
        select(LeoFTTrainingPlan).where(
            LeoFTTrainingPlan.empresa_id == uuid.UUID(str(data["empresa_id"])),
            LeoFTTrainingPlan.payment_reference == str(data.get("payment_reference") or ""),
        )
    )
    if existing:
        return existing
    item = LeoFTTrainingPlan(
        id=uuid.UUID(str(plan_json.get("planId"))) if plan_json.get("planId") else uuid.uuid4(),
        empresa_id=uuid.UUID(str(data["empresa_id"])), lead_id=uuid.UUID(str(data["lead_id"])),
        conversation_id=data.get("conversation_id"), token_reference=data.get("token_reference"),
        anamnese_token_hash=data.get("token_reference"), anamnese_status=data.get("anamnese_status"),
        payment_reference=data.get("payment_reference"), payment_status=data.get("payment_status"),
        payment_mode=data.get("payment_mode"), payment_provider=data.get("payment_provider"),
        student_name=data.get("student", {}).get("name"),
        student_email=data.get("student", {}).get("email"), student_whatsapp=data.get("student", {}).get("whatsapp"),
        plan_json=plan_json, hermes_payload=data.get("hermes_payload"), hermes_mode=data.get("hermes_mode", "mock"),
        status="pending_approval",
    )
    session.add(item)
    await session.flush()
    return item


async def get_training_plan(session, plan_id: str, empresa_id: str) -> LeoFTTrainingPlan | None:
    result = await session.execute(select(LeoFTTrainingPlan).where(LeoFTTrainingPlan.id == uuid.UUID(plan_id), LeoFTTrainingPlan.empresa_id == uuid.UUID(empresa_id)))
    return result.scalars().first()


async def update_telegram_status(session, plan: LeoFTTrainingPlan, result: dict[str, Any]) -> LeoFTTrainingPlan:
    status = str(result.get("status") or "").strip().lower()
    if status == "real":
        status = "failed"
    if status not in ALLOWED_TELEGRAM_STATUSES:
        status = "sent" if result.get("sent") else "dry_run"
    plan.telegram_status = status
    payload = dict(plan.telegram_payload or {})
    payload.update({key: value for key, value in result.items() if key not in {"message", "approval_url"}})
    plan.telegram_payload = payload
    if status == "sent":
        plan.telegram_sent_at = datetime.utcnow()
    return plan
