from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
import uuid

from db.database import get_db
from db.models import Especialista, APIConnection, especialista_tools
from app.schemas import EspecialistaCreate, EspecialistaResponse, EspecialistaToolLink, EspecialistaUpdate
from app.services.semantic_router import SemanticRouterService

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
        descricao_missao=especialista.descricao_missao,
        prompt_sistema=especialista.prompt_sistema,
        usar_rag=especialista.usar_rag,
        usar_agenda=especialista.usar_agenda,
        ativo=especialista.ativo
    )
    router_service = SemanticRouterService(db)
    await router_service.refresh_specialist_embedding(novo_especialista)
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


@router.put("/{especialista_id}", response_model=EspecialistaResponse, status_code=status.HTTP_200_OK)
async def atualizar_especialista(
    especialista_id: str,
    data: EspecialistaUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Atualiza um Especialista existente.
    """
    try:
        try:
            especialista_uuid = uuid.UUID(especialista_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de especialista inválido")

        result = await db.execute(select(Especialista).where(Especialista.id == especialista_uuid))
        especialista = result.scalars().first()
        if not especialista:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Especialista não encontrado")

        payload = data.model_dump(exclude_unset=True)
        if "nome" in payload:
            especialista.nome = payload["nome"]
        if "descricao_missao" in payload:
            especialista.descricao_missao = payload["descricao_missao"]
        if "prompt_sistema" in payload:
            especialista.prompt_sistema = payload["prompt_sistema"]
        if "usar_rag" in payload:
            especialista.usar_rag = bool(payload["usar_rag"])
        if "usar_agenda" in payload:
            especialista.usar_agenda = bool(payload["usar_agenda"])
        if "ativo" in payload:
            especialista.ativo = bool(payload["ativo"])

        router_service = SemanticRouterService(db)
        await router_service.refresh_specialist_embedding(especialista)

        await db.commit()
        await db.refresh(especialista)
        return especialista
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao atualizar especialista: {str(e)}"
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
