# A09 | Follow-ups: impacto, recuperação e otimização segura

Issue: https://github.com/ferriolimidias/agenteos/issues/10
Estado no inventário: OPEN

**Resultado utilizável:** painel por follow-up automático/programado com elegíveis, enviados, entregues, respondidos, reativados e avanços de etapa.

Reutilizar workers e stop rules; separar follow-up agendado vs inteligente e correlacionar entrega/resposta; incorporar política de opt-out, janelas, idempotência e causalidade prudente; nenhuma mudança de disparos na primeira entrega.

**Aceite:** relatórios reais e drill-down, estado unknown onde faltarem status; métricas por janela, comparações com não contatados quando viável; workers preservados.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
