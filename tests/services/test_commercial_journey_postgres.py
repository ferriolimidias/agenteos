"""Actual full-schema PostgreSQL journey projections, with synthetic rows only.

Refuses every database except the designated disposable a04_test. No transport,
worker, provider SDK or production credential is involved.
"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, insert, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.commercial_journey import commercial_journey
from db.models import (
    AgendamentoLocal, AttributionTouch, AttributionTouchResolution, CampaignRecipient,
    CampanhaDisparo, CanonicalCommercialEvent, ChannelContact, ChannelConversation,
    ConfigFollowUp, Conexao, CRMEtapa, CRMFunil, CRMLead, CRMTagEvent, Empresa,
    FollowupExecution, LeadFollowUpLog, LeoFTPaymentEvent, MensagemHistorico, TagCRM,
    TipoConexao,
)

URL = os.getenv('COMMERCIAL_JOURNEY_TEST_DATABASE_URL', '')


@unittest.skipUnless(URL, 'isolated COMMERCIAL_JOURNEY_TEST_DATABASE_URL required')
class CommercialJourneyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(URL)
        if parsed.get_backend_name() != 'postgresql' or parsed.database != 'a04_test' or parsed.host != 'a00-pg':
            raise RuntimeError('refusing journey fixture outside isolated a00-pg/a04_test')
        self.engine = create_async_engine(URL.replace('postgresql://', 'postgresql+asyncpg://', 1))
        self.rows = []
        self.t = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        self.naive = self.t.replace(tzinfo=None)
        self.tenant = await self.add(Empresa, nome_empresa='Synthetic journey A', credenciais_canais={'access_token': 'PRIVATE_TENANT_TOKEN'})
        self.other_tenant = await self.add(Empresa, nome_empresa='Synthetic journey B')
        self.lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic A', telefone_contato='PRIVATE_PHONE')
        self.other_lead = await self.add(CRMLead, empresa_id=self.other_tenant, nome_contato='Synthetic B')
        self.same_tenant_other_lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic C')
        self.connection = await self.add(Conexao, empresa_id=self.tenant, tipo=TipoConexao.META, nome_instancia=str(uuid4()))
        self.contact = await self.add(ChannelContact, empresa_id=self.tenant, conexao_id=self.connection,
            lead_id=self.lead, channel='whatsapp', provider='meta_official', external_contact_id=str(uuid4()))
        self.conversation = await self.add(ChannelConversation, empresa_id=self.tenant, conexao_id=self.connection,
            contact_id=self.contact, lead_id=self.lead, channel='whatsapp', provider='meta_official',
            conversation_key=str(uuid4()), external_conversation_id=str(uuid4()), criado_em=self.naive)

    async def asyncTearDown(self):
        # Reverse insertion order respects parent FKs. IDs are synthetic and
        # every delete is confined to the disposable guarded test database.
        try:
            async with self.engine.begin() as conn:
                for table, identifier in self.rows:
                    if table.name == 'crm_leads':
                        await conn.execute(update(table).where(table.c.id == identifier).values(etapa_id=None))
                for table, identifier in reversed(self.rows):
                    await conn.execute(delete(table).where(table.c.id == identifier))
        finally:
            await self.engine.dispose()

    async def add(self, model, **values):
        table = model.__table__
        identifier = values.setdefault('id', uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(insert(table).values(**values))
        self.rows.append((table, identifier))
        return identifier

    async def change(self, model, identifier, **values):
        async with self.engine.begin() as conn:
            await conn.execute(update(model.__table__).where(model.__table__.c.id == identifier).values(**values))

    async def journey(self, *, tenant=None, lead=None, **filters):
        async with AsyncSession(self.engine) as session:
            return await commercial_journey(session, empresa_id=tenant or self.tenant, lead_id=lead or self.lead, **filters)

    async def message(self, **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, channel_conversation_id=self.conversation,
            conexao_id=self.connection, channel='whatsapp', provider='meta_official', texto='PRIVATE_MESSAGE_CONTENT',
            criado_em=self.naive, direction='inbound', from_me=False,
            metadata_json={'access_token': 'PRIVATE_MESSAGE_TOKEN'})
        return await self.add(MensagemHistorico, **(defaults | values))

    async def touch(self, event_kind='ctwa_referral', **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, conversation_id=self.conversation,
            contact_id=self.contact, connection_id=self.connection, event_kind=event_kind,
            touch_type='acquisition', touch_source='meta_webhook', occurred_at=self.t,
            provider='meta_official', channel='whatsapp', idempotency_key=str(uuid4()),
            ctwa_clid='PRIVATE_CTWA', source_url='https://private.example/token', body='PRIVATE_BODY')
        return await self.add(AttributionTouch, **(defaults | values))

    async def followup(self, **values):
        config = await self.add(ConfigFollowUp, empresa_id=self.tenant, nome='Synthetic rule', tempo_gatilho_minutos=10, objetivo_prompt='PRIVATE_PROMPT')
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, contact_id=self.contact,
            conversation_id=self.conversation, conexao_id=self.connection, followup_id=config,
            provider='meta_official', channel='whatsapp', trigger='tag_applied', scheduled_at=self.naive+timedelta(days=3650),
            created_at=self.naive, idempotency_key=str(uuid4()), status='scheduled',
            policy_snapshot_json={'mode': 'scheduled_message', 'access_token': 'PRIVATE_FOLLOWUP_TOKEN'})
        return await self.add(FollowupExecution, **(defaults | values))

    async def campaign(self, **values):
        campaign = await self.add(CampanhaDisparo, empresa_id=self.tenant, nome='Synthetic campaign')
        defaults = dict(empresa_id=self.tenant, campanha_id=campaign, conexao_id=self.connection,
            lead_id=self.lead, contact_id=self.contact, conversation_id=self.conversation,
            provider='meta_official', channel='whatsapp', recipient_address='PRIVATE_RECIPIENT',
            idempotency_key=str(uuid4()), created_at=self.naive, status='queued',
            payload_json={'access_token': 'PRIVATE_CAMPAIGN_TOKEN'})
        return await self.add(CampaignRecipient, **(defaults | values))

    async def provider_payment(self, **values):
        defaults = dict(company_id=self.tenant, contact_id=self.lead, student_id=self.lead,
            event_type='leoft.payment_confirmed.v1', occurred_at=self.t, product_id='synthetic',
            order_id=str(uuid4()), payment_id='PRIVATE_PAYMENT_REFERENCE', provider='mercado_pago',
            amount='39.90', currency='BRL', payment_status='confirmed', environment='production',
            correlation_id=uuid4(), idempotency_key=str(uuid4()), raw_event_hash='a'*64,
            metadata={'provenance': 'provider_verified_payment_status', 'access_token': 'PRIVATE_PAYMENT_TOKEN'})
        return await self.add(LeoFTPaymentEvent, **(defaults | values))

    async def test_readonly_transaction_scoped_response_and_no_raw_data(self):
        await self.message()
        await self.touch()
        async with self.engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text('SET TRANSACTION READ ONLY'))
                async with AsyncSession(bind=conn) as session:
                    result = await commercial_journey(session, empresa_id=self.tenant, lead_id=self.lead)
                    self.assertEqual((await conn.execute(text('SHOW transaction_read_only'))).scalar(), 'on')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['lead_id'], str(self.lead))
        self.assertFalse(result['coverage']['history_complete'])
        self.assertEqual({item['category'] for item in result['items']}, {'conversation', 'message', 'acquisition'})

    async def test_keyset_same_timestamp_multiple_sources_and_category_filter(self):
        for _ in range(5):
            await self.message()
        await self.touch()
        await self.followup()
        await self.campaign(sent_at=self.naive, delivered_at=self.naive, status='delivered')
        complete = await self.journey(limit=100)
        collected, cursor = [], None
        for _ in range(20):
            page = await self.journey(limit=2, cursor=cursor)
            collected.extend(item['id'] for item in page['items'])
            if not page['has_more']:
                break
            self.assertIsNotNone(page['next_cursor'])
            cursor = page['next_cursor']
        else:
            self.fail('keyset pagination never terminated')
        self.assertEqual(collected, [item['id'] for item in complete['items']])
        self.assertEqual(len(collected), len(set(collected)))
        filtered = await self.journey(category='message', limit=2)
        self.assertTrue(all(item['category']=='message' for item in filtered['items']))
        self.assertEqual(filtered['coverage'], complete['coverage'])
        for kwargs in ({'category':'campaign'}, {'lead':self.same_tenant_other_lead}, {'tenant':self.other_tenant, 'lead':self.other_lead}):
            with self.assertRaises(ValueError):
                await self.journey(cursor=filtered['next_cursor'], **kwargs)

    async def test_missing_timestamp_and_booking_planned_time_are_not_occurrences(self):
        missing = await self.message(criado_em=None)
        booking = await self.add(AgendamentoLocal, empresa_id=self.tenant, lead_id=self.lead,
            data_hora_inicio=self.naive+timedelta(days=3650), data_hora_fim=self.naive+timedelta(days=3650, hours=1), status='agendado')
        result = await self.journey()
        self.assertFalse(any(item['evidence']['record_id'] in {str(missing),str(booking)} for item in result['items']))
        sources = {row['source']:row for row in result['coverage']['sources']}
        self.assertEqual(sources['mensagens_historico']['missing_timestamp'], 1)
        self.assertEqual(sources['agendamentos_locais']['records'], 1)
        self.assertEqual(sources['agendamentos_locais']['missing_timestamp'], 1)
        self.assertEqual(sources['agendamentos_locais']['timeline_records'], 0)

    async def test_foreign_lead_and_conversation_metadata_never_create_links(self):
        self.assertIsNone(await self.journey(lead=self.other_lead))
        foreign_conversation = await self.add(ChannelConversation, empresa_id=self.tenant, conexao_id=self.connection,
            contact_id=self.contact, lead_id=self.same_tenant_other_lead, channel='whatsapp', provider='meta_official',
            conversation_key=str(uuid4()), external_conversation_id=str(uuid4()))
        message = await self.message(channel_conversation_id=foreign_conversation,
            metadata_json={'lead_id':str(self.other_lead),'conversation_id':str(foreign_conversation)})
        wrong_lead_message = await self.message(lead_id=self.same_tenant_other_lead)
        result = await self.journey()
        items = {row['evidence']['record_id']:row for row in result['items']}
        self.assertIsNone(items[str(message)]['conversation_id'])
        self.assertNotIn(str(wrong_lead_message), items)
        empty = await self.journey(tenant=self.other_tenant, lead=self.other_lead)
        self.assertEqual(empty['items'], [])
        self.assertTrue(all(row['state']=='unknown' for row in empty['coverage']['categories']))

    async def test_outbound_crash_followup_future_and_campaign_milestones(self):
        outbound = await self.message(direction='outbound', from_me=True, message_status='UNKNOWN_DELIVERY')
        followup = await self.followup()
        campaign = await self.campaign(status='read', sent_at=self.naive, delivered_at=self.naive, read_at=self.naive)
        result = await self.journey(limit=100)
        messages = [row for row in result['items'] if row['evidence']['record_id']==str(outbound)]
        self.assertEqual(messages[0]['status'], 'unknown')
        self.assertEqual(messages[0]['details']['delivery_status'], 'UNKNOWN_DELIVERY')
        follows = [row for row in result['items'] if row['evidence']['record_id']==str(followup)]
        self.assertEqual(len(follows), 1)
        self.assertEqual(follows[0]['status'], 'pending')
        self.assertEqual(follows[0]['occurred_at'], self.t.isoformat())
        self.assertNotEqual(follows[0]['occurred_at'], follows[0]['details']['scheduled_at'])
        milestones = {row['type'] for row in result['items'] if row['evidence']['record_id']==str(campaign)}
        self.assertEqual(milestones, {'campaign_recipient_registered','campaign_sent','campaign_delivered','campaign_read'})
        self.assertFalse(any(row['category']=='acquisition' for row in result['items'] if row['evidence']['record_id']==str(campaign)))
        self.assertNotIn('PRIVATE',json.dumps(result))

    async def test_snapshots_remain_snapshots_without_artificial_transitions(self):
        funnel = await self.add(CRMFunil, empresa_id=self.tenant, nome='Synthetic funnel')
        stage = await self.add(CRMEtapa, funil_id=funnel, nome='Qualificado')
        tag = await self.add(TagCRM, empresa_id=self.tenant, nome='Interesse')
        foreign_tag = await self.add(TagCRM, empresa_id=self.other_tenant, nome='PRIVATE_FOREIGN_TAG')
        await self.change(CRMLead, self.lead, etapa_id=stage, tags=[str(tag),str(foreign_tag)], dados_adicionais={
            'commercial_state': {'stage_key':'qualification_confirmed','signals':['QualifiedLead','PRIVATE_SIGNAL'], 'updated_at':self.t.isoformat(),'token':'PRIVATE_STATE_TOKEN'},
            'commercial_state_history':[{'stage_key':'purchase_confirmed','updated_at':self.t.isoformat()}]})
        await self.change(Empresa, self.tenant, credenciais_canais={'access_token':'PRIVATE_TOKEN', 'monitor_funnel_spec': {
            'version':'monitor-funnel-v1', 'stages':[{'key':'qualification_confirmed','order':3,'display_name':'Qualificado','meta_event':'QualifiedLead','private':'PRIVATE_CRITERIA'}]}})
        result = await self.journey()
        self.assertEqual({row['category'] for row in result['items']}, {'conversation'})
        self.assertEqual(result['summary']['crm']['stage']['id'], str(stage))
        self.assertEqual([row['id'] for row in result['summary']['crm']['tags']], [str(tag)])
        self.assertTrue(result['summary']['crm']['snapshot_only'])
        self.assertFalse(result['summary']['funnel']['history_complete'])
        self.assertEqual(result['summary']['commercial_state']['signals'], ['QualifiedLead'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        # Release the stage reference so deterministic reverse-FK cleanup works.
        await self.change(CRMLead, self.lead, etapa_id=None)

    async def test_tag_and_projected_commercial_fact_have_distinct_explicit_sources(self):
        tag = await self.add(TagCRM, empresa_id=self.tenant, nome='Qualificado')
        tag_event = await self.add(CRMTagEvent, empresa_id=self.tenant, lead_id=self.lead, tag_id=tag,
            action='applied', source='ai', occurred_at=self.naive, idempotency_key=str(uuid4()))
        fact = await self.add(CanonicalCommercialEvent, empresa_id=self.tenant, lead_id=self.lead,
            tag_event_id=tag_event, event_name='QualifiedLead', event_category='lead', source_type='managed_tag',
            source_id=str(tag_event), occurred_at=self.t, idempotency_key=str(uuid4()),
            is_backfilled=True, external_dispatch_policy='historical_no_send', value='99.00', currency='BRL',
            revenue_verified=False, evidence_json={'access_token':'PRIVATE_TAG_TOKEN'})
        result = await self.journey()
        events = {row['evidence']['record_id']:row for row in result['items']}
        self.assertEqual(events[str(tag_event)]['evidence']['source'], 'crm_tag_events')
        self.assertEqual(events[str(fact)]['evidence']['source'], 'commercial_events')
        self.assertEqual(events[str(fact)]['details']['source_tag_event_id'],str(tag_event))
        self.assertTrue(events[str(fact)]['details']['historical_no_send'])
        self.assertNotIn('amount',events[str(fact)]['details'])
        self.assertNotIn('PRIVATE',json.dumps(result))

    async def test_same_tenant_foreign_lead_tag_reference_is_not_presented_as_exact(self):
        tag = await self.add(TagCRM, empresa_id=self.tenant, nome='Qualificado')
        foreign_event = await self.add(CRMTagEvent, empresa_id=self.tenant, lead_id=self.same_tenant_other_lead,
            tag_id=tag, action='applied', source='ai', occurred_at=self.naive, idempotency_key=str(uuid4()))
        fact = await self.add(CanonicalCommercialEvent, empresa_id=self.tenant, lead_id=self.lead,
            tag_event_id=foreign_event, event_name='QualifiedLead', event_category='lead', source_type='managed_tag',
            source_id=str(foreign_event), occurred_at=self.t, idempotency_key=str(uuid4()))
        result = await self.journey(category='commercial')
        self.assertEqual(result['items'][0]['evidence']['record_id'],str(fact))
        self.assertNotIn('source_tag_event_id',result['items'][0]['details'])

    async def test_legacy_followup_requires_both_tenant_scoped_parents(self):
        good_config = await self.add(ConfigFollowUp, empresa_id=self.tenant, nome='Synthetic rule', tempo_gatilho_minutos=1, objetivo_prompt='private')
        other_config = await self.add(ConfigFollowUp, empresa_id=self.other_tenant, nome='Other rule', tempo_gatilho_minutos=1, objetivo_prompt='private')
        good = await self.add(LeadFollowUpLog, lead_id=self.lead, config_followup_id=good_config, data_envio=self.naive, status_envio='enviado')
        bad = await self.add(LeadFollowUpLog, lead_id=self.lead, config_followup_id=other_config, data_envio=self.naive, status_envio='enviado')
        foreign = await self.add(LeadFollowUpLog, lead_id=self.other_lead, config_followup_id=good_config, data_envio=self.naive, status_envio='enviado')
        result = await self.journey(category='followup')
        ids = {row['evidence']['record_id'] for row in result['items']}
        self.assertIn(str(good),ids)
        self.assertNotIn(str(bad),ids)
        self.assertNotIn(str(foreign),ids)
        self.assertEqual(result['items'][0]['status'],'unknown')

    async def test_attribution_resolution_requires_this_touch_and_tenant(self):
        touch = await self.touch()
        await self.add(AttributionTouchResolution, empresa_id=self.tenant, touch_id=touch,
            resolution_status='resolved', ad_id='123', adset_id='456', campaign_id='789')
        result = await self.journey(category='acquisition')
        self.assertEqual(result['items'][0]['details']['ad_id'],'123')
        self.assertTrue(result['summary']['attribution']['ads_identity_verified'])
        self.assertEqual(result['summary']['attribution']['status_counts']['resolved'],1)

    async def test_checkout_and_provider_payment_positive_proof_reject_wrong_origin(self):
        checkout = await self.touch(event_kind='checkout_started', touch_type='conversion', touch_source='checkout_service',
            checkout_id='PRIVATE_CHECKOUT', metadata_json={'provenance':'provider_checkout_reference'})
        wrong_checkout = await self.touch(event_kind='checkout_started', touch_source='manual', checkout_id='PRIVATE_CHECKOUT',
            metadata_json={'provenance':'provider_checkout_reference'})
        approved = await self.touch(event_kind='payment_approved', touch_type='conversion', touch_source='payment_webhook',
            payment_id='PRIVATE_PAYMENT', amount='19.95', currency='BRL', metadata_json={'provenance':'provider_verified_payment_status'})
        wrong_payment = await self.touch(event_kind='payment_approved', touch_source='manual', amount='99.00', currency='BRL',
            metadata_json={'provenance':'provider_verified_payment_status'})
        result = await self.journey(limit=100)
        rows = {row['evidence']['record_id']:row for row in result['items']}
        self.assertIn(str(checkout),rows)
        self.assertIn(str(wrong_checkout),rows)
        self.assertEqual(rows[str(wrong_checkout)]['status'],'unknown')
        self.assertFalse(rows[str(wrong_checkout)]['details'].get('verified',False))
        self.assertEqual(Decimal(rows[str(approved)]['details']['amount']),Decimal('19.95'))
        self.assertTrue(rows[str(approved)]['details']['verified'])
        self.assertEqual(rows[str(wrong_payment)]['status'],'unknown')
        self.assertFalse(rows[str(wrong_payment)]['details']['verified'])
        self.assertNotIn('amount',rows[str(wrong_payment)]['details'])
        self.assertNotIn('PRIVATE',json.dumps(result))

    async def test_provider_payment_simulation_and_missing_verification_are_not_revenue_proof(self):
        verified = await self.provider_payment()
        unverified = await self.provider_payment(metadata={})
        simulated = await self.provider_payment(metadata={'provenance':'provider_verified_payment_status','is_simulated':True})
        e2e = await self.provider_payment(environment='e2e')
        result = await self.journey(category='payment')
        rows = {row['evidence']['record_id']:row for row in result['items']}
        self.assertEqual(Decimal(rows[str(verified)]['details']['amount']),Decimal('39.90'))
        self.assertEqual(rows[str(unverified)]['status'],'unknown')
        self.assertFalse(rows[str(unverified)]['details']['verified'])
        self.assertNotIn('amount',rows[str(unverified)]['details'])
        self.assertNotIn(str(simulated),rows)
        self.assertNotIn(str(e2e),rows)
        self.assertNotIn('PRIVATE',json.dumps(result))

    async def test_payment_boolean_proof_and_required_financial_fields(self):
        boolean_proof = await self.provider_payment(metadata={'provider_status_verified':True})
        string_proof = await self.provider_payment(metadata={'provider_status_verified':'true'})
        missing_reference = await self.touch(event_kind='payment_approved', touch_type='conversion',
            touch_source='payment_webhook', amount='19.95', currency='BRL',
            metadata_json={'provenance':'provider_verified_payment_status'})
        missing_amount = await self.touch(event_kind='payment_approved', touch_type='conversion',
            touch_source='payment_webhook', payment_id='PRIVATE_PAYMENT',
            metadata_json={'provenance':'provider_verified_payment_status'})
        result = await self.journey(category='payment')
        rows = {row['evidence']['record_id']:row for row in result['items']}
        self.assertTrue(rows[str(boolean_proof)]['details']['verified'])
        for identifier in (string_proof,missing_reference,missing_amount):
            self.assertEqual(rows[str(identifier)]['status'],'unknown')
            self.assertFalse(rows[str(identifier)]['details']['verified'])
            self.assertNotIn('amount',rows[str(identifier)]['details'])

    async def test_commercial_purchase_requires_more_than_a_tag_and_verified_flag(self):
        fact = await self.add(CanonicalCommercialEvent, empresa_id=self.tenant, lead_id=self.lead,
            event_name='Purchase', event_category='purchase', source_type='managed_tag',
            occurred_at=self.t, idempotency_key=str(uuid4()), value='99.00', currency='BRL', revenue_verified=True)
        valid = await self.add(CanonicalCommercialEvent, empresa_id=self.tenant, lead_id=self.lead,
            event_name='Purchase', event_category='purchase', source_type='attribution_touch',
            occurred_at=self.t, idempotency_key=str(uuid4()), value='99.00', currency='BRL',
            payment_id='PRIVATE_PAYMENT_REFERENCE', revenue_verified=True)
        result = await self.journey(category='commercial')
        rows = {row['evidence']['record_id']:row for row in result['items']}
        self.assertFalse(rows[str(fact)]['details']['verified'])
        self.assertNotIn('amount',rows[str(fact)]['details'])
        self.assertTrue(rows[str(valid)]['details']['verified'])
        self.assertEqual(Decimal(rows[str(valid)]['details']['amount']),Decimal('99.00'))

    async def test_multiple_proven_conversations_preserve_activity_boundaries(self):
        second = await self.add(ChannelConversation, empresa_id=self.tenant, conexao_id=self.connection,
            contact_id=self.contact, lead_id=self.lead, channel='whatsapp', provider='meta_official',
            conversation_key=str(uuid4()), external_conversation_id=str(uuid4()), criado_em=self.naive)
        await self.message()
        await self.message(channel_conversation_id=second, criado_em=self.naive+timedelta(seconds=15), direction='outbound', from_me=True)
        result = await self.journey()
        conversations = {row['conversation_id'] for row in result['items'] if row['category']=='conversation'}
        self.assertEqual(conversations,{str(self.conversation),str(second)})
        self.assertEqual(result['summary']['activity']['recorded_response_intervals'],0)

    async def test_activity_adjacent_intervals_are_not_causal_response_or_abandonment(self):
        await self.message(criado_em=self.naive)
        await self.message(criado_em=self.naive+timedelta(seconds=20), direction='outbound', from_me=True, message_status='sent')
        await self.message(criado_em=self.naive+timedelta(seconds=30), tipo_mensagem='reaction')
        await self.message(criado_em=self.naive+timedelta(seconds=40), metadata_json={'event_kind':'status'})
        result = await self.journey(category='message')
        self.assertEqual(len(result['items']),2)
        activity = result['summary']['activity']
        self.assertEqual(activity['inbound_messages'],1)
        self.assertEqual(activity['outbound_messages'],1)
        self.assertEqual(activity['recorded_response_intervals'],1)
        self.assertEqual(activity['mean_recorded_response_seconds'],20)
        self.assertTrue(activity['intervals_are_not_delivery_proof'])
        self.assertFalse(activity['abandonment_inferred'])
