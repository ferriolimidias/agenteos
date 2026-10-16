# Arquitetura desejada e contratos de entrega — AgentOS Intelligence

## Fluxo de negócio

```text
Meta Ads (post existente ou novo criativo) → CTWA/Inbox → Lead/Conversa
  → Estado comercial do tenant/FunnelSpec
  → JEV ACTIVE: intenções simultâneas, objeções, oportunidade, estágio e DECISÃO de tags
  → Compositor de módulos de prompt + ferramentas da etapa/assuntos
  → Resposta única coerente / follow-up programado ou inteligente
  → JEV aplica TAGS EXISTENTES pelo serviço oficial (sem aprovação manual por evento)
  → Commercial Event e Attribution Ledger → Meta sender legado/canônico controlado
  → Checkout, pagamento aprovado/refund verificado → Receita
  → Funil e análise de gargalos → melhoria dos prompts / recomendação Ads
```

Campanhas WhatsApp são intervenção para reativação e resposta; não eliminam a origem de aquisição.

## Invariantes

1. Tags continuam o trigger canônico definido no FunnelSpec para conversões; JEV ACTIVE **decide e aplica automaticamente** tags elegíveis utilizando exclusivamente a ferramenta canônica existente, com escopo tenant, evidência, idempotência e possibilidade de correção posterior. Não permitir segunda rota JEV→Meta. Compra necessita pagamento aprovado real verificado; intenção ou afirmação da IA não comprovam pagamento. A passagem SHADOW→ACTIVE depende de validação de provider e operação autorizada, não de aprovação humana por cada tag.
2. `unknown` vs `0` vs `partial` vs `not_applicable` sempre diferenciados. HTTP 200 Meta não prova melhoria de entrega/atribuição.
3. Não remover RBAC, isolamento por tenant, limites de gastos, idempotência, opt-out, follow-up stop rules ou credenciais por capacidade.
4. Mensagens podem conter múltiplos assuntos: separar **etapa principal** e **assuntos ativos**. Um compositor traz módulos preço, confiança, produto, agenda, oferta, objeções, fechamento conforme necessário. A conversa não fica rigidamente roteada a apenas um especialista.
5. Prompts carregam regras e contexto relevantes; armazenar versão/exposição por conversa. Usar histórico resumido estruturado e mensagens recuperadas sob demanda sem perder fatos.
6. Economia JEV é hipótese a medir: latência, custo, precisão/recall PT-BR, desvio de conversão e cobertura, com fallback do atendimento.
7. Aprendizado não significa reescrever regras comerciais: prompt optimizer propõe novas versões de *módulos de redação*, usa testes offline, A/B com alocação estável, amostra mínima e rollback automático. Preços, promessas, segurança e políticas não são otimizáveis automaticamente.
8. Ads optimization: calcular métricas em código (spend, CPL, CPQL, CPA, ROAS) e JEV diagnosticar contexto, qualidade de amostra e oportunidades; sem geração de criativos nem alterações automáticas de orçamento nesta fase.
9. Tratar dados pessoais, dados de cliente, mensagens e tokens com minimização e acesso controlado. Logs públicos, GitHub Issues e relatórios não incluem PII.

## Dashboard — capacidades independentes

- **Jornada por contato:** origem (ad/CTWA quando conhecida), inbound, resposta, tags, oferta, objeções, follow-up, campanha, checkout, compra verificada, duração e evidência.
- **Funil por coorte:** ads→inbound→engajou→qualificado→oferta→checkout/agendamento→Purchase; suportar volta/pulo de etapas, distinguir inativo, perda confirmada e ativo. Drill-down de cada queda na tabela de contatos.
- **Gargalos conversacionais:** primeira resposta sem réplica, pergunta não resolvida, abandono após preço/oferta, objeções por categoria, taxa de avanço por prompt_version, retomada por follow-up. Razões JEV são hipóteses e qualidade é medida.
- **Recuperação:** programado vs automático, envios, entregas, respostas, reativações, conversão posterior; intervenção da campanha WhatsApp separada da origem.
- **Performance anúncios:** campaign/adset/ad/creative e postagem orgânica reutilizada, CTR/CPC/CPM, CPQL, qualidade da atribuição, receita/ROAS só quando conhecida. Insights de melhoria de mensagem e criativo.
- **Intelligence Lab:** decisões JEV versionadas, comparação com rótulo de referência, experimentos e rollback de prompt, ganho/custo, hipóteses de otimização.
- **Admin Saúde:** workers, canais, datasets, credentials readiness, atribuição, conversões, outbox e alertas.

## Plano incremental

Tarefas independentes com contrato de aceite nas [Issues #1 a #15](https://github.com/ferriolimidias/agenteos/issues). Prioridade atualizada pelo proprietário em 16/10/2026: P0 comprovar conversões reais (#33), JEV aplicar tags (#31), rastrear event_id/outbox/delivery da Meta (#2/#3) e medir/otimizar por anúncio + versão do prompt (#9/#12/#14). Em paralelo, preparar as funções com token manual TEMPORÁRIO para demonstrar uso no App Review (#13/#32); após aprovação, o MESMO contrato de API deve usar credencial conectada automaticamente, com equivalência funcional. A fase SHADOW anterior e o token manual não são o estado final do produto. P1C pode ser concluído **em paralelo** (A01 observação; A02 LIVE apenas com autorização específica) e não bloqueia os dashboards. Checkout/pagamento A03 é iterável mesmo sem LIVE Meta.

Cada etapa deve poder ser implantada isoladamente com rollback, sob feature flag quando necessário. Não deve exigir completar o roadmap para voltar a usar o sistema.
