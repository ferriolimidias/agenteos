# Regra Permanente: Integrações Externas Multi-Tenant

Toda nova integração externa do AgentOS deve ser tenant-scoped por `empresa_id`
e não pode depender de IDs, tokens, nomes ou regras específicas de clientes
reais.

O fluxo obrigatório é:

`empresa_id -> credenciais do tenant -> ativos do tenant -> vínculos do tenant -> operação`

É proibido introduzir lógica baseada em nomes de clientes, UUIDs fixos, WABA,
Dataset, telefone, Ad Account, Page, tokens por cliente em `.env`, allowlists
manuais de tenants em variáveis de ambiente ou qualquer requisito de restart
para cadastrar uma nova empresa.

Para Conversões Meta / Business Messaging:

- `Conexao.metadata_json.dataset_id` é a fonte de verdade atual do Dataset.
- `TrafficMetaConfig.dataset_id` é somente espelho/referência.
- O token de Conversions API deve ser salvo por tenant, criptografado em
  `Conexao.credenciais`, e nunca retornado após salvar.
- Dataset configurado não significa mappings configurados nem evento enviado.
- `Purchase` deve nascer preferencialmente de um estado determinístico de
  pagamento confirmado, refletido no estado interno/CRM, e não apenas de uma
  decisão textual de IA.
