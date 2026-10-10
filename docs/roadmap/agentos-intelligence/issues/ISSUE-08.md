# A07 | Atendimento comercial com módulos de prompt combináveis

Issue: https://github.com/ferriolimidias/agenteos/issues/8
Estado no inventário: OPEN

**Resultado utilizável:** atendimento atual passa a poder combinar, na mesma resposta, módulos de preço, objeções, qualificação, oferta, fechamento e contexto; fallback para prompt anterior.

Separar etapa principal e múltiplos assuntos; usar um compositor de instruções e ferramentas, sem roteamento rígido para 1 subagente por turno; versões e exposições de prompt persistidas; parâmetros tenant/FunnelSpec como fonte de verdade. Aplicar ResponseGuard e regras comerciais.

**Aceite:** conversa simulada com 3 intenções atendidas em resposta coerente; redução mensurada de tokens vs baseline sem degradar resultado; fallback por tenant; não mudar tags/checkout/Meta por novo caminho.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
