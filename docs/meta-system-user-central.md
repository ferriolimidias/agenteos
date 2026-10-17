# Credencial central Meta System User

Issue #36 centraliza um único token de Usuário do Sistema da Ferrioli no Super Admin. A credencial é criptografada com `META_CREDENTIAL_ENCRYPTION_KEY` antes de persistir em `meta_system_user_credentials`; a API de leitura retorna apenas os quatro últimos caracteres, identidade sanitizada, permissões validadas e horário de validação. Um token inválido não substitui a credencial anterior. Remover pelo AgentOS apaga apenas a cópia criptografada local e não revoga o token no Business Manager.

## Operação

- Interface: `/admin/integracoes` → seção **Meta · Usuário do Sistema**.
- `POST /api/admin/meta/system-user/credential/validate`: valida com Graph GET `/me` e tenta consultar `/me/permissions`; não persiste o token.
- `PUT /api/admin/meta/system-user/credential`: repete a validação e salva o token criptografado; exige a chave de criptografia configurada.
- `GET /api/admin/meta/system-user/credential`: status sem segredo.
- `DELETE /api/admin/meta/system-user/credential`: remove a cópia AgentOS e mantém vínculos/auditoria; revogação externa deve ocorrer na Meta.
- `POST /api/admin/meta/system-user/assets/discover`: atualização explicitamente iniciada; enumera negócios, contas, Páginas, contas Instagram, Datasets e Pixels apenas onde a Graph API devolver objetos. Paginação é limitada e truncamento/permissão ausente aparece como estado parcial. Cada acesso por Business é guardado separadamente do Business proprietário retornado pela Meta.
- `POST /api/admin/meta/system-user/assets/validate-id`: valida um ID numérico não enumerado pelo Graph através de um Business acessível; a validação não cria vínculo com empresa.
- `GET /api/admin/meta/system-user/assets`: lista metadados sanitizados e vínculos.
- `/api/admin/meta/system-user/companies/{empresa_id}/assets`: seleciona ativos previamente descobertos/validados. O backend consulta cada ativo novamente pelo Business selecionado antes de salvar. Contas, Páginas, Instagram, Datasets e Pixels são exclusivos de uma empresa AgentOS por vez; um portfólio Business acessível pode ser compartilhado como contexto entre empresas.
- `POST /api/admin/meta/system-user/companies/{empresa_id}/datasets/crm/validate`: exige um Dataset e Business explicitamente vinculados à empresa, verifica que o Dataset registra acesso pelo Business vinculado e faz somente `GET /{dataset_id}?fields=id,name,is_crm,is_unavailable`. Persiste apenas esses metadados sanitizados e uma trilha administrativa. `asset_read_state=observed` significa que o GET funcionou; `crm_dataset_state` descreve o sinal da Meta. `capi_delivery_state` permanece `unknown`, `live_sender_enabled=false`, e nenhuma rota de envio/replay é chamada.

A enumeração de contas profissionais usa os edges `owned_instagram_assets` e `client_instagram_assets` da Business API. O tipo `InstagramBusinessAsset` fornece o `ig_user_id` usado pelo fluxo de anúncios; não se confunde com o ID do wrapper do ativo. A enumeração de Datasets usa o edge Business `ads_dataset`.

Todas as rotas exigem `super_admin`. Nenhuma delas grava token em `localStorage`, logs ou respostas. A auditoria guarda ação, ator, contagens e tipos de ativos, sem segredos.

## Escopo e compatibilidade

O vínculo da empresa armazena IDs, caminho de acesso, metadados sanitizados e evidência de validação, sem copiar o token. O vínculo de conta de anúncios usa a concessão existente `MetaMarketingTenantAccountGrant`, agora com `write_enabled` explicitamente falso por padrão. Consultas Marketing e Insights podem usar o System User quando há credencial central, permissão confirmada, conta verificada e vínculo tenant. A leitura operacional passa a aparecer como `READY_ADMIN_MANAGED_READ`, com origem `SYSTEM_USER`, somente depois de Graph GETs de conta, campanhas, conjuntos, anúncios e Insights. A comprovação do vínculo explícito Business/conta vem do resolvedor e não exige uma segunda enumeração Business que poderia exigir outro escopo. A12 e outras leituras de Página filtram a lista pelo vínculo da própria empresa; Instagram exige também vínculo explícito do `ig_user_id`.

Criação de anúncios ainda exige `ads_management`, concessão de escrita explícita por ativo, vínculo de leitura e todas as flags operacionais existentes. O resolvedor central só fornece cliente gravável para a capacidade de gerenciamento de anúncios; auditoria Business permanece somente GET. Esse diagnóstico usa o resolvedor central apenas depois dos vínculos tenant de conta/Business e da permissão `business_management`, e limita a consulta ao Business da empresa selecionada. Os endpoints já criam objetos PAUSED. A Issue #36 não dispara mutações para validar isso.

Instagram Automations mantém o contrato e a credencial próprios. WhatsApp Oficial/Embedded Signup e Evolution não são usados como credenciais de Marketing. O Dataset central de CRM permanece no vínculo Meta e **não** é copiado para `TrafficMetaConfig.dataset_id`, que continua como espelho do Dataset Business Messaging/WABA. A nova leitura de Dataset CRM é separada do contrato WABA/CAPI e não encaminha a credencial pelo resolver do sender; nenhum sender CAPI LIVE, replay ou evento histórico foi ativado. Implementar e comprovar entrega de eventos continua sob a Issue #33.

## Limites honestos

`/me` comprova que o token funciona no momento da validação, mas não prova independentemente o tipo System User nem sua associação à BM Ferrioli; a interface deixa essa identidade como não verificada. O vínculo do app só aparece como verificado quando o debug-token opcional confirma o app configurado. A validade futura pode ser desconhecida se a Meta não devolver `expires_at` via debug token. `me/permissions` pode ficar `unknown` se não autorizado; nesses casos, os resolvers centrais falham fechados. Uma lista parcial não prova ausência de ativo. Uma descoberta completa marca ativos anteriormente vistos mas não retornados como indisponíveis e desliga seus vínculos; ainda é necessária nova validação explícita para reativá-los. A substituição da credencial também desliga vínculos antigos até nova descoberta e associação. Um GET por ativo comprova leitura naquele instante, não prova a autorização de escrita nem a prontidão de uma operação comercial. Uma leitura bem-sucedida de Dataset também não comprova que eventos sejam aceitos, atribuídos ou usados para otimização.

Com sessão administrativa e token proprietário indisponíveis, testes Graph reais e a confirmação visual autenticada ficam pendentes do operador; mocks não contam como validação real.

Contratos de referência do SDK oficial Meta (gerados a partir da Graph/Marketing API): [Business.json](https://github.com/facebook/facebook-business-sdk-codegen/blob/main/api_specs/specs/Business.json), [InstagramBusinessAsset.json](https://github.com/facebook/facebook-business-sdk-codegen/blob/main/api_specs/specs/InstagramBusinessAsset.json) e [AdsDataset.json](https://github.com/facebook/facebook-business-sdk-codegen/blob/main/api_specs/specs/AdsDataset.json).
