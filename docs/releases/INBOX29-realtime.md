# Inbox29 — contexto contínuo em realtime

Baseline:36997fd9b6b6a5b3d950fcb709a307c2e500db50. Frontend-only.

Reprodução sintética antes de corrigir: evento parcial sem nome/telefone sobrescrevia identidade em memória, refetch integral restaurava telefone e disparava efeito do histórico. GET histórico503 apagava dados. RAF/MutationObserver capturaram19framesheader/48frameshistoryblank,1refetch e1GET extra; nós DOM permaneceram iguais. Não se afirma trace de causa de conversa real; sintoma operacional foi reproduzido com o contrato atual.

Contatos conhecidos agora recebem patch incremental idempotente e mensagens são mescladas por ID/external ID. Sem lista inteira por mensagem; identidade/estado/tags ausentes não apagam valores. IDs explícitos prevalecem sobre telefone no matching. Reações/edições/status e confirmação otimista preservados. Reconciliação de contatos desconhecidos tem debounce e não apaga estado válido.

GETs de lista/histórico/IA usam single-flight, AbortController, escopo de tenant+contato+telefone e identidade da requisição. Cache de histórico/rascunho/posição é local ao Inbox e é eliminado ao mudar tenant. Respostas atrasadas e erros não limpam dados válidos. Atualizações de lead protegem somente campos efetivamente alterados durante a requisição. Estado IA tem revisão separada; mensagens/fotos não invalidam seu GET.

Snapshots HTTP sem versão temporal não provam novidade sobre edits/reactions vistos por WebSocket. Conservadoramente, patches observados são preservados nas revalidações; HTTP preenche lacunas e pode avançar status de entrega monotônico. Não há protocolo servidor novo. Uma seleção real usa cache específico ou placeholder inicial, nunca histórico de outro contato.

Scroll incremental ocorre somente perto do fim; reações/status e leitura antiga preservam posição. Envio próprio mantém o fluxo anterior de scroll; respostas de envio são vinculadas à conversa que iniciou a operação. Heartbeat/backoff/permissões e transporte permanecem iguais; reconexão explícita reconcilia gaps em background.

Verificação: smoke-inbox-realtime.cjs usa APIs/sockets sintéticos interceptados; frame counters, bursts10/50, duplicatas, inbound/outbound/mídia/update/reação, slowHTTP/stale snapshots, offline503/retry/reconnect, optimistic confirmation, contato/tenant/rascunho/foco/scroll/root único. Cinco viewports1327x709/1440x900/1280x800/768x1024/390x844. Novos testes puros e testes existentes exercitam o helper real. Baselines protegidas14 intactas. Rollback frontend Journey28 preservado; backendA07 e infra não mudam.
