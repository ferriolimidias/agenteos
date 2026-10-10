# A11 | Prompt Optimizer: experimentos e autopromoção com rollback

Issue: https://github.com/ferriolimidias/agenteos/issues/12
Estado no inventário: OPEN

**Resultado utilizável:** otimizador de módulos de prompt que diagnostica gargalos, gera candidatos, faz testes offline, executa experimentos controlados, avalia métricas e promove/reverte automaticamente dentro de limites configurados.

Pré-requisito técnico: versionamento de módulos/exposição e medida de conversão disponíveis. Primeira release inclui ciclo funcional end-to-end por tenant piloto; não depende da existência futura de Ad Optimizer. Não editar preços, fatos, segurança, políticas ou respostas sem evidência. Guardrails amostrais e temporais contra otimização espúria.

**Aceite:** A/B persistido, atribuição de exposições, guardrails, testes de segurança, promoção automática auditável ou rollback automático, fallback estável para todas as outras empresas; sem envio Meta adicional.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
