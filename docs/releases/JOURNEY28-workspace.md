# Jornada Comercial — leitura dedicada e horário de Brasília

Issue #28, baseline `10fa71026e54e9923816f206963b275aa8864553`. Mudança exclusivamente frontend.

O botão Jornada comercial abre um dialog nativo amplo no desktop/notebook e uma tela de leitura no tablet/celular. A conversa continua montada, com seu histórico, rascunho e WebSocket. O fundo fica inerte durante a leitura; Escape ou Voltar à conversa fecham a Jornada e restauram o foco. O scroll externo é bloqueado e restaurado ao fechar.

A linha do tempo é o conteúdo principal. Resumo do contato e cobertura das fontes têm seções colapsáveis separadas; telas menores permitem ir diretamente a essas seções. Categorias, cursores, paginação, ordem/IDs, evidências e a distinção entre desconhecido e não ocorrência explícita mantêm o contrato A04.

Todos os horários de apresentação usam `America/Sao_Paulo`, inclusive datas históricas com horário de verão segundo Intl. O backend permanece UTC; timestamps originais continuam no atributo `datetime`, sem alteração de dados ou ordenação. UTC aparece somente na evidência técnica identificada. Não há seletor de fuso nem configuração nova de tenant.

Validação usa APIs e sockets interceptados e contatos inventados: 1327×709, desktop1440, notebook1280, tablet768, mobile390; 45 acontecimentos, filtros, paginação com sobreposição, datas históricas/limites de dia com navegador em Tóquio, detalhes, foco/teclado, rascunho, troca de contato/tenant, atualizações WebSocket simuladas, erro/vazio/retry e deep links. Os smokes A04/UX04/A05 foram adaptados somente ao novo fluxo modal e ao horário de Brasília; composer deve permanecer montado e utilizável ao retornar, enquanto o fundo modal fica inerte.

Backend, banco, A07/JEV, CRM, regras comerciais, transportes, permissões, dependências, nginx e Anamnese preservados. Release exige testes frontend completos, 14 baselines protegidas, revisão independente e rollback frontend A07 disponível. Nenhum envio, aplicação de tags, campanha, conversão ou chamada real de provider é permitido nos testes.
