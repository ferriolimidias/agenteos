# Inbox #29 — incidente de envio e histórico vazio

Baseline de diagnóstico: `32ae27b0408f9c260f3907e71d3ac94e6f8126c5`, imagem `agenteos-frontend:inbox29-realtime-20261013-05`.

## Evidência e limite do diagnóstico

Na imagem original, o fluxo GET com 36 mensagens → envio otimista → POST de sucesso → confirmação WebSocket com o mesmo UUID de lead e outro endereço telefônico → GET vazio/404 reproduz o histórico persistentemente vazio. O cabeçalho e o composer permanecem montados. Atualizar a página restaura 37 mensagens. A coleta externa dos commits React, DOM, frames e caches usa apenas dados inventados e intercepta todas as APIs e WebSockets; não modifica os assets da imagem diagnosticada.

A operação que atribui o vazio é `setMessagesState(messagesCacheRef.current.get(key) || [])` no efeito de seleção. A chave antiga inclui tenant, ID e telefone. Uma alteração válida do telefone do mesmo lead cria outra chave sem cache. A confirmação havia atualizado o cache anterior. Não é uma exclusão de dados persistidos.

Essa cadeia reproduz o sintoma informado pelo operador. A correlação do alias telefônico com o evento operacional específico ainda depende de observação sanitizada e confirmação no navegador do operador. Os testes anteriores mantinham a identidade fixa e foram insuficientes; não comprovavam recuperação real.

## Correção isolada

O cache, os rascunhos e a posição de leitura usam tenant + ID de lead, de acordo com a propriedade do histórico no backend (`Mensagens.lead_id`). UUIDs equivalentes diferindo apenas em caixa representam o mesmo lead; IDs opacos e tenants distintos permanecem separados. O telefone continua sendo o endereço usado nas APIs, sem alterar contratos ou regras de envio.

Atualizações de endereço cancelam leituras em andamento e descartam resultados de endereços antigos, sem trocar ou limpar o histórico do contato. Uma conversa já carregada não executa outro GET de histórico por uma simples atualização de endereço; seleção real e reconexão continuam revalidando. Uma carga inicial ou reconciliação ainda em andamento é retomada no endereço atualizado, preservando as mensagens em cache. O GET vazio legítimo de um contato novo continua exibindo o estado vazio.

Após envio de texto, mídia ou retry, o Inbox não busca a lista inteira. Ele revalida somente o estado efetivo de IA da conversa com uma leitura nova, sem reutilizar uma consulta iniciada antes do POST; o evento `STATUS_IA_CHANGED` também atualiza a indicação visual a partir do booleano confirmado pelo servidor. POSTs, mensagens otimistas, deduplicação, falhas e regras comerciais permanecem existentes.

## Validação e publicação

O teste de envio cobre POST 200/202, confirmação antes/depois do HTTP, alias de telefone, eventos parciais, UUID, duplicatas, 500, offline e timeout. Preserva os IDs de todas as mensagens anteriores e mede DOM/cache/frame por etapa. Nenhuma mensagem comercial é enviada pelos testes.

Antes de publicar: testes frontend, regressões Inbox/Jornada/UX01–05/CRM23/A01/A04/A05/A06/A07, 14 baselines protegidas, imagem frontend-only, revisão independente e rollback Journey28 disponível. Os resultados e digests congelados ficam no relatório do incidente, não são antecipados neste documento.

## Rollback e validação operacional

O rollback emergencial restaurou somente o frontend Journey28 (`sha256:05998dfa2716dab69ee8f9d01a95ad5dc6859f373eef25d3fc7d33bb256049b6`). Backend A07, banco, Redis, Evolution, env, volumes, flags e integrações foram preservados. A imagem retirada permanece disponível para investigação.

A saúde HTTP e baselines comprovam recuperação técnica. O operador deve confirmar o histórico no navegador real após carregar o frontend restaurado, guardando previamente qualquer rascunho. Nenhum novo deploy ocorre antes de informar diagnóstico/rollback e confirmar essa recuperação. A Issue #29 permanece aberta até confirmação operacional do histórico; mocks não autorizam encerramento. Não iniciar A08 enquanto o Inbox não estiver estabilizado.
