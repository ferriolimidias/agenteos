// Synthetic UX05 responses; no tokens, live accounts or real writes.
const routes={A:['/painel','/painel/funil','/painel/trafego/meta','/painel/trafego/meta/conversoes','/painel/instagram'],B:['/painel/followups','/painel/campanhas','/painel/campanhas/templates','/painel/campanhas/templates/novo','/painel/campanhas/templates/synthetic-template','/painel/campanhas/nova','/painel/campanhas/synthetic-campaign','/painel/campanhas/publicos','/painel/campanhas/resultados','/painel/campanhas/resultados/custos','/painel/campanhas/resultados/conversoes'],C:['/painel/agentes','/painel/rag','/painel/agenda','/painel/simulador','/painel/inteligencia'],D:['/painel/conexoes','/painel/tags','/painel/transferencias','/painel/unidades','/painel/integracoes','/painel/conversoes']};
const assert = require('node:assert/strict');
const path = require('node:path');
const T='2026-10-12T09:00:00Z';
const tags=[{id:'tag-1',nome:'Interesse',tipo:'etapa_funil',ordem:1,cor:'#235568'},{id:'tag-2',nome:'Qualificado',tipo:'etapa_funil',ordem:2,cor:'#87612f'}];
const connections=[{id:'conn-1',nome_instancia:'WhatsApp sintético',provider:'meta_whatsapp',status:'connected',campaign_send_mode:'test_only'}];
const templates=[{id:'synthetic-template',conexao_id:'conn-1',name:'convite_sintetico',language:'pt_BR',normalized_status:'approved',normalized_category:'utility',remote_template_id:'synthetic-remote',last_synced_at:T,components:[{component_type:'BODY',text:'Olá, {{1}}. Confira nossa agenda.',example:{body_text:[['Ana']]}},{component_type:'FOOTER',text:'Demonstração sintética'}]}];
const campaign={id:'synthetic-campaign',nome:'Campanha sintética',name:'Campanha sintética',status:'paused',created_at:T,results:{total:20,queued:2,sent:16,failed:1,blocked:1}};
const rules={include:{match:'any',rules:[{type:'tag',tag_id:'tag-1'}]},exclude:{match:'any',rules:[{type:'tag',tag_id:'tag-2'}]}};
const preview={selected:12,counts:{initial_included:20,excluded_by_rules:2,optout_removed:2,blocked_removed:1,invalid_phone_count:1,duplicates_removed:2,final_audience_count:12},members:{included:[{lead_id:'synthetic-lead-1',name:'Ana sintética',reason:'Interesse'}],excluded:[{lead_id:'synthetic-lead-2',name:'Bruno sintético',reason:'Opt-out'}],pagination:{included:{has_more:false},excluded:{has_more:false}}}};
function response(url,mode){
 const p=url.pathname.replace(/\/$/,''); const empty=mode==='empty';
 if(p.endsWith('/dashboard/stats'))return {total_mensagens:empty?0:32};
 if(p.endsWith('/ia-config'))return {status_openai:'ok',openai_configurada:false,telefone_notificacao:'SYNTHETIC_ADMIN'};
 if(p.endsWith('/followups'))return empty?[]:[{id:'followup-1',nome:'Retomada sintética',ativo:true,mode:'conversation_resume',delay_value:3,delay_unit:'hours',anchor_type:'last_agent_message',message_mode:'fixed_text',message_content:'Posso ajudar com outra informação?',content_type:'text',cancel_on_inbound:true,cancel_on_human:true,cancel_on_opt_out:true,cancel_on_event_end:true,cancel_tag_ids:['tag-2'],template_id:'',executions_scheduled:10,executions_sent:7,executions_failed:1,executions_cancelled:1,executions_pending:1,next_scheduled_at:'2026-10-12T15:00:00Z',last_sent_at:T,last_error:'Falha sintética',needs_review:true,timezone:'America/Sao_Paulo'}];
 if(p.endsWith('/crm/tags/oficiais'))return empty?[]:tags;
 if(p.endsWith('/meta/templates'))return empty?[]:templates;
 if(p.endsWith('/conexoes'))return empty?[]:connections;
 if(p.endsWith('/campaign-central/overview'))return empty?{}:{campaigns:{prepared:2,queued:1,paused:1},recipients:{queued:2,sent:16,failed:2},templates:{approved:1,pending:1},costs:{confirmed:null}};
 if(p.endsWith('/campaign-central/results'))return empty?{metrics:{},rates:{},timeline:{series:[]},funnel:[],costs:{state:'unavailable'},conversions:{state:'unavailable'}}:{scope:{label:'Todas as campanhas sintéticas'},metrics:{audience_total:20,meta_accepted:18,delivered:16,read:10,failed:2,replies:4},rates:{delivery_rate:16/18,read_rate:10/16,reply_rate:4/16},costs:{state:'unavailable',total:null},conversions:{state:'unavailable'},timeline:{granularity:'day',series:[{bucket:'2026-10-11T00:00:00Z',meta_accepted:8,delivered:7,read:4,failed:1},{bucket:'2026-10-12T00:00:00Z',meta_accepted:10,delivered:9,read:6,failed:1}]},funnel:[{key:'audience',label:'Público',value:20,previous_rate:null},{key:'accepted',label:'Aceitas',value:18,previous_rate:.9},{key:'delivered',label:'Entregues',value:16,previous_rate:16/18}]};
 if(p.endsWith('/campaign-central/campaigns'))return empty?[]:[campaign];
 if(p.includes('/campaign-central/audiences/')){
  if(empty)return [];
  if(p.endsWith('/tags'))return [{...tags[0],origem:'comportamento',total_contacts:20,whatsapp_ready:18,consent_known:12,consent_unknown:8,last_applied_at:T}];
  if(p.endsWith('/imports'))return [{id:'import-1',filename:'contatos_sinteticos.csv',tag_nome:'Interesse',inserted_count:12,updated_count:3,invalid_count:2}];
  if(p.endsWith('/saved'))return [{id:'audience-1',name:'Interesse sem qualificação',description:'Audiência sintética',rules_json:rules,preview_json:preview}];
  if(p.endsWith('/snapshots'))return [{campaign_id:campaign.id,created_at:T,inclusions:12,exclusions:8,audience_version:1}];
  if(p.endsWith('/funnels'))return [{id:'funnel-1',name:'Funil sintético',active:true,order_source:'manual',stages:[{id:'stage-1',name:'Interesse',position:1,active:true,rules},{id:'stage-2',name:'Qualificado',position:2,active:false,rules}]}];
  if(p.endsWith('/smart'))return [{id:'smart-1',name:'Interesse ativo',funnel_name:'Funil sintético',include_labels:['Interesse'],exclude_labels:['Qualificado']}];
 }
 if(p.includes('/traffic/meta/')){
  if(p.endsWith('/config'))return {config:{ad_account_id:'act_1234',page_id:'page-1',whatsapp_connection_id:'conn-1',dataset_id:'dataset-1'},official_whatsapp:{connected:true,connection_id:'conn-1',display_phone_number:'SYNTHETIC_A'},active_official_whatsapps:[{connection_id:'conn-1',name:'WhatsApp sintético'}],marketing_graph_version:'v26.0',meta_write_enabled:false,campaign_create_enabled:false,adset_create_enabled:false,creative_create_enabled:false,ad_create_enabled:false};
  if(p.endsWith('/credentials/status'))return {marketing_readiness_status:'READY_ADMIN_MANAGED_READ',marketing_authorization:{enabled:false},permissions:{ads_read:true,ads_management:false,business_management:true},asset_access:{ad_account:true,campaigns:true,adsets:true,ads:true},insights:{preflight:'pass'},last_sync_at:T};
  if(p.includes('/adaccounts/'))return {adaccount:{id:'act_1234',name:'Conta sintética',account_status:1,currency:'BRL',timezone_name:'America/Sao_Paulo'}};
  if(p.endsWith('/assets'))return {asset_graph_valid:true,selected_page:{id:'page-1',name:'Página sintética'},pages:[{id:'page-1',name:'Página sintética'}],whatsapps:[{connection_id:'conn-1',page_compatible:true,display_phone_number:'SYNTHETIC_A'}],dataset:{id:'dataset-1',name:'Dataset sintético'}};
  if(p.endsWith('/conversions'))return {whatsapp_connected:true,dataset_configured:true,conversion_ready:false,conversion_token_configured:false,conversion_engine:{mapping_count:0,five_event_funnel:[]}};
  if(p.endsWith('/optimization-eligibility'))return {state:'NO_EVENT_OBSERVED',purchase_optimization_ready:false,accepted_signal_counts:{}};
  if(p.endsWith('/history'))return {items:[]};
  if(p.endsWith('/campaigns'))return {campaigns:empty?[]:[{id:'ads-campaign-1',name:'Anúncio sintético',status:'PAUSED'}]};
  if(p.endsWith('/adsets'))return {adsets:[]};
  if(p.endsWith('/ads'))return {ads:[]};
  if(p.endsWith('/inspect'))return {campaign:{id:'ads-campaign-1',name:'Anúncio sintético',status:'PAUSED',effective_status:'PAUSED',objective:'OUTCOME_ENGAGEMENT'},adsets:[{id:'adset-1',name:'Conjunto sintético',status:'PAUSED',daily_budget:'2500',destination_type:'WHATSAPP',billing_event:'IMPRESSIONS'}],ads:[{id:'ad-1',name:'Criativo sintético',status:'PAUSED',creative:{id:'creative-1',name:'Imagem sintética'}}],creatives:[]};
  if(p.endsWith('/conversion-readiness'))return {ready:false,reason:'Sem sinal auditável',supported_goals:[]};
 }
 if(p.includes('/instagram/')){
  if(p.endsWith('/connections'))return {connections:empty?[]:[{connection_id:'ig-1',name:'Instagram sintético',instagram_username:'horizonte_demo',instagram_professional_account_id:'ig-account-1',status:'connected',configuration_state:'configured'}]};
  if(p.endsWith('/media'))return {media:empty?[]:[{id:'media-1',media_type:'IMAGE',caption:'Conheça nossa agenda',timestamp:T}],paging:{}};
  if(p.endsWith('/automations'))return {automations:empty?[]:[{id:'automation-1',name:'Informações sintéticas',active:true,keywords:['agenda','preço'],public_reply_enabled:true,fixed_reply:'Veja nossa agenda',private_reply_enabled:true,private_reply_config:{message_type:'button_template',text:'Confira os detalhes',buttons:[{type:'web_url',title:'Agenda',url:'https://example.invalid/agenda'}]}}]};
  if(p.endsWith('/history'))return {items:empty?[]:[{id:'execution-1',status:'error',created_at:T,comment_text:'Gostaria da agenda',public_reply:'Veja os horários',private_reply:'Detalhes sintéticos'}]};
 }
 if(p.endsWith('/rag'))return empty?[]:[{source_name:'Perguntas frequentes sintéticas',source_type:'text',total_chunks:3}];
 if(p.endsWith('/agenda'))return {config:{dias_funcionamento:{seg:{aberto:true,inicio:'08:00',fim:'18:00'},ter:{aberto:true,inicio:'08:00',fim:'18:00'}},excecoes:[]},agendamentos:empty?[]:[{id:'appointment-1',lead:{nome_contato:'Ana sintética'},data_hora_inicio:'2026-10-12T09:00:00Z',data_hora_fim:'2026-10-12T09:30:00Z',status:'confirmado'}]};
 if(p.endsWith('/agentes'))return empty?[]:[{id:'agent-1',nome:'Atendimento sintético',status:'ativo',ativo:true}];
 if(p.endsWith('/transferencias/destinos'))return empty?[]:[{id:'destination-1',nome_destino:'Equipe sintética',contatos_destino:['SYNTHETIC_ADMIN'],instrucoes_ativacao:'Pedido explícito de atendimento humano.'}];
 if(p.endsWith('/transferencias/historico'))return empty?[]:[{id:'transfer-1',nome_contato:'Ana sintética',nome_destino:'Equipe sintética',criado_em:T,status:'concluido'}];
 if(p.endsWith('/unidades'))return empty?[]:[{id:'unit-1',nome_unidade:'Unidade sintética',endereco_completo:'Endereço de demonstração',horario_funcionamento:'08h–18h',is_matriz:true}];
 if(p.endsWith('/crm/tags/grupos'))return empty?[]:[{id:'group-1',nome:'Jornada sintética',cor:'#235568'}];
 if(p.endsWith('/conexoes/meta/config'))return {configured:false,enabled:false};
 if(p.endsWith('/conexoes/meta/status'))return {status:'not_configured',connected:false};
 return undefined;
}
async function exercise(page,route,{fixtureMode,viewport,output}){
 const checks=[];
 if(fixtureMode!=='normal')return {mode:fixtureMode,checks};
 async function capture(name){await page.screenshot({path:path.join(output,`${viewport.name}-${name}.png`),fullPage:true});}
 if(route==='/painel/followups'){
  await page.getByRole('button',{name:'Novo follow-up',exact:true}).click();
  await page.getByText('Retomar conversa parada',{exact:true}).first().waitFor();
  const name=page.getByPlaceholder(/Retomada 3h|nome/i).first();if(await name.count())await name.fill('Rascunho sintético — cancelar');
  await page.getByText('Envio programado / convite',{exact:true}).first().click();
  await capture('followup-editor');
  const cancel=page.getByRole('button',{name:/Cancelar|Fechar/}).last();await cancel.click();checks.push('followup-create-mode-draft-cancel-no-write');
 }
 if(route==='/painel/campanhas/templates'){
  const button=page.getByRole('button',{name:/Ver detalhes|Detalhes|Visualizar/}).first();
  if(await button.count()){await button.click();await page.getByRole('dialog').waitFor();await capture('template-details');await page.getByRole('button',{name:'Fechar detalhes do template'}).click();checks.push('template-detail-read-only-close');}
 }
 if(route==='/painel/campanhas/publicos'){
  for(const label of ['Listas por tags','Listas importadas','Públicos salvos','Públicos sugeridos','Configuração de funil','Snapshots de campanhas']){
   const button=page.getByRole('button',{name:label,exact:true});if(await button.count()){await button.click();await capture(`audiences-${label.replaceAll(' ','-')}`);checks.push(`audience-tab:${label}`);}
  }
 }
 if(route==='/painel/trafego/meta'){
  const section=page.locator('section').filter({has:page.getByRole('heading',{name:'Campanhas existentes',exact:true})});
  await section.getByRole('button',{name:'Atualizar',exact:true}).click();
  const select=section.locator('select');await select.selectOption('ads-campaign-1');
  await page.getByText('Conjunto sintético',{exact:true}).first().waitFor();
  await capture('meta-campaign-inspection');
  await section.getByRole('button',{name:'AdSets',exact:true}).click();
  await section.getByRole('button',{name:'Ads',exact:true}).click();
  await page.getByRole('button',{name:'Nova campanha',exact:true}).click();
  await page.getByRole('button',{name:'Cancelar',exact:true}).last().waitFor();
  assert.equal(await page.getByRole('button',{name:'Criar campanha e anúncios',exact:true}).first().isEnabled(),false);
  await capture('meta-campaign-draft-live-off');await page.getByRole('button',{name:'Cancelar',exact:true}).last().click();
  checks.push('meta-inspect-get-local-draft-live-off-no-create');
 }
 if(route==='/painel/instagram'){
  const edit=page.getByRole('button',{name:'Editar',exact:true}).first();await edit.click();
  await page.getByRole('button',{name:'Cancelar',exact:true}).waitFor();
  await capture('instagram-editor');await page.getByRole('button',{name:'Cancelar',exact:true}).click();
  checks.push('instagram-editor-existing-private-buttons-cancel-no-write');
 }
 if(route==='/painel/rag'){
  for(const label of ['Link / URL','PDF','Texto']){await page.getByRole('button',{name:label,exact:true}).click();await capture('rag-'+label.replaceAll('/','-'));}checks.push('rag-three-local-tabs-no-submit');
 }
 if(route==='/painel/agenda'){
  await page.getByText('Ana sintética',{exact:true}).waitFor();
  assert.equal(/NaN|Invalid Date/i.test(await page.locator('#tenant-content').innerText()),false);
  checks.push('agenda-valid-appointment-name-start-end-rendered');
 }
 if(route==='/painel/simulador'){
  const input=page.locator('input[type="text"]');if(await input.count()){await input.fill('Rascunho sintético sem enviar');assert.equal(await input.inputValue(),'Rascunho sintético sem enviar');}checks.push('simulator-draft-no-send');
 }
 if(route==='/painel/transferencias'){
  const history=page.getByRole('button',{name:/Histórico/}).first();if(await history.count()){await history.click();await capture('transfer-history');}checks.push('transfer-history-read-only');
 }
 if(route==='/painel/agentes'){
  const area=page.locator('textarea').first();if(await area.count()){await area.fill('Solicitação local sintética sem envio');await capture('agent-local-request');}checks.push('agent-local-draft');
 }
 return {mode:'read-only-and-local-interactions',checks};
}
module.exports={routes,response,exercise};
