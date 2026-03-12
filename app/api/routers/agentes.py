from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.database import get_db
from db.models import Agente
from app.schemas import AgenteCreate, AgenteResponse

router = APIRouter(
    prefix="/agentes",
    tags=["Agentes"]
)

@router.post("/", response_model=AgenteResponse, status_code=status.HTTP_201_CREATED)
async def criar_agente(agente: AgenteCreate, db: AsyncSession = Depends(get_db)):
    """
    Cria um novo Agente no banco de dados.
    """
    novo_agente = Agente(
        empresa_id=agente.empresa_id,
        nome=agente.nome,
        modelo_ia=agente.modelo_ia,
        prompt_sistema=agente.prompt_sistema,
        ativo=agente.ativo
    )
    db.add(novo_agente)
    try:
        await db.commit()
        await db.refresh(novo_agente)
        return novo_agente
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar agente: {str(e)}"
        )


@router.get("/", response_model=List[AgenteResponse], status_code=status.HTTP_200_OK)
async def listar_agentes(db: AsyncSession = Depends(get_db)):
    """
    Retorna a lista de todos os Agentes cadastrados.
    """
    try:
        result = await db.execute(select(Agente))
        agentes = result.scalars().all()
        return agentes
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar agentes: {str(e)}"
        )
