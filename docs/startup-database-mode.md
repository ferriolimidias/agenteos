# Startup de backend com e sem ações de banco

`STARTUP_DATABASE_MODE` é uma configuração administrativa exclusiva do ambiente
do backend. Os valores aceitos são `init`, `migrate` e `skip`. Valor ausente
preserva o comportamento histórico, equivalente a `init`. Qualquer valor
presente e inválido encerra o startup antes das ações de banco.

## Deploy normal com migrations aprovadas

Use o modo `init` somente em janela planejada, com migration revisada,
backup validado e rollback aprovado. O entrypoint preserva o fluxo atual de
inicialização e migrations. A variável não é configurada por HTTP, frontend,
tenant ou banco.

O modo `migrate` aguarda o banco e executa somente `run_migrations.py`.

## Restart sem alterações de banco

Para manutenção, recuperação operacional ou futura rotação de segredo, execute
como root e no diretório `/root/agenteos`:

```sh
scripts/restart_backend_without_db_actions.sh [--build]
```

O script cria um override temporário protegido que define
`STARTUP_DATABASE_MODE=skip` somente no serviço backend, preserva o override
Compose padrão se existir, usa `--no-deps` e recria apenas o backend. Antes e
depois, ele coleta uma fotografia somente-leitura do schema; qualquer diferença
interrompe o procedimento para rollback manual. Ele não executa `docker compose
down` e não aceita comandos arbitrários.

No modo `skip`, o entrypoint não chama `init_db.py` nem `run_migrations.py` e o
lifespan não executa mutadores de schema. Os workers não são controlados por
esse modo: cada um segue seu próprio `*_WORKER_ENABLED`. Um worker habilitado
inicia e verifica Postgres e as tabelas necessárias com consultas somente de
leitura; se a dependência estiver indisponível, fica `DEGRADED` e tenta de novo
com backoff, sem derrubar a API ou iniciar migrations. A checagem de
compatibilidade de schema continua somente-leitura.

## Valor inválido ou ausência

- Ausente: `init` (retrocompatível).
- `init`: inicializa o schema e aplica as migrations previstas no entrypoint.
- `migrate`: aplica somente migrations no entrypoint.
- `skip`: bloqueia ações mutáveis de banco no startup, sem bloquear workers habilitados.
- Qualquer outro valor, inclusive vazio: erro claro antes de tarefas de banco.

`FOLLOWUP_WORKER_ENABLED` e `CAMPAIGN_WORKER_ENABLED` são independentes do modo
de banco. O modo não deve ser exposto por API, painel, banco ou configuração de
tenant.
