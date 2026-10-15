import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Dict, Any, List
from fastapi import FastAPI, Request, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import redis.asyncio as redis
from db.database import AsyncSessionLocal, engine
from db.manual_migrations import ensure_empresas_prompt_columns

from app.api.routers import empresas
from app.api.routers import agentes
from app.api.routers import api_connections
from app.api.routers import auth
from app.api.routers import orquestrador
from app.api.routers import configuracoes
from app.api.routers import openai_models
from app.api.routers import prompt_optimizer
from app.api.routers import webhook
from app.api.routers import meta_webhook
from app.api.routers import campaign_central
from app.api.routers import inbox
from app.api.routers import integracoes
from app.api.routers import conexoes
from app.api.routers import dashboard
from app.api.routers import instagram_simple
from app.api.routers import instagram_simple_webhook
from app.api.routers import meta_templates
from app.api.routers import traffic_meta
from app.api.routers import attribution
from app.api.routers import analytics
from app.api.routers import jev, jev_admin
from app.api.routers import websockets
from app.api.routers import public_anamnese
from app.api.routers import leoft_student_app
from app.api.routers import leoft_internal
from app.api.routers import leoft_generation_admin
from app.api.routers import mercado_pago_leoft
from app.api.routers import company_payments
from app.services.followup_service import processar_followups_pendentes
from app.services.campaign_central_dispatch_service import process_campaign_outbox_once
from app.core.security import validate_security_settings, decode_access_token
from db.models import is_meta_app_reviewer_role
from app.core.startup_database_mode import StartupDatabaseMode
from app.services.worker_runtime import check_worker_database_readiness, env_flag, worker_interval, worker_runtime

logger = logging.getLogger(__name__)
outbox_runtime_logger = logging.getLogger("uvicorn.error")

# Global Redis Client
redis_client: redis.Redis = None


