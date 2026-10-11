"""A05 window, unknown-state and aggregate-only export contracts."""
import csv
from copy import deepcopy
from types import SimpleNamespace
import io
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.services import commercial_funnel_dashboard as service

NOW = datetime(2026,10,11,12,tzinfo=timezone.utc)


@pytest.mark.parametrize('value,expected',[('false',False),('OFF',False),('0',False),('true',True),('1',True)])
def test_feature_flag_has_only_read_visibility_effect(monkeypatch,value,expected):
    monkeypatch.setenv('COMMERCIAL_FUNNEL_DASHBOARD_ENABLED',value)
    assert service.enabled() is expected


def test_cohort_window_defaults_ninety_days_with_fixed_horizon():
    p = service.filters(empresa_id=uuid4(),now=NOW)
    assert p['observed']==p['end']==NOW
    assert p['start']==NOW-timedelta(days=90)
    assert '[start,end)' in service._window(p)['entry_definition']


def test_today_exclusive_utc_midnight_does_not_make_future_observation_valid():
    p = service.filters(empresa_id=uuid4(),now=NOW,end='2026-10-12',observed_at='2026-10-11T11:00:00Z')
    assert p['end']==datetime(2026,10,12,tzinfo=timezone.utc)
    assert p['observed']<NOW
    with pytest.raises(ValueError):
        service.filters(empresa_id=uuid4(),now=NOW,observed_at='2026-10-11T13:00:00Z')


@pytest.mark.parametrize('kwargs',[
 {'start':'2026-10-12','end':'2026-10-11'}, {'end':'2026-10-13'},
 {'start':'2026-10-11T13:00:00Z','end':'2026-10-12'},
 {'start':'2025-01-01'}, {'observed_at':'not-date'}, {'inactivity_hours':0},
 {'inactivity_hours':8761},{'inactivity_hours':True},{'return_window_days':0},
 {'return_window_days':91},{'limit':0},{'limit':101},{'limit':1.5}, {'offset':-1},
 {'offset':100001},{'campaign_id':'fake-campaign'},{'ad_id':'1 OR 1=1'},
 {'ad_id':'1'*41},{'origin':'invented'}, {'status':'closed'}, {'stage':'<script>'},
 {'stage':'x'*101},{'bottleneck':'unsafe value'},
])
def test_invalid_filters_rejected_without_database_access(kwargs):
    with pytest.raises((ValueError,TypeError)):
        service.filters(empresa_id=uuid4(),now=NOW,**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('function',[service.dashboard,service.drilldown,service.export_csv])
async def test_invalid_service_filter_never_reaches_database(function):
    session = AsyncMock()
    with pytest.raises(ValueError):
        await function(session,empresa_id=uuid4(),campaign_id='secret-injection')
    session.execute.assert_not_awaited()


def test_missing_denominator_or_numerator_is_unknown_not_zero():
    assert service._rate(None,10)['value'] is None
    assert service._rate(0,0)['state']=='unknown'
    assert service._rate(0,10)==dict(numerator=0,denominator=10,value=0,state='observed')
    assert service._rate(2,10)['value']==.2


@pytest.mark.asyncio
async def test_csv_allowlist_excludes_private_labels_ids_and_unknown_revenue(monkeypatch):
    lead,conversation,tenant = str(uuid4()),str(uuid4()),str(uuid4())
    data = {'summary':dict(total_leads=1,conversations=1,inbound_leads=1,active=0,inactive=1,
        converted=0,lost=None,indeterminate=0,closed_without_conversion=0,
        revenue={'status':'unknown','by_currency':[]}),
        'stages':[dict(key='lead_established',label='PRIVATE_CONTACT_LABEL',reached_leads=1,state='observed',rate=service._rate(1,1))],
        'transitions':[], 'bottlenecks':[], 'followup':dict(returned_leads=0,numerator=0,denominator=0),
        'attribution':dict(exact_leads=0,partial_leads=0,unknown_leads=1,denominator=1),
        'items':[dict(lead_id=lead,conversation_id=conversation,display_name='PRIVATE_NAME')],
        'tenant':tenant,'credentials':'PRIVATE_TOKEN'}
    monkeypatch.setattr(service,'dashboard',AsyncMock(return_value=data))
    rendered = await service.export_csv(object(),empresa_id=uuid4())
    rows = list(csv.reader(io.StringIO(rendered)))
    assert next(row for row in rows if row[:2] == ['summary','lost'])[2:6] == ['', '', '1', 'unknown']
    assert next(row for row in rows if row[:2] == ['summary','converted'])[2:6] == ['0', '', '1', 'observed']
    assert all(value not in rendered for value in ('PRIVATE',lead,conversation,tenant))
    assert 'revenue,net,,,,unknown,' in rendered
    assert 'lead_established' in rendered


def test_legacy_stage_export_removes_configuration_identifiers():
    first,second = 'tag:'+str(uuid4()),'tag:'+str(uuid4())
    alias = service._export_key(first)
    assert alias.startswith('legacy_stage_')
    assert len(alias)==len('legacy_stage_')+16
    assert alias == service._export_key(first)
    assert alias != service._export_key(second)
    assert first.removeprefix('tag:') not in alias


@pytest.mark.parametrize('value',['=HYPERLINK("private")','+1','-1','@SUM(1)','\tformula','\rformula'])
def test_aggregate_csv_formula_guard_defends_even_future_allowlisted_strings(value):
    output = io.StringIO()
    service._AggregateCSVWriter(output).writerow([value])
    assert list(csv.reader(io.StringIO(output.getvalue())))[0][0] == "'"+value


@pytest.mark.asyncio
@pytest.mark.parametrize('instrumented,expected_count,expected_state',[(False,None,'unknown'),(True,0,'observed')])
async def test_dashboard_and_csv_distinguish_uninstrumented_stage_from_observed_zero(monkeypatch,instrumented,expected_count,expected_state):
    aggregate = {
        'summary':dict(total_leads=1,conversations=1,inbound_leads=0,active=0,inactive=1,converted=0,lost=0,indeterminate=0,closed_without_conversion=0),
        'financial':[],
        'stages':[dict(key='checkout_intent',label='Checkout',order=1,source='persisted_funnel_spec',
                       reached_leads=0,current_leads=0,instrumented_tags=int(instrumented),financial_stage=False)],
        'transitions':[],'bottlenecks':[],'series':[],'crm_snapshot':[],
        'followup':dict(sent_leads=0,returned_leads=0),
        'attribution':dict(exact_leads=0,partial_leads=0,unknown_leads=1,before_entry_leads=0,after_entry_leads=0,same_time_leads=0),
        'stage_history_leads':0,'entry_missing':0,'stage_facts_before_entry':0,
        'financial_ambiguous_identities':0,'unverified_purchase_leads':0,
        'options':dict(origins=[],campaigns=[],ads=[]),
    }
    monkeypatch.setattr(service,'_configuration',AsyncMock(return_value=[dict(key='checkout_intent',label='Checkout')]))
    def session():
        return SimpleNamespace(execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one=lambda:deepcopy(aggregate)),
            SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[])),
        ]))
    data = await service.dashboard(session(),empresa_id=uuid4())
    assert data['stages'][0]['reached_leads'] == expected_count
    assert data['stages'][0]['state'] == expected_state
    rendered = await service.export_csv(session(),empresa_id=uuid4())
    stage = next(row for row in csv.reader(io.StringIO(rendered)) if row[:2]==['stage','checkout_intent'])
    assert stage[2] == ('' if expected_count is None else '0')
    assert stage[5] == expected_state
