import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt
from app.services import a07_runtime
from app.services.a07_flags import rollout

TENANT = uuid.uuid4()
LEAD = uuid.uuid4()
STAGE = uuid.uuid4()
MESSAGE = 'Quanto custa, tem garantia e quando começa?'
CONFIG = {'empresa_id': str(TENANT), 'has_ai': True, 'nome_agente':'Agente canônico',
          'ia_identidade':'Cordial e breve', 'ia_regras_negocio':'Serviço fictício custa R$ 90. Garantia fictícia de revisão por 2 dias. Início fictício sexta-feira.', 'mensagem_saudacao':'Saudação canônica.'}


class Session:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


def enable(monkeypatch, channel='whatsapp'):
    suffix=TENANT.hex.upper()
    monkeypatch.setenv('A07_MODE_'+suffix,'MODULAR')
    monkeypatch.setenv('A07_LIVE_AUTHORIZED_'+suffix,'true')
    monkeypatch.setenv('A07_AUTHORIZED_CHANNELS_'+suffix,channel)


def isolated_runtime(monkeypatch, *, reason=None, persisted=True):
    import db.database
    monkeypatch.setattr(db.database,'AsyncSessionLocal',Session)
    monkeypatch.setattr(a07_runtime,'read_primary_stage',AsyncMock(return_value={'value':str(STAGE),'status':'known','source':'crm_snapshot'}))
    monkeypatch.setattr(a07_runtime,'runtime_classification',AsyncMock(return_value=(None,'local_deterministic',reason)))
    saved=AsyncMock(return_value=persisted)
    monkeypatch.setattr(a07_runtime,'persist_trace',saved)
    return saved


def make_agent(llm=None):
    provider=llm or MockAgentLLM(responses=['No cenário fictício, custa R$ 90, a revisão é por 2 dias e começa sexta-feira. Quer confirmar?'])
    agent=AtendimentoAgent(llm=provider,tool_planner=MockToolPlanner(default_plan={'selected_tools':[],'tool_args':{},'expected_missing_fields':[],'intent_summary':'test'}))
    agent._load_tenant_config=AsyncMock(return_value=CONFIG)
    return agent,provider


@pytest.mark.asyncio
async def test_actual_agent_multi_intent_one_final_generation_same_persona_tools_and_guard(monkeypatch):
    enable(monkeypatch);saved=isolated_runtime(monkeypatch)
    agent,llm=make_agent();state=ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD))
    output=await agent.run(AtendimentoAgentInput(state=state,messages=[IncomingMessage(text=MESSAGE)]))
    assert len(llm.calls)==1 and output.guard_result.allowed and not output.should_dispatch
    assert output.response_text.count('R$ 90')==1
    prompt=llm.calls[0]['system_prompt']
    assert 'Agente canônico' in prompt and CONFIG['ia_regras_negocio'] in prompt
    assert all('Módulo '+key+'@1.0.0' in prompt for key in ('pricing_conditions','trust_guarantees','availability'))
    trace=output.metadata['a07_composition']
    assert trace['primary_stage']['value']==str(STAGE) and trace['mode']=='MODULAR' and trace['trace']['persisted']
    assert saved.await_count==2 and trace['status']=='completed'
    assert 'resposta_final' not in json.dumps(trace) and MESSAGE not in json.dumps(trace)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,persisted', [('provider_error',True),('provider_timeout',True),('budget_exceeded',True),('context_mismatch',True),(None,False)])
async def test_fallback_preserves_exact_legacy_prompt_without_second_generation(monkeypatch,reason,persisted):
    enable(monkeypatch);isolated_runtime(monkeypatch,reason=reason,persisted=persisted)
    agent,llm=make_agent();state=ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD))
    legacy_memory=agent._build_prompt_memory(state,IncomingMessage(text=MESSAGE),[])
    # Freeze time-derived memory to make byte equality deterministic.
    agent._build_prompt_memory=lambda *args: legacy_memory
    output=await agent.run(AtendimentoAgentInput(state=state,messages=[IncomingMessage(text=MESSAGE)]))
    assert len(llm.calls)==1 and llm.calls[0]['system_prompt']==build_atendimento_system_prompt(CONFIG,legacy_memory)
    assert output.metadata['a07_composition']['fallback_reason']==(reason or 'ledger_unavailable')
    assert output.guard_result.allowed


