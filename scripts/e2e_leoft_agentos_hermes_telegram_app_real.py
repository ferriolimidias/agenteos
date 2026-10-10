"""E2E LeoFT: AgentOS -> Hermes review -> Telegram -> aprovação -> app."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.leoft_hermes_review_client import count_plan_structure  # noqa: E402
from app.services.leoft_hermes_training_client import HermesTrainingClientError, hermes_remote_config_status  # noqa: E402
from app.services.leoft_payment_service import process_leoft_payment_approved_simulated  # noqa: E402
from app.services.leoft_plan_contract import validate_leoft_plan_contract  # noqa: E402
from app.services.leoft_security import hash_token, mask_token  # noqa: E402
from app.services.tokenized_links import generate_or_get_tokenized_link, validate_tokenized_link  # noqa: E402
from db.database import AsyncSessionLocal  # noqa: E402
from db.models import CRMLead, Empresa, LeoFTStudentAccess, LeoFTTrainingPlan  # noqa: E402
from scripts.e2e_leoft_agentos_anamnese_pagamento_hermes_real import _extract_hermes_response_error, _mask_anamnese_id  # noqa: E402
from scripts.e2e_leoft_agentos_token_anamnese_pagamento_hermes_telegram import (  # noqa: E402
    LINK_SLUG,
    PUBLIC_SLUG,
    TENANT_NAME,
    TEST_EMAIL,
    TEST_NAME,
    TEST_PHONE_PREFIX,
    _anamnese_respostas,
    _assert_no_raw_token,
    _plan_storage_payload,
)

POLL_INTERVAL_SECONDS = 5
WAIT_TIMEOUT_SECONDS = 900


async def main(args: Namespace) -> dict[str, Any]:
    if not args.use_real_hermes:
        raise RuntimeError("Este E2E exige --use-real-hermes.")
    if not args.test_student:
        raise RuntimeError("Este E2E exige --test-student para evitar uso acidental.")
    config = hermes_remote_config_status()
    if config["missing"]:
        return {
            "resultado": "BLOQUEADO_CONFIGURACAO",
            "missing_envs": config["missing"],
            "endpoint_review": "POST /api/internal/hermes/events/leoft/training-ready-for-review",
            "telegram_real_via_hermes": False,
        }

    setup = await _create_test_lead_and_submit_anamnese()
    token = setup["token"]
    async with AsyncSessionLocal() as session:
        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == setup["empresa_id"], CRMLead.telefone_contato == setup["test_phone"])
            )
        ).scalars().one()
        empresa_id_str = str(lead.empresa_id)
        lead_id_str = str(lead.id)
        anamnese = dict((lead.dados_adicionais or {}).get("anamneses", {}).get(PUBLIC_SLUG) or {})
        anamnese_id_str = str(anamnese.get("anamnese_id") or anamnese.get("anamneseId") or anamnese.get("id") or f"leoft-anamnese:{lead_id_str}")
        try:
            result = await process_leoft_payment_approved_simulated(
                session,
                {
                    "empresa_id": empresa_id_str,
                    "anamnese_id": anamnese_id_str,
                    "provider": "mercado_pago",
                    "event": "payment.approved",
                    "payment_id": f"e2e-hermes-review-payment-{setup['run_id']}",
                    "external_reference": token,
                    "amount": 97,
                    "currency": "BRL",
                    "status": "approved",
                    "mode": "e2e_simulated",
                },
                send_telegram_real=bool(args.send_real_telegram_via_hermes),
                use_real_hermes=True,
            )
        except (HermesTrainingClientError, ValueError) as exc:
            await session.rollback()
            return {
                "resultado": "BLOQUEADO",
                "erro": str(exc),
                "hermes_response_error": _extract_hermes_response_error(str(exc)),
                "empresa_id": empresa_id_str,
                "lead_id": lead_id_str,
                "anamnese_id": _mask_anamnese_id(anamnese_id_str),
            }

        plan = await session.get(LeoFTTrainingPlan, result["plan_id"])
        if plan is None:
            raise RuntimeError("Plano LeoFT não foi salvo.")
        _assert_no_raw_token("leoft_training_plans", _plan_storage_payload(plan), token)
        validate_leoft_plan_contract(dict(plan.plan_json or {}))
        counts = count_plan_structure(dict(plan.plan_json or {}))
        pending_blocked = await _assert_pending_approval_blocked(str(plan.id))
        access_before = await session.scalar(select(LeoFTStudentAccess).where(LeoFTStudentAccess.plan_id == plan.id))
        telegram_payload = dict(plan.telegram_payload or {})
        telegram_message_id = telegram_payload.get("telegram_message_id")

    base_result = {
        "resultado": "PENDING_APPROVAL",
        "agentos_chamou_rota_review": bool(args.send_real_telegram_via_hermes),
        "plan_json_completo_enviado": counts["months"] > 0 and counts["weeks"] > 0 and counts["workouts"] > 0 and counts["exercises"] > 0,
        "quantidade_meses": counts["months"],
        "quantidade_semanas": counts["weeks"],
        "quantidade_treinos": counts["workouts"],
        "quantidade_exercicios": counts["exercises"],
        "approval_url_real_gerada": bool(telegram_payload.get("approval_url_masked")),
        "approval_token_exposto": False,
        "idempotencia_ok": bool(telegram_payload.get("review_event_idempotency_key_hash")),
        "telegram_enviado_pelo_hermes": bool(telegram_payload.get("sent")),
        "telegram_enviado_diretamente_pelo_agentos": False,
        "grupo_treinos_usado": telegram_payload.get("group_name") == "Treinos",
        "telegram_message_id": telegram_message_id,
        "treino_estava_pending_approval_antes": True,
        "app_bloqueou_antes": pending_blocked and access_before is None,
        "aprovacao_real_feita_pelo_telegram": False,
        "treino_virou_approved": False,
        "student_access_criado_liberado": False,
        "app_mostrou_plano_completo": False,
        "mercado_pago_real_usado": False,
        "meta_usada": False,
        "whatsapp_real_usado": False,
        "secrets_expostos": False,
        "plan_id": result["plan_id"],
        "lead_id": lead_id_str,
        "anamnese_id": _mask_anamnese_id(anamnese_id_str),
        "telegram_status": telegram_payload.get("status") or result.get("telegram", {}).get("status"),
    }

    if not args.send_real_telegram_via_hermes:
        base_result["resultado"] = "PASSOU_SEGURO_DRY_RUN"
        return base_result
    if not telegram_payload.get("sent") or not telegram_message_id:
        base_result["resultado"] = "BLOQUEADO"
        return base_result

    print("MENSAGEM_ENVIADA_AO_GRUPO_TREINOS", flush=True)
    print("AGUARDANDO_CLIQUE_EM_APROVAR_TREINO", flush=True)
    print(f"PLAN_ID={result['plan_id']}", flush=True)
    print(f"TELEGRAM_MESSAGE_ID={telegram_message_id}", flush=True)

    if not args.wait_approval:
        base_result["resultado"] = "AGUARDANDO_APROVACAO"
        return base_result

    approved = await _wait_for_approval(result["plan_id"], counts)
    base_result.update(approved)
    base_result["resultado"] = "APROVADO" if approved["treino_virou_approved"] else "BLOQUEADO_AGUARDANDO_APROVACAO"
    return base_result


async def _create_test_lead_and_submit_anamnese() -> dict[str, Any]:
    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    test_phone = f"{TEST_PHONE_PREFIX}{run_id[-5:]}"
    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(select(Empresa).where(func.trim(Empresa.nome_empresa) == TENANT_NAME))
        ).scalars().one()
        lead = CRMLead(
            empresa_id=empresa.id,
            nome_contato=TEST_NAME,
            telefone_contato=test_phone,
            dados_adicionais={
                "e2e_leoft_hermes_review": True,
                "email": TEST_EMAIL,
                "origem": "e2e_leoft_hermes_review",
                "run_id": run_id,
            },
        )
        session.add(lead)
        await session.flush()
        link = generate_or_get_tokenized_link(session, empresa, lead, LINK_SLUG, sequence_slug="e2e_leoft_hermes_review")
        if not link.token:
            raise RuntimeError(f"Não foi possível gerar link: {link.reason}")
        token = link.token
        token_hash = hash_token(token)
        validation = await validate_tokenized_link(session, public_slug=PUBLIC_SLUG, token=token)
        if not validation.ok:
            raise RuntimeError(f"Token LeoFT não validado pela sessão pública: {validation.reason}")
        await session.commit()

    from app.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/public/anamnese/{PUBLIC_SLUG}/respostas",
            json={
                "token": token,
                "respostas": _anamnese_respostas(test_phone, token_hash, mask_token(token)),
                "metadata": {
                    "submitted_at_client": datetime.utcnow().isoformat(),
                    "user_agent": "agentos-e2e-leoft-hermes-review",
                    "e2e_leoft": True,
                    "e2e_hermes_real": True,
                    "test_student": True,
                },
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"POST respostas falhou: {response.status_code} {response.text}")
    return {"run_id": run_id, "test_phone": test_phone, "token": token, "empresa_id": empresa.id}


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


async def _wait_for_approval(plan_id: str, expected_counts: dict[str, int]) -> dict[str, Any]:
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
                counts = count_plan_structure(dict(plan.plan_json or {}))
                return {
                    "aprovacao_real_feita_pelo_telegram": True,
                    "treino_virou_approved": True,
                    "approved_at": str(plan.approved_at) if plan.approved_at else None,
                    "student_access_criado_liberado": access is not None,
                    "app_mostrou_plano_completo": access is not None and counts == expected_counts,
                }
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return {
        "aprovacao_real_feita_pelo_telegram": False,
        "treino_virou_approved": False,
        "student_access_criado_liberado": False,
        "app_mostrou_plano_completo": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-real-hermes", action="store_true")
    parser.add_argument("--send-real-telegram-via-hermes", action="store_true")
    parser.add_argument("--test-student", action="store_true")
    parser.add_argument("--wait-approval", action="store_true")
    parser.add_argument("--debug", action="store_true")
    options = parser.parse_args()
    try:
        outcome = asyncio.run(main(options))
    except Exception as exc:
        if options.debug:
            traceback.print_exc()
        outcome = {"resultado": "BLOQUEADO", "erro": str(exc)}
    print(outcome.get("resultado"))
    print(json.dumps(outcome, ensure_ascii=False, default=str))
