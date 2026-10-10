# Política de autorização HTTP

O servidor decide o acesso. Ocultar controles no frontend ou receber `empresa_id`
do cliente não substitui autenticação, papel e ownership.

## Classes de política

- `PUBLIC_INTENTIONAL`: rota enumerada individualmente em
  `app/api/http_route_policy.py`, limitada a liveness/documentação, login,
  branding sem dados privados ou fluxos públicos com token opaco.
- `WEBHOOK_PROVIDER`: callback público para rede, autenticado pelo contrato do
  provider antes de qualquer side effect. Meta/Instagram usam verificação de
  assinatura; pagamento valida a assinatura do Mercado Pago; callbacks legados
  usam HMAC Meta, token da instância Evolution ou token por tenant.
- `AUTHENTICATED_GLOBAL`: JWT de usuário ativo obrigatório, sem escopo de tenant
  porque a operação é global ou não consulta recurso tenant.
- `TENANT_READ` / `TENANT_WRITE`: JWT obrigatório, `require_tenant_access`
  compara o tenant solicitado com o vínculo persistido do usuário e valida papel.
  O handler também filtra cada recurso por `empresa_id` na consulta.
- Um `get_current_user` isolado não define escopo tenant. Rotas com `{empresa_id}`
  ou parâmetro de identidade de recurso (por exemplo `{lead_id}` ou
  `{conexao_id}`) precisam de policy tenant explícita ou de uma exceção exata
  que prove o filtro tenant na consulta.
- `SUPER_ADMIN_ONLY`: ação global, configuração de plataforma ou administração
  de empresas exige `require_super_admin`.
- `INTERNAL_SERVICE`: callback entre serviços validado por HMAC, escopo e evento.

## Ownership de recursos

Quando uma URL contém `empresa_id` e um ID de lead, conversa, conexão, tag, etapa
ou outro recurso, a consulta deve filtrar tenant e ID juntos. Retorne `404` quando
o recurso não pertence ao tenant autorizado, sem fazer a operação externa antes
da verificação. IDs recebidos em body/query também precisam ser resolvidos dentro
do tenant da sessão.

Para inbox, envio manual, mídia e controle do bot, resolva lead/conversa e conexão
com o mesmo `empresa_id` antes de executar dispatch. Exportações de PII são
`TENANT_READ` e devem permanecer limitadas ao tenant da sessão.

## Rotas públicas

O contrato de rotas enumera cada método e template de rota público. Os fluxos
`/api/public/anamnese/*`, `/api/public/leoft-app/*` e a aprovação tokenizada
validam token de recurso dentro do handler. Eles não liberam outras rotas sob
esses prefixos automaticamente: uma rota nova continua precisando de entrada
explícita no contrato e teste de validação de token.

Webhooks não usam JWT de usuário. Eles só podem ser públicos quando validam a
assinatura/token próprio do provider. Callbacks Telegram e Chatwoot legados leem
`telegram_webhook_secret` e `chatwoot_webhook_secret` de
`Empresa.credenciais_canais` e recebem `X-AgentOS-Webhook-Token`. Evolution envia
`X-AgentOS-Webhook-Token` com o token da instância configurado pelo onboarding.
Se o segredo não estiver configurado, o callback falha fechado com `503`.

## Regressão

`tests/security/test_http_authorization_policy.py` percorre todas as rotas HTTP
FastAPI e falha quando encontra uma rota sem política reconhecida ou uma entrada
pública que deixou de existir. Rotas tenant críticas também têm testes de ausência
de token, token inválido, acesso cruzado, exportação, envio e exclusão global.
Novos endpoints devem declarar uma dependency de auth/tenant/super-admin,
autenticação de serviço, ou a allowlist pública exata e justificada.

GET deve ser somente leitura. Inicialização de funil é feita durante onboarding;
sincronização de ativos Meta fica em POST explícito. Atualizações e consumo de
estado devem usar métodos de escrita.
