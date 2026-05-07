import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Dict, Any, List
from fastapi import FastAPI, Request, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import redis.asyncio as redis
from db.database import engine
from db.manual_migrations import ensure_empresas_prompt_columns

from app.api.routers import empresas
from app.api.routers import agentes
from app.api.routers import especialistas
from app.api.routers import api_connections
from app.api.routers import auth
from app.api.routers import orquestrador
from app.api.routers import configuracoes
from app.api.routers import webhook
from app.api.routers import inbox
from app.api.routers import integracoes
from app.api.routers import conexoes
from app.api.routers import dashboard
from app.api.routers import websockets
from app.services.followup_service import processar_followups_pendentes
from app.core.security import validate_security_settings

# Global Redis Client
redis_client: redis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    validate_security_settings()
    await ensure_empresas_prompt_columns(engine)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    followup_task = None

    async def _followup_worker_loop():
        intervalo = max(30, int(os.getenv("FOLLOWUP_WORKER_INTERVAL_SECONDS", "60")))
        while True:
            try:
                resultado = await processar_followups_pendentes()
                print(f"[FOLLOWUP WORKER] ciclo concluído: {resultado}")
            except Exception as exc:
                print(f"[FOLLOWUP WORKER] falha no ciclo: {exc}")
            await asyncio.sleep(intervalo)

    followup_task = asyncio.create_task(_followup_worker_loop())
    yield
    # Cleanup Redis connection on shutdown
    if followup_task:
        followup_task.cancel()
        try:
            await followup_task
        except asyncio.CancelledError:
            pass
    await redis_client.close()

app = FastAPI(lifespan=lifespan, title="Agent OS (Omnichannel)")
MAX_REQUEST_SIZE_BYTES = 50 * 1024 * 1024


@app.get("/")
async def root():
    return {"status": "online", "message": "API Agente OS operando"}


@app.middleware("http")
async def enforce_max_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_SIZE_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Payload too large. Limite máximo: 50MB."},
                )
        except ValueError:
            pass
    return await call_next(request)


# Configuração do CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(empresas.router, prefix="/api")
app.include_router(agentes.router, prefix="/api")
app.include_router(especialistas.router, prefix="/api")
app.include_router(api_connections.router, prefix="/api")
app.include_router(orquestrador.router, prefix="/api/admin", dependencies=[Depends(auth.require_super_admin)])
app.include_router(orquestrador.status_router, prefix="/api/admin")
app.include_router(webhook.router, prefix="/api")

# Rotas que já possuem o prefixo completo no próprio APIRouter interno
app.include_router(configuracoes.router)
app.include_router(configuracoes.public_router)
app.include_router(auth.router)
app.include_router(inbox.router)
app.include_router(integracoes.router)
app.include_router(conexoes.router, prefix="/api")
app.include_router(conexoes.status_router, prefix="/api")
app.include_router(dashboard.router)
app.include_router(websockets.router, prefix="/api/empresas", tags=["websockets"])

print("--- INICIANDO MAPEAMENTO DE ROTAS ---")
for route in app.routes:
    print(f"ROTA ENCONTRADA: {route.path} | Nome: {getattr(route, 'name', 'N/A')}")
print("--- FIM DO MAPEAMENTO DE ROTAS ---")


from app.schemas import StandardMessage

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
