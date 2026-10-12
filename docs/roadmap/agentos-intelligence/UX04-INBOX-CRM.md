# UX04 — experiência operacional Inbox, CRM e Jornada

Baseline UX03 e74d9b9332e0bd4cbb39dcd0721a386ea63ae1d1. Contrato: [UX04](../../releases/UX04-inbox-crm.md). Frontend somente; Ferrioli UX01 e navegação UX02 preservados. A Jornada A04 continua somente leitura.

## Inventário de ações e dependências

| Área / ação | Contrato existente preservado | Apresentação UX04 |
| --- | --- | --- |
| Inbox `/painel/chat`, `/painel/inbox`, `/painel/conversas` | Mesma página e deep link de lead; tenant ativo via auth existente | Lista, busca, status e grupos com hierarquia; teclado e estado selecionado |
| Conversas e mensagens | GET `/empresas/:id/inbox`, `/inbox/:telefone`, `/foto`, `/ai-state` | Loading, erro/retry e vazio separados; viewport e composer |
| Mensagem / mídia / áudio / retry | POST `/inbox/:telefone/send` e endpoints multipart existentes | Handlers, Enter/Shift+Enter, ordem e payloads intactos |
| Realtime | WebSocket tenant autenticado, protocolo inbox.v1, ping/pong, backoff e deduplicação | Observação visual da conexão; nenhum reconnect/heartbeat alterado |
| IA / status atendimento / excluir | PATCH `/crm/:lead/toggle-ia`, PUT/DELETE `/crm/leads/:lead` | Ações com nome/estado busy, mesma confirmação de excluir |
| Transferência manual | POST `/leads/:lead/transferir_manual` com destino_id | Mesmo painel e callback |
| Tags oficiais e grupos | GET `/crm/tags/oficiais`, `/crm/tags/grupos`; PUT lead com IDs normalizados | Seleção e remoção manuais existentes; nenhuma ação ao renderizar/hover |
| Jornada comercial A04 | GET `/inbox/leads/:lead/journey` com filtros/cursor; sem writes | Contexto legível, cobertura/UNKNOWN/evidência e paginação preservados |
| Funil A05 → Jornada → retorno | Router state funnelContext + URLs existentes | Voltar mantém coorte/filtros; nenhum estado comercial novo |
| CRM Kanban `/painel/crm` | GET `/crm` com etapas reais ordenadas por ordem/ID | Colunas/cartões/quantidade, scroll próprio e drop target |
| CRM mover etapa | PUT `/crm/leads/:lead` com etapa_id; otimista e rollback existentes | Estado de gravação/drop visual, sem alterar transição |
| Editar contato | PUT lead com nome_contato/telefone_contato/historico_resumo/valor_conversao | Seções, rótulos, teclado/Escape/foco, footer e scroll |
| Importar leads | POST multipart `/leads/importar` com file/tags_iniciais | Fluxo e seleção existentes; nenhum upload real em testes |
| Excluir lead | DELETE lead após window.confirm existente | Operação preservada; cancelamento testado |

Tags são salvas pelo gesto manual imediatamente na baseline. Salvar os dados do contato não confirma nem reaplica tags. Valor da Venda continua informação comercial do CRM: não é receita aprovada ou Purchase. Falhas que os callbacks antigos absorvem não foram convertidas em novas regras de escrita nesta release.

## Verificação e limites

Navegadores usam fixtures sintéticas e interceptam todas as APIs/sockets. Nenhuma mensagem, tag, upload, campanha, pagamento ou provider JEV real. Capturas antes/candidata/público desktop/notebook/mobile, testes DOM de scroll/composer/contexto/WS/DnD/modal e regressões UX01–UX03/A01/A04/A05/A06. Root React, permissões e backend/DB/infra permanecem idênticos. Relatório, imagens, rollback UX03 e evidências sanitizadas em reports/ux04-inbox-crm-20261012/RELATORIO.md. REAL_VALIDATED aplica-se apenas a assets/navegação pública com APIs interceptadas.