async def _conversion_outbox_reconciliation_loop() -> None:
    """Repair missing SHADOW projections/intents without dispatching externally."""
    retry_delay_seconds = 5
    while True:
        try:
            from sqlalchemy import select
            from db.models import Empresa
            from app.services.meta.conversion_outbox import reconcile_missing_shadow_outbox

            async with AsyncSessionLocal() as session:
                tenant_ids = list((await session.execute(select(Empresa.id))).scalars().all())
                recovered = 0
                for tenant_id in tenant_ids:
                    recovered += await reconcile_missing_shadow_outbox(
                        session, empresa_id=tenant_id, limit=500,
                    )
                await session.commit()
            outbox_runtime_logger.info(
                "CONVERSION_OUTBOX_RECONCILIATION_COMPLETE tenants=%s recovered=%s",
                len(tenant_ids), recovered,
            )
            retry_delay_seconds = 5
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            outbox_runtime_logger.info("CONVERSION_OUTBOX_RECONCILIATION_STOP")
            raise
        except Exception as exc:
            outbox_runtime_logger.warning(
                "CONVERSION_OUTBOX_RECONCILIATION_DEGRADED error_type=%s retry_seconds=%s",
                type(exc).__name__, retry_delay_seconds,
            )
            await asyncio.sleep(retry_delay_seconds)
            retry_delay_seconds = min(retry_delay_seconds * 2, 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    startup_database_mode = StartupDatabaseMode.resolve()
    logger.info("Startup database mode: %s", startup_database_mode.value)
    validate_security_settings()
    if startup_database_mode.allows_database_actions:
        await ensure_empresas_prompt_columns(engine)
    else:
        logger.info("Startup database DDL/DML actions are blocked in skip mode.")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    followup_worker_enabled = env_flag("FOLLOWUP_WORKER_ENABLED", "0")
    campaign_worker_enabled = env_flag("CAMPAIGN_WORKER_ENABLED", "1")
    followup_interval = worker_interval("FOLLOWUP_WORKER_INTERVAL_SECONDS", 60, minimum=30)
    campaign_interval = worker_interval("CAMPAIGN_WORKER_INTERVAL_SECONDS", 1)

    async def _followup_readiness():
        await check_worker_database_readiness(engine, "followup")

    async def _campaign_readiness():
        await check_worker_database_readiness(engine, "campaign")

    worker_tasks = {
        "followup": worker_runtime.start_worker(
            name="followup",
            enabled=followup_worker_enabled,
            interval_seconds=followup_interval,
            readiness_check=_followup_readiness,
            run_cycle=processar_followups_pendentes,
            redis_client=redis_client,
        ),
        "campaign": worker_runtime.start_worker(
            name="campaign",
            enabled=campaign_worker_enabled,
            interval_seconds=campaign_interval,
            readiness_check=_campaign_readiness,
            run_cycle=process_campaign_outbox_once,
            redis_client=redis_client,
        ),
    }
    outbox_reconciliation_task = asyncio.create_task(
        _conversion_outbox_reconciliation_loop(),
        name="canonical-conversion-outbox-reconciliation",
    )
    logger.info(
        "WORKER_CONFIGURATION startup_database_mode=%s followup_enabled=%s campaign_enabled=%s",
        startup_database_mode.value,
        followup_worker_enabled,
        campaign_worker_enabled,
    )
    try:
        yield
    finally:
        outbox_reconciliation_task.cancel()
        await asyncio.gather(outbox_reconciliation_task, return_exceptions=True)
        await worker_runtime.stop_workers(worker_tasks, redis_client)
        close_redis = getattr(redis_client, "close", None) or getattr(redis_client, "aclose", None)
        if close_redis is not None:
            close_result = close_redis()
            if asyncio.iscoroutine(close_result):
                await close_result

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


@app.middleware("http")
async def restrict_meta_reviewer_surface(request: Request, call_next):
    """Keep the generic reviewer role inside its explicit tenant-scoped allowlist."""
    authorization = str(request.headers.get("authorization") or "")
    token = authorization.split(" ", 1)[1].strip() if authorization.lower().startswith("bearer ") else ""
    if token:
        try:
            claims = decode_access_token(token)
        except Exception:
            claims = {}
        if is_meta_app_reviewer_role(claims.get("role")):
            from app.api.routers.auth import reviewer_tenant_path_allowed
            path = str(request.url.path or "")
            allowed_public = path in {"/", "/openapi.json"} or path.startswith("/docs") or path.startswith("/api/public/")
            if not allowed_public and path != "/api/auth/login" and not reviewer_tenant_path_allowed(request):
                return JSONResponse(status_code=403, content={"detail": "Acesso restrito à conta de análise."})
            tenant_claim = str(claims.get("empresa_id") or "")
            parts = path.split("/")
            if len(parts) > 3 and parts[1:3] in (["api", "empresas"], ["api", "reviewer"]) and parts[3] != tenant_claim:
                return JSONResponse(status_code=403, content={"detail": "Tenant não autorizado."})
    return await call_next(request)


# WebSocket primeiro para garantir registro prioritário do endpoint de handshake.
app.include_router(websockets.router, prefix="/api", tags=["websockets"])
app.include_router(empresas.router, prefix="/api")
app.include_router(empresas.admin_router, prefix="/api")
app.include_router(agentes.router, prefix="/api")
app.include_router(api_connections.router, prefix="/api")
app.include_router(orquestrador.router, prefix="/api/admin", dependencies=[Depends(auth.require_super_admin)])
app.include_router(orquestrador.status_router, prefix="/api/admin")
app.include_router(orquestrador.agents_router, prefix="/api/admin")
app.include_router(campaign_central.router)
app.include_router(webhook.router, prefix="/api")
app.include_router(meta_webhook.router, prefix="/api")
app.include_router(public_anamnese.router)
app.include_router(leoft_student_app.router)
app.include_router(leoft_student_app.approval_router)
app.include_router(leoft_internal.router)
app.include_router(leoft_generation_admin.router)
app.include_router(mercado_pago_leoft.router)
app.include_router(company_payments.router)

# Rotas que já possuem o prefixo completo no próprio APIRouter interno
app.include_router(configuracoes.router)
app.include_router(configuracoes.public_router)
app.include_router(openai_models.router)
app.include_router(prompt_optimizer.router)
app.include_router(auth.router)
app.include_router(inbox.router)
app.include_router(integracoes.router)
app.include_router(conexoes.router, prefix="/api")
app.include_router(conexoes.status_router, prefix="/api")
app.include_router(conexoes.whatsapp_router, prefix="/api")
app.include_router(dashboard.router)
app.include_router(instagram_simple.router)
app.include_router(instagram_simple_webhook.router)
app.include_router(meta_templates.router)
app.include_router(traffic_meta.router, prefix="/api")
app.include_router(attribution.router)
app.include_router(analytics.router)
from app.api.routers import atendimento_modular
app.include_router(atendimento_modular.router)
app.include_router(jev.router)
app.include_router(jev_admin.router)

print("--- INICIANDO MAPEAMENTO DE ROTAS ---")
for route in app.routes:
    print(f"ROTA ENCONTRADA: {route.path} | Nome: {getattr(route, 'name', 'N/A')}")
print("--- FIM DO MAPEAMENTO DE ROTAS ---")


from app.schemas import StandardMessage

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
