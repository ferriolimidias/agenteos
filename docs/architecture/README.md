# Arquitetura de produto AgentOS — fonte de referência

**Decisões do proprietário confirmadas em 17/10/2026.**

## Leitura obrigatória para desenvolver/refatorar UX e limpar legados

1. [ARQUITETURA-UX-ADMIN-TENANT-20261017.md](ARQUITETURA-UX-ADMIN-TENANT-20261017.md) — **arquitetura-alvo de navegação, papel do cliente versus Super Admin, rotas e autorização**.
2. [PLANO-SANEAMENTO-E-LEGADOS-20261017.md](PLANO-SANEAMENTO-E-LEGADOS-20261017.md) — **ordem executiva, inventário inicial de legados, guardas de remoção, testes e rollback**.
3. [Issue #34](https://github.com/ferriolimidias/agenteos/issues/34) — execução por lotes da reorganização e saneamento; a decisão é **reorganizar antes de remover**.
4. [Issue #33](https://github.com/ferriolimidias/agenteos/issues/33) — credenciais Meta por tenant, JEV/CRM e conversões canônicas reais; coordenação obrigatória ao mexer nos módulos.

## Contrato resumido — não renegociar por impulso de implementação

### Cliente
- O usuário cliente entra diretamente em **Conversas / Inbox**; `/painel` deve encaminhar a `/painel/inbox`. Não romper links antigos nem sessão do WebSocket.
- Menu leigo: **Conversas | Clientes | Agenda | Campanhas | Resultados | Configurações**.
- Dashboard/indicadores ficam em Resultados. Follow-ups, templates e públicos aparecem dentro de Campanhas; detalhes técnicos não precisam de item próprio.
- **Prompts, experimentos A11, versões de modelos, JEV SHADOW, tokens e diagnósticos técnicos NÃO pertencem ao menu do cliente.** Ocultar a navegação e **proteger a API**, inclusive de `admin_empresa`.
- Ações de negócio continuam acessíveis por função/papel; não retirar a autonomia de atender, consultar CRM, agendar e acompanhar anúncios/resultados.

### Super Admin Ferrioli
- **Nível plataforma**: Empresas; Operação e saúde; Integrações globais; Segurança e auditoria; Configurações.
- **Nível empresa**: Visão geral; Atendimento e IA (incl. prompts e JEV); CRM/regras; Canais e credenciais; Campanhas/Meta; Pagamentos; Usuários; Diagnósticos.
- Tokens Meta Marketing/system-user, acesso Facebook/Páginas, Instagram e Dataset/CAPI sob controle da Ferrioli por empresa, validados e armazenados de forma segura; provider WhatsApp/Embedded Signup separado. Instagram Login e Facebook Login/Page token têm contratos diferentes. Sem atribuir escrita com base em GET/escopo meramente concedido.

### Saneamento
- **Nenhuma deleção de código pelo nome "legacy" ou "shadow".** Mapear importadores, tarefas, rotas, consumidores externos, dados em DB, segredos, contratos e rollback. Remover só quando `REMOVE_CONFIRMED`.
- Evolution continua operacional; história comercial e de entregas Meta pode impedir remoção imediata.
- Não apagar migrations históricas, evidências de conversões, tabelas, catálogos ou `/srv/leoft-media`; não iniciar campanhas Meta nem reprocessar eventos LIVE na refatoração.
- Implementar por PRs pequenos; builds/testes e 14 baselines; deploy restrito aos serviços afetados, com rollback. Não confundir plano versionado com funcionalidade implantada.

## Situação atual
- **PLANEJADO:** documentos de arquitetura e plano de transição.
- **NÃO EXECUTADO:** novos menus, alteração de homepage, API RBAC adicional, formulário manual de token Marketing, exclusões de legados, migração de dados ou deploy.
- Cada Issue/PR de execução deve citar os documentos e atualizar status `IMPLEMENTED/TESTED/DEPLOYED/OPERATOR_VERIFIED` separadamente.
