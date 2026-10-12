# UX03 — gestão administrativa da plataforma

Baseline UX02 `add2aa4b90b3505be11b8e4ff0744e47cf095070`. Contrato congelado: [UX03](../../releases/UX03-admin-experience.md). Gestão lista → empresa → seção, com configurações globais e JEV identificados separadamente. Nenhum backend, banco, permissão, contrato comercial ou integração é alterado.

## Inventário de rotas e navegação

| Rota existente | Experiência / operação | Estado auditado |
|---|---|---|
| `/admin` | Carteira; setup; edição incluindo logo; exclusão; impersonação; status worker | Funcional; busca/filtros/ações nomeadas UX03 |
| `/admin/empresas/:empresaId` | Dez abas abaixo; retorno para carteira | Funcional; estado de aba derivado do URL |
| `?tab=visao-geral` (ou sem tab) | Conexão, mensagens registradas, acesso a teste do agente | Dados consultados; desconhecido distinto de zero |
| `?tab=usuarios` | Listar/criar/editar; ativar/desativar; senha temporária/reset/cópia | Papel `admin_empresa` existente preservado |
| `?tab=agente` | Identidade, modelo, saudação, notificações, personalidade, ferramentas/tags IA | Configuração por empresa, sem agente global inventado |
| `?tab=regras` | Regras comerciais, monitor, prévia/sincronização, etapas CRM | Contratos comerciais intactos |
| `?tab=conhecimento` | RAG texto/PDF/lista/exclusão | Manager preservado |
| `?tab=canais` | Conexões; credencial IA; disparo operacional; Meta/WhatsApp | Manager/payloads preservados |
| `?tab=pagamentos` | Mercado Pago, limites, credenciais write-only/teste | Manager preservado |
| `?tab=agenda-automacoes` | Follow-up e parâmetros de automações já existentes | Configuração IA existente preservada |
| `?tab=imagens` | Aviso existente de capacidade de visão | Placeholder auditado, sem controle funcional inventado |
| `?tab=diagnostico` | Configuração geral e diagnóstico disponível | Não é painel de saúde avançado A14 |
| `/admin/configuracoes` | Branding e fallback IA global | GET/PUT e evento de atualização preservados |
| `/admin/configuracoes/integracoes/jev` | Empresa, chave write-only, orçamento, timeout, enable SHADOW, teste | GET/PUT/teste A06 preservados; seleção pelo link validada |
| `/admin/agente`, `/admin/orquestrador` | Aliases redirecionam `/admin/agentes` | URLs mantidas |
| `/admin/agentes` | Seleção de empresa para acessar agente | Destino antes inexistente; nova seleção apenas GET |

## Matriz de operações: ação ↔ origem ↔ endpoint ↔ papel ↔ proteção/feedback

O papel abaixo descreve o contexto administrativo existente; não introduz policy de UI ou backend. A autorização efetiva continua no backend e nos guards existentes. Todos os testes de escrita abaixo são atendidos por fixtures locais; não exercitam operações reais.

