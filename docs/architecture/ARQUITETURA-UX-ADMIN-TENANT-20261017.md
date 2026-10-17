# Arquitetura geral e UX/UI AgentOS — Cliente centrado no Inbox + Super Admin operacional

**Status:** DIRETRIZ ARQUITETURAL DO PROPRIETÁRIO para implementação; não é código implantado. Complemento vinculante: [PLANO-SANEAMENTO-E-LEGADOS-20261017.md](PLANO-SANEAMENTO-E-LEGADOS-20261017.md).
**Data:** 2026-10-17.
**Baseline examinado:** `master@de2dd5961865864065db4f056387b443541d6a4a`. Issue de execução: #34; a Issue #33 continua responsável por credenciais Meta e conversões reais.
**Princípio central:** uma aplicação de atendimento primeiro, com recursos técnicos suficientes para operar mas fora do caminho de quem só precisa atender seus clientes.

## 1. Problema comprovado pelo código atual

1. `frontend/src/App.jsx` usa `<Route path="/painel" index element={<TenantDashboard/>} />`, enquanto `/painel/inbox`, `/painel/chat` e `/painel/conversas` renderizam o Inbox. Após login de cliente, `frontend/src/pages/Login.jsx` navega para `/painel`: a tela inicial é Dashboard, não Inbox.
2. `frontend/src/utils/tenantNavigation.js` possui **cinco grupos e 28 itens**, entre eles `Prompt Optimizer A11`, `Inteligência SHADOW`, `Conversões Meta`, `Integrações técnicas` e `Configurações de conversão`. O menu é gerado para `admin_empresa` e `super_admin`; o filtro de outros roles retira somente o Prompt Optimizer. Há rótulos orientados a implementação, não tarefas.
3. `tenantNavigation.js:readCollapsedGroups` mantém o primeiro grupo (Visão geral) expandido e os demais recolhidos. Inbox está no segundo grupo, ou seja, a função principal fica escondida por padrão.
4. `frontend/src/pages/tenant/PromptOptimizer.jsx` contém **ações mutáveis** (bootstrap, avaliações, aprovação de versões, experimentos), apesar de ser governança operacional de IA. Esconder menu sem ajustar auth no backend **não bloqueia acesso**.
5. `frontend/src/pages/tenant/JevDiagnostics.jsx` e `QualityDashboard.jsx` mostram detalhes SHADOW, IDs, métricas técnicas, prompt traces e experimentos: úteis para o operador técnico, não necessários ao usuário leigo.
6. `frontend/src/pages/super-admin/EmpresaDetalhes.jsx` reúne dez abas horizontais (`TABS`), incluindo placeholders de integrações/imagens; `SuperAdminLayout.jsx` mostra apenas Empresas/Agentes/Configurações/JEV. A administração global e por empresa ficou misturada.
7. `frontend/src/App.jsx` ainda expõe rotas de placeholder `/painel/integracoes` e `/painel/conversoes` que mostram restrição administrativa em vez de ação do cliente. Há múltiplas entradas para campanhas/resultados/template e termos similares para fluxos distintos.
8. `frontend/src/pages/tenant/TenantDashboard.jsx` mantém métricas úteis; **não deve ser apagada**, mas movida à seção Resultados.
9. `frontend/src/layouts/TenantLayout.jsx` contém solução de teclado/foco para menu responsivo e modo Inbox; **preservar essas proteções**, adaptando para navegação nova. `meta_app_reviewer` possui navegação e acesso restritos próprios.

## 2. Evidências e diretrizes externas

