"""E2E LeoFT controlado: anamnese real AgentOS + Hermes remoto real."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.leoft_hermes_training_client import HermesTrainingClientError, hermes_remote_config_status  # noqa: E402
from app.services.leoft_payment_service import process_leoft_payment_approved_simulated  # noqa: E402
from app.services.leoft_plan_contract import validate_leoft_plan_contract  # noqa: E402
from app.services.leoft_security import hash_token, mask_token  # noqa: E402
from app.services.tokenized_links import generate_or_get_tokenized_link, validate_tokenized_link  # noqa: E402
from db.database import AsyncSessionLocal  # noqa: E402
from db.models import CRMLead, Empresa, LeoFTStudentAccess, LeoFTTrainingPlan  # noqa: E402
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


async def main(args) -> dict:
    if not getattr(args, "use_real_hermes", False):
        raise RuntimeError("Este E2E exige --use-real-hermes.")
    config = hermes_remote_config_status()
    if config["missing"]:
        return {
            "resultado": "BLOQUEADO_CONFIGURACAO",
            "missing_envs": config["missing"],
            "HERMES_REMOTE_BASE_URL_presente": config["base_url_present"],
            "HERMES_LEOFT_HMAC_SECRET_presente": config["hmac_secret_present"],
            "endpoint": config["payment_approved_endpoint"],
            "e2e_hermes_real": "NÃO_EXECUTADO",
        }

    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    test_phone = f"{TEST_PHONE_PREFIX}{run_id[-5:]}"
    token = ""
    token_hash = ""
    empresa_id = None

    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(
                select(Empresa).where(func.trim(Empresa.nome_empresa) == TENANT_NAME)
            )
        ).scalars().one()
        empresa_id = empresa.id
        lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.telefone_contato == test_phone))).scalars().first()
        if not lead:
            lead = CRMLead(
                empresa_id=empresa.id,
                nome_contato=TEST_NAME,
                telefone_contato=test_phone,
                dados_adicionais={
                    "e2e_leoft_hermes_real": True,
                    "email": TEST_EMAIL,
                    "origem": "e2e_leoft_hermes_real",
                    "run_id": run_id,
                },
            )
            session.add(lead)
            await session.flush()
        link = generate_or_get_tokenized_link(session, empresa, lead, LINK_SLUG, sequence_slug="e2e_leoft_hermes_real")
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
        session_response = await client.get(f"/api/public/anamnese/{PUBLIC_SLUG}/session", params={"token": token})
        if session_response.status_code != 200:
            raise RuntimeError(f"GET session falhou: {session_response.status_code} {session_response.text}")
        submit_response = await client.post(
            f"/api/public/anamnese/{PUBLIC_SLUG}/respostas",
            json={
                "token": token,
                "respostas": _anamnese_respostas(test_phone, token_hash, mask_token(token)),
                "metadata": {
                    "submitted_at_client": datetime.utcnow().isoformat(),
                    "user_agent": "agentos-e2e-leoft-hermes-real",
                    "e2e_leoft": True,
                    "e2e_no_external_side_effects": True,
                    "e2e_hermes_real": True,
                },
            },
        )
        if submit_response.status_code != 200:
            raise RuntimeError(f"POST respostas falhou: {submit_response.status_code} {submit_response.text}")

    async with AsyncSessionLocal() as session:
        lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.telefone_contato == test_phone))).scalars().one()
        empresa_id_str = str(lead.empresa_id)
        lead_id_str = str(lead.id)
        anamnese_id_str = f"leoft-anamnese:{lead_id_str}"
        _assert_no_raw_token("CRMLead.dados_adicionais", lead.dados_adicionais, token)
        anamnese = dict((lead.dados_adicionais or {}).get("anamneses", {}).get(PUBLIC_SLUG) or {})
        if anamnese.get("origem") != "public_anamnese" or not anamnese.get("submitted"):
            raise RuntimeError("Anamnese não foi persistida pelo endpoint público real.")
        anamnese_id_str = str(anamnese.get("anamnese_id") or anamnese.get("anamneseId") or anamnese.get("id") or anamnese_id_str)

        try:
            result = await process_leoft_payment_approved_simulated(
                session,
                {
                    "empresa_id": empresa_id_str,
                    "anamnese_id": anamnese_id_str,
                    "provider": "mercado_pago",
                    "event": "payment.approved",
                    "payment_id": f"e2e-hermes-real-payment-{run_id}",
                    "external_reference": token,
                    "amount": 97,
                    "currency": "BRL",
                    "status": "approved",
                    "mode": "e2e_simulated",
                },
                send_telegram_real=False,
                use_real_hermes=True,
            )
        except (HermesTrainingClientError, ValueError) as exc:
            message = str(exc)
            hermes_response_error = _extract_hermes_response_error(message)
            await session.rollback()
            if "BLOQUEADO_CONTRATO_RETORNO" in message or "Resposta Hermes inválida:" in message:
                return {
                    "resultado": "BLOQUEADO_CONTRATO_RETORNO",
                    "e2e_hermes_real": "BLOQUEADO_CONTRATO_RETORNO",
                    "empresa_id": empresa_id_str,
                    "lead_id": lead_id_str,
                    "anamnese_id": _mask_anamnese_id(anamnese_id_str),
                    "token_masked": mask_token(token),
                    "plano_local_pending_approval_salvo": False,
                    "hermes_mode": "real",
                    "hermes_mode_real_salvo": False,
                    "training_status": None,
                    "plan_id": None,
                    "motivo": message,
                    "hermes_response_error": hermes_response_error,
                    "hermes_returns_plan_json": True,
                    "called_external_services": None,
                    "no_external_side_effects_except_hermes": True,
                }
            return {
                "resultado": "FALHOU",
                "e2e_hermes_real": "FALHOU",
                "erro": message,
                "hermes_response_error": hermes_response_error,
                "empresa_id": empresa_id_str,
                "lead_id": lead_id_str,
                "anamnese_id": _mask_anamnese_id(anamnese_id_str),
                "hermes_mode": "real",
                "plano_local_pending_approval_salvo": False,
                "hermes_mode_real_salvo": False,
                "training_status": None,
                "plan_id": None,
                "hermes_returns_plan_json": False,
                "called_external_services": None,
                "no_external_side_effects_except_hermes": True,
            }
        plan = await session.scalar(select(LeoFTTrainingPlan).where(LeoFTTrainingPlan.id == result["plan_id"]))
        if plan is None:
            raise RuntimeError("Plano LeoFT não foi salvo.")
        _assert_no_raw_token("leoft_training_plans", _plan_storage_payload(plan), token)
        validate_leoft_plan_contract(dict(plan.plan_json or {}))
        if plan.hermes_mode != "real":
            raise RuntimeError(f"hermes_mode inesperado: {plan.hermes_mode}")
        if plan.status != "pending_approval":
            raise RuntimeError(f"training_status inesperado: {plan.status}")
        app_access = await session.scalar(select(LeoFTStudentAccess).where(LeoFTStudentAccess.plan_id == plan.id))
        if app_access is not None:
            raise RuntimeError("E2E Hermes real criou acesso do app antes da aprovação.")

        return {
            "resultado": "PASSOU",
            "e2e_hermes_real": "OK",
            "empresa_id": empresa_id_str,
            "lead_id": lead_id_str,
            "anamnese_id": _mask_anamnese_id(anamnese_id_str),
            "plan_id": str(plan.id),
            "token_masked": mask_token(token),
            "token_bruto_persistido": False,
            "endpoint_session_status": session_response.status_code,
            "endpoint_submit_status": submit_response.status_code,
            "payment_status": plan.payment_status,
            "payment_mode": result.get("payment_mode"),
            "payment_provider": result.get("payment_provider"),
            "hermes_mode": plan.hermes_mode,
            "training_status": plan.status,
            "hermes_response_error": result.get("hermes_response_error"),
            "hermes_returns_plan_json": bool(result.get("hermes_returns_plan_json", True)),
            "plano_local_pending_approval_salvo": plan.status == "pending_approval",
            "hermes_mode_real_salvo": plan.hermes_mode == "real",
            "called_external_services": result.get("called_external_services"),
            "telegram_mode": plan.telegram_status,
            "app_access_created": False,
            "no_external_side_effects_except_hermes": True,
        }


def _extract_hermes_response_error(message: str) -> str | None:
    marker = "HTTP "
    if marker not in str(message or ""):
        return None
    json_start = message.find("{")
    if json_start < 0:
        return None
    try:
        payload = json.loads(message[json_start:])
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        for key in ("error", "erro", "reason", "message"):
            if payload.get(key):
                return str(payload[key])
    return None


def _mask_anamnese_id(value: str) -> str:
    text = str(value or "")
    if len(text) <= 12:
        return text
    return f"{text[:16]}...{text[-8:]}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-real-hermes", action="store_true")
    parser.add_argument("--debug", action="store_true")
    options = parser.parse_args()
    if not options.use_real_hermes:
        print("Este E2E exige --use-real-hermes.")
        raise SystemExit(1)
    try:
        outcome = asyncio.run(main(options))
    except Exception as exc:
        if options.debug:
            traceback.print_exc()
        outcome = {
            "resultado": "FALHOU",
            "e2e_hermes_real": "FALHOU",
            "erro": str(exc),
            "hermes_response_error": _extract_hermes_response_error(str(exc)),
        }
    print("PASSOU" if outcome.get("resultado") == "PASSOU" else outcome.get("resultado"))
    print(json.dumps(outcome, ensure_ascii=False, default=str))
