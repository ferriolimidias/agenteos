#!/usr/bin/env sh
set -eu

echo "Aguardando banco de dados..."
ATTEMPT=1
MAX_ATTEMPTS=40

until python - <<'PY'
import asyncio
import asyncpg
import os
from urllib.parse import urlparse

database_url = os.getenv("DATABASE_URL", "")
if not database_url:
    raise RuntimeError("DATABASE_URL nao definida")

# Converte SQLAlchemy URL para URL compativel com asyncpg
normalized = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
parsed = urlparse(normalized)
if not parsed.hostname:
    raise RuntimeError("DATABASE_URL invalida")

async def check():
    conn = await asyncpg.connect(
        user=parsed.username or "postgres",
        password=parsed.password or "",
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=(parsed.path or "/postgres").lstrip("/"),
    )
    await conn.execute("SELECT 1;")
    await conn.close()

asyncio.run(check())
PY
do
  if [ "${ATTEMPT}" -ge "${MAX_ATTEMPTS}" ]; then
    echo "Banco de dados indisponivel apos ${MAX_ATTEMPTS} tentativas."
    exit 1
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 3
done

echo "Rodando inicializacao de schema..."
python init_db.py

echo "Rodando migracoes customizadas..."
python run_migrations.py

echo "Iniciando API..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