- [GOV.UK: Navigate a service](https://design-system.service.gov.uk/patterns/navigate-a-service/): simplificar a jornada antes de multiplicar navegação; links principais para seções relevantes, menu não é sitemap.
- [Microsoft Fluent 2: Nav](https://fluent2.microsoft.design/components/web/react/core/nav/usage/): navegação breve, legível, orientada a tarefas; recolhível em telas estreitas.
- [Microsoft Fluent 2: Layout](https://fluent2.microsoft.design/layout): proximidade, hierarquia e re-arquitetura da informação em telas menores.
- [NN/g: Supporting Power Users Isn't Enough](https://www.nngroup.com/articles/complex-apps-users/): projetar para aprendizes e operadores de domínio, não apenas usuários técnicos.
- [W3C WCAG 2.2 3.2.3](https://www.w3.org/WAI/WCAG22/Understanding/consistent-navigation): ordem consistente de navegação; [2.4.7](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible): foco visível.

**Consequência prática:** Complexidade é mantida como capacidade operacional, mas aparece progressivamente conforme a tarefa e permissão, não em 28 links.

## 3. Áreas e responsabilidades, sem duplicação de produto

### Painel do CLIENTE — trabalho cotidiano
- Objetivo: receber, qualificar, transferir e acompanhar conversas, com visão simples de negócio.
- Primeira rota após autenticação de cliente: `/painel/inbox`. `/painel` redireciona para `/painel/inbox` (sem loops; preservar query e rotas profundas); login de `super_admin` continua levando a `/admin`; `meta_app_reviewer` continua nas rotas autorizadas específicas.
- Menu principal estável (até 6 entradas):
  1. **Conversas** — Inbox, filtro de não lidas, IA ativa/pausada no atendimento se o papel permite, transferência humana; contexto do lead no Inbox.
  2. **Clientes** — CRM/funil, contatos e tags; evitar duplicar cadastros.
  3. **Agenda** — compromissos e agendamentos.
  4. **Campanhas** — subitens claros: **Anúncios Meta** (aquisição WhatsApp) e **Mensagens e follow-ups** (campanhas WhatsApp, follow-ups/templates e públicos). Exibir estado real de integração sem criar uma segunda interface de anúncios.
  5. **Resultados** — resumo (antigo Dashboard), qualidade percebida, funil, mensagens, gastos e conversões quando mensurados. Visões técnicas escondidas atrás de "Detalhes"/perfil operador, se permitidas.
  6. **Configurações** — dados básicos da conta e conexão WhatsApp que o cliente pode operar. Base de conhecimento somente quando edição pelo cliente for de fato suportada/autorizada.
- **Não mostrar ao cliente** no menu ou navegação lateral: Prompt Optimizer/A11, JEV SHADOW, scripts, versões de modelos, tokens Marketing/Instagram/CAPI, regras/prompt de IA inacessíveis, simuladores técnicos, auditoria de transporte, integrações técnicas, selectors de objetivos Meta, placeholders.
- **Agente de Atendimento:** se houver valor para cliente, permitir só um cartão de status/resumo no Inbox ou Configurações (ex. "Atendimento automático ativo"), sem botão de editar prompt. Solicitações de melhoria seguem canal de suporte legítimo, não `mailto:` placeholder.
- Fluxos não concluídos: usar mensagens leigas (“Peça à equipe Ferrioli para conectar sua conta de anúncios”), mas não badges falsos "pronto"; ações existentes permanecem navegáveis se liberadas.
- Preserve links diretos para telas já existentes quando o usuário tiver autorização; retire o excesso do menu, **não exclua uma rota operacional por estar fora da sidebar**.

### Super Admin — operação Ferrioli
**Nível plataforma** (sem empresa selecionada):
- **Empresas**: busca, filtros, saúde geral, status de canais; atalho "Abrir operação do cliente" com contexto audível de impersonação; não imprimir dados privados.
- **Operação e saúde**: fila de incidentes, canais e workers, status de integrações, falhas de envio, qualidade de dados, alerts legítimos.
- **Integrações e credenciais globais**: app Meta e parâmetros globais, JEV e provedores, políticas de conectores, datas/validade de segredos sem exibição; **nenhum formulário de token bruto voltado ao tenant**.
- **Segurança e auditoria**: usuários e acessos administrativos, trilhas de mudança de tokens/prompts, grants, histórico de ações.
- **Configurações da plataforma**: branding, parâmetros globais, defaults técnicos, guardrails.

**Nível empresa** (`/admin/empresas/:empresaId`, subnavegação vertical ou abas compactas agrupadas por domínio, sem dez abas horizontais competindo):
- **Visão geral**: status e alertas operacionais, atalhos.
- **Atendimento e IA**: prompt, modelo, regras do agente, JEV, avaliação/prompt optimizer e versões (governança exclusivamente admin).
- **CRM e regras**: funil, etapas, tags e mapeamentos canônicos, sem duplicar tela do CRM comercial.
- **Canais e credenciais**: WhatsApp provider/Embedded Signup; Instagram Login específico (token+IG ID); Facebook/Páginas; Marketing API e system-user token; Dataset CRM/CAPI. Cada credencial isolada por finalidade e vínculo a tenant/asset. Credenciais admin com criptografia, máscara, troca/revogação e validação real; OAuth como alternativa, não obrigatório.
- **Campanhas e Meta**: assets, readiness READ vs WRITE, diagnósticos A12, eventos entregues vs aceitos vs otimizados.
- **Pagamentos**: provedor do tenant e status, não misturar com token de publicidade.
- **Usuários e permissões**: contas do tenant, papéis, mínimo privilégio.
- **Diagnósticos e auditoria**: logs sanitizados, testes sintéticos, execução JEV, métricas operacionais avançadas e integridade por tenant.
- Evitar tela "Agentes por empresa" competindo com a aba Agente, preferir página de lista/atalho para a seção do tenant.

## 4. Arquitetura de módulos / fontes únicas

| Domínio | Fonte canônica | Não duplicar |
|---|---|---|
| Conversas/Inbox | API Inbox e dados de conversa existentes | outro chat/histórico separado |
| Lead/CRM | CRM leads, etapas, tags canônicas e Commercial Events | novas tabelas de tag paralelas |
| Atendimento inteligente | agente atual + JEV como motor decisório autorizado | segunda edição de prompt no tenant |
| WhatsApp Oficial | conexão provider Meta/Embedded Signup | usar WABA token como Ads |
| Evolution | provider operacional independente, preservado enquanto houver consumidores | apagar por etiqueta `legacy` |
| Instagram automação | `graph.instagram.com`, token de IG Login e IG User ID no código atual; se houver fluxo Facebook Login, seu contrato exige token de Página | substituir um pelo token Marketing |
| Ads/Facebook/Insights | Marketing/Graph com token apropriado, escopos e assets tenant-scoped, admin Ferrioli | campos legados redundantes sem migração |
| Dataset CRM/CAPI | conexão/credential por Dataset + outbox/eventos canônicos e event_id estável | dois disparadores LIVE, confundindo WABA Dataset com CRM Dataset |
| Diagnósticos | serviços existentes agregados por intenção, não telas "A11/A12/SHADOW" para cliente | listas técnicas no menu leigo |
| Dados LeoFT | `/srv/leoft-media`, PostgreSQL, catálogo, prescrições e anamnese | qualquer reorganização física em conjunto com UI AgentOS |

Mover páginas de local **não significa criar nova API ou novo armazenamento**. Reutilizar componentes e serviços existentes; remover legados somente depois da matriz de consumidores da Issue #34.

## 5. Contrato de acesso (UI + API)

| Capacidade | Usuário cliente | Admin da empresa | Super Admin |
|---|---|---|---|
| Inbox/CRM/agenda | conforme acesso comercial | sim | apenas em contexto autorizado/auditável |
| Desligar/retomar IA em conversa | somente se permitido por papel | se permitido | em suporte autorizado |
| Editar prompt/modelo/A11/JEV | não | **não** | sim, com controle de versão, revisão e auditoria |
| Configurar token Marketing, IG, CAPI | não | não | sim, criptografado |
| Conectar WhatsApp via Embedded Signup | conforme política de onboarding | sim | suporte autorizado |
| Criar campanha (sempre PAUSED inicialmente) | conforme permissão comercial | sim | suporte autorizado |
| Ativar campanha paga | confirmação explícita e permissão apropriada | confirmação explícita | confirmação explícita |
| Ver detalhes de transporte/logs | não | no máximo status simples | sim |

**Segurança:** a ocultação no menu NÃO é a regra de acesso. A APIs mutáveis de Prompt Optimizer, JEV/admin, modelo e tokens devem exigir `require_super_admin` ou capability específica efetivamente autorizada no backend, mais tenant scoping e auditoria. Resolver conflitos com papéis existentes antes de fechar acesso (não quebrar revisão Meta, suporte, impersonação). Verificar todas as rotas de `PromptOptimizer.jsx` por chamadas `POST`/`PUT`/`DELETE`.

## 6. Contrato de navegação e preservação

- O canonical de Inbox será `/painel/inbox`. `/painel/chat` e `/painel/conversas` continuam aliases/redirects seguros; respeitar query/deep links `lead_id`, `conversation_id`, `funnelContext` e history state. NÃO fazer reload total que apague histórico WebSocket.
- A nova Dashboard estará em `/painel/resultados` ou `/painel/resultados/resumo`; conservar `/painel` como entrada de Inbox. Links antigos de relatórios e campanhas devem continuar a funcionar (ou encaminhar ao subitem novo sem perder filtro).
- A lista/rota usada pelo App Reviewer segue independente da navegação normal.
- Menu fixo, labels curtos, ajuda contextual só onde necessário. Evitar códigos internos nas páginas voltadas ao cliente.
- Erro autenticado deve identificar ação (“Conta de anúncios não conectada, peça à Ferrioli”) em vez de erro técnico. Loading/empty/permission states têm apresentação distinta.
- Mobile Inbox: lista de conversas -> conversa -> painel de contato como navegação com voltar; não sobrepor sidebar ao teclado; histórico virtualizado/resiliente; ações de transferir, pausar IA, responder e pesquisar alcançáveis com toque.
- Desktop Inbox: lista à esquerda, conversa central, painel contextual à direita opcional; priorizar área de leitura e digitação, preservando comportamento do componente existente.
- Acessibilidade: WCAG 2.2 AA nos fluxos principais; skip links, foco visível, diálogos acessíveis, navegação teclado, indicadores de não lidas com texto e não só cor, contraste, tamanhos de toque adequados.
- Conservar design system existente (`components/ui`, `TenantNavigation`, `SuperAdminLayout`, estilos existentes), migrar incrementalmente; sem refazer tema inteiro nem gerar SPA paralelo.

## 7. Migração incremental antes da Issue #34 destrutiva

**Fase UX-0: contratos e testes de regressão**
- Congelar lista de rotas e autorização vigente; colher estado da VPS/roles; definir testes de redirecionamento Inbox, deep links, impersonação, fluxo App Reviewer, telas Meta/CRM, Anamnese LeoFT e dimensões mobile. Revisar design como mudança arquitetural **antes de apagar legado**.

**Fase UX-1: quick wins, menor risco**
- `/painel` -> Inbox; login cliente -> Inbox; Dashboard movida para Resultados sem perder acesso; menu reconstruído e Inbox fixado visível por padrão; rótulos em linguagem comercial; componentes mantidos. Não mexer no backend sem os contratos de autorização.

**Fase UX-2: permissões e fronteiras técnicas**
- Retirar Prompt Optimizer/JEV diagnóstico/configurações técnicas do menu cliente **e restringir as APIs mutáveis**; colocar ferramentas técnicas na seção admin da empresa sem copiar código. Verificar se endpoints externos apontam para rotas legadas e preservar compatibilidade. Se usuário tentar URL proibida, responder com 403 claro e sem vazar dados.

**Fase UX-3: refatoração do Super Admin**
- Reorganizar Empresas e EmpresaDetalhes; menu global simples e subnavegação por área; centralizar tokens e integrações na aba Canais e Credenciais por tenant, usando a implementação canônica da Issue #33; diagnóstico real de READ/WRITE e estados de bloqueio; sem duplicar conexões.
  
**Fase UX-4: saneamento e remoções (Issue #34)**
- Só após testes de rotas/permissões e de funcionalidades, remover páginas e scripts comprovadamente sem consumidores. Migrations, bancos, evidence/dedup e infraestrutura LeoFT intocados. Remoções por lote com prova e rollback.

## 8. Critérios de aceite verificáveis
1. Cliente `admin_empresa` após login entra imediatamente em `/painel/inbox`; após refresh, navegar entre conversas não apaga histórico, não causa flicker nem reconecta sem necessidade.
2. O menu cliente apresenta o Inbox imediatamente, máximo seis entradas principais, sem `Prompt Optimizer`, `SHADOW`, `A11`, placeholders de tokens técnicos e quaisquer rótulos internos.
3. Dashboard existe e funciona por Resultados; antigas rotas/deep links não quebram.
4. Usuário comum não pode editar prompts/modelos/experimentos via URL/API direta; Super Admin consegue fazê-lo numa área clara e auditável.
5. Super Admin consegue navegar por empresa e localizar tokens/canais, IA, CRM, campanhas, pagamentos, usuários e diagnósticos sem navegar entre dez abas horizontais.
6. Estado Meta lido e estado de escrita comprovada aparecem separados; cadastro de token de sistema Ferrioli no Super Admin será testado end-to-end, nunca apresentado como concluído só por UI.
7. Verificação desktop 1440x900/768x1024 e mobile 390x844/430x932 com teclado e contraste; sem overflow e com leitura do Inbox confortável; smoke de App Reviewer.
8. Builds, 14 baselines protegidas, regressões Inbox (WS/histórico), A11/A12, JEV/tags CRM, WhatsApp/Evolution, Meta, Anamnese e LeoFT; deploy restrito, sem rebuild de Postgres/Redis/Evolution/LeoFT.
9. Nenhum envio Meta LIVE, ativação automática de anúncio ou replay histórico durante testes de UX.
10. Relatório do Codex separa `PLAN_APPROVED`, `IMPLEMENTED`, `AUTHORIZATION_ENFORCED`, `TESTED`, `DEPLOYED`, `OPERATOR_VERIFIED`. Não chamar de concluído até passar a etapa correspondente.

**Ordem executiva:** aprovar/usar esta arquitetura como contrato de UX; concluir UX-0/UX-1/UX-2 e UX-3 em candidatos revisados; só então fazer a limpeza/aposentadoria de legados da Issue #34.