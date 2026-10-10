from dataclasses import asdict
from datetime import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlalchemy.future import select
from pydantic import BaseModel, Field
from typing import Optional

from db.database import get_db
from db.models import Empresa, FerramentaAPI, FollowupExecution, MessageOutbox
from app.agents.graph import run_new_agent_flow
from app.agents.schemas import IncomingMessage
from app.api.routers.auth import require_super_admin
from app.services.worker_runtime import worker_runtime

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/orquestrador",
    tags=["Orquestrador"],
    dependencies=[Depends(require_super_admin)],
)
status_router = APIRouter(
    prefix="/workers",
    tags=["Workers"],
    dependencies=[Depends(require_super_admin)],
)
agents_router = APIRouter(
    prefix="/agents",
    tags=["Admin Agents"],
    dependencies=[Depends(require_super_admin)],
)

class FerramentaCreate(BaseModel):
    nome_ferramenta: str
    descricao_ia: str
    url: Optional[str] = None
    metodo: Optional[str] = "GET"
    headers: Optional[str] = None
    payload: Optional[str] = None
    parameters_json: Optional[dict] = None


class AgentSimulationRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    empresa_id: Optional[str] = None
    lead_id: Optional[str] = None
    telefone: Optional[str] = None
    nome: Optional[str] = None
    canal: Optional[str] = "whatsapp"
    status_atendimento: Optional[str] = "ia_ativa"


@router.get("/agents/architecture")
@agents_router.get("/architecture")
async def arquitetura_agentes():
    return {
        "status": "active",
        "production_graph": "app/agents/graph.py",
        "import_path": "app.agents.graph",
        "legacy": {
            "active": False,
            "note": "Fluxos antigos de agentes foram removidos do runtime e do codigo de agentes.",
        },
        "flow": [
            "Mensagem",
            "ConversationState",
            "AtendimentoAgent",
            "ToolPlanner",
            "Tools estruturadas",
            "AgenteFiscal",
            "ResponseGuard",
            "Dispatcher",
        ],
        "components": [
            {"key": "ConversationState", "status": "active", "description": "Estado normalizado da conversa e do tenant."},
            {"key": "AtendimentoAgent", "status": "active", "description": "Única camada autorizada a compor texto final ao cliente."},
            {"key": "ToolPlanner", "status": "active", "description": "Planeja ferramentas por LLM ou fixture de teste, sem selecao local por termos."},
            {"key": "StructuredTools", "status": "active", "description": "Retornam dados, campos faltantes, ações e atualização de memória."},
            {"key": "AgenteFiscal", "status": "active", "description": "Valida coerência entre resposta final, tools e memória."},
            {"key": "ImageAnalysis", "status": "fallback-ready", "description": "Camada genérica de análise de imagem por tenant, com mock/fallback seguro e provider BYOK preparado."},
            {"key": "ToolExecutor", "status": "protected", "description": "Executa apenas ferramentas solicitadas pelo plano estruturado."},
            {"key": "ResponseGuard", "status": "protected", "description": "Sanitiza segredos, marcadores internos, JSON técnico, stack traces e URLs internas."},
            {"key": "Dispatcher", "status": "dry-run-visible", "description": "Entrega real fica fora do simulador; dry-run sempre retorna should_dispatch=false."},
        ],
        "planning": {
            "mode": "llm_first",
            "selection": "structured_plan_only",
            "local_term_router": False,
        },
        "tools": [
            {"key": "context", "name": "Contexto"},
            {"key": "orcamento", "name": "Orçamento"},
            {"key": "agenda", "name": "Agenda"},
            {"key": "handoff", "name": "Atendimento humano"},
        ],
        "tool_security": [
            "bloqueio de localhost",
            "bloqueio de IP privado",
            "bloqueio de metadata endpoint",
            "controle por tenant, escopo e risco",
        ],
        "guard_blocks": ["sk-...", "Bearer token", "JWT", "stack trace", "JSON interno", "[SISTEMA_*]", "URLs internas/IPs privados"],
        "image_analysis": {
            "status": "fallback-ready",
            "providers": ["mock", "openai_byok"],
            "default": "disabled_until_tenant_enables",
            "safety": "resultado visual é apoio e exige confirmação humana quando usado para decisão",
        },
    }


