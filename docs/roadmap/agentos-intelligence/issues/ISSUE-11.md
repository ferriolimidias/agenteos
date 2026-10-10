# A10 | Campanhas WhatsApp: jornada, resposta e conversão assistida

Issue: https://github.com/ferriolimidias/agenteos/issues/11
Estado no inventário: OPEN

**Resultado utilizável:** campanha WhatsApp ganha analytics de recipient→sent/delivered/read/reply→reativação→etapa→compra, distinguindo origem inicial e intervenção assistida.

Reutilizar Campaign Central, message_outbox, attribution_touches e commercial events; não atribuir toda compra após disparo como causada por ele; preservar retries/tenant isolation.

**Aceite:** painel por campanha/segmento e destinatário, cobertura dos status, dedup e opt-out, breakdown de origem vs assistência; sem novos disparos para testar.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
