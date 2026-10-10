# A12 | Traffic Meta: promover publicação existente e manter novo criativo

Issue: https://github.com/ferriolimidias/agenteos/issues/13
Estado no inventário: OPEN

**Resultado utilizável:** na aba Traffic Meta, criar anúncio por publicação já existente de Instagram/Facebook, com seletor de post, preview e validação de compatibilidade, mantendo fluxo de criar novo criativo.

Auditar IDs usados no módulo de comentários vs IDs exigidos por Marketing API; mapear media/post ao IG account/FB Page/ad account, verificar API/version, permissões de escrita e limites; reutilizar módulos atuais e sempre exigir confirmação para criar anúncio real.

**Aceite:** seleção/listagem read-only validada; criação de criativo em mock end-to-end; criação real apenas após autorização separada; nenhuma regressão no fluxo atual de anúncio novo.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
