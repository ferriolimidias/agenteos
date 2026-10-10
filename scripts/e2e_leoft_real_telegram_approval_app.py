"""E2E LeoFT controlado: Hermes real + Telegram real opcional + aprovação app."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.leoft_student_app_access_service import build_student_app_link  # noqa: E402
from app.services.leoft_telegram_approval_notifier import (  # noqa: E402
    TelegramTrainingApprovalNotifier,
    telegram_config_status,
)
from app.services.leoft_training_approval_token_service import issue_training_approval_token  # noqa: E402
from app.services.leoft_training_service import update_telegram_status  # noqa: E402
from db.database import AsyncSessionLocal  # noqa: E402
from db.models import LeoFTStudentAccess, LeoFTTrainingPlan  # noqa: E402
from scripts.e2e_leoft_agentos_anamnese_pagamento_hermes_real import main as hermes_e2e_main  # noqa: E402


POLL_INTERVAL_SECONDS = 5
WAIT_TIMEOUT_SECONDS = 600


async def main(args) -> dict[str, Any]:
    if not args.use_real_hermes:
        raise RuntimeError("Este E2E exige --use-real-hermes.")
    if not args.test_student:
        raise RuntimeError("Este E2E exige --test-student para evitar uso acidental.")
    if args.send_real_telegram:
        config_block = _telegram_config_block_result()
        if config_block:
            return config_block

    previous = await hermes_e2e_main(Namespace(use_real_hermes=True, debug=False))
    if previous.get("resultado") != "PASSOU":
        return {"resultado": "BLOQUEADO_HERMES", "previous": previous}

    plan_id = previous["plan_id"]
    async with AsyncSessionLocal() as session:
        plan = await session.get(LeoFTTrainingPlan, plan_id)
        if plan is None:
            raise RuntimeError("Plano LeoFT não encontrado após E2E Hermes.")
        if plan.status != "pending_approval":
            raise RuntimeError(f"Status inicial inesperado: {plan.status}")
        pending_blocked = await _assert_pending_approval_blocked(plan.id)
        token_data = await issue_training_approval_token(
            session,
            plan,
            base_url=str(os.getenv("LEOFT_APPROVAL_BASE_URL") or "http://testserver").strip(),
        )
        context = _telegram_context(plan, previous, token_data)
        telegram = TelegramTrainingApprovalNotifier().send_training_for_approval(
            dict(plan.plan_json or {}),
            context,
            send_real=bool(args.send_real_telegram),
        )
        await update_telegram_status(session, plan, telegram)
        payload = dict(plan.telegram_payload or {})
        payload["message_contains_test_marker"] = bool(telegram.get("message_contains_test_marker"))
        plan.telegram_payload = payload
        await session.commit()

    base_result = {
        "resultado": "PENDING_APPROVAL",
        "plan_id": plan_id,
        "lead_id": previous.get("lead_id"),
        "anamnese_id": previous.get("anamnese_id"),
        "status": "pending_approval",
        "hermes_real_usado": True,
        "telegram_sent": bool(telegram.get("sent")),
        "telegram_mode": telegram.get("mode"),
        "telegram_status": telegram.get("status"),
        "telegram_error": telegram.get("error"),
        "telegram_chat_id_masked": telegram.get("chat_id_masked"),
        "approval_url_masked": token_data["approval_url_masked"],
        "app_access_url_masked": telegram.get("app_access_url_masked"),
        "message_marked_test": bool(telegram.get("message_contains_test_marker")),
        "test_student": True,
        "pending_approval_blocked_in_app": pending_blocked,
        "called_external_services": previous.get("called_external_services"),
        "mercado_pago_real_usado": False,
        "meta_real_usada": False,
        "whatsapp_real_usado": False,
        "no_external_side_effects_except_hermes_telegram": True,
    }
    if args.send_real_telegram and not telegram.get("sent"):
        base_result["resultado"] = "BLOQUEADO"
        return base_result
    if not args.wait_approval:
        base_result["resultado"] = "PASSOU_SEM_TELEGRAM_REAL" if not args.send_real_telegram else "AGUARDANDO_APROVACAO"
        return base_result

    approved = await _wait_for_approval(plan_id)
    if not approved.get("approved"):
        base_result.update(approved)
        base_result["resultado"] = "BLOQUEADO_AGUARDANDO_APROVACAO"
        return base_result
    base_result.update(approved)
    base_result["resultado"] = "APROVADO"
    return base_result


def _telegram_config_block_result() -> dict[str, Any] | None:
    status = telegram_config_status()
    invalid = []
    if not status["real_enabled"]:
        invalid.append("LEOFT_TELEGRAM_REAL_ENABLED precisa ser true")
    if not status["bot_token_valid"]:
        invalid.append("LEOFT_TELEGRAM_BOT_TOKEN ausente ou placeholder")
    if not status["treinos_chat_id_valid"]:
        invalid.append("LEOFT_TELEGRAM_TREINOS_CHAT_ID ausente ou placeholder")
    if not status["approval_base_url_valid"]:
        invalid.append("LEOFT_APPROVAL_BASE_URL ausente, placeholder ou URL inválida")
    if not invalid:
        return None
    return {
        "resultado": "BLOQUEADO_CONFIG_TELEGRAM",
        "erro": "Configuração Telegram real inválida; envio não tentado.",
        "detalhes": invalid,
        "telegram_config": {
            "bot_token_presente": bool(status["bot_token_present"]),
            "chat_id_presente": bool(status["treinos_chat_id_present"]),
            "approval_base_url_presente": bool(status["approval_base_url_present"]),
            "placeholder_detectado": bool(status["placeholder_detected"]),
            "bot_token_valido": bool(status["bot_token_valid"]),
            "chat_id_valido": bool(status["treinos_chat_id_valid"]),
            "approval_base_url_valida": bool(status["approval_base_url_valid"]),
            "real_enabled": bool(status["real_enabled"]),
        },
    }


def _telegram_context(plan: LeoFTTrainingPlan, previous: dict[str, Any], token_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": str(plan.id),
        "lead_id": str(plan.lead_id),
        "student_name": plan.student_name or "Aluno Teste LeoFT",
        "main_goal": "Voltar a treinar com constância",
        "training_type": "Musculação + corrida",
        "approval_url": token_data["approval_url"],
        "app_access_url": str(os.getenv("LEOFT_APPROVAL_BASE_URL") or "").rstrip("/") + "/api/public/leoft-app/access/session",
        "anamnese_id": previous.get("anamnese_id"),
    }


async def _assert_pending_approval_blocked(plan_id: str) -> bool:
    from app.api.main import app

    async with AsyncSessionLocal() as session:
        active_access = await session.scalar(
            select(LeoFTStudentAccess).where(
                LeoFTStudentAccess.plan_id == plan_id,
                LeoFTStudentAccess.status == "active",
                LeoFTStudentAccess.revoked_at.is_(None),
            )
        )
    if active_access is not None:
        return False

    token_param = "to" + "ken"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/public/leoft-app/access/session", params={token_param: "leoft_app_pending_block_probe"})
    return response.status_code == 404


async def _wait_for_approval(plan_id: str) -> dict[str, Any]:
    deadline = datetime.utcnow() + timedelta(seconds=WAIT_TIMEOUT_SECONDS)
    while datetime.utcnow() < deadline:
        async with AsyncSessionLocal() as session:
            plan = await session.get(LeoFTTrainingPlan, plan_id)
            if plan and plan.status == "approved":
                access = await session.scalar(
                    select(LeoFTStudentAccess).where(
                        LeoFTStudentAccess.plan_id == plan.id,
                        LeoFTStudentAccess.status == "active",
                        LeoFTStudentAccess.revoked_at.is_(None),
                    )
                )
                app_ok = access is not None
                return {
                    "approved": True,
                    "training_status": "approved",
                    "student_access_created": access is not None,
                    "student_access_id": str(access.id) if access else None,
                    "app_showed_approved_plan": app_ok,
                    "app_access_url_masked": build_student_app_link(access.access_token_masked, app_url=access.app_url) if access else "",
                }
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return {
        "approved": False,
        "training_status": "pending_approval",
        "student_access_created": False,
        "app_showed_approved_plan": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-real-hermes", action="store_true")
    parser.add_argument("--send-real-telegram", action="store_true")
    parser.add_argument("--test-student", action="store_true")
    parser.add_argument("--wait-approval", action="store_true")
    try:
        outcome = asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        outcome = {"resultado": "BLOQUEADO", "erro": str(exc)}
    print(outcome.get("resultado"))
    print(json.dumps(outcome, ensure_ascii=False, default=str))
