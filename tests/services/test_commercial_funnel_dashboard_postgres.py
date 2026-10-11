"""A05 queries against disposable PostgreSQL, with synthetic fixtures only.

Fixture mutation refuses every database except a00-pg/a05_test. Service calls
run in a READ ONLY transaction. No transports, providers or runtime writes.
"""
import csv
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, event, insert, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.commercial_funnel_dashboard import dashboard, drilldown, export_csv
from db.models import (
    AttributionTouch, AttributionTouchResolution, CanonicalCommercialEvent,
    ChannelContact, ChannelConversation, ConfigFollowUp, Conexao, CRMEtapa,
    CRMFunil, CRMLead, CRMTagEvent, Empresa, FollowupExecution, LeadFollowUpLog,
    LeoFTPaymentEvent, MensagemHistorico, TagCRM, TipoConexao,
)

URL = os.getenv('FUNNEL_ANALYTICS_TEST_DATABASE_URL', '')
STAGES = (
    ('content_engaged', 'ViewContent'), ('lead_established', 'LeadSubmitted'),
    ('qualification_confirmed', 'QualifiedLead'), ('selection_confirmed', 'AddToCart'),
    ('checkout_intent', 'InitiateCheckout'), ('order_commitment', 'OrderCreated'),
    ('abandonment', 'CartAbandoned'), ('purchase_confirmed', 'Purchase'),
)