| Ação | Origem da UI | Request existente | Papel/contexto | Confirmação / feedback |
|---|---|---|---|---|
| Listar empresas | Carteira/detalhes/JEV/seletor agentes | GET `/empresas/` | Super Admin | Loading, erro/retry, vazio |
| Criar ambiente e administrador | Nova empresa | POST `/empresas/setup`, oito campos originais | Super Admin | Formulário requerido; salvar/cancelar; erro/sucesso |
| Editar nome/área/logo | Carteira → editar | PUT `/empresas/:id`, três campos originais | Super Admin | Salvar/cancelar; busy; erro/sucesso |
| Editar nome/área | Detalhes → dados da empresa (canais) | PUT `/empresas/:id`, dois campos originais | Super Admin | Salvar; busy; erro/sucesso |
| Excluir empresa/dados | Carteira → ações | DELETE `/empresas/:id` | Super Admin | Confirmação com empresa/irreversibilidade; cancelamento sem request |
| Entrar no painel do cliente | Carteira → ações | POST `/auth/impersonate/:id` | Super Admin | Aviso de contexto; sessão original preservada; banner/Sair e Voltar UX02 |
| Testar agente | Detalhes → visão geral | Navegação `/painel/simulador?empresaId=:id`; storage existente | Super Admin | Aviso antes da navegação; handler idêntico |
| Consultar worker follow-up | Carteira → seção recolhível | GET `/admin/workers/status`, polling30s existente | Super Admin | Atualizar; ausente não declara saúde |
| Consultar modelos | Detalhes | GET `/admin/modelos-ia` | Super Admin | IDs canônicos/labels preservados |
| Listar acessos | Usuários | GET `/admin/empresas/:id/usuarios` | Super Admin | Loading, erro/retry, vazio separado |
| Criar usuário | Usuários → novo | POST `/admin/empresas/:id/usuarios`, nome/email/role/ativo/senha_temporaria | Super Admin | Modal acessível; senha apresentada uma vez; papel existente |
| Editar usuário | Usuários → editar | PUT `/admin/empresas/:id/usuarios/:uid`, nome/email/role/ativo | Super Admin | Salvar/cancelar; busy/error |
| Ativar/desativar usuário | Usuários → ação status | POST `/admin/empresas/:id/usuarios/:uid/ativar` ou `/desativar` | Super Admin | Confirmação acessível antes da ação; mesmo request/papel; feedback |
| Reset senha temporária | Usuários → reset | POST `/admin/empresas/:id/usuarios/:uid/reset-senha`, senha_temporaria | Super Admin | Formulário e aviso; resultado exibido uma vez |
| Copiar senha recém-criada | Resultado criação/reset | Clipboard; sem API | Super Admin | Feedback local; sem armazenamento novo |
| Consultar agente/regras/automação | Detalhes | GET `/empresas/:id/ia-config` | Super Admin | Mesmo contrato; tags independentes |
| Salvar agente/regras/automação | Abas detalhes | PUT `/empresas/:id/ia-config`, spread e coerções originais | Super Admin | Salvar; busy; feedback |
| Selecionar tags permitidas IA | Agente → tags | GET `/empresas/:id/crm/tags/oficiais`; PUT ia-config | Super Admin | Erro tags separado; UUIDs não eventos de conversão |
| Consultar/preview/sync funil | Regras comerciais | GET `/empresas/:id/monitor/funnel`; POST `/preview`, `/sync` | Super Admin | Prévia antes de confirmar sincronização; sem regras novas |
| CRUD etapas CRM | Regras → EmpresaCrmEtapasManager | GET `/empresas/:id/crm`; POST `/crm/etapas`; PUT/DELETE `/crm/etapas/:etapaId` | Super Admin | Manager original; confirmação de excluir; busy/error |
| RAG texto/PDF/exclusão | Conhecimento → RAGManager | GET/POST/DELETE `/empresas/:id/rag`; POST `/rag/pdf` multipart | Super Admin | Manager original; confirmação de excluir; loading/errors |
| Listar/criar/editar/excluir conexões | Canais → EmpresaConexoesManager | GET/POST `/empresas/:id/conexoes/`; PUT/DELETE `/conexoes/:cid` | Super Admin | Manager original; confirmações existentes |
| Status e QR | Canais → conexão | GET `/conexoes/status/:cid`, polling existente | Super Admin | Manager original; nenhum sender alterado |
| Conexão operacional/disparo | Canais → conexão principal | PUT `/empresas/:id`, payload original do manager | Super Admin | Manager original; feedback |
| Provisionar/sincronizar WhatsApp | Canais → manager | POST `/empresas/:id/conexoes/provision-whatsapp` e sync existente | Super Admin | Manager byte-idêntico; nenhum teste externo |
| Salvar credencial IA | Canais | PUT `/empresas/:id/credenciais`, openai_api_key | Super Admin | Campo password; handler/payload original |
| Consultar status/contagem | Visão geral | GET `/empresas/:id/whatsapp/status`; `/dashboard/stats` | Super Admin | Desconhecido não vira desconectado ou zero |
| Configurar/testar Mercado Pago | Pagamentos → EmpresaPaymentManager | GET/PUT `/admin/empresas/:id/payments/mercado-pago`; POST `/test` | Super Admin | Manager intacto; tokens write-only |
| Branding/fallback global | Configurações globais | GET/PUT `/admin/configuracoes`, cinco campos originais | Super Admin | Save/busy/error/success; descarte local; password |
| Branding público | Shell/root existente | GET `/public/configuracoes-branding` | Público | Fallback sem alterar autenticação |
| Consultar/configurar JEV | JEV → empresa | GET/PUT `/admin/empresas/:id/integrations/jev` | Apenas Super Admin, guard A06 | Allowlist de resposta, api_key omitida se vazio; remoção explícita; orçamento/timeout |
| Testar JEV salvo | JEV → teste | POST `/admin/empresas/:id/integrations/jev/test`, `{}` | Apenas Super Admin | Exige configurado; sem conversa; fixture apenas no QA |

## Duplicação e complexidade comprovadas

Na baseline, `showCredenciaisModal`, `showConfigIAModal`, `showRagModal` são inicializados `false` e nunca recebem `true`. `empresaSelecionada` também não é preenchida por ação acessível. Os botões já navegam para abas canais/agente/conhecimento. Portanto os antigos blocos de credenciais/IA/RAG e CRUD local de tags contidos nesses blocos não são funcionalidades acessíveis na baseline; eram duplicações sem caminho de abertura. A UX03 remove somente esses ramos. Configuração e operações acessíveis seguem nos managers/detalhes, sem unir seus tratamentos. Cadastro e edição de logo da carteira permanecem com seus payloads próprios, em formulário completo em vez de modal longo.

Nenhum novo indicador A14 foi inventado: worker é apenas a consulta existente, recolhível; status de cadastro sem campo ativo é “Não informado”. A UI do JEV jamais recebe chave salva, mesmo se uma resposta sintética indevida incluir `api_key`; campo permanece vazio e não é persistido no navegador. A integração real e os fluxos comerciais continuam fora da validação UX03.