@pytest.mark.asyncio
async def test_legacy_and_unapproved_channels_do_not_query_stage_jev_or_trace(monkeypatch):
    spy=AsyncMock(side_effect=AssertionError('LEGACY must not call any modular service'))
    monkeypatch.setattr(a07_runtime,'prepare_runtime',spy)
    for channel in ('whatsapp','instagram','simulator'):
        agent,llm=make_agent();state=ConversationState(empresa_id=str(TENANT),canal=channel,metadata={'a07_composition':{'prompt':'client injection'}})
        output=await agent.run(AtendimentoAgentInput(state=state,messages=[IncomingMessage(text=MESSAGE)]))
        assert len(llm.calls)==1 and 'Composição comercial' not in llm.calls[0]['system_prompt']
        assert 'a07_composition' not in output.metadata
    enable(monkeypatch,'whatsapp')
    agent,llm=make_agent();state=ConversationState(empresa_id=str(TENANT),canal='instagram')
    await agent.run(AtendimentoAgentInput(state=state,messages=[IncomingMessage(text=MESSAGE)]))
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_first_turn_json_and_exact_greeting_remain_legacy(monkeypatch):
    enable(monkeypatch)
    spy=AsyncMock(side_effect=AssertionError('first turn must remain legacy'))
    monkeypatch.setattr(a07_runtime,'prepare_runtime',spy)
    agent,llm=make_agent(MockAgentLLM(responses=[json.dumps({'needs_extension':False,'extension':''})]))
    output=await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id=str(TENANT),first_agent_response=True),messages=[IncomingMessage(text=MESSAGE)]))
    assert output.first_turn_decision is not None and not output.first_turn_decision.needs_extension
    assert '<CONFIGURED_GREETING>' in llm.calls[0]['system_prompt'] and 'Composição comercial' not in llm.calls[0]['system_prompt']
    assert output.metadata['a07_composition']['fallback_reason']=='first_turn_legacy'
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_manual_no_planner_provider_or_modular_activity(monkeypatch):
    enable(monkeypatch);spy=AsyncMock(side_effect=AssertionError('manual'))
    monkeypatch.setattr(a07_runtime,'prepare_runtime',spy)
    agent,llm=make_agent()
    output=await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id=str(TENANT),status_atendimento='manual'),messages=[IncomingMessage(text=MESSAGE)]))
    assert not output.response_text and not output.decision.should_respond and not llm.calls
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_composition_deadline_fallback_before_generation(monkeypatch):
    enable(monkeypatch);isolated_runtime(monkeypatch)
    async def slow(*args): await asyncio.sleep(.1)
    monkeypatch.setattr(a07_runtime,'read_primary_stage',slow)
    monkeypatch.setattr(a07_runtime,'DEADLINE_SECONDS',.005)
    agent,llm=make_agent()
    output=await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id=str(TENANT)),messages=[IncomingMessage(text=MESSAGE)]))
    assert len(llm.calls)==1 and output.metadata['a07_composition']['fallback_reason']=='composition_timeout'
    assert 'Composição comercial' not in llm.calls[0]['system_prompt']


@pytest.mark.asyncio
async def test_generator_timeout_does_not_retry_or_send_and_guard_still_blocks(monkeypatch):
    enable(monkeypatch);isolated_runtime(monkeypatch)
    async def slow(**kw): await asyncio.sleep(.1);return 'late'
    llm=SimpleNamespace(generate=AsyncMock(side_effect=slow))
    agent,_=make_agent(llm)
    monkeypatch.setattr(a07_runtime,'DEADLINE_SECONDS',.005)
    output=await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id=str(TENANT)),messages=[IncomingMessage(text=MESSAGE)]))
    assert llm.generate.await_count==1 and not output.should_dispatch and not output.response_text
    assert output.metadata['a07_composition']['status']=='generation_timeout'
    agent,llm=make_agent(MockAgentLLM(responses=['[SISTEMA_SECRET]']))
    output=await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id=str(TENANT)),messages=[IncomingMessage(text=MESSAGE)]))
    assert not output.guard_result.allowed and output.metadata['a07_composition']['status']=='guard_blocked'


