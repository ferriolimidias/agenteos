from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlalchemy.orm import selectinload

from db.database import get_db
from db.models import Empresa, Especialista, FerramentaAPI
from app.services.semantic_router import SemanticRouterService

router = APIRouter(
    prefix="/orquestrador",
    tags=["Orquestrador"]
)

class EspecialistaCreate(BaseModel):
    nome: str
    descricao_missao: Optional[str] = None
    prompt_sistema: str
    modelo_ia: Optional[str] = "gpt-4o-mini"
    usar_rag: Optional[bool] = False
    usar_agenda: Optional[bool] = False
    peso_prioridade: Optional[int] = Field(default=1, ge=1)
    ferramentas_ids: Optional[List[str]] = Field(default_factory=list)

class FerramentaCreate(BaseModel):
    nome_ferramenta: str
    descricao_ia: str
    url: Optional[str] = None
    metodo: Optional[str] = "GET"
    headers: Optional[str] = None
    payload: Optional[str] = None
    parameters_json: Optional[dict] = None

@router.get("/empresas/{empresa_id}/especialistas")
async def listar_especialistas(empresa_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Especialista)
        .where(Especialista.empresa_id == empresa_id)
        .options(selectinload(Especialista.ferramentas))
    )
    especialistas = result.scalars().all()
    
    # Format to include list of ferramenta IDs
    retorno = []
    for esp in especialistas:
        retorno.append({
            "id": esp.id,
            "nome": esp.nome,
            "descricao_missao": esp.descricao_missao,
            "prompt_sistema": esp.prompt_sistema,
            "modelo_ia": getattr(esp, 'modelo_ia', "gpt-4o-mini"),
            "usar_rag": getattr(esp, 'usar_rag', False),
            "usar_agenda": getattr(esp, 'usar_agenda', False),
            "peso_prioridade": int(getattr(esp, "peso_prioridade", 1) or 1),
            "ferramentas_ids": [str(f.id) for f in esp.ferramentas] if esp.ferramentas else []
        })
    return retorno

@router.post("/empresas/{empresa_id}/especialistas")
async def criar_especialista(empresa_id: str, payload: EspecialistaCreate, db: AsyncSession = Depends(get_db)):
    # Verifica empresa
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
        
    novo = Especialista(
        empresa_id=empresa.id,
        nome=payload.nome,
        descricao_missao=payload.descricao_missao,
        prompt_sistema=payload.prompt_sistema,
        modelo_ia=payload.modelo_ia,
        usar_rag=payload.usar_rag,
        usar_agenda=payload.usar_agenda,
        peso_prioridade=int(payload.peso_prioridade or 1),
        ativo=True
    )
    
    # Associar ferramentas
    if payload.ferramentas_ids:
        ferramentas_result = await db.execute(
            select(FerramentaAPI).where(FerramentaAPI.id.in_(payload.ferramentas_ids), FerramentaAPI.empresa_id == empresa_id)
        )
        ferramentas_objs = ferramentas_result.scalars().all()
        novo.ferramentas = ferramentas_objs

    router_service = SemanticRouterService(db)
    await router_service.refresh_specialist_embedding(novo)

    db.add(novo)
    await db.commit()
    await db.refresh(novo, attribute_names=["ferramentas"])
    
    return {
        "id": novo.id,
        "nome": novo.nome,
        "descricao_missao": novo.descricao_missao,
        "prompt_sistema": novo.prompt_sistema,
        "modelo_ia": getattr(novo, 'modelo_ia', 'gpt-4o-mini'),
        "usar_rag": novo.usar_rag,
        "usar_agenda": novo.usar_agenda,
        "peso_prioridade": int(getattr(novo, "peso_prioridade", 1) or 1),
        "ferramentas_ids": [str(f.id) for f in novo.ferramentas] if novo.ferramentas else []
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
@router.put("/empresas/{empresa_id}/especialistas/{especialista_id}")
async def atualizar_especialista(empresa_id: str, especialista_id: str, payload: EspecialistaCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Especialista).where(Especialista.id == especialista_id, Especialista.empresa_id == empresa_id).options(selectinload(Especialista.ferramentas)))
    especialista = result.scalars().first()
    if not especialista:
        raise HTTPException(status_code=404, detail="Especialista não encontrado")
    
    especialista.nome = payload.nome
    especialista.descricao_missao = payload.descricao_missao
    especialista.prompt_sistema = payload.prompt_sistema
    especialista.modelo_ia = payload.modelo_ia
    especialista.usar_rag = payload.usar_rag
    especialista.usar_agenda = payload.usar_agenda
    especialista.peso_prioridade = int(payload.peso_prioridade or 1)
    
    
    # Atualizar ferramentas
    if payload.ferramentas_ids is not None:
        if payload.ferramentas_ids:
            ferramentas_result = await db.execute(
                select(FerramentaAPI).where(FerramentaAPI.id.in_(payload.ferramentas_ids), FerramentaAPI.empresa_id == empresa_id)
            )
            especialista.ferramentas = ferramentas_result.scalars().all()
        else:
            especialista.ferramentas = []

    router_service = SemanticRouterService(db)
    await router_service.refresh_specialist_embedding(especialista)
            
    await db.commit()
    await db.refresh(especialista)
    return {"mensagem": "Especialista atualizado"}

@router.delete("/empresas/{empresa_id}/especialistas/{especialista_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deletar_especialista(empresa_id: str, especialista_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Especialista).where(Especialista.id == especialista_id, Especialista.empresa_id == empresa_id))
    especialista = result.scalars().first()
    if not especialista:
        raise HTTPException(status_code=404, detail="Especialista não encontrado")
    
    await db.delete(especialista)
    await db.commit()
    return None

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
