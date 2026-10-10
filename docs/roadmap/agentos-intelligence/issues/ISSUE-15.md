# A14 | Admin Saúde + confiabilidade de testes e integração

Issue: https://github.com/ferriolimidias/agenteos/issues/15
Estado no inventário: OPEN

**Resultado utilizável:** console central de saúde da API, workers, canais, Meta ads/readiness, Business Messaging, Shadow/outbox, última sync e alertas, com logs sanitizados e caminhos de remediação.

Tratar explicitamente as 20 falhas preexistentes da suíte integral como débito técnico verificável; criar runner/CI reproduzível para o novo pipeline com contrato de baseline, sem bloquear novos módulos por falso positivo; releases com rollback.

**Aceite:** dashboard autenticado, sinais temporais/heartbeat, estado unknown em vez de falso healthy, integração com reporte das falhas, testes de auth e documentação operacional. Não expor tokens.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
