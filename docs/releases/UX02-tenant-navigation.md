# UX02 — contrato congelado de navegação do cliente

Baseline autorizado `51a0b45ef1ab687b60af8b4d1df97af7d083b8c9`, master e imagem UX01 ativa `agenteos-frontend:ux01-ferrioli-visual-20261012-02` confirmados. Issue #19 integral, AGENTS, skill safe-production-change, relatório UX01 e Design System Ferrioli lidos. Fonte isolada `/tmp/agentos-ux02-20261012/source`; árvore principal suja registrada e preservada. Autorização inclui publicação master, frontend-only deploy e update Issue #19 após gates.

## Objetivo e limites

Reorganizar somente a navegação Tenant para encontro rápido de todos os módulos e contexto por cabeçalho/breadcrumb, mantendo ferramentas, URLs, links contextuais, state do router, direitos, identidade e drawer UX01. Cinco grupos recolhíveis: Visão geral e resultados; Atendimento e clientes; Aquisição e tráfego; Automações e inteligência; Configurações e integrações. Persistência guarda somente preferências visuais por tenant/papel; storage inválido/indisponível não quebra navegação; grupo ativo fica identificável e acessível ao entrar diretamente/back-forward. Não criar segunda sidebar, drawer ou root React.

Zero backend/DB/migrations, callbacks/APIs comerciais, JEV, campanhas/WhatsApp/Meta, respostas/prompt/tags, login/RBAC novos, rotas/aliases alterados ou removidos. Super Admin, tokens/sharedcomponents/design-system/shell UX01 e páginas comerciais permanecem byte-identical, salvo a exceção de compatibilidade comprovada abaixo; alteração visual no TenantLayout e CSS novo da navegação. Inbox preserva header desktop oculto/topbar móvel e composer viewport. A01/A04/A05/A06 e React#16 protegidos.

## Exceção mínima de compatibilidade demonstrada

O navegador na imagem UX01 imutável comprovou `ReferenceError: Send is not defined` em `/painel/agentes`, que deixa o root vazio. Para cumprir o critério já congelado de todas as rotas acessíveis, o integrador acrescenta exclusivamente o import `Send` de lucide-react em pages/tenant/TenantAgentes.jsx. Nenhuma lógica, ação ou funcionalidade dessa página muda. A comparação antes registra essa falha preexistente e a candidata deve eliminá-la.

## Auditoria de rotas congelada

`App.jsx` permanece íntegro. Tenant inclui índice; funil; inteligencia; agentes; followups; crm; rag; agenda; simulador; inbox/chat/conversas (aliases sem redirect); conexoes; instagram; trafego/meta; trafego/meta/conversoes; integracoes (aviso restrito); transferencias; campanhas/* e suas rotas templates/novo/:id, nova/:id, publicos, resultados/custos/conversoes; templates-whatsapp (redirect replace existente); tags; unidades; conversoes (aviso restrito). Campanhas mantém seu roteamento interno. URLs arbitrárias no wildcard mantêm fallback legado; auditoria/testes distinguem famílias declaradas de exemplos concretos.

Todas as ferramentas antigas e caminhos antes sem link no sidebar terão entrada de módulo ou acesso contextual preservado. Ações novo/editar/detalhes continuam no módulo e por URL direta. Templates, públicos/resultados e avisos de integrações/configuração de conversão ficam encontráveis; não expor JEV Admin no Tenant. Links Funil → Jornada Inbox com `funnelContext` e retorno conservam state/filtros; links A06 → Funil/Inbox intactos.

Revisor Meta mantém exatamente `/painel/conexoes` e `/painel/tags` e redirect existente das demais URLs; somente duas entradas visíveis. Impersonation mantém branding/tenant ativo, banner, original_user/token e Sair e Voltar. Persistência de navegação não altera/guarda credenciais e não reexibe opções sem role. Resolução ativa trata aliases/trailing slash/subrotas de campanhas sem múltiplas entradas ativas.

## Ownership

Implementador navegação: frontend/src/layouts/TenantLayout.jsx; frontend/src/components/TenantNavigation.jsx (se necessário); frontend/src/utils/tenantNavigation.js e testes; frontend/src/styles/tenant-navigation.css. Adaptação de testes de contratos JSX existentes somente se necessário, preservando significado. Nenhuma edição App.jsx, auth.js, SuperAdminLayout, páginas comerciais, arquivos design system UX01.
Implementador navegador: frontend/scripts/smoke-ux02-navigation.cjs e fixtures próprias se necessário; ajuste mínimo de smoke-panel.cjs para abrir grupo antes de clicar (comportamento de suite preservado). Todos API requests interceptados, sockets/external bloqueados e fixtures sintéticas; nenhum envio real.
Integrador: import Send em frontend/src/pages/tenant/TenantAgentes.jsx conforme evidência de compatibilidade; docs/releases e documentação inventário/mapeamento UX02; backup/runtime/evidências/buildimagem/deploy/rollback/relatório/Issue19. Apenas integrador declara conclusão.
Auditor independente: somente leitura, diff final/evidências contra critérios congelados; P0/P1 relacionados bloqueiam deploy.

## Gates

Frontend integral baseline148+ testes significativos de resolver/contexto/persistência/menu, build Node22, guard single-root e lint focado sem erros/advertências novas. WhatsApp/auth backend regressão focada em mocks, sem alteração backend ou repetição integral injustificada; 18 falhas históricas A06 continuam explicitadas.
14 baselines workspace+imagem backend A06 `--candidate-image ... --require-candidate` antes/pré/pós deploy. Fonte/diff autorizados e scan sanitizado sem segredos; nginx+59Anamnese idênticos; backend/PG/Redis/Evolution/env/mounts/flags/start/IDs preservados.
Chromium com fixtures/APIs interceptadas: todas as rotas Tenant declaradas (exemplos concretos para parâmetros), URL direta + navegação por links/contexto, reload, back/forward, aliases, grupos/collapse/persistência, teclado/aria/foco, tenant normal, reviewer e impersonation. Desktop1440×1000, notebook1280×800, tablet820×1180, mobile390×844; sem overflow novo causado por shell; limitações preexistentes de páginas legadas distinguidas. Drawer UX01 com foco inicial, trapping de itens visíveis, Escape/retorno, reduced-motion; Inbox composer dentroviewport e políticaaltura intacta. Regressões A01/A04/A05/A06 e Funil → Jornada → retorno comstate. Screenshots antes/depois em rotas representativas; painel público pós-deploy usa mesmos testes/fonte/assets reais, APIs sintéticas interceptadas.
Zero novas regressões nos gates afetados. Não alegar auditoria global WCAG, provider real JEV ou suíte backend integral verde.

## Release

Imagem `panel_release` compilando grafo completo sobre UX01 aprovada. Fast-forward master após gates/revisão; trocar só `agent_os_frontend_issue16`, readiness+rollback automático e imagem/container anterior preservados. Não usar compose down nem recriar infra. Verificar HTML/assets públicos, matriz navegador e preservação após deploy; interromper/rollback se regressão de release. Relatório único `reports/ux02-tenant-navigation-20261012/RELATORIO.md`, evidências sanitizadas/screenshots/manifest, script rollback dry-run. Issue #19 atualizada somente com evidência final; UX02 completa sem UX03+.
