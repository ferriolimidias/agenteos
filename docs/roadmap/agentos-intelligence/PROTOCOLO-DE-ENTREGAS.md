# Protocolo de execução por issue — AgentOS

**Regra de início:** nenhuma codificação baseada em `master` ou `main` obsoletos. Executar primeiro A00, confirmar o GitHub como fonte fiel da VPS. Até lá, tarefas de observação read-only não devem assumir que o repo reflete a produção.

## Para toda issue

1. **Revisar** este plano, GitHub issue e `AGENTS.md` da release atual; inventariar o recurso existente, opções de reuso e dependências verdadeiras.
2. **Baseline:** registrar `GIT_HEAD`, `GIT_DIRTY`, imagens Docker backend/frontend, migrations, health, workers, flags Meta, filas, route auth, runtime de tenant.
3. **Contrato fechado:** declarar comportamento entregue no UI/API, feature flag padrão, API/DB, métricas, migrações, rollback e estados `unknown`; nenhum recurso fantasma aguardando issue futura.
4. **Patch mínimo:** entregar somente escopo da issue, sem quebrar módulos anteriores. Branch/PR revisável. Nunca `git reset --hard`, `git clean` ou build indiscriminado do worktree sujo.
5. **Gates:** PostgreSQL isolado para migration/schema/concurrency; unit/focused; tenant isolation 401/403/200; Meta inbound; workers; tags→Commercial Events; historical no-send; baselines no workspace e candidate; rodar suíte integral e classificar falhas preexistentes.
6. **Deployment:** aplicar migration versionada, checar candidato cumulativo/reproduzível, preservar DNS alias `backend`, PG/Redis/Evolution, front e rollback. Não assumir que um teste mock provou entrega Meta real.
7. **Prova:** funcionalidade usável com caso real (quando disponível) ou dados de teste claramente indicados; dashboard mostra cobertura/inexistência de dados sem zerar ficticiamente.
8. **Fechamento:** atualizar a issue com SHA, imagem, checks, migrações, relatório, capturas sanitizadas, riscos e próxima tarefa. Só marcar concluído quando feature em produção/validada conforme escopo.
9. **Externo:** nenhum Graph POST, envios WhatsApp, campanha, mutação de credencial, LIVE do sender, gasto ou otimização automática de anúncio sem autorização específica para ação e escopo.

## Estado padronizado das issues

`PLANNED` → `IN_PROGRESS` → `TESTED` → `DEPLOYED` → `REAL_VALIDATED` → `DONE`.

Exceções claras: `BLOCKED`, `PARTIAL`, `DEPLOYED_NOT_REAL_VALIDATED`. Evitar `DONE` quando apenas uma candidata foi criada.

## Checklist de continuidade entre chats/sessões Codex

- Ler `README.md`, `ESTADO-2026-10-10.md`, esta página e a issue selecionada.
- Confirmar qual parte do plano já foi aplicada usando as imagens e relatórios; não repetir auditorias/implementações sem evidência.
- Atualizar a documentação de baseline quando o estado mudar; manter `GitHub ↔ VPS` rastreável.
- Não confundir plano e código implantado; não colocar secrets/PII no GitHub público.
