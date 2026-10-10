# A00 — Substituição INTEGRAL do GitHub obsoleto pelo AgentOS atual da VPS

## Autorização e escopo

**O proprietário autorizou expressamente em 10/10/2026 descartar integralmente o projeto antigo do GitHub, sem criar qualquer cópia de segurança dele.** Não criar branch `archive/*`, tag de arquivo, espelho, dump de Git, bundle, clone de preservação ou outro mecanismo para conservar a versão antiga. Não manter os commits antigos na nova história oficial.

A fonte da versão nova é a implantação atual **real** na VPS AgentOS, não `master`, `main` nem `snapshot/pre-funil-audits-20260831`. A migração é uma operação **GitHub-only**: não substitui containers, não altera PostgreSQL, não envia WhatsApp/Meta nem mexe na configuração da VPS.

Repositório público: `ferriolimidias/agenteos`.

Estado dos refs antigos em 10/10/2026 (somente para identificação, NÃO para arquivar):
- `master`: `697ed2e9249f21389b02b01363033b88207d71a8` (default antigo)
- `main`: `7d3cc08eec970a777907cdb8575e99e9ca3dc531` (com arquivo `.env` rastreado; **não expor valores**)
- `snapshot/pre-funil-audits-20260831`: `b945c7213910c6b53a2d1c9147003bebc832b241`
- `planning/agentos-intelligence-20261010`: documentação temporária, também derivada da árvore antiga. **Incorporar documentação na nova raiz antes de removê-la.**

Último runtime REPORTADO: backend `agenteos-backend:p1c4-final-validation-20261010-01`; frontend `agenteos-frontend:p1b2-marketing-token-scope-20261008-01`. Reconfirmar por Docker inspect. O worktree foi reportado sujo, com overlays acumulados não comitados.

## G0 — Inventário da realidade na VPS, SEM modificar produção

1. Ler `/root/agenteos/AGENTS.md` e relatórios de P0 até P1C4; comparar runtime com imagens Docker, entrypoints, mounts, build manifests e frontend realmente servido.
2. Verificar origens e hashes dos arquivos de **backend e frontend**; o worktree pode não reproduzir o que os containers executam.
3. Identificar source, scripts de build, dependências, testes, migrations até P1C4, frontend, assets públicos, configurações exemplificativas e documentação.
4. Delimitar componentes do AgentOS; não capturar fontes ou dados de outros projetos da VPS.
5. Registrar baseline de API/PostgreSQL/Redis/workers e flags de envio, sem mudar serviços, bancos ou filas.

## G1 — Preparar apenas a fonte ATUAL para publicação

1. Criar um **diretório de trabalho temporário e limpo** fora de `/root/agenteos` para montar o código atual; isso é staging para construir o **novo Git**, não cópia do projeto antigo. Apagar o staging temporário depois de confirmar publicação, se já não for necessário.
2. Reconstruir a fonte atual do backend/frontend a partir do runtime validado mais arquivos de origem, migrations e testes necessários no workspace; conferir hash/conteúdo. Não confundir arquivos no contêiner com fonte completa.
3. Incluir P0 Auth, inbound Meta, P1 workers, Attribution Ledger, Commercial Events, Ads Insights/readiness, Dataset/WABA, Shadow, outbox e instrumentação do sender legado P1C4, além de todos os recursos anteriores do AgentOS.
4. Excluir rigidamente `.env`, tokens, segredos, arquivos privados, dados de clientes, DB dumps, logs, mídias privadas, caches, `node_modules`, chaves, credenciais, arquivos binários sensíveis e volumes de produção. O repositório é **público**.
5. Executar scanner de secrets e inspecionar toda a lista de arquivos staged. `.env.example` deve ser sanitizado.
6. Importar para a **nova** árvore os documentos de `docs/roadmap/agentos-intelligence/` e as especificações das Issues #1–#15. Não copiar o restante do código remoto antigo por conveniência.

## G2 — Validar antes de publicar o novo conteúdo

1. Preparar projeto Git novo a partir desse staging (pode usar `git init` e primeiro commit sem parents). **Não criar histórico novo que descenda dos commits obsoletos.**
2. Verificar build frontend e import/backend contra código publicado no staging; candidato isolado deve reproduzir a aplicação que já roda na VPS, com diferenças documentadas.
3. Rodar testes focados P0, inbound Meta, workers, P1A–P1C4 e multi-tenant; validar migrations em PostgreSQL descartável, nunca executar `init_db` em produção.
4. Comparar suíte integral ao baseline reportado P1C4 (1.061 passed, 20 failed preexistentes, 10 skipped). Não declarar suite integral verde se não estiver.
5. Verificar hashes, interfaces, migrations, assets e semântica de runtime backend/frontend; secret scan final.
6. Se os testes/paridade não passarem, **não substituir a branch principal**; relatar a causa, sem criar backups do projeto antigo ou alterar produção.

## G3 — Substituir integralmente o remoto GitHub

1. Usar somente a **nova raiz Git**, com backend, frontend, migrations, testes e roadmap atuais. Não preservar histórico/arquivos remotos antigos.
2. Após G0–G2 aprovados, fazer push do novo histórico para `master` (branch default observada), usando lease/expected SHA para não sobrescrever mudanças feitas por outros durante o trabalho. **Sem backups/arquivos/refs antigos.**
3. Verificar que `master` contém somente a fonte atual validada e a documentação/planejamento; confirmar branch default.
4. Remover branches obsoletas (`main`, `snapshot/pre-funil-audits-20260831`, `planning/agentos-intelligence-20261010`) após incorporar documentos ao novo `master`. Não deixar branches de arquivo ou tags do código antigo. Conferir refs remanescentes; não usar `git push --mirror`.
5. Preservar as Issues #1–#15 (metadados de planejamento, não código), atualizando links de documentação para `master`. Isso não requer manter branches antigas.
6. **Atenção à história pública:** remover refs e sobrescrever branch não garante apagar imediatamente objetos antigos do cache do GitHub, de forks ou cópias de terceiros. Se o `.env` antigo tiver segredos válidos, reportar necessidade de revogação/rotação, mesmo que nenhum arquivo antigo permaneça no novo HEAD. Não reproduzir segredos.
7. Não fazer deploy nem alterar imagens da VPS durante G3.

## Aceite — sem cópia de segurança do repositório antigo

- `GITHUB_ROOT_COMMIT_HAS_NO_OLD_PARENTS=SIM`.
- `GITHUB_REPO_CONTENT_REPLACED=SIM`: código atual da VPS substituiu o antigo.
- `OLD_REPO_BACKUP_CREATED=NÃO`.
- `OLD_REPO_ARCHIVE_REFS=0`.
- `OLD_BRANCHES_REMOVED=SIM` após migrar o roadmap.
- `GITHUB_SOURCE_MATCHES_ACTIVE_RUNTIME=SIM` ou diferenças justificadas e validadas.
- `REPOSITORY_PUBLIC_SECRET_SCAN=PASS`.
- `BACKEND_AND_FRONTEND_BUILD_PASS=SIM`.
- `MIGRATIONS_AND_TESTS_INCLUDED=SIM`.
- `PRODUCTION_CHANGED=NÃO` (imagens, banco e workers idênticos ao BEFORE).
- `DEFAULT_BRANCH_REPLACED=SIM`, SHA do novo commit publicado.
- `ROADMAP_PRESERVED=SIM` **apenas** os documentos/Issues atuais, não o projeto antigo.

Atualizar [issue #1](https://github.com/ferriolimidias/agenteos/issues/1) com o relatório e o SHA da nova raiz. Não declarar finalizado antes de verificar o GitHub remoto.
