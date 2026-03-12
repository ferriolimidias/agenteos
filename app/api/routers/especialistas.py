from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.database import get_db
from db.models import Especialista, APIConnection, especialista_tools
from app.schemas import EspecialistaCreate, EspecialistaResponse, EspecialistaToolLink

router = APIRouter(
    prefix="/especialistas",
    tags=["Especialistas"]
)

@router.post("/", response_model=EspecialistaResponse, status_code=status.HTTP_201_CREATED)
async def criar_especialista(especialista: EspecialistaCreate, db: AsyncSession = Depends(get_db)):
    """
    Cria um novo Especialista no banco.
    """
    novo_especialista = Especialista(
        empresa_id=especialista.empresa_id,
        nome=especialista.nome,
        prompt_sistema=especialista.prompt_sistema,
        ativo=especialista.ativo
    )
    db.add(novo_especialista)
    try:
        await db.commit()
        await db.refresh(novo_especialista)
        return novo_especialista
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar especialista: {str(e)}"
        )

@router.get("/", response_model=List[EspecialistaResponse], status_code=status.HTTP_200_OK)
async def listar_especialistas(db: AsyncSession = Depends(get_db)):
    """
    Lista todos os especialistas cadastrados.
    """
    try:
        result = await db.execute(select(Especialista))
        return result.scalars().all()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar especialistas: {str(e)}"
        )

@router.post("/vincular-ferramenta", status_code=status.HTTP_200_OK)
async def vincular_ferramenta(link_data: EspecialistaToolLink, db: AsyncSession = Depends(get_db)):
    """
    Vincula uma Ferramenta (APIConnection) a um Especialista.
    """
    try:
        # Verifica se ambos existem
        esp_result = await db.execute(select(Especialista).where(Especialista.id == link_data.especialista_id))
        especialista = esp_result.scalar_one_or_none()
        if not especialista:
            raise HTTPException(status_code=404, detail="Especialista não encontrado")
            
        api_result = await db.execute(select(APIConnection).where(APIConnection.id == link_data.api_connection_id))
        api_conn = api_result.scalar_one_or_none()
        if not api_conn:
            raise HTTPException(status_code=404, detail="Conexão API não encontrada")

        # Verifica se o link já existe
        link_query = select(especialista_tools).where(
            especialista_tools.c.especialista_id == link_data.especialista_id,
            especialista_tools.c.api_connection_id == link_data.api_connection_id
        )
        existing_link = (await db.execute(link_query)).first()
        if existing_link:
            return {"status": "Link já existe"}

        # Insere o link na tabela associativa
        await db.execute(
            especialista_tools.insert().values(
                especialista_id=link_data.especialista_id,
                api_connection_id=link_data.api_connection_id
            )
        )
        await db.commit()
        return {"status": "Ferramenta vinculada com sucesso ao especialista!"}
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao vincular ferramenta: {str(e)}"
        )
