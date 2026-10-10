# A05 | Dashboard de funil e gargalos por coorte

Issue: https://github.com/ferriolimidias/agenteos/issues/6
Estado no inventário: OPEN

**Resultado utilizável:** aba de funil com gráfico etapa→avanço/abandono/inatividade/recuperação, drill-down nos contatos, períodos, campanha, anúncio e versão de prompt **quando disponível**.

Métricas com população/coorte explícita e sem confundir click Meta com conversa única. Contatos podem voltar e pular etapas; distinguir perda confirmada de inativo.

**Aceite:** dashboard alimentado por dados persistidos atuais, exemplo real agregado por tenant, intervalos e taxa com denominadores; unknown/null para attribution incompleta; CSV sem PII por padrão; nenhuma dependência obrigatória de JEV.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
