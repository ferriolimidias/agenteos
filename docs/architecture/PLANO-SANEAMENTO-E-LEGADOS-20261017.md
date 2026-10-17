# AgentOS — plano executável de saneamento e desativação de legados

**Situação:** CONTRATO DE PLANEJAMENTO, não execução. **Data:** 2026-10-17.
**Arquitetura de destino:** [ARQUITETURA-UX-ADMIN-TENANT-20261017.md](ARQUITETURA-UX-ADMIN-TENANT-20261017.md).
**Controle:** [Issue #34](https://github.com/ferriolimidias/agenteos/issues/34) (saneamento e UX) | [Issue #33](https://github.com/ferriolimidias/agenteos/issues/33) (Meta, credenciais, JEV e conversões).
**Base de evidência:** inspeção de `master@de2dd5961865864065db4f056387b443541d6a4a`, com **976 arquivos versionados** à época. Estado real da VPS/consumidores externos NÃO foi auditado nesta etapa.

> **Regra de ouro:** "Legado", "SHADOW", nome velho ou código repetido **não prova que está morto**. Só excluir após demonstrar ausência de consumidores reais, dados persistidos, integrações externas e necessidade de rollback. A limpeza deve simplificar o produto, e não gerar uma segunda implementação.

## 0. Dependência obrigatória: UX e permissões primeiro

Antes de executar remoções: adotar contratos da arquitetura de destino, estabelecer mapa de rotas atual/futura, perfis `super_admin`, `admin_empresa`, outros clientes, `meta_app_reviewer`, risco de deep links e baselines. **A sequência é UX-0→UX-1→UX-2→UX-3→LEGACY-1/2/3/4**. Pode ser entregue em PRs pequenos com revisão e rollback; não bloquear UX-1 por remotas dependências de API Meta.

## 1. Arquitetura lógica a preservar (fontes únicas)

```text
AgentOS — Frontend
  /admin (Ferrioli)
    Empresas → por empresa: Atendimento/IA | CRM/regras | Canais/credenciais
                              Campanhas/Meta | Pagamentos | Usuários | Diagnósticos
    Saúde/Operação | Integrações globais | Segurança/auditoria | Sistema
  /painel (cliente) → redirect /painel/inbox
    Conversas | Clientes | Agenda | Campanhas | Resultados | Conta
    Técnicas/A11/SHADOW/tokens não aparecem no menu de cliente

Backend — responsabilidades (reusar serviços existentes)
  /api/empresas/{tenant}/inbox/*       → conversa, histórico, ação de atendente
  CRM existente                       → contatos, funil, tags oficiais, eventos
  AtendimentoAgent + JEV              → respostas e decisões, admin regula prompts
  Canais WhatsApp                     → Meta Oficial/Embedded Signup e Evolution (ambos)
  Instagram automações                → conta IG Login atual; Page token só no
                                        contrato de integração FB Login compatível
  Marketing/Graph Ads + Facebook      → token System User/Marketing + assets+RBAC
  CRM Dataset/Conversions API         → credencial por event source e outbox canônica
  Auditoria/diagnósticos              → Super Admin, sanitisados e tenant-scoped
  LeoFT e Anamnese                    → domínio ativo protegido (sem mudanças destrutivas)
```

**Não criar** módulo CRUD duplicado, segunda fonte de tags, segundo Inbox, provedor alternativo Meta independente nem cópia dos componentes de agente, CRM ou anúncios. Mantêm-se módulos/rotas atuais até contrato e consumidores serem validados.

## 2. Inventário dirigido: candidatos e dependências

| Área/candidato a consolidar | Evidência concreta no código | Investigar antes de remover | Tratamento esperado |
|---|---|---|---|
| Token Meta legado em `Empresa.meta_access_token`/Pixel | `app/api/routers/empresas.py` PUT e `app/services/ads_integration_service.py` `enviar_payload_meta_capi` ainda utilizam; token não é Marketing | produção CAPI, UI, versões, consumidores externos, encoding/segurança | migrar com verificação de equivalência por Dataset e manter leitura legada temporária; só aposentar após cutover |
| Token Marketing admin/tenant/legado | `app/services/meta/marketing_credentials.py`: variáveis globais, `Conexao` Marketing e lookup compatível em `meta_official` | tipos de credencial, grants, tokens existentes, clientes por tenant, permissão real WRITE | Super Admin único por empresa e resolvedor central; transição segura; NÃO usar token WABA para Ads |
| CAPI Business Messaging legado | `app/services/ads_integration_service.py` com transporte Graph; `app/services/meta/business_messaging_conversions.py` resolve token e WABA/Dataset | quais eventos foram enviados, `event_id`, `events_received`, destino CRM Dataset vs WABA | preservar until sender canônico LIVE testado/permitido, depois cortar duplicidade com dedup |
| SHADOW, reconciliação, outbox | `canonical_conversion_shadow.py`, `conversion_outbox.py`, `legacy_delivery_evidence.py`, `legacy_delivery_reconciliation.py` | callbacks, jobs, tabelas `CanonicalCommercialEvent`, `MetaConversionEvent`, `CanonicalConversionOutbox`, evidências e identidades | manter trilhas até garantir migração sem dupla entrega; não "habilitar LIVE" trocando constante |
| Evolution legado | `channels/providers/evolution_legacy.py` e `channels/resolver.py` ainda roteiam o provider; Evolution VPS rodando | tenants Evolution ativos, jobs/cron, envios e webhooks | **PRESERVAR** enquanto operacional; nome não autoriza exclusão |
| Instagram login/Graph Facebook | `app/services/instagram_simple.py` usa `graph.instagram.com`, token específico Instagram Login; publicações de Ads dependem do contrato FB Graph | tipo de token por operação, conta/IG ID, webhooks, clientes e automações de DM/comentários | separar responsabilidades, sem substituir ou copiar token; confirmar necessidade de Page Access Token no modelo FB Login |
| `PromptOptimizer.jsx` no tenant | `frontend/src/App.jsx` permite rota direta, `tenantNavigation.js` expõe para `admin_empresa`; painel faz POST/PUT de versões/experimentos | todos os endpoints `prompt_optimizer.py` + role/tenant scope e meta reviewer | retirar da navegação cliente, proteger backend e transferir governança ao Super Admin, reusando componentes; não apagar histórico |
| `JevDiagnostics.jsx`/SHADOW no tenant | rota e link disponíveis no menu para cliente | autorização JEV e diagnósticos reais/sintéticos | mover para diagnóstico Super Admin; mostrar ao cliente apenas estado do atendimento quando relevante |
| Menus/rotas redundantes | `App.jsx`: /painel/chat/inbox/conversas e rotas campanhas, placeholders `integracoes`/`conversoes` | deep links, filtros e histórico em browser/contas reais | manter alias/redirecionamento compatível; remover links enganosos e placeholders após substituto operacional |
| `TenantDashboard.jsx` | componente ativo e rota index hoje | dependências de API/relatórios | **PRESERVAR**, mover rota para Resultados |
| Migrations e scripts root avulsos | `app/api/main.py` usa `ensure_empresas_prompt_columns`, runner usa lista de SQL e checksums; scripts como `add_columns.py`, `alter_db.py`, `fix_db_columns.py` coexistem | referências no deploy/cron/RUNBOOKS; aplicação histórica; checksum de produção | migrations aplicadas **NÃO deletar/reescrever**; eliminar scripts avulsos apenas com prova de não uso |
| `docker/Dockerfile.*` experimentais, relatórios | há Dockerfiles de shadow/operacional e histórico em docs | pipelines, scripts, recuperação/rollback, CI local | arquivar/apagar somente órfãos; conservar contratos e linhas de investigação úteis |
| LeoFT/Anamnese | `apps/anamnese-leonardo`, APIs e 77 migrations globais coexistem | catálogo, dados e containers separados com volume e build compartilhado | **não limpar por proximidade de diretórios**; nenhuma exclusão de dados/volumes |

### Classificação OBRIGATÓRIA por símbolo/caminho

Cada candidato recebe um dos estados:
- `KEEP_ACTIVE`: tem consumidor ou requisito atual;
- `KEEP_COMPAT`: depende de cliente externo, deep link, histórico ou rollback;
- `REFACTOR`: duplicado mas ainda tem uso, migrar consumers primeiro;
- `QUARANTINE`: suspeito de abandono, mas sem evidência suficiente;
- `REMOVE_CONFIRMED`: ausência demonstrada de consumidores e substituto testado.

Colunas do relatório e do PR: `path:symbol` | `classification` | `static_imports` | `runtime_callers` | `external_routes_webhooks` | `db_or_secret_dependency` | `replacement` | `test_evidence` | `rollback` | `commit`. Nunca inferir "sem uso" de um resultado de grep isolado.

## 3. Fases executáveis, com saída testável

### UX-0 — travar contratos, sem exclusão
- Extrair rotas React/FastAPI e importadores + consumidores de workers/cron/webhooks, papel efetivo e permissões; capturar smoke pré-mudança (cliente, Super Admin, App Reviewer e mobile).
- Fixar critérios de acesso backend: prompt/modelo/experimento, tokens técnicos, pipeline comercial e dados cross-tenant.
- Mapear problemas observados de Inbox WebSocket/histórico e URLs profundas para não regredir.
- Saída: matriz de navegação atual→futura, perfis e baselines.

### UX-1 — Inbox-first e navegação cliente
- `/painel` redireciona a `/painel/inbox` e login cliente também; não redirecionar Super Admin ou App Reviewer erroneamente.
- Disponibilizar resumo atual como `/painel/resultados`. Menu com **até seis entradas principais** e subitens contextuais, termos de negócio, sem códigos internos Axx/SHADOW.
- Inbox primeiro/visível; preservar `/painel/chat` e `/painel/conversas` e parâmetros/estado de links.
- Testar desktop 1440×900, tablet 768×1024 e mobile 390×844 e 430×932; teclado, foco, scroll, sidebar.

### UX-2 — fronteiras de acesso, governança e prompts
- Tirar Prompt Optimizer, prompts, JEV SHADOW, integrações técnicas/credenciais do menu cliente; interface isolada pelo Super Admin.
- Defender **POST/PUT/DELETE** e operações READ sensíveis no backend (não basta esconder link). `admin_empresa` **não edita prompts, modelos ou experimentos**, conforme decisão do proprietário.
- Preservar ações legítimas do Inbox/CRM e rotas app-reviewer, com 403 para acesso não autorizado e auditabilidade.

### UX-3 — Super Admin contextual
- Reorganizar dashboard admin global/tenant: visão geral, IA, CRM, canais/tokens, campanhas Meta, usuários, pagamentos, diagnósticos.
- Administração de tokens na área de tenant, reutilizando `Conexao` + criptografia/validação do GitHub #33, sem novo storage; permissões `ads_read` e `ads_management` como fatos diferentes de `WRITE_READY`.
- Status real e verificável, nunca “pronto” só por HTTP de leitura. Preservar provider WhatsApp Meta/Evolution e Instagram atual.

### LEGACY-1 — remoções órfãs provadas, baixo risco
- Identificar 5–15 símbolos/arquivos órfãos **após análise de consumidores runtime**; remover em PR isolado com testes.
- Priorizar scripts descartados, utilitários não chamados, placeholders e páginas substituídas (após redirects).
- Entregar lista concreta dos itens excluídos, motivação e comparativo de endpoints/rotas.

### LEGACY-2 — consolidar conectores e credenciais
- Mover chamadas para resolvedores canônicos já usados; desligar paths redundantes só depois de verificar persistência e perfil de acesso de cada tenant.
- Migração expand→migrate→contract com compatibilidade, validação de tokens/ativos e rollback, sem copiar segredos a logs.

### LEGACY-3 — depreciação de envios e sinais
- Completar sender canônico e idempotência em #33. Depois, cutover específico por tenant e caminho; confirmar evidência Meta real e ausência de duas entregas com mesmo evento.
- **Nunca replay automático** de eventos antigos, não confundir HTTP aceito com otimização CTWA.

### LEGACY-4 — consolidar documentação e nomenclatura
- Manter `README.md`, `AGENTS.md`, rotas/contratos atualizados; arquivos históricos úteis em documentação marcada como histórica.
- Buscar ausência de referências quebradas e testar bootstrap em DB descartável; migrações de produção intocadas.

## 4. Gates e rollback obrigatórios para cada lote

- `./scripts/validate_protected_baselines.sh --candidate-image <backend-image> --require-candidate` quando alterar backend, e baselines de origem correspondentes; se `PROTECTED_BASELINE_FAILED`, bloquear.
- Verificar testes do domínio alterado, build frontend e Anamnese, autorizações Super Admin/tenant/App Reviewer, Inbox mensagens/WebSocket, filtros CRM/tags, Meta/WhatsApp/Instagram, LeoFT catálogos e treinos.
- Sem ações LIVE Meta não autorizadas, disparos WhatsApp, campanhas ativas, conversões ou replay.
- Sem `docker compose down`; não recriar Postgres/Redis/Evolution/LeoFT; deploy de backend/frontend por imagens fixas e `--no-deps --no-build --pull never` com rollback. Conferir storage; preservar `/srv/leoft-media` (~3 GB), catálogo e prescrições.
- Check `STARTUP_DATABASE_MODE=skip` em produção; schema changes só quando indispensáveis, migrações versionadas e validadas, nunca `DROP` de tabelas/eventos históricos para "limpar".
- Registrar `IMPLEMENTED`, `TESTED`, `DEPLOYED`, `AUTHENTICATED_VERIFIED` separadamente. Relatório final de cada lote, commits/PRs e pendências classificadas.

## 5. Critérios de pronto da limpeza

- Menu cliente tem Inbox inicial, no máximo seis seções, sem ferramentas de engenharia/prompt, mas funcionalidades comerciais mantidas; link antigo abre destino equivalente.
- Super Admin encontra uma configuração única para cada responsabilidade, sem cadastro técnico obrigatório no cliente, com status do token/asset verificado.
- Nenhuma rotina ativa duplicada de envio de eventos ou tags; histórico fica consultável e preservado.
- Cada remoção tem evidência rastreável de ausência de consumidores e testes; **zero remoções meramente pelo nome**.
- LeoFT, Evolution, WhatsApp Oficial, Instagram automações, Inbox/histórico, campanhas e backups funcionam como antes.
- Não houve mudança de dados/segredos/produção na fase de documentação.

**Não confundir conclusão deste plano com conclusão de implementação.** A Issue #34 só encerra quando mudanças de UX e remoções estiverem implantadas e verificadas, com pendências reais discriminadas.