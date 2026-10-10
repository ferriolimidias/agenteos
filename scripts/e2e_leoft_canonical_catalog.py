import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.services import leoft_sale_fulfillment_service as svc
from db.database import AsyncSessionLocal
from db.models import CRMLead


COMPANY = uuid.UUID("a7384cb4-98b2-4750-94bc-3b329d35156d")


async def main():
    async with AsyncSessionLocal() as session:
        async with session.begin_nested():
            lead_id = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO crm_leads(id, empresa_id, nome_contato, telefone_contato, tags, dados_adicionais, status_atendimento, ia_ativa)
                    VALUES (:id, :company, 'E2E CANONICAL CATALOG 20261002', '5511990007788', '[]'::jsonb, '{}'::jsonb, 'aberto', true)
                """),
                {"id": lead_id, "company": COMPANY},
            )
            await session.execute(
                text("""
                    INSERT INTO leoft_offer_fulfillments(company_id, product_id, offer_code, product_type, fulfillment_type, active, metadata)
                    VALUES (:company, 'e2e_canonical_personalized', 'e2e_canonical_personalized', 'PERSONALIZED_PROGRAM', 'PERSONALIZED_PROGRAM_REQUEST', true, '{"requires_template": false}'::jsonb)
                """),
                {"company": COMPANY},
            )
            lead = await session.get(CRMLead, lead_id)
            base_payment = {
                "status": "approved",
                "provider": "mercado_pago",
                "transaction_amount": 97,
                "currency_id": "BRL",
                "date_approved": datetime.now(timezone.utc).isoformat(),
                "metadata": {"product_id": "e2e_canonical_personalized", "environment": "e2e"},
            }

            first_payment = dict(base_payment, id="e2e-canonical-payment-1", external_reference="e2e-canonical-order-1")
            waiting = await svc.fulfill_sale_confirmed(
                session, payment=first_payment, lead=lead, anamnese_id=None, environment="e2e"
            )
            assert waiting["status"] == "partial" and waiting["student_program_id"] is None, waiting
            request_one = await session.execute(
                text("SELECT id,status FROM leoft_personalization_requests WHERE sale_fulfillment_id=:sale"),
                {"sale": waiting["fulfillment_id"]},
            )
            request_one = request_one.mappings().one()
            assert request_one["status"] == "waiting_for_anamnesis", request_one

            duplicate = await svc.fulfill_sale_confirmed(
                session, payment=first_payment, lead=lead, anamnese_id=None, environment="e2e"
            )
            assert duplicate["duplicate"] is True and duplicate["fulfillment_id"] == waiting["fulfillment_id"], duplicate
            assert await session.scalar(text("SELECT count(*) FROM leoft_personalization_requests")) == 1

            lead.dados_adicionais = {
                "anamneses": {"leoft": {"anamnese_id": "e2e-anamnesis-canonical-20261002", "respostas": {"trainingProfile": {"mainGoal": "força"}}}}
            }
            await session.flush()
            second_payment = dict(base_payment, id="e2e-canonical-payment-2", external_reference="e2e-canonical-order-2", metadata={**base_payment["metadata"], "analysis_id": "e2e-analysis-20261002"})
            ready = await svc.fulfill_sale_confirmed(
                session, payment=second_payment, lead=lead, anamnese_id="e2e-anamnesis-canonical-20261002", environment="e2e"
            )
            assert ready["status"] == "partial" and ready["student_program_id"] is None, ready
            request_two = await session.execute(
                text("SELECT id,status,anamnesis_id,analysis_id FROM leoft_personalization_requests WHERE sale_fulfillment_id=:sale"),
                {"sale": ready["fulfillment_id"]},
            )
            request_two = request_two.mappings().one()
            assert request_two["status"] == "ready_for_creation", request_two
            assert request_two["anamnesis_id"] == "e2e-anamnesis-canonical-20261002"
            assert request_two["analysis_id"] == "e2e-analysis-20261002"
            assert ready["student_id"] == waiting["student_id"] and ready["access_id"] == waiting["access_id"], ready
            assert await session.scalar(text("SELECT count(*) FROM leoft_personalization_requests")) == 2

            current_payment = dict(
                base_payment,
                id="e2e-canonical-current-offer",
                external_reference="e2e-canonical-current-order",
                metadata={"product_id": "leoft_metodo_hibrido", "environment": "e2e"},
            )
            current = await svc.fulfill_sale_confirmed(
                session, payment=current_payment, lead=lead, anamnese_id=None, environment="e2e"
            )
            assert current["status"] == "partial" and current["student_program_id"] is None, current
            assert current["fulfillment_id"]
            assert await session.scalar(text("SELECT count(*) FROM leoft_personalization_requests WHERE sale_fulfillment_id=:sale"), {"sale": current["fulfillment_id"]}) == 1
            print("CURRENT_METHOD_HIBRIDO_DISPATCH=PASS")

            print("CANONICAL_CATALOG_PERSONALIZED_DISPATCH=PASS")
            print("PERSONALIZATION_REQUEST_CREATED=PASS")
            print("PERSONALIZED_DOES_NOT_REQUIRE_TEMPLATE=PASS")
            print("WAITING_FOR_ANAMNESIS=PASS")
            print("ANAMNESIS_ANALYSIS_LINK=PASS")
            print("PERSONALIZED_IDEMPOTENCY=PASS")
            print("STUDENT_ACCESS_REUSE=PASS")
            print("NO_PROGRAM_CREATED=PASS")
            print("REAL_PAYMENT_CREATED=NO")
            print("REAL_MESSAGE_SENT=NO")
        await session.rollback()


asyncio.run(main())