@unittest.skipUnless(URL, 'isolated FUNNEL_ANALYTICS_TEST_DATABASE_URL required')
class CommercialFunnelPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(URL)
        if parsed.get_backend_name() != 'postgresql' or parsed.database != 'a05_test' or parsed.host != 'a00-pg':
            raise RuntimeError('refusing funnel fixtures outside isolated a00-pg/a05_test')
        self.engine = create_async_engine(URL.replace('postgresql://', 'postgresql+asyncpg://', 1))
        self.rows = []
        self.t = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        self.naive = self.t.replace(tzinfo=None)
        self.horizon = datetime(2026, 1, 31, 23, tzinfo=timezone.utc)
        self.spec = {'version': 'synthetic-funnel-v1', 'stages': [
            {'key': key, 'order': index, 'display_name': key.replace('_', ' '), 'meta_event': meta,
             'source_policy': 'payment' if meta == 'Purchase' else 'conversational'}
            for index, (key, meta) in enumerate(STAGES, 1)]}
        self.tenant = await self.add(Empresa, nome_empresa='Synthetic cohort A',
            credenciais_canais={'monitor_funnel_spec': self.spec, 'access_token': 'PRIVATE_TENANT_TOKEN'})
        self.other_tenant = await self.add(Empresa, nome_empresa='Synthetic cohort B')
        self.lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic A',
            telefone_contato='PRIVATE_PHONE', dados_adicionais={'access_token': 'PRIVATE_CRM_TOKEN'})
        self.other_lead = await self.add(CRMLead, empresa_id=self.other_tenant, nome_contato='Synthetic other tenant')
        self.same_tenant_other_lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic C')
        self.connection = await self.add(Conexao, empresa_id=self.tenant, tipo=TipoConexao.META, nome_instancia=str(uuid4()))
        self.contact = await self.add(ChannelContact, empresa_id=self.tenant, conexao_id=self.connection,
            lead_id=self.lead, channel='whatsapp', provider='meta_official', external_contact_id=str(uuid4()))
        self.conversation = await self.conversation_for()
        self.tags = {}
        for index, (key, meta) in enumerate(STAGES, 1):
            instruction = '[AGENTEOS_MONITOR_FUNNEL]'+json.dumps({
                'managed_by_monitor_funnel': True, 'funnel_stage_key': key,
                'funnel_version': self.spec['version'], 'meta_event_name': meta,
                'display_order': index, 'active': True, 'monitor_allowed': meta != 'Purchase'})
            self.tags[key] = await self.add(TagCRM, empresa_id=self.tenant, nome=key.replace('_', ' '),
                instrucao_ia=instruction, ativa_no_funil=True, tipo='conversao', ordem=index)

    async def asyncTearDown(self):
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
        identifier = values.setdefault('id', uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(insert(model.__table__).values(**values))
        self.rows.append((model.__table__, identifier))
        return identifier

    async def change(self, model, identifier, **values):
        async with self.engine.begin() as conn:
            await conn.execute(update(model.__table__).where(model.__table__.c.id == identifier).values(**values))

    async def conversation_for(self, **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, contact_id=self.contact,
            conexao_id=self.connection, channel='whatsapp', provider='meta_official',
            conversation_key=str(uuid4()), external_conversation_id=str(uuid4()), criado_em=self.naive,
            last_message_at=self.naive)
        return await self.add(ChannelConversation, **(defaults | values))

    async def message(self, **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, channel_conversation_id=self.conversation,
            conexao_id=self.connection, channel='whatsapp', provider='meta_official', texto='PRIVATE_MESSAGE_CONTENT',
            criado_em=self.naive, direction='inbound', from_me=False,
            metadata_json={'access_token': 'PRIVATE_MESSAGE_TOKEN'})
        return await self.add(MensagemHistorico, **(defaults | values))

    async def tag_event(self, key='lead_established', **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, conversation_id=self.conversation,
            tag_id=self.tags[key], action='applied', source='manual', occurred_at=self.naive,
            idempotency_key=str(uuid4()), metadata_json={'access_token': 'PRIVATE_TAG_TOKEN'})
        return await self.add(CRMTagEvent, **(defaults | values))

    async def commercial(self, name='LeadSubmitted', **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, conversation_id=self.conversation,
            event_name=name, event_category='conversational', source_type='persisted_tag_event',
            occurred_at=self.t, idempotency_key=str(uuid4()), is_backfilled=True,
            external_dispatch_policy='historical_no_send', evidence_json={'access_token': 'PRIVATE_EVENT_TOKEN'})
        return await self.add(CanonicalCommercialEvent, **(defaults | values))

    async def touch(self, **values):
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, conversation_id=self.conversation,
            contact_id=self.contact, connection_id=self.connection, event_kind='ctwa_referral',
            touch_type='acquisition', touch_source='meta_webhook', occurred_at=self.t,
            provider='meta_official', channel='whatsapp', idempotency_key=str(uuid4()),
            source_url='https://private.example/token', body='PRIVATE_TOUCH_BODY')
        return await self.add(AttributionTouch, **(defaults | values))

    async def resolution(self, touch, **values):
        defaults = dict(empresa_id=self.tenant, touch_id=touch, resolution_status='resolved',
            ad_id='101', adset_id='201', campaign_id='301')
        return await self.add(AttributionTouchResolution, **(defaults | values))

    async def payment(self, **values):
        defaults = dict(company_id=self.tenant, contact_id=self.lead, student_id=self.lead,
            event_type='leoft.payment_confirmed.v1', occurred_at=self.t, product_id='synthetic',
            order_id=str(uuid4()), payment_id='synthetic-payment', provider='mercado_pago',
            amount='39.90', currency='BRL', payment_status='confirmed', environment='production',
            correlation_id=uuid4(), idempotency_key=str(uuid4()), raw_event_hash='a'*64,
            metadata={'provenance': 'provider_verified_payment_status', 'access_token': 'PRIVATE_PAYMENT_TOKEN'})
        return await self.add(LeoFTPaymentEvent, **(defaults | values))

    async def financial_fact(self, source, *, lead=None, payment_id='synthetic-financial',
                             amount='100', currency='BRL', metadata=None, evidence=None):
        """The same positively verified financial fact in each supported source."""
        lead = lead or self.lead
        proof = {'provenance': 'provider_verified_payment_status'}
        if source == 'provider':
            return await self.payment(contact_id=lead, student_id=lead, payment_id=payment_id,
                amount=amount, currency=currency, metadata=proof | (metadata or {}))
        if source == 'ledger':
            return await self.touch(lead_id=lead, conversation_id=None, event_kind='payment_approved',
                touch_type='payment', touch_source='payment_webhook', provider='mercado_pago',
                payment_id=payment_id, amount=amount, currency=currency,
                metadata_json=proof | (metadata or {}))
        if source == 'commercial':
            return await self.commercial('Purchase', lead_id=lead, conversation_id=None,
                provider='mercado_pago', payment_id=payment_id, value=amount, currency=currency,
                revenue_verified=True, metadata_json=metadata or {}, evidence_json=proof | (evidence or {}))
        raise AssertionError('unknown fixture source')

    async def followup(self, **values):
        config = await self.add(ConfigFollowUp, empresa_id=self.tenant, nome='Synthetic follow-up',
            tempo_gatilho_minutos=10, objetivo_prompt='PRIVATE_PROMPT')
        defaults = dict(empresa_id=self.tenant, lead_id=self.lead, conversation_id=self.conversation,
            contact_id=self.contact, conexao_id=self.connection, followup_id=config,
            provider='meta_official', channel='whatsapp', trigger='tag_applied',
            scheduled_at=self.naive, sent_at=self.naive+timedelta(days=1), status='sent',
            idempotency_key=str(uuid4()), policy_snapshot_json={'mode': 'conversation_resume'})
        return await self.add(FollowupExecution, **(defaults | values))

    async def call(self, function=dashboard, *, tenant=None, **filters):
        args = dict(empresa_id=tenant or self.tenant, start='2026-01-01', end='2026-02-01',
            observed_at=self.horizon, inactivity_hours=168, return_window_days=7) | filters
        async with AsyncSession(self.engine) as session:
            await session.execute(text('SET TRANSACTION READ ONLY'))
            result = await function(session, **args)
            self.assertEqual((await session.execute(text('SHOW transaction_read_only'))).scalar(), 'on')
            return result

    @staticmethod
    def stages(result):
        return {row['key']: row for row in result['stages']}

    async def test_actual_postgres_readonly_snapshot_and_aggregate_no_pii(self):
        await self.message()
        result = await self.call()
        self.assertEqual(result['summary']['total_leads'], 1)
        self.assertEqual(result['summary']['inbound_leads'], 1)
        serialized = json.dumps(result)
        for value in ('PRIVATE', 'Synthetic A', str(self.lead), 'telefone_contato', 'texto'):
            self.assertNotIn(value, serialized)
        self.assertIn('lead', result['window']['unit'].lower())

    async def test_first_entry_entire_history_before_window_not_reentered(self):
        await self.change(ChannelConversation, self.conversation, criado_em=datetime(2025, 12, 1), last_message_at=self.naive)
        await self.conversation_for(criado_em=self.naive+timedelta(days=1))
        await self.message(criado_em=self.naive+timedelta(days=2))
        result = await self.call()
        self.assertEqual(result['summary']['total_leads'], 0)
        self.assertEqual((await self.call(drilldown))['total'], 0)

    async def test_inbound_prior_to_first_conversation_controls_cohort(self):
        await self.message(criado_em=datetime(2025, 12, 31), channel_conversation_id=None)
        self.assertEqual((await self.call())['summary']['total_leads'], 0)

    async def test_multiple_conversations_messages_duplicate_stage_do_not_multiply_contact(self):
        await self.conversation_for(criado_em=self.naive+timedelta(days=1))
        for _ in range(4):
            await self.message()
        for hour in (0, 1):
            tag = await self.tag_event(occurred_at=self.naive+timedelta(hours=hour))
            await self.commercial(tag_event_id=tag, occurred_at=self.t+timedelta(hours=hour))
        result = await self.call()
        self.assertEqual(result['summary']['total_leads'], 1)
        self.assertEqual(result['summary']['conversations'], 2)
        self.assertEqual(self.stages(result)['lead_established']['reached_leads'], 1)
        self.assertFalse(any(row['from_stage']==row['to_stage'] for row in result['transitions']))

    async def test_skip_and_loop_transitions_use_observed_sequence_not_fabricated_steps(self):
        for hour, key in enumerate(('lead_established', 'selection_confirmed', 'qualification_confirmed', 'lead_established')):
            tag = await self.tag_event(key, occurred_at=self.naive+timedelta(hours=hour))
            name = dict(STAGES)[key]
            await self.commercial(name, tag_event_id=tag, occurred_at=self.t+timedelta(hours=hour))
        result = await self.call()
        edges = {(row['from_stage'], row['to_stage']): row for row in result['transitions']}
        self.assertIn(('lead_established', 'selection_confirmed'), edges)
        self.assertIn(('selection_confirmed', 'qualification_confirmed'), edges)
        self.assertIn(('qualification_confirmed', 'lead_established'), edges)
        self.assertNotIn(('lead_established', 'qualification_confirmed'), edges)
        self.assertEqual(sum(row['occurrences'] for row in edges.values()), 3)

    async def test_current_crm_snapshot_never_becomes_historical_transition(self):
        funnel = await self.add(CRMFunil, empresa_id=self.tenant, nome='Synthetic CRM')
        stage = await self.add(CRMEtapa, funil_id=funnel, nome='Stage now', tipo='qualified', ordem=1)
        await self.change(CRMLead, self.lead, etapa_id=stage)
        result = await self.call()
        self.assertEqual(sum(row['current_leads'] for row in result['crm_snapshot']), 1)
        self.assertEqual(result['transitions'], [])
        self.assertEqual(self.stages(result)['qualification_confirmed']['reached_leads'], 0)

    async def test_seven_purchase_tags_without_payment_never_convert_or_zero_revenue(self):
        await self.tag_event('purchase_confirmed')
        for _ in range(6):
            lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic unverified purchase')
            await self.message(lead_id=lead, channel_conversation_id=None)
            await self.tag_event('purchase_confirmed', lead_id=lead, conversation_id=None)
        await self.commercial('Purchase', revenue_verified=False, payment_id=None)
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 0)
        self.assertIsNone(self.stages(result)['purchase_confirmed']['reached_leads'])
        self.assertEqual(result['summary']['revenue']['status'], 'unknown')
        self.assertNotEqual(result['summary']['revenue']['by_currency'], {'BRL': 0})
        self.assertIsNone(result['summary']['lost'])
        self.assertEqual(result['coverage']['financial']['unverified_purchase_signal_leads'], 7)
        self.assertEqual(result['coverage']['financial']['state'], 'unknown')

    async def test_checkout_operational_touch_and_commercial_projection_count_once(self):
        touch = await self.touch(event_kind='checkout_started', touch_type='checkout', touch_source='checkout_service',
            checkout_id='synthetic-checkout', metadata_json={'provenance':'provider_checkout_reference'})
        await self.commercial('InitiateCheckout', attribution_touch_id=touch, occurred_at=self.t)
        result = await self.call()
        self.assertEqual(self.stages(result)['checkout_intent']['reached_leads'], 1)
        self.assertEqual(result['transitions'], [])
        self.assertEqual((await self.call(drilldown, stage='checkout_intent'))['total'], 1)

    async def test_ambiguous_payment_identity_shared_by_contacts_is_not_assigned(self):
        await self.message(lead_id=self.same_tenant_other_lead, channel_conversation_id=None)
        await self.payment(payment_id='synthetic-ambiguous', amount='100')
        await self.touch(event_kind='payment_approved', touch_type='payment', touch_source='payment_webhook',
            provider='mercado_pago', lead_id=self.same_tenant_other_lead, conversation_id=None,
            payment_id='synthetic-ambiguous', amount='100', currency='BRL',
            metadata_json={'provenance':'provider_verified_payment_status'})
        result = await self.call()
        self.assertEqual(result['summary']['total_leads'], 2)
        self.assertEqual(result['summary']['converted'], 0)
        self.assertEqual(result['summary']['revenue']['status'], 'unknown')

    async def test_financial_ownership_conflict_outside_entry_cohort_blocks_all_sources(self):
        await self.message(lead_id=self.same_tenant_other_lead, channel_conversation_id=None,
            criado_em=datetime(2025, 12, 1))
        for source in ('provider', 'ledger', 'commercial'):
            with self.subTest(conflicting_source=source):
                payment_id = 'synthetic-outside-entry-'+source
                await self.financial_fact(source, payment_id=payment_id)
                await self.financial_fact(source, lead=self.same_tenant_other_lead, payment_id=payment_id)
                result = await self.call()
                self.assertEqual(result['summary']['total_leads'], 1)
                self.assertEqual(result['summary']['converted'], 0)
                self.assertEqual(result['summary']['revenue']['status'], 'unknown')
                self.assertEqual(result['summary']['revenue']['by_currency'], [])
                self.assertGreaterEqual(result['coverage']['financial']['ambiguous_payment_identities'], 1)
                self.assertEqual((await self.call(drilldown, status='converted'))['total'], 0)

    async def test_outside_cohort_currency_and_amount_conflicts_remain_unknown(self):
        await self.message(lead_id=self.same_tenant_other_lead, channel_conversation_id=None,
            criado_em=datetime(2025, 12, 1))
        for source, amount, currency in (('provider', '101', 'BRL'), ('ledger', '100', 'USD'),
                                         ('commercial', '101', 'USD')):
            with self.subTest(source=source, amount=amount, currency=currency):
                payment_id = 'synthetic-outside-value-'+source
                await self.financial_fact('provider', payment_id=payment_id)
                await self.financial_fact(source, lead=self.same_tenant_other_lead, payment_id=payment_id,
                    amount=amount, currency=currency)
                result = await self.call()
                self.assertEqual(result['summary']['converted'], 0)
                self.assertEqual(result['summary']['revenue']['status'], 'unknown')
                self.assertGreaterEqual(result['coverage']['financial']['ambiguous_payment_identities'], 1)

    async def test_financial_identity_conflict_survives_stage_origin_and_ads_filters(self):
        await self.message(lead_id=self.same_tenant_other_lead, channel_conversation_id=None)
        await self.tag_event('lead_established')
        await self.resolution(await self.touch(channel='instagram'), ad_id='101', campaign_id='301', adset_id='201')
        await self.financial_fact('provider', payment_id='synthetic-filter-conflict')
        await self.financial_fact('commercial', lead=self.same_tenant_other_lead,
            payment_id='synthetic-filter-conflict')
        for filters in ({'stage':'lead_established'}, {'campaign_id':'301'}, {'ad_id':'101'}, {'origin':'instagram'}):
            with self.subTest(filters=filters):
                result = await self.call(**filters)
                self.assertEqual(result['summary']['total_leads'], 1)
                self.assertEqual(result['summary']['converted'], 0)
                self.assertEqual(result['summary']['revenue']['status'], 'unknown')
                self.assertGreaterEqual(result['coverage']['financial']['ambiguous_payment_identities'], 1)
                self.assertEqual((await self.call(drilldown, **filters))['total'], 1)

    async def test_same_financial_identity_in_other_tenant_does_not_taint_this_tenant(self):
        await self.financial_fact('provider', payment_id='synthetic-tenant-scoped')
        await self.payment(company_id=self.other_tenant, contact_id=self.other_lead, student_id=self.other_lead,
            payment_id='synthetic-tenant-scoped', amount='999', currency='USD')
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 1)
        self.assertEqual(result['summary']['revenue']['by_currency'][0]['currency'], 'BRL')
        self.assertEqual(Decimal(str(result['summary']['revenue']['by_currency'][0]['gross'])), Decimal('100'))
        self.assertEqual(result['coverage']['financial']['ambiguous_payment_identities'], 0)

    async def test_same_owner_amount_and_currency_conflicts_do_not_pick_source_priority(self):
        for source, amount, currency in (('ledger', '101', 'BRL'), ('commercial', '100', 'USD')):
            with self.subTest(source=source):
                payment_id = 'synthetic-same-owner-conflict-'+source
                await self.financial_fact('provider', payment_id=payment_id)
                await self.financial_fact(source, payment_id=payment_id, amount=amount, currency=currency)
                result = await self.call()
                self.assertEqual(result['summary']['converted'], 0)
                self.assertEqual(result['summary']['revenue']['status'], 'unknown')
                self.assertGreaterEqual(result['coverage']['financial']['ambiguous_payment_identities'], 1)

    async def test_positive_financial_proof_deduplicates_provider_payment_id_across_sources(self):
        await self.payment(amount='100.00', payment_id='synthetic-shared')
        await self.touch(event_kind='payment_approved', touch_type='payment', touch_source='payment_webhook',
            provider='mercado_pago', payment_id='synthetic-shared', amount='100', currency='BRL',
            metadata_json={'provenance': 'provider_verified_payment_status'})
        await self.commercial('Purchase', provider='mercado_pago', payment_id='synthetic-shared',
            revenue_verified=True, value='100', currency='BRL',
            evidence_json={'provenance':'provider_verified_payment_status'})
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 1)
        amounts = result['summary']['revenue']['by_currency']
        if isinstance(amounts, list):
            amounts = {row['currency']: row['gross'] for row in amounts}
        self.assertEqual(Decimal(str(amounts['BRL'])), Decimal('100'))
        self.assertEqual(self.stages(result)['purchase_confirmed']['reached_leads'], 1)

    async def test_nonproduction_simulated_and_string_true_are_not_payment_proof(self):
        await self.payment(payment_id='synthetic-e2e', environment='e2e', provider='agentos_controlled_e2e')
        await self.payment(payment_id='synthetic-string', metadata={'provider_status_verified': 'true'})
        await self.payment(payment_id='synthetic-simulated', metadata={'provider_status_verified': True, 'simulated': True})
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 0)
        self.assertEqual(result['summary']['revenue']['status'], 'unknown')

    async def test_simulated_and_is_simulated_aliases_excluded_in_each_financial_source(self):
        for source in ('provider', 'ledger', 'commercial'):
            for flag in ('simulated', 'is_simulated'):
                with self.subTest(source=source, flag=flag):
                    await self.financial_fact(source, payment_id='synthetic-flag-'+source+'-'+flag,
                        metadata={flag:True})
                    result = await self.call()
                    self.assertEqual(result['summary']['converted'], 0)
                    self.assertEqual(result['summary']['revenue']['status'], 'unknown')
                    self.assertIsNone(self.stages(result)['purchase_confirmed']['reached_leads'])

    async def test_commercial_evidence_simulation_aliases_override_verified_provenance(self):
        for flag in ('simulated', 'is_simulated'):
            for value in (True, 'true', '1'):
                with self.subTest(flag=flag, value=value):
                    await self.financial_fact('commercial', payment_id='synthetic-evidence-'+flag+'-'+str(value),
                        evidence={flag:value})
                    result = await self.call()
                    self.assertEqual(result['summary']['converted'], 0)
                    self.assertEqual(result['summary']['revenue']['status'], 'unknown')

    async def test_verified_revenue_currencies_stay_separate(self):
        await self.payment(payment_id='synthetic-brl', amount='20.00', currency='BRL')
        await self.payment(payment_id='synthetic-usd', amount='10.00', currency='USD',
            metadata={'provider_status_verified': True})
        result = await self.call()
        amounts = result['summary']['revenue']['by_currency']
        if isinstance(amounts, list):
            amounts = {row['currency']: row['gross'] for row in amounts}
        self.assertEqual({key: Decimal(str(value)) for key, value in amounts.items()},
                         {'BRL': Decimal('20'), 'USD': Decimal('10')})

    async def test_ledger_only_negative_refund_preserves_positive_financial_magnitude(self):
        for kind, amount in (('payment_approved', '100'), ('refund', '-30')):
            await self.touch(event_kind=kind, touch_type='payment', touch_source='payment_webhook',
                provider='mercado_pago', payment_id='synthetic-ledger-refund', amount=amount, currency='BRL',
                metadata_json={'provenance':'provider_verified_payment_status'})
        result = await self.call()
        money = result['summary']['revenue']['by_currency'][0]
        self.assertEqual(money['currency'], 'BRL')
        self.assertEqual(Decimal(str(money['gross'])), Decimal('100'))
        self.assertEqual(Decimal(str(money['refunded'])), Decimal('30'))
        self.assertEqual(Decimal(str(money['net'])), Decimal('70'))
        self.assertEqual(result['coverage']['financial']['ambiguous_payment_identities'], 0)

    async def test_negative_ledger_and_positive_commercial_refund_deduplicate(self):
        await self.touch(event_kind='payment_approved', touch_type='payment', touch_source='payment_webhook',
            provider='mercado_pago', payment_id='synthetic-shared-refund', amount='100', currency='BRL',
            metadata_json={'provenance':'provider_verified_payment_status'})
        refund = await self.touch(event_kind='refund', touch_type='payment', touch_source='payment_webhook',
            provider='mercado_pago', payment_id='synthetic-shared-refund', amount='-30', currency='BRL',
            metadata_json={'provenance':'provider_verified_payment_status'})
        await self.commercial('Refund', provider='mercado_pago', payment_id='synthetic-shared-refund',
            value='30', currency='BRL', revenue_verified=True, attribution_touch_id=refund,
            evidence_json={'provenance':'provider_verified_payment_status'})
        result = await self.call()
        money = result['summary']['revenue']['by_currency'][0]
        self.assertEqual(Decimal(str(money['gross'])), Decimal('100'))
        self.assertEqual(Decimal(str(money['refunded'])), Decimal('30'))
        self.assertEqual(Decimal(str(money['net'])), Decimal('70'))
        self.assertEqual(result['summary']['converted'], 1)
        self.assertEqual(result['coverage']['financial']['ambiguous_payment_identities'], 0)

    async def test_negative_purchase_and_unverified_refund_are_not_financial_proof(self):
        await self.financial_fact('ledger', payment_id='synthetic-negative-purchase', amount='-100')
        await self.financial_fact('commercial', payment_id='synthetic-negative-commercial', amount='-100')
        await self.touch(event_kind='refund', touch_type='payment', touch_source='payment_webhook',
            provider='mercado_pago', payment_id='synthetic-unverified-refund', amount='-30', currency='BRL',
            metadata_json={})
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 0)
        self.assertEqual(result['summary']['revenue']['status'], 'unknown')
        self.assertEqual(result['summary']['revenue']['by_currency'], [])

    async def test_closed_contact_without_payment_is_indeterminate_not_lost(self):
        await self.change(CRMLead, self.lead, status_atendimento='fechado')
        result = await self.call()
        self.assertEqual(result['summary']['converted'], 0)
        self.assertIsNone(result['summary']['lost'])
        self.assertEqual(result['summary']['closed_without_conversion'], 1)
        self.assertEqual(result['summary']['indeterminate'], 1)

    async def test_explicit_lost_crm_state_is_exclusive_from_inactive(self):
        funnel = await self.add(CRMFunil, empresa_id=self.tenant, nome='Synthetic CRM loss')
        stage = await self.add(CRMEtapa, funil_id=funnel, nome='Explicitly lost', tipo='lost', ordem=1)
        await self.change(CRMLead, self.lead, etapa_id=stage)
        result = await self.call()
        self.assertEqual(result['summary']['lost'], 1)
        self.assertEqual(result['summary']['inactive'], 0)
        self.assertEqual(result['summary']['indeterminate'], 0)
        self.assertEqual((await self.call(drilldown, status='lost'))['total'], 1)

    async def test_no_data_and_unknown_activity_are_explicit(self):
        empty = await self.call(tenant=self.other_tenant)
        self.assertEqual(empty['summary']['total_leads'], 0)
        self.assertEqual(empty['transitions'], [])
        await self.change(ChannelConversation, self.conversation, last_message_at=None, last_inbound_at=None, last_outbound_at=None)
        result = await self.call()
        self.assertEqual(result['summary']['indeterminate'], 1)
        row = (await self.call(drilldown))['items'][0]
        self.assertIsNone(row['last_activity_at'])

    async def test_future_message_status_reaction_do_not_create_activity(self):
        await self.change(ChannelConversation, self.conversation, last_message_at=None)
        await self.message(criado_em=self.horizon.replace(tzinfo=None)+timedelta(days=1))
        await self.message(tipo_mensagem='status', criado_em=self.horizon.replace(tzinfo=None))
        await self.message(metadata_json={'event_kind': 'reaction'}, criado_em=self.horizon.replace(tzinfo=None))
        result = await self.call()
        self.assertEqual(result['summary']['indeterminate'], 1)
        self.assertEqual(result['summary']['inbound_leads'], 0)

    async def test_followup_canonical_legacy_unique_contact_temporal_same_conversation(self):
        await self.followup()
        config = await self.add(ConfigFollowUp, empresa_id=self.tenant, nome='Legacy rule', tempo_gatilho_minutos=5, objetivo_prompt='Synthetic')
        await self.add(LeadFollowUpLog, lead_id=self.lead, config_followup_id=config,
            data_envio=self.naive+timedelta(days=1), status_envio='enviado')
        await self.message(criado_em=self.naive+timedelta(days=2))
        result = await self.call()
        self.assertEqual(result['followup']['sent_leads'], 1)
        self.assertEqual(result['followup']['returned_leads'], 1)
        self.assertEqual(result['followup']['denominator'], 1)
        self.assertIn('temporal', result['followup']['relation'])

    async def test_followup_return_outside_window_or_wrong_conversation_not_counted(self):
        await self.followup()
        other_conversation = await self.conversation_for(criado_em=self.naive+timedelta(hours=1))
        await self.message(criado_em=self.naive+timedelta(days=2), channel_conversation_id=other_conversation)
        await self.message(criado_em=self.naive+timedelta(days=9))
        result = await self.call()
        self.assertEqual(result['followup']['sent_leads'], 1)
        self.assertEqual(result['followup']['returned_leads'], 0)

    async def test_followup_with_parent_conversation_of_other_contact_is_excluded(self):
        wrong_conversation = await self.conversation_for(lead_id=self.same_tenant_other_lead)
        await self.followup(conversation_id=wrong_conversation)
        await self.message(criado_em=self.naive+timedelta(days=2))
        result = await self.call()
        self.assertEqual(result['followup']['sent_leads'], 0)
        self.assertEqual(result['followup']['returned_leads'], 0)

    async def test_legacy_tenant_uses_multiple_configured_stage_tags_without_spec(self):
        await self.message(empresa_id=self.other_tenant, lead_id=self.other_lead,
            channel_conversation_id=None, conexao_id=None)
        legacy_tags = []
        for order in (1, 2):
            tag = await self.add(TagCRM, empresa_id=self.other_tenant, nome=f'Legacy stage {order}',
                ativa_no_funil=True, ordem=order, instrucao_ia=None)
            legacy_tags.append(tag)
            await self.add(CRMTagEvent, empresa_id=self.other_tenant, lead_id=self.other_lead,
                tag_id=tag, action='applied', source='manual', occurred_at=self.naive+timedelta(hours=order),
                idempotency_key=str(uuid4()))
        result = await self.call(tenant=self.other_tenant)
        keys = {'tag:'+str(tag) for tag in legacy_tags}
        self.assertEqual(set(self.stages(result)), keys)
        self.assertEqual([stage['reached_leads'] for stage in result['stages']], [1, 1])
        self.assertEqual(len(result['transitions']), 1)

    async def test_legacy_csv_stage_aliases_distinct_stable_without_raw_identifiers(self):
        await self.message(empresa_id=self.other_tenant, lead_id=self.other_lead,
            channel_conversation_id=None, conexao_id=None)
        tags = []
        for order in (1, 2):
            tag = await self.add(TagCRM, empresa_id=self.other_tenant, nome=f'PRIVATE stage label {order}',
                ativa_no_funil=True, ordem=order)
            tags.append(tag)
            await self.add(CRMTagEvent, empresa_id=self.other_tenant, lead_id=self.other_lead,
                tag_id=tag, action='applied', source='manual', occurred_at=self.naive+timedelta(hours=order),
                idempotency_key=str(uuid4()))
        first = await self.call(export_csv, tenant=self.other_tenant)
        second = await self.call(export_csv, tenant=self.other_tenant)
        self.assertEqual(first, second)
        rows = list(csv.DictReader(io.StringIO(first)))
        aliases = [row['metric'] for row in rows if row['section']=='stage']
        self.assertEqual(len(aliases), 2)
        self.assertEqual(len(set(aliases)), 2)
        self.assertTrue(all(alias.startswith('legacy_stage_') for alias in aliases))
        self.assertTrue(all(row['metric'].split('>')[0] in aliases and row['metric'].split('>')[1] in aliases
            for row in rows if row['section']=='transition'))
        for raw in (str(self.other_lead), str(self.other_tenant), *(str(tag) for tag in tags), 'PRIVATE', 'Synthetic'):
            self.assertNotIn(raw, first)

    async def test_exact_partial_unknown_attribution_and_any_identified_ad_filter(self):
        first = await self.touch()
        await self.resolution(first)
        second = await self.touch(occurred_at=self.t+timedelta(days=1))
        await self.resolution(second, ad_id='102', adset_id='202', campaign_id='302')
        result = await self.call()
        self.assertEqual(result['attribution']['exact_leads'], 1)
        for kwargs in ({'campaign_id':'301'}, {'ad_id':'102'}, {'origin':'whatsapp'}):
            aggregate = await self.call(**kwargs)
            detail = await self.call(drilldown, **kwargs)
            self.assertEqual(aggregate['summary']['total_leads'], 1)
            self.assertEqual(detail['total'], 1)
        await self.resolution(await self.touch(lead_id=self.same_tenant_other_lead, conversation_id=None),
            resolution_status='partial', ad_id='999', campaign_id='888', adset_id=None)
        await self.message(lead_id=self.same_tenant_other_lead, channel_conversation_id=None)
        partial = await self.call()
        self.assertEqual(partial['attribution']['partial_leads'], 1)
        self.assertEqual((await self.call(campaign_id='888'))['summary']['total_leads'], 0)

    async def test_campaign_intervention_not_acquisition_and_unknown_ads_not_inferred(self):
        await self.touch(event_kind='campaign_sent', touch_source='campaign_sender', touch_type='intervention')
        await self.message(metadata_json={'campaign_id':'301','ad_id':'101'})
        result = await self.call()
        self.assertEqual(result['attribution']['unknown_leads'], 1)
        self.assertEqual(result['attribution']['exact_leads'], 0)
        self.assertEqual((await self.call(campaign_id='301'))['summary']['total_leads'], 0)

    async def test_paginated_drill_stage_status_and_bottleneck_share_predicates(self):
        for _ in range(5):
            lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic paginated')
            await self.message(lead_id=lead, channel_conversation_id=None)
            await self.tag_event('lead_established', lead_id=lead, conversation_id=None)
        await self.tag_event('lead_established')
        expected = await self.call(drilldown, stage='lead_established', status='inactive', limit=100)
        paged = []
        for offset in (0,2,4):
            page = await self.call(drilldown, stage='lead_established', status='inactive', limit=2, offset=offset)
            self.assertEqual(page['total'], 6)
            paged.extend(row['lead_id'] for row in page['items'])
        self.assertEqual(paged, [row['lead_id'] for row in expected['items']])
        self.assertEqual(len(set(paged)), 6)
        aggregate = await self.call(stage='lead_established', status='inactive')
        self.assertEqual(aggregate['summary']['total_leads'], expected['total'])
        for bottleneck in aggregate['bottlenecks']:
            detail = await self.call(drilldown, stage='lead_established', status='inactive', bottleneck=bottleneck['key'])
            self.assertEqual(detail['total'], bottleneck['count'])

    async def test_export_aggregate_same_cohort_without_contact_ids_or_pii(self):
        await self.tag_event('lead_established')
        rendered = await self.call(export_csv, stage='lead_established')
        self.assertIsInstance(rendered, str)
        self.assertTrue(list(csv.reader(io.StringIO(rendered))))
        for secret in (str(self.lead), str(self.conversation), 'PRIVATE', 'Synthetic A', 'telefone', 'nome_contato'):
            self.assertNotIn(secret, rendered)
        self.assertIn('lead_established', rendered)

    async def test_cross_tenant_parent_stage_and_facts_do_not_leak(self):
        other_funnel = await self.add(CRMFunil, empresa_id=self.other_tenant, nome='PRIVATE other funnel')
        other_stage = await self.add(CRMEtapa, funil_id=other_funnel, nome='PRIVATE other stage', tipo='sale', ordem=1)
        await self.change(CRMLead, self.lead, etapa_id=other_stage)
        await self.message(empresa_id=self.other_tenant, lead_id=self.other_lead,
            channel_conversation_id=None, conexao_id=None)
        await self.payment(company_id=self.other_tenant, contact_id=self.other_lead, student_id=self.other_lead)
        result = await self.call()
        self.assertEqual(result['summary']['total_leads'], 1)
        self.assertEqual(result['summary']['converted'], 0)
        self.assertNotIn('PRIVATE', json.dumps(result))
        detail = await self.call(drilldown)
        self.assertEqual([row['lead_id'] for row in detail['items']], [str(self.lead)])

    async def test_unknown_stage_and_bottleneck_or_invalid_filters_rejected(self):
        for function, kwargs in ((dashboard, {'stage':'missing'}), (drilldown, {'bottleneck':'inactive:missing'})):
            with self.assertRaises(LookupError):
                await self.call(function, **kwargs)
        for kwargs in ({'start':'2026-02-01','end':'2026-01-01'}, {'inactivity_hours':0},
                       {'return_window_days':91}, {'campaign_id':'not-numeric'}, {'status':'invented'}):
            with self.assertRaises(ValueError):
                await self.call(**kwargs)

    async def test_bounded_query_count_independent_of_number_of_contacts(self):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(('SELECT', 'WITH')):
                statements.append(statement)
        event.listen(self.engine.sync_engine, 'before_cursor_execute', capture)
        try:
            await self.call()
            first_count = len(statements)
            for _ in range(12):
                lead = await self.add(CRMLead, empresa_id=self.tenant, nome_contato='Synthetic query bound')
                await self.message(lead_id=lead, channel_conversation_id=None)
            statements.clear()
            result = await self.call()
            self.assertEqual(result['summary']['total_leads'], 13)
            self.assertEqual(len(statements), first_count)
            self.assertLessEqual(first_count, 16)
            self.assertTrue(any('WITH' in statement.upper() for statement in statements))
        finally:
            event.remove(self.engine.sync_engine, 'before_cursor_execute', capture)

    async def test_actual_set_based_aggregate_has_explain_plan_in_postgres(self):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('WITH'):
                statements.append((statement, parameters))
        event.listen(self.engine.sync_engine, 'before_cursor_execute', capture)
        try:
            await self.call()
        finally:
            event.remove(self.engine.sync_engine, 'before_cursor_execute', capture)
        self.assertEqual(len(statements), 1)
        statement, parameters = statements[0]
        async with self.engine.connect() as conn:
            await conn.execute(text('SET TRANSACTION READ ONLY'))
            plan = (await conn.exec_driver_sql('EXPLAIN (ANALYZE, FORMAT JSON) '+statement, parameters)).scalar_one()
            if isinstance(plan, str):
                plan = json.loads(plan)
            self.assertTrue(plan[0]['Plan'])
            self.assertGreaterEqual(plan[0]['Execution Time'], 0)
            self.assertLess(plan[0]['Execution Time'], 1000)
