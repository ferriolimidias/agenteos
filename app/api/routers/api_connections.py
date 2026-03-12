from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.database import get_db
from db.models import APIConnection
from app.schemas import APIConnectionCreate, APIConnectionResponse

router = APIRouter(
    prefix="/api_connections",
    tags=["API Connections"]
)

@router.post("/", response_model=APIConnectionResponse, status_code=status.HTTP_201_CREATED)
async def criar_conexao_api(conexao: APIConnectionCreate, db: AsyncSession = Depends(get_db)):
    """
    Cria uma nova Conexão de API dinâmica no banco.
    """
    nova_conexao = APIConnection(
        empresa_id=conexao.empresa_id,
        nome=conexao.nome,
        url=conexao.url,
        metodo=conexao.metodo,
        headers_json=conexao.headers_json,
        params_schema_json=conexao.params_schema_json
    )
    db.add(nova_conexao)
    try:
        await db.commit()
        await db.refresh(nova_conexao)
        return nova_conexao
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar conexão API: {str(e)}"
        )

@router.get("/", response_model=List[APIConnectionResponse], status_code=status.HTTP_200_OK)
async def listar_conexoes_api(db: AsyncSession = Depends(get_db)):
    """
    Lista todas as conexões de API cadastradas.
    """
    try:
        result = await db.execute(select(APIConnection))
        return result.scalars().all()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar conexões API: {str(e)}"
        )