@router.post("/agents/simulate")
@agents_router.post("/simulate")
async def simular_agentes(payload: AgentSimulationRequest):
    mensagem = str(payload.message or "").strip()
    incoming = IncomingMessage(
        text=mensagem,
        tenant_id=payload.empresa_id,
        lead_id=payload.lead_id,
        phone=payload.telefone,
        channel=payload.canal or "whatsapp",
    )
    output = await run_new_agent_flow(
        {
            "empresa_id": payload.empresa_id,
            "lead_id": payload.lead_id,
            "telefone": payload.telefone,
            "nome": payload.nome,
            "canal": payload.canal or "whatsapp",
            "status_atendimento": payload.status_atendimento or "ia_ativa",
            "mensagem_atual": mensagem,
        },
        message=incoming,
    )
    return {
        "resposta_final": output.response_text,
        "intent": output.decision.intent,
        "specialist_key": output.decision.specialist_key,
        "confidence": output.decision.confidence,
        "reason_codes": output.decision.reason_codes,
        "guard_result": asdict(output.guard_result) if output.guard_result else None,
        "should_dispatch": False,
        "metadata": output.metadata,
    }


@status_router.get("/status")
async def worker_status(db: AsyncSession = Depends(get_db)):
    from app.api.main import redis_client

    lock_key = "lock:followup_worker"
    key_ultima_execucao = "metrics:followup:ultima_execucao"
    key_erros_recentes = "metrics:followup:erros_recentes"
    key_leads_hoje = f"metrics:followup:leads_processados:{datetime.utcnow().strftime('%Y-%m-%d')}"

    lock_val = ultima_execucao = leads_hoje = erros_recentes = None
    redis_healthy = False
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_healthy = True
            lock_val = await redis_client.get(lock_key)
            ultima_execucao = await redis_client.get(key_ultima_execucao)
            leads_hoje = await redis_client.get(key_leads_hoje)
            erros_recentes = await redis_client.get(key_erros_recentes)
        except Exception as exc:
            logger.warning("WORKER_STATUS_REDIS_UNAVAILABLE exception_type=%s", type(exc).__name__)

    database_healthy = False
    backlog = {
        "followup": {"pending": None, "retry": None, "processing": None},
        "campaign": {"pending": None, "retry": None, "processing": None},
    }
    try:
        followup_rows = (await db.execute(
            select(FollowupExecution.status, func.count())
            .where(FollowupExecution.status.in_(["scheduled", "retry", "processing", "locked"]))
            .group_by(FollowupExecution.status)
        )).all()
        campaign_rows = (await db.execute(
            select(MessageOutbox.status, func.count())
            .where(MessageOutbox.operation_type == "campaign_template_send")
            .where(MessageOutbox.status.in_(["queued", "retry", "locked"]))
            .group_by(MessageOutbox.status)
        )).all()
        database_healthy = True
        followup_counts = {str(status): int(count) for status, count in followup_rows}
        campaign_counts = {str(status): int(count) for status, count in campaign_rows}
        backlog["followup"] = {
            "pending": followup_counts.get("scheduled", 0),
            "retry": followup_counts.get("retry", 0),
            # Follow-up claims are held as SELECT FOR UPDATE SKIP LOCKED and
            # are not persisted as a separate processing status.
            "processing": None,
            "processing_visibility": "transaction_row_lock",
        }
        backlog["campaign"] = {
            "pending": campaign_counts.get("queued", 0),
            "retry": campaign_counts.get("retry", 0),
            "processing": campaign_counts.get("locked", 0),
            "processing_visibility": "message_outbox_locked_status",
        }
    except Exception as exc:
        await db.rollback()
        logger.warning("WORKER_STATUS_BACKLOG_UNAVAILABLE exception_type=%s", type(exc).__name__)

    try:
        runtime = await worker_runtime.snapshots(redis_client)
    except Exception as exc:
        logger.warning("WORKER_STATUS_RUNTIME_UNAVAILABLE exception_type=%s", type(exc).__name__)
        runtime = {
            name: {"local": worker_runtime.local_snapshot(name), "instances": [worker_runtime.local_snapshot(name)]}
            for name in ("followup", "campaign")
        }
    workers = {}
    for name in ("followup", "campaign"):
        local = runtime[name]["local"]
        instances = runtime[name]["instances"]
        reported_instances = [instance for instance in instances if instance.get("enabled")]
        representative = max(
            reported_instances or [local],
            key=lambda instance: str(instance.get("last_heartbeat") or ""),
        )
        cluster_running = any(instance.get("running") for instance in reported_instances)
        cluster_status = (
            "RUNNING" if cluster_running
            else "DEGRADED" if any(instance.get("status") == "DEGRADED" for instance in reported_instances)
            else str(representative.get("status") or "STOPPED")
        )
        workers[name] = {
            **representative,
            "enabled": any(instance.get("enabled") for instance in instances),
            "status": cluster_status,
            "running": cluster_running,
            "instance_count": len(reported_instances),
            "instances": reported_instances,
            **backlog[name],
        }

    return {
        "status_lock": "Ocupado" if lock_val else "Livre" if redis_healthy else "Indisponível",
        "ultima_execucao": ultima_execucao,
        "leads_processados_hoje": int(leads_hoje or 0),
        "erros_recentes": int(erros_recentes or 0),
        "database": {"healthy": database_healthy},
        "redis": {"healthy": redis_healthy, "required_for_worker_claim": False},
        "workers": workers,
    }

