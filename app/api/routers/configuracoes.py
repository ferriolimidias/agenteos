from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from db.database import get_db
from db.models import ConfiguracoesGlobais

router = APIRouter(prefix="/api/admin", tags=["Configurações Globais"])

class ConfiguracaoGlobalUpdate(BaseModel):
    nome_sistema: str
    cor_primaria: str
    openai_key_global: str | None = None

class ConfiguracaoGlobalResponse(BaseModel):
    id: int
    nome_sistema: str
    cor_primaria: str
    openai_key_global: str | None = None

    class Config:
        from_attributes = True

@router.get("/configuracoes", response_model=ConfiguracaoGlobalResponse)
async def get_configuracoes_globais(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    config = result.scalars().first()
    
    if not config:
        config = ConfiguracoesGlobais(id=1, nome_sistema="ANTIGRAVITY", cor_primaria="#6366f1", openai_key_global="")
        db.add(config)
        await db.commit()
        await db.refresh(config)
        
    return config

@router.put("/configuracoes", response_model=ConfiguracaoGlobalResponse)
async def update_configuracoes_globais(config_data: ConfiguracaoGlobalUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    config = result.scalars().first()
    
    if not config:
        config = ConfiguracoesGlobais(id=1)
        db.add(config)
    
    config.nome_sistema = config_data.nome_sistema
    config.cor_primaria = config_data.cor_primaria
    config.openai_key_global = config_data.openai_key_global
    
    await db.commit()
    await db.refresh(config)
    
    return config

@router.get("/modelos-ia", response_model=list[str])
async def get_modelos_disponiveis():
    from app.api.utils import get_available_models
    return await get_available_models()
