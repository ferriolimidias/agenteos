"""Verificações de segurança do delivery LeoFT para app do aluno."""
from __future__ import annotations

import asyncio
import json
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import get_password_hash, is_bcrypt_hash
from app.services.leoft_plan_contract import LEOFT_PLAN_SCHEMA_VERSION
from app.services.leoft_security import hash_token, mask_token
from db.database import AsyncSessionLocal
from db.models import LeoFTStudentAccess, LeoFTTrainingPlan

FORBIDDEN_PLAN_KEYS = {
    "payment_reference",
    "tokenReferenceHash",
    "leadToken",
    "hermes_payload",
    "telegram_payload",
    "access_token_hash",
    "password_hash",
}


async def main() -> dict:
    approved_token = None
    pending_token = "pending_check_" + uuid.uuid4().hex
    approved_access_id = None
    pending_plan_id = None

    async with AsyncSessionLocal() as session:
        approved = await session.scalar(
            select(LeoFTTrainingPlan)
            .where(LeoFTTrainingPlan.status == "approved")
            .order_by(LeoFTTrainingPlan.updated_at.desc())
        )
        if approved is None:
            raise RuntimeError("Nenhum plano approved encontrado para verificar.")
        approved_access = await session.scalar(
            select(LeoFTStudentAccess)
            .where(LeoFTStudentAccess.plan_id == approved.id, LeoFTStudentAccess.status == "active")
            .order_by(LeoFTStudentAccess.updated_at.desc())
        )
        if approved_access is None:
            raise RuntimeError("Nenhum acesso ativo encontrado para plano approved.")
        approved_access_id = str(approved_access.id)
        _assert_access_storage_safe(approved_access)

        pending = await session.scalar(
            select(LeoFTTrainingPlan)
            .where(LeoFTTrainingPlan.status == "pending_approval")
            .order_by(LeoFTTrainingPlan.created_at.desc())
        )
        if pending is None:
            pending_plan = dict(approved.plan_json or {})
            pending_plan["planId"] = str(uuid.uuid4())
            pending_plan["status"] = "pending_approval"
            pending = LeoFTTrainingPlan(
                id=uuid.UUID(pending_plan["planId"]),
                empresa_id=approved.empresa_id,
                lead_id=approved.lead_id,
                payment_reference="verificador_app_delivery_" + uuid.uuid4().hex,
                payment_status="approved_simulated",
                product_name=approved.product_name,
                status="pending_approval",
                student_name=approved.student_name,
                student_email=approved.student_email,
                student_whatsapp=approved.student_whatsapp,
                plan_json=pending_plan,
                hermes_mode="mock",
                telegram_status="dry_run",
            )
            session.add(pending)
            await session.flush()
        pending_plan_id = str(pending.id)
        pending_access = LeoFTStudentAccess(
            id=uuid.uuid4(),
            empresa_id=pending.empresa_id,
            lead_id=pending.lead_id,
            plan_id=pending.id,
            student_email=pending.student_email or approved_access.student_email,
            student_whatsapp=pending.student_whatsapp,
            login_identifier=pending.student_email or approved_access.student_email,
            password_hash=get_password_hash("LeoFT-Pending-Synthetic-123"),
            temporary_password_masked="LeoF...c123",
            access_token_hash=hash_token(pending_token),
            access_token_masked=mask_token(pending_token),
            app_url="https://app.leoft.local/aluno",
            status="active",
        )
        session.add(pending_access)
        await session.commit()

    from app.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        pending_response = await client.get("/api/public/leoft-app/plan", params={"token": pending_token})
        if pending_response.status_code == 200:
            raise RuntimeError("Endpoint retornou plano pending_approval para app.")

        async with AsyncSessionLocal() as session:
            approved_access = await session.get(LeoFTStudentAccess, approved_access_id)
            approved_token = "verify_" + uuid.uuid4().hex
            approved_access.access_token_hash = hash_token(approved_token)
            approved_access.access_token_masked = mask_token(approved_token)
            await session.commit()

        session_response = await client.get("/api/public/leoft-app/access/session", params={"token": approved_token})
        plan_response = await client.get("/api/public/leoft-app/plan", params={"token": approved_token})

    if session_response.status_code != 200:
        raise RuntimeError(f"Endpoint session approved falhou: {session_response.status_code}")
    if plan_response.status_code != 200:
        raise RuntimeError(f"Endpoint plan approved falhou: {plan_response.status_code}")
    plan_payload = plan_response.json()
    _assert_response_safe(plan_payload, approved_token)
    if plan_payload.get("plan", {}).get("schemaVersion") != LEOFT_PLAN_SCHEMA_VERSION:
        raise RuntimeError("plan_json retornado não tem schemaVersion.")

    _assert_no_dispatcher_in_dry_run_source()
    result = {
        "resultado": "PASSOU",
        "approved_access_id": approved_access_id,
        "pending_plan_id": pending_plan_id,
        "session_endpoint_status": session_response.status_code,
        "plan_endpoint_status": plan_response.status_code,
        "pending_endpoint_status": pending_response.status_code,
        "whatsapp_dispatcher_called": False,
        "raw_access_token_saved": False,
        "raw_password_saved": False,
    }
    print("PASSOU")
    print(json.dumps(result, ensure_ascii=False, default=str))
    return result


def _assert_access_storage_safe(access: LeoFTStudentAccess) -> None:
    if not is_bcrypt_hash(access.password_hash):
        raise RuntimeError("password_hash do acesso não é bcrypt.")
    serialized = json.dumps(
        {
            "password_hash": access.password_hash,
            "temporary_password_masked": access.temporary_password_masked,
            "access_token_hash": access.access_token_hash,
            "access_token_masked": access.access_token_masked,
        },
        ensure_ascii=False,
        default=str,
    )
    if "leoft_app_" in serialized or "Senha temporária:" in serialized:
        raise RuntimeError("Segredo bruto aparenta estar persistido no acesso.")
    if not access.access_token_hash or len(access.access_token_hash) != 64:
        raise RuntimeError("access_token_hash inválido.")


def _assert_response_safe(payload, raw_token: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if raw_token in serialized:
        raise RuntimeError("Endpoint expôs access token bruto.")
    for key in FORBIDDEN_PLAN_KEYS:
        if f'"{key}"' in serialized:
            raise RuntimeError(f"Endpoint retornou campo proibido: {key}")


def _assert_no_dispatcher_in_dry_run_source() -> None:
    source = open("app/services/leoft_student_app_access_service.py", encoding="utf-8").read()
    if "dispatcher" in source or "send_message" in source or "Evolution" in source:
        raise RuntimeError("Serviço dry-run de acesso referencia dispatcher de envio real.")


if __name__ == "__main__":
    asyncio.run(main())
