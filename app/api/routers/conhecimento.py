from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import OpenAIEmbeddings

from db.database import get_db
from db.models import Conhecimento
from app.schemas import ConhecimentoUpload

router = APIRouter(
    prefix="/conhecimento",
    tags=["Conhecimento"]
)

# Inicializando o modelo de embeddings
embeddings_model = OpenAIEmbeddings(model="text-embedding-ada-002")

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_conhecimento(upload: ConhecimentoUpload, db: AsyncSession = Depends(get_db)):
    """
    Recebe um texto, gera o embedding usando a OpenAI e salva no banco de dados.
    """
    try:
        # Gerar o embedding
        vetor = await embeddings_model.aembed_query(upload.conteudo)
        
        # Salvar no banco de dados
        novo_conhecimento = Conhecimento(
            conteudo=upload.conteudo,
            embedding=vetor
        )
        
        db.add(novo_conhecimento)
        await db.commit()
        await db.refresh(novo_conhecimento)
        
        return {
            "id": novo_conhecimento.id,
            "mensagem": "Conhecimento salvo com sucesso."
        }
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar e salvar o conhecimento: {str(e)}"
        )
