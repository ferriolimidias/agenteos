# Contrato de inicialização do backend

`STARTUP_DATABASE_MODE` é uma variável administrativa do backend. O valor
ausente preserva o comportamento histórico (`init`). Os únicos valores
aceitos são `init`, `migrate` e `skip`; qualquer outro valor encerra o startup
antes de ações de banco.

- `init`: aguarda o banco, executa `init_db.py` e `run_migrations.py`.
- `migrate`: aguarda o banco e executa somente `run_migrations.py`.
- `skip`: não aguarda nem acessa o banco no entrypoint, não executa
  `init_db.py`, `run_migrations.py`, seed ou DDL, e inicia a aplicação.

O modo `skip` impede mutações de schema no lifespan, mas não controla workers.
`FOLLOWUP_WORKER_ENABLED` e `CAMPAIGN_WORKER_ENABLED` são os gates exclusivos
de cada worker. Workers habilitados fazem readiness somente-leitura de Postgres
e schema, sem executar migrations; falhas deixam o worker `DEGRADED` com retry
e backoff sem impedir a API. Redis é telemetria/otimização de lock e não é a
fonte durável de jobs. O servidor é iniciado com `exec`, preservando o
encaminhamento de sinais e o healthcheck do container.
