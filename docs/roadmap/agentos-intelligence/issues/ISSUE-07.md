# A06 | JEV Core: diagnóstico de mensagens e multi-intenções em shadow

Issue: https://github.com/ferriolimidias/agenteos/issues/7
Estado no inventário: OPEN

**Resultado utilizável:** resultados JEV consultáveis no Inbox/diagnóstico, sem afetar respostas; avaliação de intenção múltipla, objeções, urgência, dúvida sem resposta e sugestão de tag com evidência.

Gateway por tenant; versão de perguntas e esquema; `Choice/Score/Noul` conforme contrato do provider conferido; contexto resumido + trechos necessários; custo p50/p95, falhas, privacidade, orçamento, consentimento para dados externos. Dataset revisado PT-BR comparado com rótulos humanos.

**Aceite:** liga/desliga por tenant, shadow com teste/simulação aprovada; nenhum efeito no atendimento, CRM, tags ou Meta; métricas de precisão e falsos positivos, fallback e orçamento finito.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
