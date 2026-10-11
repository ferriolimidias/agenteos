# A05 — Dashboard comercial de funil e gargalos

Contrato congelado da Issue #6 e solicitação do proprietário. Baseline GitHub/runtime A04 `4e9a85e304922184f1431753081f93acdfbd907e`. Objetivo único: dashboard de coortes utilizável, com métricas comprovadas, filtros, gargalos e drill-down para a jornada A04. Fora do escopo: JEV, prompts, novos pipelines, mutações CRM/históricas, pagamentos, transportes, campanhas, LIVE e próximas fases.

## Dados e métricas

Unidade: CRMLead.id no tenant, não número de mensagens/conversas/cliques. Coorte pela primeira entrada elegível em toda a história até `observed_at`, só depois filtrada por `[start,end)` UTC: mínimo da primeira conversa válida ou inbound válido; base e cobertura explícitas. Excluir status/reaction e direção desconhecida de inbound. Múltiplas conversas não multiplicam o contato; não unir contatos por nome/telefone. Padrão últimos 90 dias, pois a base real contém entradas julho–setembro e sete dias ficaria vazia. Janela máxima 366 dias; horizonte explícito; períodos inválidos/futuros rejeitados.

Etapas configuradas: FunnelSpec persistido + tags com marcador/versão e stage_key explícitos; tags de etapas legadas quando não há Spec. Distribuição CRM é snapshot separado, sem inventar ligações tag↔CRM por nome nem transições antigas. Etapa atingida é COUNT DISTINCT lead com evidência; não construir barras decrescentes artificiais. Transições apenas entre evidências ordenadas e deduplicadas (tag e projeção do mesmo tag_event não são dois passos); saltos/retornos explicados, histórico parcial pela idempotência das tags existentes. Taxas mostram numerador, denominador e cobertura; etapa não instrumentada fica desconhecida, não zero artificial.

Purchase/Refund e receita exigem pagamento real/provenance verificada, ID financeiro, valor/moeda válidos e escopo tenant/lead. A tag purchase_confirmed existente sem prova financeira NÃO é compra nem receita. Expor esse sinal não confirmado na cobertura. LeoFT controlado/e2e/simulado não é receita real. Deduplicar provider/payment_id entre fontes; moedas separadas; receita sem prova permanece null/unknown. Valor editável CRM, fechado e etapa fechamento não provam compra/perda. Perda confirmada só com evidência explícita; ausência de instrumentação é declarada. Estados exclusivos ativos/inativos/convertidos/perdidos/indeterminados; encerrados sem conversão ficam explícitos e não viram perda por inferência.

Atividade por mensagens/conversas até horizonte, sem timestamps futuros/schedule. Limiar inatividade configurável 1..8760 horas (padrão 168). Gargalos: sem avanço posterior observado e inatividade, com estágio observável, contagem, denominador e duração temporal comprovada; nunca motivo psicológico de abandono. Datas do snapshot CRM não viram tempo parado na etapa. Retorno após follow-up requer sent_at/log enviado + inbound posterior na mesma conversa explícita quando disponível; janela 1..90 dias (padrão 7), leads únicos e relação temporal, não causalidade. Campanhas WhatsApp como intervenções, sem sobrescrever aquisição.

Aquisição Ads vem de vínculos persistidos e resolução/IDs verificados; exact/partial/unknown explícitos. Filtros origin/campaign/ad usam EXISTS sobre touches de aquisição identificados do lead até o horizonte, sem duplicar contatos. Cobertura exact conta lead com pelo menos um touch completamente resolvido; não exige touch anterior à entrada e NÃO alega causalidade do primeiro ingresso. Mostrar cobertura temporal antes/depois/mesmo instante da entrada; lead com múltiplos anúncios pode aparecer em filtros diferentes e suas somas não são aditivas. Campanhas WhatsApp não são touches de aquisição. Origem e filtros campaign/ad/adset somente com identificação comprovada. Ads Insights persistidos em seção separada, um único nível, contas/moedas/janelas/attribution_setting separados; cliques/conversas Meta não são contatos locais. Não inventar ROAS ou exposição de prompt. Sem fetch Graph na API.

Evidência real auditada somente leitura: coorte principal 01/07–12/10 exclusivo possui 1096 contatos, 186 com inbound, 96 CRM snapshot e 1000 sem etapa CRM; oito etapas Spec, quatro volumes observados 4/76/23/19. Sete tags Purchase sem prova financeira são sinais não confirmados; única linha financeira é E2E. Ads completos em algum touch:80/1096, sem extrapolar cobertura A04. Três leads com followupenviado e um retorno temporal. Em agosto:1045 contatos e volumes0/38/1/3. Conferência deve distinguir horizonte/filtros/fatos anteriores à primeira entrada registrada, sem alterar registros para coincidir.

## API e interface

Adicionar somente GET no router analytics existente, preservando endpoints legados/A01:

- `/api/empresas/{empresa_id}/analytics/commercial-funnel`: dashboard agregado.
- `/api/empresas/{empresa_id}/analytics/commercial-funnel/leads`: drill-down paginado.
- `/api/empresas/{empresa_id}/analytics/commercial-funnel/export`: CSV agregado allowlisted sem PII, IDs de contatos/conversas, textos ou payloads.

