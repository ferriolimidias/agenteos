# A08 | Diagnóstico de qualidade do atendimento e objeções

Issue: https://github.com/ferriolimidias/agenteos/issues/9
Estado no inventário: OPEN

**Resultado utilizável:** painel de perguntas não resolvidas, objeções recorrentes, abandono após preço/oferta, performance por versão de prompt e evidências navegáveis.

Reutilizar registros de conversa, JEV quando disponível, ou regras objetivas com indicação de cobertura; nunca declarar motivo causal apenas pela correlação; respeitar privacidade/tenant.

**Aceite:** gráficos e exemplos anonimizados, filtros por anúncio/oferta/etapa, distinção hipótese vs fato, edição por período, métricas por denominador e cobertura; operação independente de otimizador automático.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
