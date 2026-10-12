# UX05 — auditoria de dimensão e fronteiras de release

Auditoria somente leitura da fonte `/tmp/agentos-ux04-20261012/source`, limpa no commit `73084772ed5b0c03b4b2659f2208dfad617447ec`. Issue #22 lida integralmente com `gh`; AGENTS, protocolo produção, perfil AgentOS e documentos UX01–04 consultados. Nenhuma operação comercial executada.

## Dimensão e recomendação

17 páginas tenant elegíveis: 10.083 linhas JSX; RAGManager compartilhado adiciona273 linhas. A dimensão e diversidade de operações justificam quatro entregas autossuficientes, cada uma com candidato, navegador, auditoria e rollback próprios. Nenhuma precisa da implantação posterior para funcionar.

| Release | Arquivos completos | Linhas JSX |
| --- | --- | ---: |
| UX05-A Analytics/aquisição | `frontend/src/pages/tenant/TenantDashboard.jsx`, `frontend/src/pages/tenant/TenantDashboard.css`, `frontend/src/pages/tenant/FunnelDashboard.jsx`, `frontend/src/pages/tenant/FunnelDashboard.css`, `frontend/src/pages/tenant/TrafficMeta.jsx`, `frontend/src/pages/tenant/ConversionOperations.jsx`, `frontend/src/pages/tenant/Instagram.jsx` |3005|
| UX05-B Automações/campanhas | `frontend/src/pages/tenant/TenantFollowUps.jsx`, `frontend/src/pages/tenant/Campanhas.jsx` |3240|
| UX05-C Inteligência/ferramentas | `frontend/src/pages/tenant/TenantAgentes.jsx`, `frontend/src/pages/tenant/Rag.jsx`, `frontend/src/pages/tenant/Agenda.jsx`, `frontend/src/pages/tenant/Simulador.jsx`, `frontend/src/pages/tenant/JevDiagnostics.jsx`, `frontend/src/pages/tenant/JevDiagnostics.css`; apresentação tenant escopada de `frontend/src/components/RAGManager.jsx` |1608 com RAG|
| UX05-D Configuração/operação | `frontend/src/pages/tenant/Conexoes.jsx`, `frontend/src/pages/tenant/GestaoTags.jsx`, `frontend/src/pages/tenant/Transferencias.jsx`, `frontend/src/pages/tenant/Unidades.jsx`, `frontend/src/pages/tenant/AcessoRestritoOperacional.jsx` |2503|

Cada release pode adicionar CSS próprio e testes de navegador. Reutilizar `frontend/src/components/ui.jsx`, `frontend/src/styles/design-tokens.css` e componentes Ferrioli; evitar mudar o DS global para pintar páginas locais.

## Rotas e estados internos

A: `/painel`, `/painel/funil`, `/painel/trafego/meta`, `/painel/trafego/meta/conversoes`, `/painel/instagram`. Dashboard: canal, atividade, recentes e configuração IA; Funil: filtros, coortes, cobertura/unknown, drilldown e Jornada; Meta: autorizações, conta/página/WhatsApp/dataset, credenciais, conversões, drafts/criação, reporting/debug; Instagram: conexão, posts, automações, histórico.

B: `/painel/followups`; `/painel/campanhas`, `/painel/campanhas/nova`, `/painel/campanhas/:id`, `/painel/campanhas/templates`, `/painel/campanhas/templates/novo`, `/painel/campanhas/templates/:id`, `/painel/campanhas/publicos`, `/painel/campanhas/resultados`, `/painel/campanhas/resultados/custos`, `/painel/campanhas/resultados/conversoes`; redirect `/painel/templates-whatsapp`. Campanhas tem sete abas principais e seis abas de públicos: tags, imports, saved, snapshots, funnels, smart. Followups: lista, modal criação/edição, modos, âncoras, cancelamentos, template/fixed text/IA e mídia.

C: `/painel/agentes`, `/painel/rag`, `/painel/agenda`, `/painel/simulador`, `/painel/inteligencia`. RAG texto/URL/PDF e lista; Agenda horários/exceções/modal; Simulador conversa/poll/reset; JEV status/ledger/exemplos SHADOW.

D: `/painel/conexoes`, `/painel/tags`, `/painel/transferencias`, `/painel/unidades`, `/painel/integracoes`, `/painel/conversoes`. Conexões e Tags possuem ramo específico do reviewer Meta. Transferências inclui destinos/histórico/teste; Tags grupos/tags/configuração IA/modais; Unidades CRUD/modal; integrações/conversões são avisos restritos existentes.

`Integracoes.jsx`, `ConfiguracoesConversoes.jsx`, `Ferramentas.jsx`, `TemplatesWhatsApp.jsx` não são telas atualmente montadas por App.jsx. Não contam como tela elegível observável.

## Inconsistências comprovadas no código

Referências utilitárias hardcoded de cores: Campanhas354, Tags217, Followups201, TrafficMeta195, Agenda134, Conexoes120, Transferencias109, Instagram108. O Dashboard/Funil/JEV já é piloto UX01 e exige refinamento proporcional.

Clipping/scroll a conferir no navegador: tabelas Tags sob overflow-hidden; modal Followups max92vh/p6 com grade1fr150px; filtros Campanhas de largura mínima, SVG min640px com scroll local; painéis e estados Tráfego. Estes são candidatos de apresentação, não autorização para alterar lógica.

## Dependências e riscos de teste

- `MetaTemplateSelector.jsx` compartilhado Followups/Campanhas/legado templates; preservar values/callbacks e preferir escopo local.
- `RAGManager.jsx` também aparece no Admin UX03. Escopo CSS tenant em Rag evita alteração visual global no Admin.
- TrafficMeta pode efetuar POST marketing/session na inicialização de autorização e carregar Facebook SDK. Interceptar APIs, SDK, OAuth e todos externos; flagsLIVE continuam OFF.
- Campanhas contém mais de20 endpoints mutantes, upload/import/template submit/sync/audience/campaign prepare/actions; tudo sintético interceptado.
- Simulador POST /simulador e polling/reset não podem alcançar provider real.
- Transferencias botão testar, Tags reviewer apply-test-tag e Conexoes conectar/desconectar/select-number exigem interceptação absoluta.
- Dashboard consulta até20 históricos Inbox após buscar lista. Fixtures devem cobrir chamadas encadeadas.

## Invariantes

Preservar `frontend/src/App.jsx`, `frontend/src/main.jsx`, `frontend/src/services/api.js`, `frontend/src/utils/auth.js`, `frontend/src/utils/commercialFunnel.js`, `frontend/src/utils/conversionOperations.js`, `frontend/src/utils/commercialJourney.js`, `frontend/src/utils/jevDiagnostics.js`, EmbeddedSignup, layouts/navegação UX02, Admin UX03, Inbox/CRM/Jornada UX04. Hooks, requests, URLs, payloads, callbacks, permissões e tenant isolation não mudam. Manter NO_EVENT_OBSERVED/unknown/SHADOW/LIVEOFF, denominadores e links Funil→Jornada→Inbox.

Gates: mesmos dados antes/candidata/público, desktop/notebook/mobile, reviewer e impersonation, teclado/foco/scroll e estados; frontend161PASS baseline; root único;14baselines; revisão independente; rollbackUX04. Issue23 e18falhas históricas A06 fora do escopo. Touch Kanban não certificado e não abrangido.
