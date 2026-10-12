# UX02 — navegação cliente orientada à operação

Baseline UX01 `51a0b45ef1ab687b60af8b4d1df97af7d083b8c9`. A Issue #19 reorganiza encontro/contexto dos módulos; as URLs e funcionalidades permanecem intactas. O contrato verificável está em `docs/releases/UX02-tenant-navigation.md`.

## Inventário auditado

`frontend/src/App.jsx` declara 32 padrões Tenant, contando o índice `/painel` e o wildcard de campanhas. Parâmetros são validados com exemplos sintéticos concretos; o wildcard não é uma lista finita de URLs. O router e seus lazy loads permanecem sem alteração.

| Família / URL existente | Grupo de navegação | Acesso preservado |
| --- | --- | --- |
| `/painel` | Visão geral e resultados | Dashboard |
| `/painel/funil` | Visão geral e resultados | A05, filtros/coorte e drill |
| `/painel/simulador` | Automações e inteligência | Simulação existente |
| `/painel/campanhas/resultados`, `/custos`, `/conversoes` | Visão geral e resultados | Resultados/custos/conversões de campanhas |
| `/painel/chat`, `/inbox`, `/conversas` | Atendimento e clientes | Um módulo Inbox, três URLs existentes |
| `/painel/crm`, `/agenda`, `/transferencias`, `/tags` | Atendimento e clientes | CRM, agenda e atendimento humano |
| `/painel/trafego/meta` e `/trafego/meta/conversoes` | Aquisição e tráfego | Tráfego e operações de conversão A01 |
| `/painel/instagram` | Aquisição e tráfego | Instagram |
| `/painel/campanhas/publicos` | Automações e inteligência | Públicos de campanhas |
| `/painel/campanhas`, `/campanhas/nova`, `/campanhas/:id`, `/campanhas/*` | Automações e inteligência | Lista, nova/edição/detalhe por ação contextual; fallback wildcard existente |
| `/painel/followups`, `/agentes`, `/inteligencia` | Automações e inteligência | Follow-ups, agente e JEV A06 SHADOW |
| `/painel/conexoes`, `/rag`, `/unidades` | Configurações e integrações | Canais, conhecimento e conta |
| `/painel/campanhas/templates`, `/novo`, `/:id` | Automações e inteligência | Templates e ações contextuais |
| `/painel/templates-whatsapp` | Automações e inteligência | Redirect replace preexistente para templates |
| `/painel/integracoes`, `/conversoes` | Configurações e integrações | Avisos restritos de configuração gerenciada pelo Super Admin |

Os sufixos abreviados na tabela pertencem à família explicitada, não representam novas rotas. URLs públicas `/` e administrativas não são reorganizadas nesta fase. Arquivos legados de integrações/conversão que não estão montados pelo router continuam sem ser ativados; suas rotas atuais exibem os avisos existentes.

## Experiência e permissões

Os cinco grupos permitem recolher os itens. Estado ativo acompanha aliases, subrotas e browser back/forward; breadcrumbs descrevem grupo/módulo e subpágina. Preferências visuais são escopadas por tenant/papel e não contêm credenciais. Dados inválidos ou storage indisponível usam fallback seguro para a navegação.

Reutilizam-se tokens, superfícies, sidebar, topbar, branding e drawer da UX01. O Inbox conserva sua política de altura: header desktop do Tenant oculto, acesso móvel ao menu e composer dentro do viewport. Não se cria root React ou drawer adicional.

`meta_app_reviewer` mantém exclusivamente Conexões WhatsApp e Gestão de Tags, com o redirect preexistente para `/painel/conexoes` ao entrar nas demais URLs. A navegação não substitui autorização backend. Impersonation mantém tenant ativo, nome/logo, banner, identidade original e retorno ao Admin. Integração JEV Admin não entra no menu Tenant.

Funil → Jornada Inbox mantém `funnelContext` no state do router e retorno com os filtros. Os links de Inteligência → Funil/Inbox e de Dashboard → Funil continuam inalterados.

## Verificação e release

Os testes de Chromium usam APIs interceptadas e fixtures sintéticas, bloqueiam sockets/serviços externos e não executam mensagens, tags, conversões ou providers reais. Cobrem os 32 padrões em exemplos concretos, acesso por links e URL, reload/back/forward, quatro viewports, grupos/persistência, teclado, reviewer e impersonation. A01/A04/A05/A06, React#16 e as 14 baselines protegidas são gates da release.

Apenas frontend é implantado sobre a imagem UX01 aprovada, preservando nginx/Anamnese, backend A06, banco e infraestrutura. Rollback, commit, imagens e evidências finais ficam em `reports/ux02-tenant-navigation-20261012/`. A entrega é utilizável independentemente da UX03 e não altera JEV ou fluxos comerciais.

A comparação antes registra a falha legada de `/painel/agentes` (`Send` ausente). A candidata corrige somente o import do ícone, necessário para manter todas as rotas utilizáveis. Não altera a ação de suporte ou a lógica comercial.