@router.get("/empresas/{empresa_id}/ferramentas")
async def listar_ferramentas(empresa_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FerramentaAPI).where(FerramentaAPI.empresa_id == empresa_id))
    ferramentas = result.scalars().all()
    return ferramentas

@router.post("/empresas/{empresa_id}/ferramentas")
async def criar_ferramenta(empresa_id: str, payload: FerramentaCreate, db: AsyncSession = Depends(get_db)):
    # Verifica empresa
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
        
    nova = FerramentaAPI(
        empresa_id=empresa.id,
        nome_ferramenta=payload.nome_ferramenta,
        descricao_ia=payload.descricao_ia,
        url=payload.url,
        metodo=payload.metodo,
        headers=payload.headers,
        payload=payload.payload,
        schema_parametros=payload.parameters_json
    )
    db.add(nova)
    await db.commit()
    await db.refresh(nova)
@router.put("/empresas/{empresa_id}/ferramentas/{ferramenta_id}")
async def atualizar_ferramenta(empresa_id: str, ferramenta_id: str, payload: FerramentaCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FerramentaAPI).where(FerramentaAPI.id == ferramenta_id, FerramentaAPI.empresa_id == empresa_id))
    ferramenta = result.scalars().first()
    if not ferramenta:
        raise HTTPException(status_code=404, detail="Ferramenta não encontrada")
    
    ferramenta.nome_ferramenta = payload.nome_ferramenta
    ferramenta.descricao_ia = payload.descricao_ia
    ferramenta.url = payload.url
    ferramenta.metodo = payload.metodo
    ferramenta.headers = payload.headers
    ferramenta.payload = payload.payload
    ferramenta.schema_parametros = payload.parameters_json
    
    await db.commit()
    await db.refresh(ferramenta)
    return {"mensagem": "Ferramenta atualizada"}

@router.delete("/empresas/{empresa_id}/ferramentas/{ferramenta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deletar_ferramenta(empresa_id: str, ferramenta_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FerramentaAPI).where(FerramentaAPI.id == ferramenta_id, FerramentaAPI.empresa_id == empresa_id))
    ferramenta = result.scalars().first()
    if not ferramenta:
        raise HTTPException(status_code=404, detail="Ferramenta não encontrada")
    
    await db.delete(ferramenta)
    await db.commit()
    return None
