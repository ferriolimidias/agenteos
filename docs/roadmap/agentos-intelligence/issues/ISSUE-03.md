# A02 | P1C: cutover canônico controlado por tenant e evento

Issue: https://github.com/ferriolimidias/agenteos/issues/3
Estado no inventário: OPEN

**Resultado utilizável:** substituição reversível de **um** tipo de evento e tenant autorizado por operação, com outbox, dedup, lease, UNKNOWN_DELIVERY e rollback sem reenvio histórico.

Pré-condições de execução: evidência real do legado, validação de ownership comum, credencial Dataset/WABA tenant, shadow canário, autorização explícita de envio. Não habilitar automaticamente quando outros tickets concluírem.

**Aceite:** comparação shadow vs legacy; canário live autorizado; único proprietário do dispatch para o escopo; event_id estável, logs e métricas de falha, rollback ensaiado; escopo não selecionado continua no legado; no-send histórico imutável.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
