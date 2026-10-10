# AgentOS

Plataforma omnichannel multi-tenant: FastAPI/SQLAlchemy, React/Vite, PostgreSQL/pgvector, Redis, Evolution e integrações WhatsApp/Meta/Instagram.

Esta raiz contém a fonte atual da VPS e o [roadmap AgentOS Intelligence](docs/roadmap/agentos-intelligence/README.md). O histórico Git foi iniciado novamente na A00; Issues de planejamento foram preservadas. Leia [AGENTS.md](AGENTS.md) antes de alterar o projeto.

## Validação e execução

- Backend: Python 3.11, `requirements.txt`; pytest/pytest-asyncio são ferramentas de desenvolvimento.
- Frontend: Node 22, `npm ci` e `npm run build`, em `frontend/` e `apps/anamnese-leonardo/`.
- Docker: `Dockerfile` e `frontend/Dockerfile`, com contexto na raiz.
- Ferramentas de seed/teste exigem `AGENTEOS_SEED_ADMIN_PASSWORD` ou `AGENTEOS_TEST_TENANT_PASSWORD` no ambiente, sem senha padrão e sem imprimir credenciais.
- Configuração: copie e configure `.env.example` localmente; segredos nunca integram o repositório.
- `STARTUP_DATABASE_MODE`: `init`, `migrate`, `skip`. A composição usa `skip`, com schema previamente validado. Workers e ações externas têm controles independentes.
- `python run_migrations.py` exige modo `init` ou `migrate`. O runner registra filename/checksum/status em `agentos_schema_migrations`, com lock e transação única; recusa modificações de SQL já registrado. Na adoção de schema existente, reaplica SQL idempotente e reconhece somente a limpeza legada já superada por schema completo verificado. Não presume histórico por data.

## Limites conhecidos da A00

A suíte integral mantém 20 falhas preexistentes, classificadas no relatório; não está integralmente verde. O bootstrap de banco vazio tem uma limitação preexistente: uma FK composta LeoFT exige uma UNIQUE ausente no ORM antes das migrations. A validação da A00 usa PostgreSQL descartável com schema atual, sem dados de clientes. Não use `init` em produção sem resolver e validar essa limitação.

A correção A00 do splitter/runner foi validada isoladamente e publicada como fonte; não foi aplicada aos containers da VPS. Migrations históricas não foram reescritas. O frontend recompilado elimina mapas/assets antigos deixados por overlays e usa `/api` relativo ao host; diferenças de empacotamento constam do relatório. A Issue #16 permanece separada.
