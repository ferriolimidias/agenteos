# A01 | P1C: observar evidência real e fechar instrumentação dos senders

Issue: https://github.com/ferriolimidias/agenteos/issues/2
Estado no inventário: OPEN

**Resultado utilizável:** painel/API operacional de evidência dos dois send paths legados já instrumentados, com estados `accepted/rejected/unknown/no_events` e alertas; observação de canário natural sem provocá-lo.

Baseline: P1C.4 implantado; 2/2 caminhos instrumentados; 126 históricos bloqueados; 0 vínculos exatos, 88 prováveis, 686 não ligados; canário natural ausente; LIVE canônico OFF.

**Aceite:** leitura autenticada por tenant com última tentativa, identidade e destino; comparar saída com o registro de tentativa; distinguir ausência de tráfego de falha; política de ownership documentada e simulada em concorrência; histórico preservado; sem ativar sender LIVE nem inventar evento.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
