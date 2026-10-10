# A03 | Fechar checkout e receita verificáveis até a atribuição

Issue: https://github.com/ferriolimidias/agenteos/issues/4
Estado no inventário: OPEN

**Resultado utilizável:** relatório de compra/receita e jornada do primeiro pagamento verificado no provider, com estados `unknown` quando não houver vendas, sem fabricar Purchase.

Reutilizar checkout/payment, attribution_touches, commercial_events e outbox; conferir webhooks de aprovação/refund, correlação lead/conversa e dedup; não usar valor estimado do CRM como faturamento.

**Aceite:** testes com provider mock e PostgreSQL + canário natural quando houver; uma aprovação conta uma vez, refund reduz receita, tenant isolado; métricas separadas de cobertura; sinal externo permanece sob gates P1C.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