@pytest.mark.asyncio
async def test_stage_query_scopes_parent_and_funnel_without_mutation():
    queries=[]
    async def execute(query):queries.append(str(query));return SimpleNamespace(first=lambda:(STAGE,))
    db=SimpleNamespace(execute=execute)
    assert (await a07_runtime.read_primary_stage(db,TENANT,LEAD))['value']==str(STAGE)
    assert 'crm_leads.empresa_id' in queries[0] and 'crm_funis.empresa_id' in queries[0]
    assert 'UPDATE' not in queries[0] and 'INSERT' not in queries[0]
    assert (await a07_runtime.read_primary_stage(db,TENANT,None))['status']=='unknown'


@pytest.mark.asyncio
async def test_runtime_jev_missing_key_or_privacy_never_calls_provider(monkeypatch):
    config=SimpleNamespace(enabled=False,api_key=None)
    monkeypatch.setattr(a07_runtime.jev_shadow,'configuration',AsyncMock(return_value=config))
    analyze=AsyncMock(side_effect=AssertionError('no authorization'))
    monkeypatch.setattr(a07_runtime.jev_shadow,'analyze',analyze)
    state=ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD),mensagem_atual=MESSAGE)
    assert await a07_runtime.runtime_classification(object(),TENANT,state)==(None,'local_deterministic',None)
    analyze.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', ['context','stage_ignored','stale','foreign_lead','abstained'])
async def test_trusted_jev_current_context_and_config_validation(monkeypatch,mismatch):
    enable(monkeypatch)
    config=SimpleNamespace(version=1)
    monkeypatch.setattr(a07_runtime.jev_shadow,'configuration',AsyncMock(return_value=config))
    monkeypatch.setattr(a07_runtime.jev_shadow,'configured',lambda c:True)
    monkeypatch.setattr(a07_runtime.jev_shadow,'real_authorized',lambda t:True)
    before=[{'id':'m1','text':MESSAGE,'role':'customer'}]
    contexts=[None] if mismatch=='context' else [before, [] if mismatch=='stale' else before]
    monkeypatch.setattr(a07_runtime,'current_jev_context',AsyncMock(side_effect=contexts))
    run={'source':'real','status':'completed','schema_version':'a06.v1','lead_id':str(uuid.uuid4()) if mismatch=='foreign_lead' else str(LEAD),
         'classification':{'intents':[{'key':'price','confidence':.9}], 'evidence':[{'message_id':'m1'}], 'primary_stage':{'key':'decision'}, 'abstention':{'abstained':mismatch=='abstained'}}}
    provider=AsyncMock(return_value=run)
    monkeypatch.setattr(a07_runtime.jev_shadow,'analyze',provider)
    result=await a07_runtime.runtime_classification(object(),TENANT,ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD),mensagem_atual=MESSAGE))
    if mismatch=='context':
        assert result[2]=='context_mismatch';provider.assert_not_called()
    elif mismatch=='stale':assert result[2]=='configuration_changed'
    elif mismatch=='foreign_lead':assert result[2]=='context_mismatch'
    else:
        assert result[1]=='jev_observed' and result[0]['primary_stage']['key']=='decision'
        from app.services.a07_composer import compose
        composition=compose('Base',message=MESSAGE,primary_stage={'value':str(STAGE),'source':'crm_snapshot','status':'known'},classification=result[0],classification_source=result[1])
        assert composition['primary_stage']['value']==str(STAGE)
        assert bool(composition['fallback_reason'])==(mismatch=='abstained')


