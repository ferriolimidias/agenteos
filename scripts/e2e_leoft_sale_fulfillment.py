import asyncio
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from db.database import AsyncSessionLocal
from app.services import leoft_sale_fulfillment_service as svc

COMPANY = uuid.UUID('a7384cb4-98b2-4750-94bc-3b329d35156d')
PHONE = '5511990001234'
NAME = 'E2E SALE FULFILLMENT 20261001'

async def main():
    async with AsyncSessionLocal() as s:
        # Controlled transaction: all rows are rolled back at the end.
        async with s.begin_nested():
            lead_id = uuid.uuid4()
            await s.execute(text("""
                INSERT INTO crm_leads(id, empresa_id, nome_contato, telefone_contato, tags, dados_adicionais, status_atendimento, ia_ativa)
                VALUES (:id, :company, :name, :phone, '[]'::jsonb, CAST(:data AS jsonb), 'aberto', true)
            """), {'id': lead_id, 'company': COMPANY, 'name': NAME, 'phone': PHONE,
                    'data': '{"anamneses":{"leoft":{"submitted":true,"anamnese_id":"e2e-anamnese-20261001","respostas":{"personalData":{"name":"E2E Sale","email":"e2e.sale.20261001@example.test"},"trainingProfile":{"mainGoal":"condicionamento"}}}}}'})
            connection = (await s.execute(text("SELECT id, channel, provider FROM conexoes WHERE empresa_id=:c ORDER BY criado_em LIMIT 1"), {'c': COMPANY})).mappings().first()
            assert connection, 'tenant connection required for contact resolution'
            contact_id = uuid.uuid4()
            await s.execute(text("""
                INSERT INTO channel_contacts(id,empresa_id,conexao_id,lead_id,channel,provider,external_contact_id,normalized_address,phone_e164,display_name)
                VALUES (:id,:c,:connection,:lead,:channel,:provider,:external,:address,:phone,:name)
            """), {'id': contact_id, 'c': COMPANY, 'connection': connection['id'], 'lead': lead_id,
                    'channel': connection['channel'], 'provider': connection['provider'], 'external': PHONE,
                    'address': '+' + PHONE, 'phone': '+' + PHONE, 'name': NAME})
            exercise = (await s.execute(text("SELECT id FROM leoft_catalog_entries WHERE empresa_id=:c LIMIT 1"), {'c': COMPANY})).scalar()
            assert exercise, 'catalog exercise required'
            template_id, workout_id = uuid.uuid4(), uuid.uuid4()
            await s.execute(text("""
                INSERT INTO leoft_training_programs(id, empresa_id, student_id, name, description, objective, level, suggested_frequency, status)
                VALUES (:id,:c,NULL,'E2E Template 20261001','template','E2E','Intermediário',3,'active')
            """), {'id': template_id, 'c': COMPANY})
            await s.execute(text("""
                INSERT INTO leoft_manual_workouts(id, empresa_id, name, description, modality, active)
                VALUES (:id,:c,'E2E A','Prescrição E2E','musculacao',true)
            """), {'id': workout_id, 'c': COMPANY})
            await s.execute(text("""
                INSERT INTO leoft_manual_workout_exercises(id,workout_id,exercise_id,position,sets,reps,created_at,updated_at)
                VALUES (gen_random_uuid(),:w,:e,1,3,'10',now(),now())
            """), {'w': workout_id, 'e': exercise})
            await s.execute(text("""
                INSERT INTO leoft_training_program_sessions(id,empresa_id,program_id,workout_id,session_order,label,name)
                VALUES (gen_random_uuid(),:c,:p,:w,1,'A','E2E A')
            """), {'c': COMPANY, 'p': template_id, 'w': workout_id})
            await s.execute(text("""
                INSERT INTO leoft_offer_fulfillments(company_id,product_id,offer_code,product_type,fulfillment_type,training_program_template_id,active)
                VALUES (:c,'e2e_fulfillment_product','e2e_fulfillment_product','STANDARD_PROGRAM','STANDARD_PROGRAM_COPY',:p,true)
            """), {'c': COMPANY, 'p': template_id})
            payment = {'id':'e2e-payment-20261001-1','status':'approved','provider':'mercado_pago','external_reference':'e2e-order-1','transaction_amount':97,'currency_id':'BRL','date_approved':datetime.now(timezone.utc).isoformat(),'metadata':{'product_id':'e2e_fulfillment_product','environment':'e2e','anamnesis_id':'e2e-anamnese-20261001'}}
            from db.models import CRMLead
            lead = await s.get(CRMLead, lead_id)
            first = await svc.fulfill_sale_confirmed(s,payment=payment,lead=lead,anamnese_id='e2e-anamnese-20261001',environment='e2e')
            assert first['status'] == 'completed', first
            assert first['student_id'] and first['access_id'] and first['student_program_id'], first
            assert first['fulfillment_id'] and first['lead_id'] and first['access_id'] and first['student_program_id']
            sale_contact = await s.scalar(text("SELECT contact_id FROM leoft_sale_fulfillments WHERE id=:id"), {'id': first['fulfillment_id']})
            assert str(sale_contact) == str(contact_id), sale_contact
            duplicate = await svc.fulfill_sale_confirmed(s,payment=payment,lead=lead,anamnese_id='e2e-anamnese-20261001',environment='e2e')
            assert duplicate['duplicate'] is True and duplicate['student_program_id'] == first['student_program_id'], duplicate
            payment2 = dict(payment); payment2['id']='e2e-payment-20261001-2'; payment2['external_reference']='e2e-order-2'
            second = await svc.fulfill_sale_confirmed(s,payment=payment2,lead=lead,anamnese_id='e2e-anamnese-20261001',environment='e2e')
            assert second['status'] == 'completed' and second['student_id'] == first['student_id'] and second['access_id'] == first['access_id'], second
            assert second['student_program_id'] != first['student_program_id'], second
            count = await s.scalar(text("SELECT count(*) FROM leoft_training_program_sessions WHERE program_id=:p"), {'p': second['student_program_id']})
            assert count == 1
            # Missing mapping must stop at manual_review without choosing a template.
            payment3 = dict(payment); payment3['id']='e2e-payment-20261001-3'; payment3['external_reference']='e2e-order-3'; payment3['metadata']={'product_id':'e2e_without_mapping','environment':'e2e'}
            third = await svc.fulfill_sale_confirmed(s,payment=payment3,lead=lead,anamnese_id='e2e-anamnese-20261001',environment='e2e')
            assert third['status'] == 'manual_review' and third['student_program_id'] is None and third['student_id'], third
            # Recoverable post-sale failure becomes partial and retry is idempotent.
            original = svc._trigger_post_sale
            async def fail_once(*args, **kwargs): raise RuntimeError('controlled_message_failure')
            svc._trigger_post_sale = fail_once
            payment4 = dict(payment); payment4['id']='e2e-payment-20261001-4'; payment4['external_reference']='e2e-order-4'
            fourth = await svc.fulfill_sale_confirmed(s,payment=payment4,lead=lead,anamnese_id='e2e-anamnese-20261001',environment='production')
            assert fourth['status'] == 'partial', fourth
            svc._trigger_post_sale = original
            async def recover(*args, **kwargs):
                sale = kwargs['sale']; sale.post_sale_status = 'skipped'
            svc._trigger_post_sale = recover
            retried = await svc.retry_sale_fulfillment(s,str(fourth['fulfillment_id']))
            assert retried['status'] == 'completed', retried
            svc._trigger_post_sale = original
            print('SALE_FULFILLMENT_E2E=PASS')
            print('IDEMPOTENCY=PASS')
            print('EXISTING_STUDENT_ACCESS_REUSE=PASS')
            print('CONTACT_RESOLUTION=PASS')
            print('PROGRAM_COPY_INDEPENDENCE=PASS')
            print('MISSING_TEMPLATE_MANUAL_REVIEW=PASS')
            print('PARTIAL_RETRY=PASS')
            print('REAL_MESSAGE_SENT=NO')
            print('REAL_PAYMENT_CREATED=NO')
        await s.rollback()

asyncio.run(main())