require_tenant_access/get_db; 200/401/403/404; reviewer Meta proibido. Feature flag `COMMERCIAL_FUNNEL_DASHBOARD_ENABLED`, padrão true, falso 404. Filtros comuns: start/end ISO UTC, observed_at, inactivity_hours, return_window_days, stage, origin, campaign_id, ad_id, status. Drill-down também bottleneck, limit1..100/default25, offset0..100000 com ordenação estável entry_at DESC/lead_id e horizonte reutilizado entre páginas. UUIDs/IDs de Ads/predicados permitidos validados; estágio/gargalo inexistente 404, filtros inválidos 422. Todos os filtros de agregado/drill/export compartilham a mesma coorte. SQL set-based, agregação PostgreSQL, sem N+1 ou consulta jornada por contato. Nenhuma migration demonstrada como necessária.

Resposta dashboard fixa:

```
window: {start,end,observed_at,inactivity_hours,return_window_days,entry_definition,unit}
summary: {total_leads,conversations,inbound_leads,active,inactive,converted,lost,indeterminate,closed_without_conversion,revenue:{status,by_currency}}
stages: [{key,label,order,source,reached_leads,current_leads,state,coverage,rate:{numerator,denominator,value,state}}]
transitions: [{from_stage,to_stage,lead_count,occurrences,rate:{numerator,denominator,value,state},kind}]
bottlenecks: [{key,stage_key,title,count,denominator,rate,duration_hours,coverage}]
series: [{period,leads,active,inactive,converted,indeterminate}]
crm_snapshot: [{key,label,current_leads}]
followup: {sent_leads,returned_leads,numerator,denominator,rate,window_days,relation}
attribution: {exact_leads,partial_leads,unknown_leads,denominator,coverage}
ads_comparison: {state,level,rows,limitations}
coverage: {entry,stage_history,financial,loss,prompt_exposure,limitations}
options: {stages,origins,campaigns,ads,statuses}
```

Valores faltantes são null com state/limitação, não zeros fabricados. Drill-down: `{items:[{lead_id,display_name,entry_at,last_activity_at,status,current_stage_key,current_stage_label,attribution,reached_stage_keys,journey_path}],total,limit,offset,window}`. Display_name apenas no drill autenticado e nunca export/screenshot público; telefone/texto/email não necessários. Cada item corresponde ao mesmo predicado do agregado/gargalo.

Nova página `/painel/funil` com navegação visível e link do dashboard existente. Gráfico por volumes reais, taxas, cartões de estados/gargalos, tabela de duração, série temporal, filtros, paginação, detalhes/cobertura/Ads separado e export. Mobile utilizável com sidebar compacta restrita à A05. Tenant/scope/AbortController evitam respostas antigas. Ausência de dados/erro/flag OFF não bloqueia dashboard/Inbox. Root React único preservado.

Link A04 `/painel/inbox?lead=<uuid>&journey=1`: selecionar apenas objeto retornado pelo Inbox tenant-scoped, incluindo contatos encerrados, abrir timeline. Sem fabricar contato nem telefone. Voltar ao funil preserva filtros via estado local/rota allowlisted, sem redirect externo.

## Gates e responsabilidades

Backend: serviço `commercial_funnel_dashboard.py`, rotas additive analytics, testes unit/API próprios. Frontend: página/helpers/testes/harness, App/TenantLayout/TenantDashboard, CSS restrito e deeplink Inbox. Testes PostgreSQL: worker separado, cenários retorno/salto/multiconversa/duplicidade/tags compra sem pagamento/financeiro positivo/missing Ads/sem dados/tenant/paginação/filtros/closed. Integrador: contrato, staging/infra isolada, imagens, secret scan, regressão e deploy/rollback/relatório. Auditor independente somente leitura; implementador não aprova entrega.

Gates: builds/imports/focused/front/PG/tenant/14baselines/secret scan/browser/root/paridade PASS; full regression somente fechamento, classificar as 20 falhas antigas por ID, zero regressões novas. EXPLAIN/latência set-based e números reais conferidos por queries independentes; browser com coorte existente, filtros, drill-down e link real A04; screenshots sanitizados. Nenhuma escrita no banco real. Não depender de evento natural Meta A01.

Publicar master fast-forward apenas após gates/aprovação independente/SHA remoto conferido. Imagens A05 versionadas incrementais sobre A04; preservar nginx/Anamnese. Deploy somente backend/frontend; Env/config/mounts/aliases iguais; rollback A04 disponível, readiness limitada e rollback automático se falha. PG/Redis/Evolution e 126 históricos/outbox preservados, LIVE OFF, sem envio externo manual. Validar API/browser/health/A01/A04/baselines/infra/hash pós-deploy antes conclusão.

Relatório final único `/root/agenteos/reports/a05-cohort-funnel-20261011/RELATORIO.md`, campos do usuário/protocolo, números/cobertura reais, limites, SHA/imagens/gates/rollback e capturas sanitizadas. Atualizar Issue #6 após gates. Estado final REAL_VALIDATED somente com evidência; suite full jamais declarada verde se mantém falhas.
