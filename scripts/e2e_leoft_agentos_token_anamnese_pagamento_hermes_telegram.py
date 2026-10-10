"""E2E interno LeoFT: somente dados TESTE E2E LeoFT e integrações mock/dry-run."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.services.leoft_payment_service import process_leoft_payment_approved_simulated
from app.services.leoft_security import hash_token, mask_token
from app.services.tokenized_links import generate_or_get_tokenized_link, validate_tokenized_link
from db.database import AsyncSessionLocal
from db.models import CRMLead, Empresa, LeoFTTrainingPlan

TENANT_NAME = "Leonardo Personal"
LINK_SLUG = "anamnese_leoft"
PUBLIC_SLUG = "leoft"
TEST_NAME = "Aluno Teste LeoFT"
TEST_PHONE_PREFIX = "555499998"
TEST_EMAIL = "aluno.teste.leoft@example.com"


async def main(args) -> dict:
    if getattr(args, "send_telegram_real", False):
        raise RuntimeError("Envio real de Telegram está desabilitado neste E2E.")

    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    test_phone = f"{TEST_PHONE_PREFIX}{run_id[-5:]}"
    token = ""
    token_hash = ""
    empresa_id = None
    endpoint_session_status = None
    endpoint_submit_status = None

    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(
                select(Empresa).where(func.trim(Empresa.nome_empresa) == TENANT_NAME)
            )
        ).scalars().one()
        empresa_id = empresa.id
        lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.telefone_contato == test_phone))).scalars().first()
        if not lead:
            lead = CRMLead(empresa_id=empresa.id, nome_contato=TEST_NAME, telefone_contato=test_phone, dados_adicionais={"e2e_leoft": True, "email": TEST_EMAIL, "origem": "e2e_leoft", "run_id": run_id})
            session.add(lead)
            await session.flush()
        link = generate_or_get_tokenized_link(session, empresa, lead, LINK_SLUG, sequence_slug="e2e_leoft")
        if not link.token:
            raise RuntimeError(f"Não foi possível gerar link: {link.reason}")
        token = link.token
        token_hash = hash_token(token)
        validation = await validate_tokenized_link(session, public_slug=PUBLIC_SLUG, token=token)
        if not validation.ok:
            raise RuntimeError(f"Token LeoFT não validado pela sessão pública: {validation.reason}")
        await session.commit()

    from app.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.get(f"/api/public/anamnese/{PUBLIC_SLUG}/session", params={"token": token})
        endpoint_session_status = session_response.status_code
        if session_response.status_code != 200:
            raise RuntimeError(f"GET session falhou: {session_response.status_code} {session_response.text}")

        submit_response = await client.post(
            f"/api/public/anamnese/{PUBLIC_SLUG}/respostas",
            json={
                "token": token,
                "respostas": _anamnese_respostas(test_phone, token_hash, mask_token(token)),
                "metadata": {
                    "submitted_at_client": datetime.utcnow().isoformat(),
                    "user_agent": "agentos-e2e-leoft-safe",
                    "e2e_leoft": True,
                    "e2e_no_external_side_effects": True,
                },
            },
        )
        endpoint_submit_status = submit_response.status_code
        if submit_response.status_code != 200:
            raise RuntimeError(f"POST respostas falhou: {submit_response.status_code} {submit_response.text}")

    async with AsyncSessionLocal() as session:
        lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.telefone_contato == test_phone))).scalars().one()
        _assert_no_raw_token("CRMLead.dados_adicionais", lead.dados_adicionais, token)
        anamnese = dict((lead.dados_adicionais or {}).get("anamneses", {}).get(PUBLIC_SLUG) or {})
        if anamnese.get("origem") != "public_anamnese" or not anamnese.get("submitted"):
            raise RuntimeError("Anamnese não foi persistida pelo endpoint público real.")
        if anamnese.get("token_ref") != token_hash:
            raise RuntimeError("Hash do token persistido na anamnese não confere.")

        result = await process_leoft_payment_approved_simulated(
            session,
            {
                "empresa_id": str(lead.empresa_id),
                "provider": "mercado_pago",
                "event": "payment.approved",
                "payment_id": f"e2e-mp-payment-{run_id}",
                "external_reference": token,
                "amount": 97,
                "currency": "BRL",
                "status": "approved",
                "mode": "e2e_simulated",
            },
            send_telegram_real=False,
            use_real_hermes=False,
        )
        plan = await session.scalar(select(LeoFTTrainingPlan).where(LeoFTTrainingPlan.id == result["plan_id"]))
        if plan is None:
            raise RuntimeError("Plano LeoFT não foi salvo.")
        _assert_no_raw_token("leoft_training_plans", _plan_storage_payload(plan), token)
        if plan.payment_status != "approved_simulated":
            raise RuntimeError(f"payment_status inseguro: {plan.payment_status}")
        if plan.telegram_status != "dry_run":
            raise RuntimeError(f"Telegram mode inesperado: {plan.telegram_status}")
        if plan.hermes_mode != "mock":
            raise RuntimeError(f"Hermes mode inesperado: {plan.hermes_mode}")
        if plan.status != "pending_approval":
            raise RuntimeError(f"Status do treino inesperado: {plan.status}")

        return {
            "resultado": "PASSOU",
            "empresa_id": str(lead.empresa_id),
            "lead_id": str(lead.id),
            "plan_id": str(plan.id),
            "token_masked": mask_token(token),
            "token_reference_hash": token_hash,
            "link_masked": (validation.record or {}).get("url") or "",
            "endpoint_real_usado": True,
            "endpoint_session_status": endpoint_session_status,
            "endpoint_submit_status": endpoint_submit_status,
            "token_bruto_persistido": False,
            "payment_status": plan.payment_status,
            "payment_mode": result.get("payment_mode"),
            "payment_provider": result.get("payment_provider"),
            "telegram_mode": plan.telegram_status,
            "hermes_mode": plan.hermes_mode,
            "training_status": plan.status,
        }


def _anamnese_respostas(phone: str, token_hash: str, token_masked: str) -> dict:
    return {
        "tokenReferenceHash": token_hash,
        "leadTokenMasked": token_masked,
        "source": {
            "utm_source": "e2e",
            "utm_medium": "test",
            "utm_campaign": "leoft_funil_e2e",
            "utm_content": "teste_integracao",
            "utm_term": "metodo_hibrido_leoft",
            "fbclid": "fbclid_e2e_fake",
            "fbc": "fbc_e2e_fake",
            "fbp": "fbp_e2e_fake",
        },
        "personalData": {
            "name": TEST_NAME,
            "age": "35",
            "gender": "Homem",
            "email": TEST_EMAIL,
            "whatsapp": phone,
            "instagram": "@alunotesteleoft",
        },
        "trainingProfile": {
            "mainGoal": "Voltar a treinar com constância",
            "goalPriority": "Ter um plano claro pra seguir",
            "trainingType": "Musculação + corrida",
            "currentLevel": "Já treinei, mas estou parado",
            "recentTrainingStatus": "Não estou treinando",
            "daysPerWeek": "4 dias",
            "timePerSession": "45 a 60 minutos",
            "trainingPlace": "Academia + corrida",
            "equipment": ["Academia completa", "Esteira"],
            "runningExperience": "Quero começar do zero",
            "limitations": ["Joelho"],
            "limitationsDetails": "Sente desconforto leve no joelho direito em impacto alto.",
            "specialCare": "Tenho pouco tempo",
            "specialCareDetails": "Rotina de trabalho instável, prefere treinos objetivos.",
            "weight": "82",
            "height": "1.76",
            "mainDifficulty": "Começar e parar",
            "commitmentLevel": "Sim, mas preciso de algo possível pra minha rotina",
        },
        "status": {
            "anamneseStarted": True,
            "anamneseCompleted": True,
            "checkoutStarted": True,
            "paymentStatus": "pending",
        },
    }


def _assert_no_raw_token(label: str, payload, token: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if token and token in serialized:
        raise RuntimeError(f"Token bruto persistido em {label}.")


def _plan_storage_payload(plan: LeoFTTrainingPlan) -> dict:
    return {
        "anamnese_token_hash": plan.anamnese_token_hash,
        "payment_reference": plan.payment_reference,
        "plan_json": plan.plan_json,
        "hermes_payload": plan.hermes_payload,
        "telegram_payload": plan.telegram_payload,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-telegram-real", action="store_true", help="Bloqueado neste E2E; sempre falha se usado.")
    options = parser.parse_args()
    outcome = asyncio.run(main(options))
    print("PASSOU")
    print(json.dumps(outcome, ensure_ascii=False, default=str))
