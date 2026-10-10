"""E2E LeoFT: aprovar treino e entregar acesso ao app em modo controlado."""
from __future__ import annotations

import argparse
import asyncio
import json
from argparse import Namespace
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.services.leoft_student_app_access_service import build_dry_run_whatsapp_access_message
from app.services.leoft_training_approval_service import approve_training_plan
from db.database import AsyncSessionLocal
from db.models import LeoFTStudentAccess, LeoFTTrainingPlan
from scripts import e2e_leoft_agentos_token_anamnese_pagamento_hermes_telegram as safe_e2e

APPROVED_BY = "Leonardo Ferrioli Telles - E2E"
APPROVAL_NOTES = "Aprovado em teste E2E controlado."
FORBIDDEN_RESPONSE_KEYS = {
    "password",
    "password_hash",
    "temporary_password",
    "access_token",
    "access_token_hash",
    "payment_reference",
    "tokenReferenceHash",
    "leadToken",
    "hermes_payload",
    "telegram_payload",
}


async def main(args) -> dict[str, Any]:
    if getattr(args, "send_whatsapp_real", False):
        raise RuntimeError("Envio real de WhatsApp está desabilitado neste E2E.")
    if getattr(args, "send_telegram_real", False):
        raise RuntimeError("Envio real de Telegram está desabilitado neste E2E.")
    if getattr(args, "use_real_hermes", False):
        raise RuntimeError("Hermes real está desabilitado neste E2E.")

    previous = await safe_e2e.main(Namespace(send_telegram_real=False))
    plan_id = previous["plan_id"]
    approval = await approve_training_plan(plan_id, APPROVED_BY, APPROVAL_NOTES)
    access = approval["app_access"]
    access_token = access["access_token"]
    dry_run_message = build_dry_run_whatsapp_access_message(access)

    from app.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.get("/api/public/leoft-app/access/session", params={"token": access_token})
        plan_response = await client.get("/api/public/leoft-app/plan", params={"token": access_token})

    if session_response.status_code != 200:
        raise RuntimeError(f"Endpoint session falhou: {session_response.status_code} {session_response.text}")
    if plan_response.status_code != 200:
        raise RuntimeError(f"Endpoint plan falhou: {plan_response.status_code} {plan_response.text}")

    session_payload = session_response.json()
    plan_payload = plan_response.json()
    _assert_no_raw_secret("session endpoint", session_payload, access_token)
    _assert_no_raw_secret("plan endpoint", plan_payload, access_token)
    _assert_no_forbidden_keys("session endpoint", session_payload)
    _assert_no_forbidden_keys("plan endpoint", plan_payload)
    if plan_payload.get("plan", {}).get("status") != "approved":
        raise RuntimeError("Endpoint do app não retornou plano approved.")
    if "Senha temporária:" not in dry_run_message or access["temporary_password_masked"] not in dry_run_message:
        raise RuntimeError("Mensagem dry-run de acesso não contém senha mascarada.")
    if access_token in dry_run_message:
        raise RuntimeError("Mensagem dry-run expôs access token bruto.")

    async with AsyncSessionLocal() as db:
        plan = await db.get(LeoFTTrainingPlan, plan_id)
        access_row = await db.get(LeoFTStudentAccess, access["id"])
        if plan is None or access_row is None:
            raise RuntimeError("Plano aprovado ou acesso não encontrado no banco.")
        if plan.status != "approved" or dict(plan.plan_json or {}).get("status") != "approved":
            raise RuntimeError("Status approved não foi persistido corretamente.")
        stored = json.dumps(
            {
                "access": {
                    "password_hash": access_row.password_hash,
                    "temporary_password_masked": access_row.temporary_password_masked,
                    "access_token_hash": access_row.access_token_hash,
                    "access_token_masked": access_row.access_token_masked,
                },
                "plan": plan.plan_json,
            },
            ensure_ascii=False,
            default=str,
        )
        if access_token in stored:
            raise RuntimeError("Access token bruto persistido no banco.")
        if access["temporary_password_masked"] not in stored:
            raise RuntimeError("Senha temporária mascarada não foi registrada.")
        if access_row.login_identifier != access_row.student_email:
            raise RuntimeError("Login principal do app não é o e-mail.")

    return {
        "resultado": "PASSOU",
        "previous_e2e": previous,
        "plan_id": plan_id,
        "training_status": "approved",
        "access_id": access["id"],
        "access_token_masked": access["access_token_masked"],
        "student_email": access["student_email"],
        "session_endpoint_status": session_response.status_code,
        "plan_endpoint_status": plan_response.status_code,
        "session_endpoint_ok": True,
        "plan_endpoint_ok": True,
        "whatsapp_delivery": "dry_run",
        "dry_run_message": dry_run_message,
        "no_external_side_effects": True,
    }


def _assert_no_raw_secret(label: str, payload: Any, raw_secret: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if raw_secret and raw_secret in serialized:
        raise RuntimeError(f"{label} expôs segredo bruto.")


def _assert_no_forbidden_keys(label: str, payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_RESPONSE_KEYS:
                raise RuntimeError(f"{label} retornou campo proibido: {key}")
            _assert_no_forbidden_keys(label, value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_forbidden_keys(label, value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-whatsapp-real", action="store_true", help="Bloqueado neste E2E; sempre falha se usado.")
    parser.add_argument("--send-telegram-real", action="store_true", help="Bloqueado neste E2E; sempre falha se usado.")
    parser.add_argument("--use-real-hermes", action="store_true", help="Bloqueado neste E2E; sempre falha se usado.")
    outcome = asyncio.run(main(parser.parse_args()))
    print("PASSOU")
    print(json.dumps(outcome, ensure_ascii=False, default=str))
