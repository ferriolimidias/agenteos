# A13 | Traffic Meta Intelligence: comparar anúncios e sugerir melhorias

Issue: https://github.com/ferriolimidias/agenteos/issues/14
Estado no inventário: OPEN

**Resultado utilizável:** cards de insights por anúncio/campanha cruzando spend/CPQL/conversas/objeções/checkout/receita verificável e recomendações JEV justificadas, sem geração de imagem/anúncio.

Calcular indicadores deterministicamente, janelas comparáveis e qualidade de amostra; JEV avalia padrões, não inventa ROAS ou causa; extrair metadados de vídeo/copy/transcrição quando disponíveis.

**Aceite:** dashboard com ranking por métricas e cobertura, 5 anúncios comparáveis quando houver dados, recomendações auditáveis e opção de revisão; nenhuma alteração automática em orçamento/status/optimization_goal neste ticket.

### Contrato obrigatório de entrega
- Unidade útil isoladamente no AgentOS atual; telas/rotas de leitura funcionam mesmo sem implementar futuras fases, com estado `unknown` quando faltarem dados.
- Integrar componentes atuais; jamais criar segundo caminho independente para tags→Commercial Events→Meta.
- Preservar RBAC multi-tenant, dados pessoais, filas, WhatsApp, Ads e conversões; testes PostgreSQL quando houver schema.
- Descobrir imagem/commit real de produção antes de tocar código. Feature flag com rollback; sem deploy direto do GitHub `master` desatualizado.
- Testes focados + baselines, comparação explícita com 20 falhas históricas da suíte; relatório com antes/depois, evidência de uso, riscos e rollback.
- Publicar documentação e atualizar status da issue. **Nenhuma escrita remota Meta/WhatsApp ou envio LIVE por inferência**; ações externas exigem escopo e autorização específicos.

Documentação-mãe na branch `planning/agentos-intelligence-20261010`: `docs/roadmap/agentos-intelligence/README.md`.
