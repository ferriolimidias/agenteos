# A00 | Substituir integralmente GitHub obsoleto pelo AgentOS atual, sem backups

Issue: https://github.com/ferriolimidias/agenteos/issues/1
Estado no inventário: OPEN

## Prioridade A00 — substituir integralmente o projeto antigo do GitHub pelo AgentOS atual da VPS

**Autorização expressa do proprietário em 10/10/2026:** eliminar o código antigo do repositório remoto, sem qualquer cópia de segurança, branch `archive`, tag, bundle ou espelho do projeto antigo. Publicar a fonte atual e funcional do AgentOS instalado na VPS, com novo histórico Git sem pais dos commits antigos. Integrar ao novo código somente a documentação do roadmap e as Issues atuais.

**Nenhuma cópia de segurança do repositório antigo deve ser criada.** Não conservar `master/main/snapshot/planning` como refs de arquivo após a troca. Não afetar a produção na VPS.

### Fontes verificadas no GitHub (obsoletas; não são fonte de código)

- `master` (default antigo): `697ed2e9249f21389b02b01363033b88207d71a8`.
- `main`: `7d3cc08eec970a777907cdb8575e99e9ca3dc531`; continha `.env` rastreado, não publicar/imprimir segredos.
- `snapshot/pre-funil-audits-20260831`: `b945c7213910c6b53a2d1c9147003bebc832b241`.
- `planning/agentos-intelligence-20261010`: documentação temporária a integrar na nova raiz; remover após migração.
- Produção **reportada**, a reconfirmar: `agenteos-backend:p1c4-final-validation-20261010-01` e `agenteos-frontend:p1b2-marketing-token-scope-20261008-01`, com worktree sujo e overlays cumulativos.

### Procedimento

**G0 — inventário SOMENTE LEITURA:** ler `/root/agenteos/AGENTS.md` e relatórios P0→P1C4; inspecionar Docker backend/frontend ativos, assets servidos, migrações, testes e código-fonte que originou os overlays. Não assumir que o worktree corresponde ao runtime.

**G1 — staging da NOVA fonte:** preparar em diretório de trabalho temporário, fora do worktree sujo, a árvore limpa do AgentOS *atual*, contendo backend, frontend, scripts, migrations, testes, P0/P1A/P1B/P1C4 e roadmap. Isso é material necessário ao novo commit, não preservação do projeto antigo. Excluir `.env`, segredos, logs, dumps, mídias privadas e dados/PII dos tenants. Secret scan obrigatório.

**G2 — gates:** build e imports reproduzíveis, PostgreSQL descartável, testes protegidos, confronto de hashes/assets e endpoint/DB da candidata com a produção; classificar 20 falhas preexistentes da suíte integral sem inventar “PASS”. Se divergência relevante não explicada, **não publicar como versão oficial**, relatar bloqueio.

**G3 — substituição REMOTA:** com gates aprovados, iniciar novo Git de raiz sem ancestrais antigos, commitar somente versão atual + docs e publicar em `master` usando verificação de SHA/ref, em operação controlada. Verificar HEAD/arquivos, remover branches antigas `main`, `snapshot/pre-funil-audits-20260831` e `planning/agentos-intelligence-20261010` após copiar os documentos, sem criar nenhum ref de backup. Preservar as Issues (planejamento, não o código antigo). Não mexer na VPS em G3.

**História pública:** apagar refs antigos não garante expurgo imediato de caches/forks do GitHub. Se o `.env` antigo continha credenciais reais, reportar necessidade de revogação/rotação, sem publicá-las.

### Aceite

- [ ] Fonte nova reproduz runtime atual backend + frontend.
- [ ] Todos os módulos atuais, migrações e testes incluídos.
- [ ] Staging novo tem histórico Git independente dos commits antigos.
- [ ] `REPOSITORY_PUBLIC_SECRET_SCAN=PASS`.
- [ ] Testes/baselines e paridade candidata/produção validados.
- [ ] `OLD_REPO_BACKUP_CREATED=NÃO`.
- [ ] `OLD_REPO_ARCHIVE_REFS=0`.
- [ ] Branches antigas removidas após integração da documentação.
- [ ] Branch principal/HEAD remoto atualizados e conferidos.
- [ ] Imagens/DB/workers da VPS inalterados.
- [ ] Relatório e novo SHA adicionados nesta issue; depois continuar roadmap.

Runbook atualizado: https://github.com/ferriolimidias/agenteos/blob/master/docs/roadmap/agentos-intelligence/MIGRACAO-VPS-PARA-GITHUB.md

**Não autorizado nesta issue:** Meta Graph WRITE, envios WhatsApp, alteração de Ads, ativação canônica LIVE, migrações em produção ou reinício de serviços.

