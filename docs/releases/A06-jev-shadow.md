# A06 — contrato de execução congelado

Autorização: solicitação A06 de 12/10/2026, Issue #7 completa e roadmap oficial.
Baseline: 19d2837e63acb73c6e261ade3109cd4a2cb7e1ec; imagens A05 confirmadas.
Objetivo: diagnóstico conversacional multi-intenção utilizável em SHADOW, independente da A07.

## Escopo e ownership
Backend: novos módulos `app/services/jev_*`, `app/api/routers/jev.py`, testes `test_jev*`, fixtures PT-BR versionadas; somente registro de router em main.
Frontend: página `/painel/inteligencia`, utilitários e testes JEV, links na navegação, integração opcional de leitura A04/A05; nenhum fluxo de envio alterado.
Integrador: imagem overlay A05, testes/runtime/browser, docs/roadmap e relatório em reports/a06-jev-shadow-20261012; publicação fast-forward no master após auditor independente.
Auditor: somente leitura, decisão independente contra estes critérios.

## Contrato funcional/API
Prefixo autenticado `/api/empresas/{empresa_id}/jev`.
GET `/status`: provider/config/budget/ledger e cobertura com denominador de execuções explícito, sem confundir coorte total com avaliados.
GET `/examples`: exemplos sintéticos PT-BR e rótulos de referência de demonstração.
POST `/analyze`: `example_id` para demo controlada ou `lead_id` para diagnóstico explícito com contexto limitado de leitura; source fixture/real e status distinguíveis. Provider nunca ativado pela entrada HTTP. Sem provider: demo explícita para exemplos, not_configured/not_evaluated para reais.
GET `/runs`: ledger sanitizado com filtros de status/intenção e limites.
GET `/runs/{run_id}`: detalhe tenant-scoped, evidências limitadas.
Esquema versionado: intents simultâneas, objections, primary_stage independente, active_topics, signals/interesse/prontidão/humano/risco, evidências IDs/trechos limitados, abstention, hypothetical tag_suggestions com IDs existentes por tenant, nunca aplicação.
Configuração server-side por tenant, segredo fora da UI. Timeout e orçamento fail-closed, reserva atômica compartilhada, idempotência/dedupe por contexto+versões, concorrência limitada, sem fila automática; indisponibilidade Redis/provider explícita. Retenção e tamanho limitados do ledger. Sem histórico integral ou conteúdo de cliente em logs/relatórios. Ausência de custo/usage mantém unknown; limites conservadores documentados, não custo inventado.

## Provider auditado
Somente TypeSafe oficial: https://docs.typesafe.ai/api e /models; POST https://api.typesafe.ai/v1/systemone, Bearer, modelo fixado jev-1.13.0. Noul probabilidade, Choice distribuição/confidence, Score escala/distribuição/confidence. Input $0.042/Mtok documentado em 12/10/2026, output gratuito. Limites publicados sujeitos a mudança. Nenhuma garantia oficial de deduplicação de cobrança em retries pós-envio encontrada: não repetir resposta ambígua/timeout; dedupe local e retries só de rejeições explícitas 429/529 com reserva financeira para tentativas. Contrato mock não prova provider real. Medir precisão/recall e p50/p95/custo somente com amostra real suficiente; reportar resultados sintéticos separadamente.

## Fora de escopo e preservação
Não editar prompts/atendimento ativo, resposta do agente, FunnelSpec, tags, Commercial Events, follow-ups, pagamentos, campanhas/Ads, conversões Meta ou transportes WhatsApp. Canonical LIVE OFF; preservar A01/A04/A05 e React #16. Nenhum envio real de conversa externa sem flags/config/autorização correspondente. Sem criar/alterar infraestrutura, sem migrations salvo necessidade comprovada e contrato ajustado pelo integrador antes da execução.

## Gates congelados
Unit/backend e API 200/401/403/404/422; isolamento tenant/parents; falhas provider e Redis; orçamento/concorrência/idempotência/minimização; demo multi-intenção/abstenção/tag suggestions sem writes comerciais; frontend testes/build/guard single-root/browser desktop/mobile; regressões WhatsApp/auth/A01/A04/A05; 14 protected baselines workspace+candidata+pós-deploy. Suíte integral comparada aos 20 IDs de falha A05: NEW_REGRESSIONS=0. Scan segredo nos arquivos publicados. Auditor independente P0/P1=0 antes de publicar/deploy.

## Release e rollback
Overlay backend somente arquivos A06 sobre A05. Frontend panel_release recompilado completo sobre A05 preservando nginx/Anamnese byte a byte. Substituir somente dois containers de aplicativo, mesma config/env/mounts/rede e alias backend; preservar PG/Redis/Evolution e imagens A05. Rollback somente aplicativos às imagens/containers A05 preservados; nenhum rollback destrutivo de dados. Feature flag JEV_SHADOW_ENABLED default ON para diagnóstico local, provider default OFF. Sem migration prevista. Evidências sanitizadas e relatório final exigidos, com campos solicitados; sem pacote de dados privados. Atualizar Issue #7 com SHA/imagens/limites/evidências pós-deploy.

## Alteração autorizada pelo proprietário durante a execução

12/10/2026: incluir Configurações → Integrações → JEV restrita ao Super Admin. CRUD seguro de chave write-only cifrada, provider oficial/modelo/limite diário USD, ativação SHADOW, teste de conexão/status sem reiniciar. Conta ainda inexistente; NÃO CONFIGURADO verdadeiro. Adicionar tabela isolada `jev_tenant_config`, migration idempotente antes do deploy, sem dados reais ou credenciais nos relatórios. Chave de cifra server-side forte/domain-separated; nunca armazenar em JSON de credenciais exposto por endpoints antigos. RBAC 401/403/404/422 e testes PostgreSQL de migration/configuração/segredo/isolamento/concorrência. Endpoints admin `/api/admin/empresas/{empresa_id}/integrations/jev` GET/PUT e POST `/test`, resposta has_key sem segredo. Teste /v1/models gratuito apenas após cadastro; decisões sintéticas podem usar provider explicitamente habilitado pelo tenant, orçamento e botão de diagnóstico. Cadastro não autoriza egress de conversa real; permanece flag separada default OFF. Backend gateway lê configuração DB por requisição. Encriptação compatível com secret server-side já existente, sem valor default. Migração aditiva pode permanecer no rollback; preservar configuração cifrada e dados sem drop. Dono config backend a06_config; frontend a06_frontend; integrador autoriza este ajuste por solicitação explícita do proprietário.
