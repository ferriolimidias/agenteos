# A04 — Jornada comercial no Inbox

Contrato de execução autorizado pela solicitação do proprietário e pela Issue #5. Baseline `ee527ee2e767e0e3aa2c6fb0f047fb9fa8cdd67c`. Critérios abaixo congelados para implementação e revisão independente.

Objetivo: timeline comercial utilizável no Inbox, consultando fatos já persistidos, sem segunda fonte de verdade. Escopo: API GET por tenant/lead, componente integrado, filtros, detalhes seguros, cobertura, estados desconhecido/sem registro/em andamento, paginação estável e prova com jornada real preexistente. Fora do escopo: JEV, A05, migrações/backfill, alterações do CRM/pagamentos/campanhas, envios ou enforcement.

## Contrato API e UI

`GET /api/empresas/{empresa_id}/inbox/leads/{lead_id}/journey` com `category` opcional, `limit` entre 1 e 100 (padrão 30) e `cursor` opaco opcional. Autenticação e autorização `require_tenant_access`; 401 sem usuário, 403 tenant incorreto, 404 lead inexistente ou de outro tenant. Feature flag `INBOX_COMMERCIAL_JOURNEY_ENABLED`, padrão habilitado; falso retorna 404. Endpoint somente leitura via `get_db`.

Resposta: `checked_at`, `lead_id`, `items`, `limit`, `next_cursor`, `has_more`, `summary`, `coverage`. Categorias: `acquisition`, `conversation`, `message`, `crm`, `commercial`, `followup`, `campaign`, `checkout`, `payment`, `booking`. Cada item: `id` global estável, `occurred_at` ISO UTC, `category`, `type`, `title`, `status` (`observed`, `unknown`, `pending`, `not_occurred`), `conversation_id` quando comprovado, `evidence` (`source`, `record_id`, `relationship`), `details` com allowlist. Nenhum payload bruto, token, telefone, texto integral de mensagem, URL de pagamento/mídia ou credencial.

Ordenação cronológica, mais recentes primeiro, com desempate estável por ID. Cursor valida escopo tenant/lead/categoria, limita tamanho e preserva fronteira de paginação; consulta de eventos limitada no banco, sem carregar todo histórico. Timestamps ausentes não serão inventados: informar na cobertura. Mesma evidência não é duplicada por joins, mas fatos de fontes distintas permanecem distintos e identificados. Múltiplas conversas explicitamente ligadas ao lead entram na mesma jornada; não unir leads por telefone/nome nem atribuir campanha WhatsApp como aquisição.

`coverage` descreve fontes, contagens e limites, com atribuição própria distinguindo resolução completa/parcial/desconhecida/não aplicável. `summary` descreve estado atual CRM/tags/FunnelSpec sem inventar transições; atividade/inatividade baseada em timestamps, sem inferir abandono ou causa. Ausência de registro é `unknown` com indicação “não observado nesta fonte”; `not_occurred` exige evidência explícita de não ocorrência (por exemplo, envio cancelado/bloqueado); `pending` exige registro em andamento. Pagamentos aprovados/refund só são tratados como verificados quando a fonte traz essa evidência.

No Inbox, botão “Jornada comercial” abre painel associado ao lead selecionado, com filtros por categoria, detalhes, cobertura, timezone explícito, paginação e estados de loading/erro/sem evidência. Troca de tenant/lead/filtro cancela ou invalida respostas antigas; erro/flag desabilitada não impede conversa e envio normal existente. Nenhuma ação de escrita pela timeline.

## Fontes e integridade

Reutilizar Attribution Ledger/resolutions, conversas/mensagens, CRMTagEvent e Commercial Events; CRM/FunnelSpec como snapshots; followups programados/automáticos/executions/logs, CampaignRecipient/outbox/status, agendamentos, checkout e pagamentos existentes. Cada relação exige tenant e chaves persistidas explícitas. Tabelas sem empresa_id exigem join com parent tenant-scoped. Dados de origem/Ads só quando comprovados; vínculos prováveis permanecem prováveis. Fontes sem histórico informam limite da cobertura.

Sender canônico LIVE OFF; SHADOW sem enforcement; 126 históricos e outbox bloqueados preservados; A01 e React root único da #16 preservados. Não modificar workers/transportes ou variáveis de ambiente atuais.

## Responsabilidades e gates

Backend: serviço `app/services/commercial_journey.py`, adição GET no router Inbox, testes de serviço/HTTP/PostgreSQL. Frontend: componente e helpers/testes de jornada, integração mínima Inbox, teste de browser. Integrador: staging, contrato, candidate incremental, gates, secret scan, publicação/deploy/rollback e relatório. Auditor independente: revisão somente leitura do diff e evidências; implementadores não aprovam a própria entrega.

Gates: builds/imports; testes focados API/frontend; PostgreSQL isolado (consultas READ ONLY, isolamento, ordenação/paginação/sem duplicação); baselines protegidas workspace e candidato; regressão integral classificada contra as 20 falhas antigas; zero regressões novas; public secret scan; browser sem erros/root extra; API e browser exibem pelo menos uma jornada real preexistente, sem efeitos externos. Sem prova útil, bloquear fechamento.

Publicar apenas após aprovação independente e gates, verificando SHA remoto antes de push fast-forward. Deploy troca somente backend/frontend, preservando Postgres/Redis/Evolution e imagens/containers anteriores. Rollback definido antes da troca. Checks pós-deploy: health, API/auth/tenant, timeline real/browser, A01, baselines, infraestrutura e fingerprint dos 126 históricos. Nenhuma migration ou envio em produção.

Relatório final em `/root/agenteos/reports/a04-inbox-journey-20261011/RELATORIO.md` com campos solicitados, imagens antes/depois, SHA, testes, cobertura/limites, evidência real sanitizada, rollback e próximo passo. Atualizar Issue #5; nenhum dado privado em GitHub/relatório.
