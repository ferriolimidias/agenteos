# A04 | Jornada comercial por contato no Inbox

Issue: https://github.com/ferriolimidias/agenteos/issues/5
Estado no inventário: OPEN

**Resultado utilizável:** timeline no Inbox mostrando origem, mensagens/etapas/tags/oferta/inatividade, follow-ups, campanhas, checkout e compra quando identificáveis.

Reutilizar fatos existentes e complementar instrumentação de eventos faltantes sem reescrever histórico. Diferenciar `sem dado`, `não ocorreu` e `ainda em andamento`; expor endpoints tenant-scoped com paginação.

**Aceite:** timeline navegável em uma conversa real preexistente, origem parcial marcada, nenhum side effect em GET; tratamento de retornos e passos não lineares; filtros; cobertura e testes.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
