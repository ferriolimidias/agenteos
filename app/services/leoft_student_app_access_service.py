from __future__ import annotations

import secrets
import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.security import get_password_hash
from app.services.leoft_security import hash_token, mask_token
from db.models import Empresa, LeoFTStudentAccess, LeoFTTrainingPlan

DEFAULT_LEOFT_APP_URL = "https://app.leoft.com.br/acesso"


async def create_or_get_student_access_for_approved_plan(session, plan: LeoFTTrainingPlan) -> dict[str, Any]:
    if plan.status != "approved":
        raise ValueError("Acesso do aluno só pode ser criado para plano approved.")
    email = _student_email(plan)
    if not email:
        raise ValueError("Plano aprovado precisa de e-mail do aluno para login.")

    access_token = "leoft_app_" + secrets.token_urlsafe(32)
    temporary_password = _temporary_password()
    temporary_password_masked = _mask_password(temporary_password)
    access_token_hash = hash_token(access_token)
    access_token_masked = mask_token(access_token)
    app_url = await _resolve_app_url(session, plan)

    access = await session.scalar(
        select(LeoFTStudentAccess).where(
            LeoFTStudentAccess.empresa_id == plan.empresa_id,
            LeoFTStudentAccess.plan_id == plan.id,
            LeoFTStudentAccess.status == "active",
        )
    )
    if access is None:
        access = LeoFTStudentAccess(
            id=uuid.uuid4(),
            empresa_id=plan.empresa_id,
            lead_id=plan.lead_id,
            plan_id=plan.id,
            student_email=email,
            student_whatsapp=plan.student_whatsapp,
            login_identifier=email,
            password_hash=get_password_hash(temporary_password),
            temporary_password_masked=temporary_password_masked,
            access_token_hash=access_token_hash,
            access_token_masked=access_token_masked,
            app_url=app_url,
            status="active",
        )
        session.add(access)
    else:
        access.student_email = email
        access.student_whatsapp = plan.student_whatsapp
        access.login_identifier = email
        access.access_token_hash = access_token_hash
        access.access_token_masked = access_token_masked
        access.app_url = app_url
        access.updated_at = datetime.utcnow()
    await session.flush()

    return {
        "id": str(access.id),
        "empresa_id": str(access.empresa_id),
        "lead_id": str(access.lead_id),
        "plan_id": str(access.plan_id) if access.plan_id else None,
        "student_email": access.student_email,
        "student_whatsapp": access.student_whatsapp,
        "login_identifier": access.login_identifier,
        "temporary_password_masked": access.temporary_password_masked,
        "access_token": access_token,
        "access_token_masked": access.access_token_masked,
        "app_url": access.app_url,
        "app_link": build_student_app_link(access_token, app_url=access.app_url),
        "app_link_masked": build_student_app_link(access.access_token_masked, app_url=access.app_url),
        "status": access.status,
    }


def build_student_app_link(access_token: str, *, app_url: str | None = None) -> str:
    base = str(app_url or DEFAULT_LEOFT_APP_URL).rstrip("/")
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}token={access_token}"


def build_dry_run_whatsapp_access_message(access: dict[str, Any]) -> str:
    return (
        "Seu treino LeoFT foi aprovado.\n\n"
        "Acesse o app:\n"
        f"{access.get('app_link_masked') or access.get('app_link')}\n\n"
        "Login:\n"
        f"{access.get('student_email')}\n\n"
        "Senha temporária:\n"
        f"{access.get('temporary_password_masked')}\n\n"
        "Por segurança, troque a senha no primeiro acesso."
    )


def public_access_payload(access: LeoFTStudentAccess) -> dict[str, Any]:
    return {
        "id": str(access.id),
        "student_id": str(access.student_id) if access.student_id else None,
        "empresa_id": str(access.empresa_id),
        "plan_id": str(access.plan_id),
        "student_email": access.student_email,
        "student_whatsapp": access.student_whatsapp,
        "login_identifier": access.login_identifier,
        "access_token_masked": access.access_token_masked,
        "app_url": access.app_url,
        "status": access.status,
    }


async def _resolve_app_url(session, plan: LeoFTTrainingPlan) -> str:
    configured_env = str(os.getenv("LEOFT_STUDENT_APP_URL") or "").strip()
    if configured_env:
        return configured_env.rstrip("/")
    empresa = await session.get(Empresa, plan.empresa_id)
    credenciais = getattr(empresa, "credenciais_canais", None) if empresa else None
    if isinstance(credenciais, dict):
        leoft = credenciais.get("leoft") if isinstance(credenciais.get("leoft"), dict) else {}
        configured = str(leoft.get("student_app_url") or leoft.get("app_url") or "").strip()
        if configured:
            return configured
    return DEFAULT_LEOFT_APP_URL


def _student_email(plan: LeoFTTrainingPlan) -> str:
    email = str(plan.student_email or "").strip().lower()
    if email:
        return email
    plan_json = plan.plan_json if isinstance(plan.plan_json, dict) else {}
    return str(plan_json.get("studentEmail") or "").strip().lower()


def _temporary_password() -> str:
    return "LeoFT-" + secrets.token_urlsafe(18)


def _mask_password(password: str) -> str:
    if len(password) <= 8:
        return "****"
    return f"{password[:4]}...{password[-4:]}"