@pytest.mark.asyncio
async def test_permission_revoked_during_context_read_no_provider_egress(monkeypatch):
    enable(monkeypatch)
    config=SimpleNamespace(version=1)
    monkeypatch.setattr(a07_runtime.jev_shadow,'configuration',AsyncMock(return_value=config))
    monkeypatch.setattr(a07_runtime.jev_shadow,'configured',lambda c:True)
    monkeypatch.setattr(a07_runtime.jev_shadow,'real_authorized',lambda t:True)
    async def context(*args):
        monkeypatch.setenv('A07_LIVE_AUTHORIZED_'+TENANT.hex.upper(),'false')
        return [{'id':'m1','text':MESSAGE,'role':'customer'}]
    monkeypatch.setattr(a07_runtime,'current_jev_context',context)
    provider=AsyncMock(side_effect=AssertionError('revoked permission'))
    monkeypatch.setattr(a07_runtime.jev_shadow,'analyze',provider)
    result=await a07_runtime.runtime_classification(object(),TENANT,ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD),mensagem_atual=MESSAGE))
    assert result[2]=='configuration_changed'
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_current_context_channel_conversation_and_message_match_before_provider(monkeypatch):
    conversation=uuid.uuid4();state=ConversationState(empresa_id=str(TENANT),lead_id=str(LEAD),canal='instagram',mensagem_atual=MESSAGE,metadata={'conversation_id':str(conversation)})
    queries=[]
    async def scalar(query):queries.append(str(query));return conversation
    async def execute(query):queries.append(str(query));return SimpleNamespace(first=lambda:(conversation,))
    db=SimpleNamespace(scalar=scalar,execute=execute)
    messages=[{'id':'m1','text':MESSAGE,'role':'customer'}]
    monkeypatch.setattr(a07_runtime.jev_shadow,'read_context',AsyncMock(return_value=messages))
    assert await a07_runtime.current_jev_context(db,TENANT,state)==messages
    assert 'channel_conversations.empresa_id' in queries[0] and 'channel_conversations.channel' in queries[0]
    assert 'mensagens_historico.empresa_id' in queries[1] or 'mensagens_historico' in queries[1]
    state.mensagem_atual='DIFFERENT'
    assert await a07_runtime.current_jev_context(db,TENANT,state) is None
    state.metadata['conversation_id']='invalid'
    assert await a07_runtime.current_jev_context(db,TENANT,state) is None


def test_observed_mock_receipt_distinct_from_real_and_unknown_cost():
    trace={'metrics':{'estimated_input_tokens':300}}
    provider=SimpleNamespace(last_usage_observation={'input_tokens':120,'output_tokens':23,'cost_usd':99.0})
    a07_runtime.capture_generation_usage(trace,provider)
    assert trace['metrics']['input_tokens']==120 and trace['metrics']['output_tokens']==23
    assert trace['metrics']['cost_usd'] is None and trace['measurement_source']=='synthetic_provider_receipt'
    assert trace['metric_scope']=='final_generation_only'
    trace={'metrics':{}}
    a07_runtime.capture_generation_usage(trace,SimpleNamespace(last_usage_observation={'input_tokens':True,'output_tokens':None}))
    assert trace=={'metrics':{}}


@pytest.mark.asyncio
async def test_openai_usage_observation_opt_in_legacy_kwargs_unchanged(monkeypatch):
    import sys
    from app.agents.llm.openai_provider import OpenAIAgentLLM
    response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=123,completion_tokens=15),choices=[SimpleNamespace(message=SimpleNamespace(content='Resposta segura'),finish_reason='stop')])
    call=AsyncMock(return_value=response)
    class Client:
        def __init__(self, **kw):self.chat=SimpleNamespace(completions=SimpleNamespace(create=call))
    monkeypatch.setitem(sys.modules,'openai',SimpleNamespace(AsyncOpenAI=Client))
    provider=OpenAIAgentLLM(api_key='synthetic-key',model='gpt-4o-mini',tenant_id=str(TENANT))
    assert await provider.generate(system_prompt='Base',messages=[{'role':'user','content':'teste'}])=='Resposta segura'
    legacy_kwargs=dict(call.call_args.kwargs)
    assert provider.last_usage_observation is None
    provider.enable_usage_observation()
    assert await provider.generate(system_prompt='Base',messages=[{'role':'user','content':'teste'}])=='Resposta segura'
    assert call.call_args.kwargs==legacy_kwargs
    assert provider.last_usage_observation['input_tokens']==123 and provider.last_usage_observation['output_tokens']==15
    assert provider.last_usage_observation['cost_usd'] is None
