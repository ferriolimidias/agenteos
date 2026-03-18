import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Dict, Any, List
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis.asyncio as redis

from app.api.routers import empresas
from app.api.routers import agentes
from app.api.routers import conhecimento
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

# Global Redis Client
redis_client: redis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    yield
    # Cleanup Redis connection on shutdown
    await redis_client.close()

app = FastAPI(lifespan=lifespan, title="Agent OS (Omnichannel)")


@app.get("/")
async def root():
    return {"status": "online", "message": "API Agente OS operando"}


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
app.include_router(conhecimento.router, prefix="/api")
app.include_router(especialistas.router, prefix="/api")
app.include_router(api_connections.router, prefix="/api")
app.include_router(orquestrador.router, prefix="/api/admin")
app.include_router(webhook.router, prefix="/api")

# Rotas que já possuem o prefixo completo no próprio APIRouter interno
app.include_router(configuracoes.router)
app.include_router(auth.router)
app.include_router(inbox.router)
app.include_router(integracoes.router)
app.include_router(conexoes.router, prefix="/api")
app.include_router(conexoes.status_router, prefix="/api")
app.include_router(dashboard.router)
app.include_router(websockets.router, prefix="/api/empresas", tags=["websockets"])


from app.api.schemas import StandardMessage

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
