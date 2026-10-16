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

## Modelo de autorização Meta durante App Review — decisão do proprietário em 16/10/2026

**O aplicativo AgentOS é da Ferrioli Mídias e pertence à BM/portfólio empresarial da Ferrioli. O usuário cliente nunca precisa receber, copiar, gerar ou cadastrar token manual.** A fase temporária é **operacionalmente administrada pela Ferrioli Mídias**, com credencial do app/usuário de sistema autorizado. A BM do cliente compartilha em modo parceiro a conta de anúncios, Página e Instagram conforme necessidade e atividades permitidas; não há transferência de propriedade. A Ferrioli associa esses ativos à credencial autorizada, a operador/usuário de sistema e ao tenant certo, mantendo permissão mínima e auditabilidade.

**WhatsApp Cloud API** tem onboarding automático já implementado no painel do cliente via Meta Embedded Signup, independentemente da aprovação futura de Ads/Instagram. Não exigir token manual do cliente. No mesmo painel há um conector Evolution QR code **separado**, que não é o QR da API Oficial; o modo WhatsApp Business App coexistence dentro do Embedded Signup pode apresentar etapa de QR próprio da Meta.

**Marketing/Instagram/Facebook temporários** usam token administrado pela Ferrioli, vinculado a ativos do cliente que foram compartilhados com sua BM (na medida permitida por scopes, nível de acesso Meta, permissões do token e papéis). Depois de concedidos e aprovados os acessos necessários no App Review, a origem da credencial passa para autorização automática do próprio app, **sem substituir as ferramentas nem seus contratos**; o cliente então clica para conectar os ativos necessários e aprova o acesso. O processo pode envolver permissões/diálogos distintos por produto Meta.

**Achados concretos de código ainda pendentes de correção/validação:**
- `frontend/src/pages/tenant/Instagram.jsx` atualmente permite apresentar formulário de `Instagram User Access Token` dentro do painel tenant. Rever autorização backend/RBAC e restringir o gerenciamento manual à Ferrioli/admin durante a fase provisória, mantendo ao cliente apenas estado/recursos conectados. Não afirmar que somente Super Admin pode cadastrar sem prova do RBAC atual.
- `app/services/meta/marketing_credentials.py` contempla `META_MARKETING_ADMIN_ACCESS_TOKEN` e `MetaMarketingTenantAccountGrant` **apenas para leitura validada/escopada**; não assumir que cobre toda escrita Ads. O armazenamento em variável de ambiente não prova que exista editor dessa variável no Super Admin.
- `app/api/routers/conexoes.py` exige uma conexão WhatsApp Meta antes da sessão OAuth Marketing. Remover tal acoplamento apenas depois de testar a equivalência e confirmar que não é precondição real da operação Marketing, preservando controles necessários.
- App Review/Advanced Access da Meta e atribuição real de ativos são requisitos distintos: compartilhamento na BM não garante que qualquer token do aplicativo acesse contas de terceiros nem substitui Advanced Access quando exigido. Validar com endpoints reais e documentação oficial atual.

**Sequência operacional:** 1) cliente conecta WhatsApp pelo AgentOS; 2) cliente compartilha ativos de anúncios, Facebook e Instagram com a BM Ferrioli; 3) operador Ferrioli atribui ativos e configura credencial temporária em seu ambiente administrativo; 4) o AgentOS executa recursos e gera evidências reais sanitizadas para App Review; 5) aprovação Meta por permissão; 6) cliente conecta os outros ativos por autorização automática e o mesmo backend passa a resolver a nova credencial; 7) testar equivalência e desativar cadastro manual na experiência cliente. Sem duplicar um módulo por origem de token.


## Padrão único de vendas por WhatsApp — atualização 16/10/2026

O AgentOS **não oferece ao cliente um gerenciador de objetivos de anúncios**: nesta fase existe uma única estratégia comercial. O cliente escolhe criativos próprios ou postagem/Reels existente, orçamento e parâmetros comerciais apropriados; o sistema valida permissões, cria automaticamente Campaign + AdSet + Creative + Ad de destino **WhatsApp Oficial** inicialmente PAUSED, e seu atendimento/CRM/JEV conduz toda a jornada. Páginas de vendas e outros objetivos ficam fora do escopo atual. O backend escolhe/valida o objetivo de performance compatível com a Meta, sem menu técnico de alternativas para o cliente, e sinaliza se a otimização qualificada não estiver elegível. Referência do código: `traffic_campaign_builder.py` mantém `OUTCOME_SALES`, destino `WHATSAPP`, CTA `WHATSAPP_MESSAGE`; `TrafficMeta.jsx` ainda tem seletor "Objetivo comercial" a alinhar.

**Prioridade absoluta de conversões:** recuperar a configuração anterior do Gerenciador de Eventos/CRM que o proprietário relata ter utilizado com bons resultados; prover envio confiável de resultados comerciais do CRM a Dataset autorizado, com identidade, dedup, evidência do resultado e verificação no Events Manager/Ads Manager. Não confundir recebimento HTTP com **uso do evento na otimização CTWA**. A documentação de CRM Conversion Leads é explicitamente voltada sobretudo a leads de Instant Forms, enquanto o CTWA possui o contrato Business Messaging com `ctwa_clid`; por isso, validar empiricamente na conta de anúncios autorizada a relação do Dataset CRM com o `optimization_goal` do AdSet WhatsApp. Se for necessário o contrato específico Business Messaging para otimização, corrigir a saída técnica como adaptador invisível, **sem trocar a estratégia de vendas nem criar segundo motor comercial**. O dataset WABA que falhou anteriormente NÃO deve ser presumido funcional nem eliminado sem análise de causa; não reproduzir falhas ou envios históricos duplicados.

**Matching legítimo:** a conversa WhatsApp normalmente traz telefone/wa_id e nome do perfil, além do contexto/referral do anúncio e `ctwa_clid` quando há CTWA real. Ela **não fornece automaticamente email do perfil Facebook da pessoa que clicou**. Usar email, nome completo e outros campos somente quando de fato coletados com finalidade/base legal e verificados no CRM; JEV pode extrair informações voluntárias sem interrogatório. Aplicar tratamento/hash/normalização específicos a cada contrato; `ctwa_clid` não deve ser alterado ou inventado. Pagamento/Purchase só com confirmação independente. A IA aplica tags canônicas e registra evidências; a infraestrutura comercial e de envio permanece única.

Roteiro executivo prioritário e referências técnicas: [Issue #33, decisão CTWA](https://github.com/ferriolimidias/agenteos/issues/33#issuecomment-6217192084); [Meta Blueprint CRM CAPI](https://www.facebookblueprint.com/student/path/264294-api-conversoes-crm); [Meta Lead Generation Guide](https://about.fb.com/ltam/wp-content/uploads/sites/14/2023/11/LeadGenerationGuide.pdf); [amostra fbsamples CAPI CTWA](https://github.com/fbsamples/lead-ads-webhook-sample/blob/main/postman/FB%20Conversions%20API%20%28Part%201%20-%20online%29.postman_collection.json).
