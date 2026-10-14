# A08 — diagnóstico de qualidade do atendimento

Status: candidata local `feat/a08-quality-20261014`, baseada em `8ba952c1aee595d289a98f25999ce0734ff5003b`. A08 ainda não está implantada.

## Contrato de leitura

Os endpoints autenticados `GET /api/empresas/{empresa_id}/analytics/attendance-quality` e `/attendance-quality/evidence` são tenant-scoped, somente leitura, não armazenam resultados e respondem com `Cache-Control: no-store`. A feature flag de visibilidade é `ATTENDANCE_QUALITY_ANALYTICS_ENABLED` (padrão ligado; valores `0`, `false`, `off` ou `no` ocultam a tela/endpoints).

Uma unidade das métricas principais é uma mensagem persistida em `mensagens_historico`, deduplicada por ID e associada a um `CRMLead` e, quando presente, a uma conversa do mesmo tenant. `from_me` é fallback somente quando `direction` não é `inbound`/`outbound`. Eventos de status e reação são excluídos. O banco calcula apenas marcadores booleanos de pergunta/preço/oferta; o serviço não seleciona nem carrega `texto` ou `metadata_json` completos. O período usa intervalo `[start, end)` UTC; respostas usam mensagens posteriores na mesma conversa até o horizonte de observação.

| Métrica | Numerador | Denominador | Limite |
| --- | --- | --- | --- |
| Perguntas sem resposta observada | Inbound com `?`, sem outbound posterior em até 24 h (configurável), com janela madura | Inbound com `?` e janela madura | Marcador textual; silêncio não identifica resolução, objeção ou intenção |
| Sem continuidade após preço/oferta | Outbound com marcador determinístico sem inbound posterior em até 7 dias (configurável), com janela madura | Outbound marcado com janela madura | Heurística determinística; silêncio não prova abandono |
| Cobertura de resposta | Inbound com qualquer outbound posterior dentro de 24 h | Inbound com janela de 24 h madura | Pares são calculados na mesma conversa; sem conversa/direção a cobertura fica parcial |
| Mediana da resposta | Mediana de segundos até o primeiro outbound posterior | Pares inbound/outbound respondidos incluídos na mediana | Inbounds maduros sem resposta são exibidos à parte e não entram na mediana; só timestamps persistidos; não estima satisfação |
| Objeções recorrentes | Eventos e contatos distintos por tag | Contatos distintos com tag explícita de objeção | Apenas tags cujo nome contém `objeção/objecao`; ausência da tag não prova ausência da objeção |
| A07 — composição observada | Execuções completas, estado, latência e uso observado por composição/modo/origem/escopo | Execuções A07 retidas no período | Redis, até 7 dias, sem ID de conversa/lead; composição não é versão exata do prompt; comparação descritiva, não causal |

Os estados são `observed` quando existe denominador maduro, `partial` quando janelas ou associações necessárias estão ausentes/truncadas, `unknown` quando não há base observável e `not_applicable` quando uma medida não se aplica à fonte. Zero observado é distinto de desconhecido. `messages_in_period` conta mensagens elegíveis no período selecionado; `messages_loaded` também inclui a janela posterior carregada para observar respostas e continuidade. O carregamento é limitado a 100.000 mensagens por chamada; ao atingir o limite, métricas e evidências são explicitamente parciais. A cobertura geral fica parcial diante de janelas incompletas, direção desconhecida, conversa ausente ou associação desconhecida necessária aos filtros.

O filtro de canal usa o canal persistido da mensagem; mensagens com canal nulo são excluídas da coorte e contadas como associação desconhecida, tornando a cobertura parcial. Tags CRM sem conversa válida do mesmo tenant também são excluídas e contadas como associação desconhecida quando o canal é filtrado. O filtro de etapa usa a etapa atual do lead no CRM, inclusive ao filtrar mensagens e eventos históricos; isso é um snapshot atual, não etapa no momento do evento. O filtro de anúncio define uma coorte de contatos: qualquer touch de anúncio resolvido para o mesmo lead, sem vínculo temporal ou causal com cada mensagem/evento. Essas limitações são mostradas junto aos filtros. O filtro de sinal aplica-se à métrica preço/oferta. A composição filtra somente os traces A07; os traces não se relacionam a mensagens, então não alteram métricas de conversa. A07 não persiste versão exata do prompt. Tokens e custo aparecem somente quando o trace contém uso do provider observado na geração final; totais exibem quantidade de amostras e execuções elegíveis, com estado parcial quando a cobertura é incompleta. Estimativas e receipts sintéticos não são mostrados como uso real. Nomes de objeção são convertidos a uma lista curta de categorias estáticas ou `Outra tag explícita`, para impedir que nomes livres de tags cheguem à tela. Somente eventos de aplicação de tag são contados.

O drill-down autenticado retorna IDs UUID e timestamps para abrir a conversa no Inbox; nunca retorna texto, nome, telefone, endereço, payload, token ou conteúdo livre. O navegador oferece paginação e usa abortamento/isolamento de tenant e filtro. Nenhuma mensagem é enviada, tag aplicada, classificação JEV executada ou evento comercial criado.

## Limitações conhecidas

- Não há classificações persistidas de perguntas ou objeções em nível de mensagem; `?` e marcadores de preço/oferta são sinais determinísticos documentados.
- Contatos são deduplicados por UUID de `CRMLead`; registros distintos que possam representar a mesma pessoa não são consolidados sem identidade canônica observada.
- Tags CRM de objeção dependem de nomenclatura explícita, e o histórico pode ser incompleto.
- A07 mantém traces agregados por tenant em Redis por 7 dias; custo só é exibido para origem real e custo efetivamente medido. Simulações são separadas.
- Não há correlação causal entre prompt, etapa, anúncio, resposta, satisfação ou conversão nesta entrega.
- A janela usa mensagens recentes em lote limitado e pode ficar parcial quando atingir o teto técnico.

## Rollback

Rollback de código: reverter somente o commit A08 para o predecessor `8ba952c1aee595d289a98f25999ce0734ff5003b`. Não há migration, alteração de schema, configuração obrigatória ou efeito externo. A feature pode também ser ocultada com `ATTENDANCE_QUALITY_ANALYTICS_ENABLED=false`.

## Validação da candidata (2026-10-14)

- Backend A08 focado: 20 testes passaram, incluindo RBAC, isolamento por empresa, filtros, denominadores, cobertura incompleta, deduplicação por UUID e evidência sem conteúdo de mensagem.
- Suíte backend: 1.411 passaram, 115 ignorados e 20 falharam; a comparação com o baseline limpo produziu exatamente os mesmos 20 IDs de falha, sem regressões novas nem falhas históricas resolvidas.
- Frontend: build e validação de artefatos passaram; ESLint terminou sem erros (5 avisos). As 12 suítes Node existentes reportaram 130 testes aprovados.
- Navegador com APIs sintéticas: desktop 1327×709 e celular 390×844 sem overflow horizontal; só houve GET interceptado, nenhum efeito comercial, uma raiz React e nenhuma exceção do navegador. O smoke das páginas existentes também passou com APIs interceptadas.
- `scripts/validate_protected_baselines.sh`: 14 módulos protegidos passaram. Não houve implantação e nenhum serviço de produção foi alterado.
- Revisões independentes de release e segurança: aprovadas. Permanece a limitação operacional de que até 100.001 projeções compactas e os itens de evidência intermediários são materializados por consulta; não foi feito teste de carga em banco de produção.

Capturas com dados inteiramente sintéticos ficam em `reports/a08-attendance-quality-20261014/`.
