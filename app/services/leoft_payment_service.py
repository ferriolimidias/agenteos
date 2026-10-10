"""Pagamento simulado LeoFT: persistência de teste, sem gateways externos."""
from __future__ import annotations

import uuid
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.services.leoft_hermes_contract import build_hermes_leoft_payload_from_lead
from app.services.leoft_hermes_review_client import (
    HermesReviewClient,
    HermesReviewClientError,
    build_training_ready_for_review_payload,
    dry_run_review_result,
    idempotency_key_hash,
    training_summary_from_plan,
)
from app.services.leoft_hermes_training_client import HermesTrainingClient
from app.services.leoft_security import hash_token
from app.services.leoft_training_approval_token_service import issue_training_approval_token
from app.services.leoft_training_service import create_pending_training_plan, update_telegram_status
from db.models import CRMLead, Empresa

SIMULATED_PAYMENT_STATUS = "approved_simulated"
SIMULATED_PAYMENT_MODE = "e2e_simulated"
SIMULATED_PAYMENT_PROVIDER = "mercado_pago_mock"
DEFAULT_AGENTOS_PUBLIC_BASE_URL = "https://agenteos.ferriolimidias.com.br"


async def process_leoft_payment_approved_simulated(session, payload: dict[str, Any], *, send_telegram_real: bool = False, use_real_hermes: bool = False) -> dict[str, Any]:
    reference = str(payload.get("external_reference") or "").strip()
    if str(payload.get("status")) != "approved" or not reference:
        raise ValueError("Pagamento simulado exige status approved e external_reference.")
    token_hash = hash_token(reference)
    empresa_id = uuid.UUID(str(payload["empresa_id"]))
    result = await session.execute(
        select(CRMLead).where(CRMLead.empresa_id == empresa_id)
    )
    lead = next((item for item in result.scalars().all() if _has_token(item, token_hash)), None)
    if not lead:
        raise ValueError("Referência/token não vinculada a lead do tenant.")
    empresa = await session.get(Empresa, empresa_id)
    dados = dict(lead.dados_adicionais or {})
    anamnese = dict((dados.get("anamneses") or {}).get("leoft") or {})
    if not anamnese.get("submitted"):
        raise ValueError("Anamnese LeoFT não concluída.")
    leoft = dict(dados.get("leoft") or {})
    leoft["payment"] = {
        "status": SIMULATED_PAYMENT_STATUS,
        "external_status": "approved",
        "mode": SIMULATED_PAYMENT_MODE,
        "provider": SIMULATED_PAYMENT_PROVIDER,
        "payment_id": str(payload.get("payment_id")),
        "reference_hash": token_hash,
        "amount": payload.get("amount"),
        "currency": payload.get("currency"),
    }
    dados["leoft"] = leoft
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    student = dict((anamnese.get("respostas") or {}).get("personalData") or {})
    training_profile = dict((anamnese.get("respostas") or {}).get("trainingProfile") or {})
    hermes_mode = "real" if use_real_hermes else "mock"
    anamnese_id = str(anamnese.get("anamnese_id") or anamnese.get("anamneseId") or anamnese.get("id") or f"leoft-anamnese:{lead.id}")
    if use_real_hermes:
        hermes_payload = build_hermes_leoft_payload_from_lead(
            lead=lead,
            empresa=empresa,
            payment={
                "payment_id": str(payload.get("payment_id") or ""),
                "anamnese_id": anamnese_id,
                "reference_hash": token_hash,
                "amount": 97,
                "currency": "BRL",
                "status": SIMULATED_PAYMENT_STATUS,
                "mode": SIMULATED_PAYMENT_MODE,
                "provider": "mercado_pago",
            },
        )
    else:
        hermes_payload = {
            "mode": "mock",
            "empresa_id": str(lead.empresa_id),
            "lead_id": str(lead.id),
            "anamnese_id": anamnese_id,
            "student": {"name": student.get("name"), "email": student.get("email"), "whatsapp": student.get("whatsapp")},
            "trainingProfile": training_profile,
            "payment_reference_hash": token_hash,
            "payment_mode": SIMULATED_PAYMENT_MODE,
            "payment_provider": SIMULATED_PAYMENT_PROVIDER,
        }
    hermes_client = HermesTrainingClient(use_real=use_real_hermes)
    plan_json = hermes_client.generate_training_plan(hermes_payload, mode=hermes_mode)
    hermes_remote_response = hermes_client.last_remote_response or {}
    plan = await create_pending_training_plan(
        session,
        empresa_id=lead.empresa_id,
        lead_id=lead.id,
        token_reference=token_hash,
        anamnese_status="submitted",
        payment_reference=token_hash,
        payment_status=SIMULATED_PAYMENT_STATUS,
        payment_mode=SIMULATED_PAYMENT_MODE,
        payment_provider=SIMULATED_PAYMENT_PROVIDER,
        student={"name": student.get("name"), "email": student.get("email"), "whatsapp": student.get("whatsapp")},
        plan_json=plan_json,
        hermes_payload=hermes_payload,
        hermes_mode=hermes_mode,
    )
    token_data = await issue_training_approval_token(
        session,
        plan,
        base_url=str(os.getenv("LEOFT_APPROVAL_BASE_URL") or os.getenv("AGENTOS_PUBLIC_BASE_URL") or DEFAULT_AGENTOS_PUBLIC_BASE_URL),
    )
    review_payload = build_training_ready_for_review_payload(
        empresa_id=str(lead.empresa_id),
        lead_id=str(lead.id),
        anamnese_id=anamnese_id,
        plan_id=str(plan.id),
        student={"name": student.get("name") or plan.student_name or "Aluno Teste LeoFT"},
        training_summary=training_summary_from_plan(plan_json, training_profile),
        plan_json=plan_json,
        approval_url=token_data["approval_url"],
        test_mode=True,
    )
    if send_telegram_real:
        try:
            telegram = HermesReviewClient().send_training_ready_for_review(review_payload)
        except HermesReviewClientError as exc:
            telegram = {
                "mode": "via_hermes",
                "status": "failed",
                "sent": False,
                "called_real_telegram": False,
                "approval_url_masked": token_data["approval_url_masked"],
                "review_event_idempotency_key_hash": idempotency_key_hash(str(review_payload.get("idempotency_key") or "")),
                "error": str(exc),
            }
    else:
        telegram = dry_run_review_result(payload=review_payload)
    await update_telegram_status(session, plan, telegram)
    await session.commit()
    return {
        "lead_id": str(lead.id),
        "plan_id": str(plan.id),
        "status": plan.status,
        "payment_status": SIMULATED_PAYMENT_STATUS,
        "payment_mode": SIMULATED_PAYMENT_MODE,
        "payment_provider": SIMULATED_PAYMENT_PROVIDER,
        "hermes_mode": hermes_mode,
        "hermes_response_error": hermes_remote_response.get("error"),
        "hermes_returns_plan_json": isinstance(hermes_remote_response.get("plan_json") or hermes_remote_response.get("planJson") or hermes_remote_response.get("trainingPlan") or hermes_remote_response.get("plan"), dict),
        "called_external_services": hermes_remote_response.get("called_external_services"),
        "telegram": telegram,
    }


def _has_token(lead: CRMLead, token_hash: str) -> bool:
    links = (lead.dados_adicionais or {}).get("links_tokenizados") or {}
    value = links.get("anamnese_leoft") if isinstance(links, dict) else None
    return isinstance(value, dict) and value.get("token_hash") == token_hash
