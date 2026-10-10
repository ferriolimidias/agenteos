#!/usr/bin/env sh
# Recria somente o backend no modo oficial sem ações mutáveis de banco.
set -eu

ROOT_DIR=/root/agenteos
BUILD_BACKEND=false

if [ "${1:-}" = "--build" ] && [ "$#" -eq 1 ]; then
  BUILD_BACKEND=true
elif [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--build]" >&2
  exit 64
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "This administrative command must be run as root." >&2
  exit 77
fi
if [ "$(pwd -P)" != "$ROOT_DIR" ]; then
  echo "Run this command from /root/agenteos." >&2
  exit 64
fi
if [ ! -f docker-compose.yml ] || [ ! -f docker/backend-entrypoint.sh ] || [ ! -f scripts/verify_startup_schema.py ]; then
  echo "Required startup files are missing." >&2
  exit 66
fi

# A imagem é construída com o contexto completo. Não é seguro empacotar um
# worktree que já contenha mudanças fora deste procedimento administrativo.
if [ "$BUILD_BACKEND" = true ] && [ -n "$(git status --porcelain --untracked-files=all -- . ':!app/core/startup_database_mode.py' ':!app/api/main.py' ':!db/database.py' ':!db/manual_migrations.py' ':!docker/backend-entrypoint.sh' ':!init_db.py' ':!run_migrations.py' ':!scripts/restart_backend_without_db_actions.sh' ':!scripts/verify_startup_schema.py' ':!tests/test_startup_database_mode.py' ':!docs/startup-database-mode.md' ':!.env.example' ':!reports/startup-backend-sem-migrations')" ]; then
  echo "Refusing --build: worktree contains changes outside the approved startup path." >&2
  exit 65
fi

umask 077
OVERRIDE_FILE=$(mktemp "$ROOT_DIR/.startup-db-skip.XXXXXX.yml")
SCHEMA_BEFORE_FILE=$(mktemp "$ROOT_DIR/.startup-schema-before.XXXXXX")
SCHEMA_AFTER_FILE=$(mktemp "$ROOT_DIR/.startup-schema-after.XXXXXX")
cleanup() { rm -f "$OVERRIDE_FILE" "$SCHEMA_BEFORE_FILE" "$SCHEMA_AFTER_FILE"; }
trap cleanup EXIT HUP INT TERM
printf '%s\n' 'services:' '  backend:' '    environment:' '      STARTUP_DATABASE_MODE: skip' > "$OVERRIDE_FILE"

COMPOSE_ARGS="-f docker-compose.yml"
if [ -f docker-compose.override.yml ]; then
  COMPOSE_ARGS="$COMPOSE_ARGS -f docker-compose.override.yml"
fi
COMPOSE_ARGS="$COMPOSE_ARGS -f $OVERRIDE_FILE"

if [ "$BUILD_BACKEND" = true ]; then
  # shellcheck disable=SC2086
  docker compose $COMPOSE_ARGS build backend
fi

echo "Capturing schema before backend recreation."
CONTAINER_ID=$(docker compose $COMPOSE_ARGS ps -q backend)
if [ -z "$CONTAINER_ID" ]; then
  echo "Backend container is not running; cannot capture a safe before snapshot." >&2
  exit 69
fi

# The running image can predate this helper. Copying this non-secret,
# read-only file to /tmp avoids an inline command and is removed immediately.
docker cp scripts/verify_startup_schema.py "$CONTAINER_ID:/tmp/verify_startup_schema.py"
# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS exec -T backend python /tmp/verify_startup_schema.py > "$SCHEMA_BEFORE_FILE"
# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS exec -T backend rm -f /tmp/verify_startup_schema.py

if ! grep -qx 'migration_meta_etapa2_applied=false' "$SCHEMA_BEFORE_FILE"; then
  echo "Meta foundation migration is not pending before recreation; refusing restart." >&2
  exit 65
fi

echo "Recreating only backend in startup database mode skip."
# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS up -d --no-deps --force-recreate backend

echo "Waiting for backend health."
attempt=1
while [ "$attempt" -le 30 ]; do
  if docker compose $COMPOSE_ARGS ps --format '{{.Service}} {{.Health}}' backend | grep -qx 'backend healthy'; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
if ! docker compose $COMPOSE_ARGS ps --format '{{.Service}} {{.Health}}' backend | grep -qx 'backend healthy'; then
  echo "Backend did not become healthy; inspect sanitized logs and rollback manually." >&2
  exit 1
fi

# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS exec -T backend python scripts/verify_startup_schema.py > "$SCHEMA_AFTER_FILE"
if ! cmp -s "$SCHEMA_BEFORE_FILE" "$SCHEMA_AFTER_FILE"; then
  echo "Schema snapshot changed; rollback is required." >&2
  exit 1
fi
if ! grep -qx 'migration_meta_etapa2_applied=false' "$SCHEMA_AFTER_FILE"; then
  echo "Meta foundation migration was unexpectedly applied; rollback is required." >&2
  exit 1
fi

docker compose $COMPOSE_ARGS exec -T backend python - <<'PY'
import asyncio
import urllib.request
import websockets

for path in ("/", "/openapi.json"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status for {path}: {response.status}")

async def check_websocket():
    async with websockets.connect(
        "ws://127.0.0.1:8000/api/empresas/startup-check/ws", open_timeout=5
    ):
        return None

asyncio.run(check_websocket())
print("HTTP_ROOT_OK")
print("OPENAPI_OK")
print("WEBSOCKET_OK")
PY

if docker compose $COMPOSE_ARGS logs --no-color backend | grep -q 'Traceback'; then
  echo "Backend logs contain a traceback after recreation." >&2
  exit 1
fi
echo "Backend recreated in skip mode; schema snapshot unchanged."
