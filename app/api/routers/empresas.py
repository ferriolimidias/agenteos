from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, UploadFile, File
import io
import csv
import uuid
import PyPDF2
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from fastapi.responses import Response

from db.database import get_db
from sqlalchemy.orm import selectinload
from db.models import (
    Empresa,
    Usuario,
    ConhecimentoRAG,
    CRMFunil,
    CRMEtapa,
    CRMLead,
    AgendaConfiguracao,
    AgendamentoLocal,
    Conexao,
    DestinosTransferencia,
    HistoricoTransferencia,
    is_root_admin_email,
    normalize_user_email,
    normalize_user_role,
)
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

from app.core.security import get_password_hash

class EvolutionCredentials(BaseModel):
    evolution_url: str
    evolution_apikey: str
    evolution_instance: str
    openai_api_key: Optional[str] = None

from app.schemas import EmpresaCreate, EmpresaResponse

router = APIRouter(
    prefix="/empresas",
    tags=["Empresas"]
)

@router.post("/", response_model=EmpresaResponse, status_code=status.HTTP_201_CREATED)
async def criar_empresa(empresa: EmpresaCreate, db: AsyncSession = Depends(get_db)):
    """
    Cria uma nova Empresa no banco de dados.
    """
    nova_empresa = Empresa(
        nome_empresa=empresa.nome_empresa,
        credenciais_canais=empresa.credenciais_canais
    )
    db.add(nova_empresa)
    try:
        await db.commit()
        await db.refresh(nova_empresa)
        return nova_empresa
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar empresa: {str(e)}"
        )


@router.get("/", response_model=List[EmpresaResponse], status_code=status.HTTP_200_OK)
async def listar_empresas(db: AsyncSession = Depends(get_db)):
    """
    Retorna a lista de todas as Empresas cadastradas.
    """
    try:
        result = await db.execute(select(Empresa))
        empresas = result.scalars().all()
        return empresas
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar empresas: {str(e)}"
        )

# --- ROTAS DA IA CONFIGURATOR ---
from fastapi import Header

async def require_ia_config_access(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(None),
):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Usuário não autenticado.")

    try:
        user_uuid = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Identificador de usuário inválido.")

    result = await db.execute(select(Usuario).where(Usuario.id == user_uuid))
    usuario_bd = result.scalars().first()
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="Usuário não encontrado.")

    if is_root_admin_email(usuario_bd.email):
        return usuario_bd

    role_normalizada = normalize_user_role(usuario_bd.role)
    print(f"Role no Banco: {usuario_bd.role}")
    roles_super_admin = {"super_admin", "superadmin"}
    roles_admin_empresa = {"admin_empresa", "adminempresa"}

    if role_normalizada in roles_super_admin:
        return usuario_bd

    if role_normalizada in roles_admin_empresa:
        try:
            empresa_uuid = uuid.UUID(empresa_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="ID da empresa inválido.")

        if usuario_bd.empresa_id != empresa_uuid:
            raise HTTPException(status_code=403, detail="Acesso negado para esta empresa.")
        return usuario_bd

    raise HTTPException(
        status_code=403,
        detail="Acesso negado. Apenas Super Admin ou Admin da própria empresa."
    )

class IAConfigResponse(BaseModel):
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    modelo_ia: str | None = None
    modelo_roteador: str | None = None
    followup_ativo: bool = False
    followup_espera_nivel_1_minutos: int = 20
    followup_espera_nivel_2_minutos: int = 10
    informacoes_adicionais: str | None = None
    coletar_nome: bool = True

class IAConfigUpdateRequest(BaseModel):
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    modelo_ia: str | None = None
    modelo_roteador: str | None = None
    followup_ativo: bool | None = None
    followup_espera_nivel_1_minutos: int | None = None
    followup_espera_nivel_2_minutos: int | None = None
    informacoes_adicionais: str | None = None
    coletar_nome: bool | None = None
    
@router.get("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
async def get_ia_config(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return {
        "ia_instrucoes_personalizadas": empresa.ia_instrucoes_personalizadas, 
        "ia_tom_voz": empresa.ia_tom_voz,
        "nome_agente": empresa.nome_agente,
        "mensagem_saudacao": empresa.mensagem_saudacao,
        "modelo_ia": empresa.modelo_ia,
        "modelo_roteador": getattr(empresa, 'modelo_roteador', 'gpt-4o-mini'),
        "followup_ativo": getattr(empresa, 'followup_ativo', False) or False,
        "followup_espera_nivel_1_minutos": getattr(empresa, 'followup_espera_nivel_1_minutos', 20) or 20,
        "followup_espera_nivel_2_minutos": getattr(empresa, 'followup_espera_nivel_2_minutos', 10) or 10,
        "informacoes_adicionais": getattr(empresa, 'informacoes_adicionais', None),
        "coletar_nome": getattr(empresa, 'coletar_nome', True) if getattr(empresa, 'coletar_nome', True) is not None else True,
    }

@router.put("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
@router.post("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
async def put_ia_config(
    empresa_id: str,
    data: IAConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if data.ia_instrucoes_personalizadas is not None:
        empresa.ia_instrucoes_personalizadas = data.ia_instrucoes_personalizadas
    if data.ia_tom_voz is not None:
        empresa.ia_tom_voz = data.ia_tom_voz
    if data.nome_agente is not None:
        empresa.nome_agente = data.nome_agente
    if data.mensagem_saudacao is not None:
        empresa.mensagem_saudacao = data.mensagem_saudacao
    if data.modelo_ia is not None:
        empresa.modelo_ia = data.modelo_ia
    if data.modelo_roteador is not None:
        empresa.modelo_roteador = data.modelo_roteador
    if data.followup_ativo is not None:
        empresa.followup_ativo = data.followup_ativo
    if data.followup_espera_nivel_1_minutos is not None:
        empresa.followup_espera_nivel_1_minutos = data.followup_espera_nivel_1_minutos
    if data.followup_espera_nivel_2_minutos is not None:
        empresa.followup_espera_nivel_2_minutos = data.followup_espera_nivel_2_minutos
    if data.informacoes_adicionais is not None:
        empresa.informacoes_adicionais = data.informacoes_adicionais
    if data.coletar_nome is not None:
        empresa.coletar_nome = data.coletar_nome
    try:
        await db.commit()
        await db.refresh(empresa)
        return {
            "ia_instrucoes_personalizadas": empresa.ia_instrucoes_personalizadas, 
            "ia_tom_voz": empresa.ia_tom_voz,
            "nome_agente": empresa.nome_agente,
            "mensagem_saudacao": empresa.mensagem_saudacao,
            "modelo_ia": empresa.modelo_ia,
            "modelo_roteador": getattr(empresa, 'modelo_roteador', 'gpt-4o-mini'),
            "followup_ativo": getattr(empresa, 'followup_ativo', False) or False,
            "followup_espera_nivel_1_minutos": getattr(empresa, 'followup_espera_nivel_1_minutos', 20) or 20,
            "followup_espera_nivel_2_minutos": getattr(empresa, 'followup_espera_nivel_2_minutos', 10) or 10,
            "informacoes_adicionais": getattr(empresa, 'informacoes_adicionais', None),
            "coletar_nome": getattr(empresa, 'coletar_nome', True) if getattr(empresa, 'coletar_nome', True) is not None else True,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar: {str(e)}")

class EmpresaUpdateRequest(BaseModel):
    nome_empresa: str | None = None
    area_atuacao: str | None = None
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None
    conexao_disparo_id: str | None = None

@router.put("/{empresa_id}", response_model=EmpresaResponse, status_code=status.HTTP_200_OK)
async def atualizar_empresa(empresa_id: str, data: EmpresaUpdateRequest, db: AsyncSession = Depends(get_db)):
    """
    Atualiza dados básicos da Empresa (nome e área de atuação).
    """
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    
    if data.nome_empresa is not None:
        empresa.nome_empresa = data.nome_empresa
    if data.area_atuacao is not None:
        empresa.area_atuacao = data.area_atuacao
    if data.ia_instrucoes_personalizadas is not None:
        empresa.ia_instrucoes_personalizadas = data.ia_instrucoes_personalizadas
    if data.ia_tom_voz is not None:
        empresa.ia_tom_voz = data.ia_tom_voz
    if data.conexao_disparo_id is not None:
        if data.conexao_disparo_id == "":
            empresa.conexao_disparo_id = None
        else:
            try:
                conexao_disparo_uuid = uuid.UUID(data.conexao_disparo_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="conexao_disparo_id inválido")

            result_conexao = await db.execute(
                select(Conexao).where(
                    Conexao.id == conexao_disparo_uuid,
                    Conexao.empresa_id == empresa.id,
                )
            )
            conexao = result_conexao.scalars().first()
            if not conexao:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conexão de disparo não encontrada para esta empresa",
                )
            empresa.conexao_disparo_id = conexao.id
        
    try:
        await db.commit()
        await db.refresh(empresa)
        return empresa
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar empresa: {str(e)}")

@router.delete("/{empresa_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deletar_empresa(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Remove uma Empresa e todos os dados vinculados (via cascade configurado no banco).
    """
    import uuid
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")
         
    result = await db.execute(select(Empresa).where(Empresa.id == emp_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
        
    try:
        await db.delete(empresa)
        await db.commit()
        return None
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao deletar empresa: {str(e)}")

class EmpresaSetupRequest(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    admin_nome: str
    admin_email: str
    admin_senha: str

@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup_empresa(data: EmpresaSetupRequest, db: AsyncSession = Depends(get_db)):
    """
    Cria uma nova Empresa e seu respectivo Usuário admin (admin_empresa).
    """
    print(f"Iniciando setup_empresa para: {data.nome_empresa}")
    
    # Verifica se o email já existe
    admin_email_normalizado = normalize_user_email(data.admin_email)
    result = await db.execute(select(Usuario).where(Usuario.email == admin_email_normalizado))
    if result.scalars().first():
        print(f"Setup falhou: E-mail {data.admin_email} já em uso.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="E-mail já está em uso por outro usuário."
        )

    try:
        print("Criando empresa...")
        nova_empresa = Empresa(
            nome_empresa=data.nome_empresa,
            area_atuacao=data.area_atuacao
        )
        db.add(nova_empresa)
        await db.flush() # Para obter o ID da empresa gerado
        
        print("Criando usuário admin...")
        is_root_admin = is_root_admin_email(data.admin_email)
        novo_usuario = Usuario(
            empresa_id=None if is_root_admin else nova_empresa.id,
            nome=data.admin_nome,
            email=admin_email_normalizado,
            senha_hash=get_password_hash(data.admin_senha),
            role="super_admin" if is_root_admin else "admin_empresa",
            ativo=True
        )
        db.add(novo_usuario)
        
        print("Commitando transação...")
        await db.commit()
        await db.refresh(nova_empresa)
        await db.refresh(novo_usuario)
        
        print(f"Setup concluído com sucesso. ID da empresa: {nova_empresa.id}")
        return {
            "mensagem": "Empresa e usuário criados com sucesso!",
            "empresa": {
                "id": str(nova_empresa.id),
                "nome": nova_empresa.nome_empresa
            },
            "usuario": {
                "id": str(novo_usuario.id),
                "email": novo_usuario.email
            }
        }
    except ValueError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        print(f"Erro durante o setup da empresa: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro banco de dados: {str(e)}"
        )


@router.get("/{empresa_id}/dashboard")
async def get_dashboard_data(empresa_id: str):
    """
    Retorna dados mockados para o Dashboard do Cliente (Tenant).
    """
    return {
        "total_leads": 12,
        "agendamentos_hoje": 3,
        "status_ia": "Ativa"
    }


class RAGCreateRequest(BaseModel):
    tipo: str
    conteudo: str


class CRMLeadResponse(BaseModel):
    id: str
    nome_contato: str
    telefone: str | None = None
    historico_resumo: str | None = None
    tags: List[str] = Field(default_factory=list)
    dados_adicionais: Dict[str, Any] = Field(default_factory=dict)
    criado_em: str | None = None


class CRMEtapaResponse(BaseModel):
    id: str
    nome: str
    tipo: str | None = None
    ordem: int
    leads: List[CRMLeadResponse]


class CRMFunilResponse(BaseModel):
    funil_id: str
    funil_nome: str
    etapas: List[CRMEtapaResponse]


class CRMEtapaUpdateRequest(BaseModel):
    nome: str | None = None
    tipo: str | None = None
    ordem: int | None = None


class CRMLeadUpdateRequest(BaseModel):
    nome_contato: str | None = None
    telefone_contato: str | None = None
    historico_resumo: str | None = None
    etapa_id: str | None = None
    tags: List[str] | None = None
    dados_adicionais: Dict[str, Any] | None = None


class DestinoTransferenciaBase(BaseModel):
    nome_destino: str
    contatos_destino: List[str] = Field(default_factory=list)
    instrucoes_ativacao: str | None = None


class DestinoTransferenciaCreate(DestinoTransferenciaBase):
    pass


class DestinoTransferenciaUpdate(BaseModel):
    nome_destino: str | None = None
    contatos_destino: List[str] | None = None
    instrucoes_ativacao: str | None = None


class DestinoTransferenciaResponse(DestinoTransferenciaBase):
    id: str
    criado_em: str | None = None


class HistoricoTransferenciaResponse(BaseModel):
    id: str
    criado_em: str | None = None
    lead_id: str
    lead_nome: str | None = None
    destino_id: str | None = None
    destino_nome: str | None = None
    motivo_ia: str | None = None
    resumo_enviado: str | None = None

@router.get("/{empresa_id}/rag")
async def listar_conhecimento_rag(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna a lista de conhecimentos RAG da empresa.
    """
    try:
        result = await db.execute(select(ConhecimentoRAG).where(ConhecimentoRAG.empresa_id == empresa_id))
        conhecimentos = result.scalars().all()
        return [{"id": str(c.id), "tipo": c.tipo, "conteudo": c.conteudo, "status": c.status} for c in conhecimentos]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar conhecimento RAG: {str(e)}"
        )

@router.post("/{empresa_id}/rag", status_code=status.HTTP_201_CREATED)
async def adicionar_conhecimento_rag(empresa_id: str, data: RAGCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    Adiciona um novo conhecimento RAG (texto ou url) processando com Langchain e embeddings, igual ao PDF.
    """
    if data.tipo not in ["texto", "url", "pdf"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de conhecimento inválido.")

    texto_para_processar = ""
    label_fonte = ""

    if data.tipo == "texto":
        texto_para_processar = data.conteudo
        label_fonte = "[Texto Manual]"
    elif data.tipo == "url":
        try:
            import requests
            from bs4 import BeautifulSoup
            
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(data.conteudo, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            # Remove scripts, styles, etc
            for script in soup(["script", "style", "header", "footer", "nav"]):
                script.extract()
                
            texto_para_processar = soup.get_text(separator=' ', strip=True)
            label_fonte = f"[{data.conteudo}]"
            
            if not texto_para_processar:
                 raise ValueError("Nenhum texto principal encontrado na URL.")
                 
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao extrair conteúdo da URL: {str(e)}")

    if not texto_para_processar.strip():
        raise HTTPException(status_code=400, detail="O conteúdo fornecido está vazio.")

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        chunks = text_splitter.split_text(texto_para_processar)
        
        from langchain_openai import OpenAIEmbeddings
        from db.models import Conhecimento
        embeddings_model = OpenAIEmbeddings(model="text-embedding-ada-002")
        chunks_embeddings = await embeddings_model.aembed_documents(chunks)
        
        primeiro_id = None
        for i, (chunk, emb) in enumerate(zip(chunks, chunks_embeddings)):
            novo_chunk_rag = ConhecimentoRAG(
                empresa_id=empresa_id,
                tipo=data.tipo,
                conteudo=f"{label_fonte} " + chunk,
                status="Ativo"
            )
            db.add(novo_chunk_rag)
            await db.flush() # obtem o id
            
            if i == 0:
                primeiro_id = novo_chunk_rag.id
            
            novo_vetor = Conhecimento(
                empresa_id=empresa_id,
                conteudo=chunk,
                embedding=emb
            )
            db.add(novo_vetor)
            
        await db.commit()
        return {"mensagem": "Conhecimento adicionado com sucesso!", "id": str(primeiro_id)}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro no processamento de RAG: {str(e)}"
        )


@router.post("/{empresa_id}/rag/pdf", status_code=status.HTTP_201_CREATED)
async def adicionar_conhecimento_rag_pdf(empresa_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    Cadastra e vetoriza um arquivo PDF na base de conhecimento.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Arquivo deve ser um PDF.")
        
    try:
        content = await file.read()
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
        text_content = ""
        for page in pdf_reader.pages:
            try:
                extr = page.extract_text()
                if extr:
                    text_content += str(extr) + "\n"
            except Exception:
                pass # Ignore pages failing to decode
                
        if not text_content.strip():
            raise HTTPException(status_code=400, detail="Não foi possível extrair texto do PDF (pode ser um PDF de imagens ou vazio).")
            
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        chunks = text_splitter.split_text(text_content)
        
        from langchain_openai import OpenAIEmbeddings
        from db.models import Conhecimento
        embeddings_model = OpenAIEmbeddings(model="text-embedding-ada-002")
        chunks_embeddings = await embeddings_model.aembed_documents(chunks)
        
        for chunk, emb in zip(chunks, chunks_embeddings):
            # Salva o chunk no histórico/visualização do Tenant (tabela conhecimento_rag) com tipo pdf vinculando à empresa
            novo_chunk_rag = ConhecimentoRAG(
                empresa_id=empresa_id,
                tipo="pdf",
                conteudo=f"[{file.filename}] " + chunk,
                status="Ativo"
            )
            db.add(novo_chunk_rag)
            
            # Salva na tabela vetorial (utilizada pelo Agent no similarity search)
            novo_vetor = Conhecimento(
                empresa_id=empresa_id,
                conteudo=chunk,
                embedding=emb
            )
            db.add(novo_vetor)
            
        await db.commit()
        return {"mensagem": "PDF processado e fragmentado com sucesso!"}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{empresa_id}/crm", response_model=CRMFunilResponse)
async def obter_crm(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna a estrutura do Funil da empresa com as Etapas e os Leads aninhados.
    Se não existir um funil, cria um padrão ("Pipeline Padrão" com colunas: "Novo Lead", "Em Atendimento", "Fechado").
    """
    try:
        # Busca o primeiro funil da empresa, carregando as etapas e os leads das etapas
        result = await db.execute(
            select(CRMFunil)
            .where(CRMFunil.empresa_id == empresa_id)
            .options(
                selectinload(CRMFunil.etapas).selectinload(CRMEtapa.leads)
            )
        )
        funil = result.scalars().first()

        # Se não existir, cria o funil padrão
        if not funil:
            novo_funil = CRMFunil(empresa_id=empresa_id, nome="Pipeline Padrão")
            db.add(novo_funil)
            await db.flush() # Para pegar o ID do funil

            etapas_padrao = [
                CRMEtapa(funil_id=novo_funil.id, nome="Novo Lead", tipo="entrada", ordem=1),
                CRMEtapa(funil_id=novo_funil.id, nome="Em Atendimento", tipo="atendimento", ordem=2),
                CRMEtapa(funil_id=novo_funil.id, nome="Fechado", tipo="fechamento", ordem=3)
            ]
            db.add_all(etapas_padrao)
            await db.commit()
            
            # Recarrega o funil agora com as etapas
            result = await db.execute(
                select(CRMFunil)
                .where(CRMFunil.id == novo_funil.id)
                .options(
                    selectinload(CRMFunil.etapas).selectinload(CRMEtapa.leads)
                )
            )
            funil = result.scalars().first()

        # Ordena as etapas pela ordem
        etapas_ordenadas = sorted(funil.etapas, key=lambda x: x.ordem)

        resposta: dict = {
            "funil_id": str(funil.id),
            "funil_nome": funil.nome,
            "etapas": []
        }

        for etapa in etapas_ordenadas:
            etapa_dict = {
                "id": str(etapa.id),
                "nome": etapa.nome,
                "tipo": etapa.tipo,
                "ordem": etapa.ordem,
                "leads": [
                    {
                        "id": str(lead.id),
                        "nome_contato": lead.nome_contato,
                        "telefone": lead.telefone_contato,
                        "historico_resumo": lead.historico_resumo,
                        "tags": lead.tags or [],
                        "dados_adicionais": lead.dados_adicionais or {},
                        "criado_em": lead.criado_em.isoformat() if lead.criado_em else None
                    } for lead in etapa.leads
                ]
            }
            resposta["etapas"].append(etapa_dict)

        return resposta

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao carregar CRM: {str(e)}"
        )


@router.get("/{empresa_id}/transferencias/destinos", response_model=List[DestinoTransferenciaResponse])
async def listar_destinos_transferencia(empresa_id: str, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(DestinosTransferencia)
        .where(DestinosTransferencia.empresa_id == emp_uuid)
        .order_by(DestinosTransferencia.criado_em.desc())
    )
    destinos = result.scalars().all()
    return [
        {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
        for destino in destinos
    ]


@router.post("/{empresa_id}/transferencias/destinos", response_model=DestinoTransferenciaResponse, status_code=status.HTTP_201_CREATED)
async def criar_destino_transferencia(
    empresa_id: str,
    data: DestinoTransferenciaCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    contatos_normalizados = [str(contato).strip() for contato in data.contatos_destino if str(contato).strip()]
    destino = DestinosTransferencia(
        empresa_id=emp_uuid,
        nome_destino=data.nome_destino.strip(),
        contatos_destino=contatos_normalizados,
        instrucoes_ativacao=(data.instrucoes_ativacao or "").strip() or None,
    )
    db.add(destino)

    try:
        await db.commit()
        await db.refresh(destino)
        return {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar destino: {str(e)}")


@router.put("/{empresa_id}/transferencias/destinos/{destino_id}", response_model=DestinoTransferenciaResponse)
async def atualizar_destino_transferencia(
    empresa_id: str,
    destino_id: str,
    data: DestinoTransferenciaUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        destino_uuid = uuid.UUID(destino_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(DestinosTransferencia).where(
            DestinosTransferencia.id == destino_uuid,
            DestinosTransferencia.empresa_id == emp_uuid,
        )
    )
    destino = result.scalars().first()
    if not destino:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destino não encontrado")

    if data.nome_destino is not None:
        destino.nome_destino = data.nome_destino.strip()
    if data.contatos_destino is not None:
        destino.contatos_destino = [str(contato).strip() for contato in data.contatos_destino if str(contato).strip()]
    if data.instrucoes_ativacao is not None:
        destino.instrucoes_ativacao = data.instrucoes_ativacao.strip() or None

    try:
        await db.commit()
        await db.refresh(destino)
        return {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar destino: {str(e)}")


@router.delete("/{empresa_id}/transferencias/destinos/{destino_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_destino_transferencia(
    empresa_id: str,
    destino_id: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        destino_uuid = uuid.UUID(destino_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(DestinosTransferencia).where(
            DestinosTransferencia.id == destino_uuid,
            DestinosTransferencia.empresa_id == emp_uuid,
        )
    )
    destino = result.scalars().first()
    if not destino:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destino não encontrado")

    try:
        await db.delete(destino)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir destino: {str(e)}")


@router.get("/{empresa_id}/transferencias/historico", response_model=List[HistoricoTransferenciaResponse])
async def listar_historico_transferencia(empresa_id: str, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(HistoricoTransferencia)
        .where(HistoricoTransferencia.empresa_id == emp_uuid)
        .options(
            selectinload(HistoricoTransferencia.lead),
            selectinload(HistoricoTransferencia.destino),
        )
        .order_by(HistoricoTransferencia.criado_em.desc())
    )
    historicos = result.scalars().all()
    return [
        {
            "id": str(item.id),
            "criado_em": item.criado_em.isoformat() if item.criado_em else None,
            "lead_id": str(item.lead_id),
            "lead_nome": item.lead.nome_contato if item.lead else None,
            "destino_id": str(item.destino_id) if item.destino_id else None,
            "destino_nome": item.destino.nome_destino if item.destino else None,
            "motivo_ia": item.motivo_ia,
            "resumo_enviado": item.resumo_enviado,
        }
        for item in historicos
    ]


@router.put("/{empresa_id}/crm/etapas/{etapa_id}", status_code=status.HTTP_200_OK)
async def atualizar_etapa_crm(
    empresa_id: str,
    etapa_id: str,
    data: CRMEtapaUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Atualiza dados de uma etapa do CRM (nome, tipo e ordem).
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
        etapa_uuid = uuid.UUID(etapa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(CRMEtapa)
        .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
        .where(CRMEtapa.id == etapa_uuid, CRMFunil.empresa_id == emp_uuid)
    )
    etapa = result.scalars().first()
    if not etapa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada")

    if data.nome is not None:
        etapa.nome = data.nome
    if data.tipo is not None:
        etapa.tipo = data.tipo
    if data.ordem is not None:
        etapa.ordem = data.ordem

    try:
        await db.commit()
        await db.refresh(etapa)
        return {
            "id": str(etapa.id),
            "nome": etapa.nome,
            "tipo": etapa.tipo,
            "ordem": etapa.ordem
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar etapa: {str(e)}")


@router.put("/{empresa_id}/crm/leads/{lead_id}", status_code=status.HTTP_200_OK)
async def atualizar_lead_crm(
    empresa_id: str,
    lead_id: str,
    data: CRMLeadUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Atualiza dados de um lead do CRM, incluindo dados_adicionais.
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
        lead_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == emp_uuid))
    lead = result.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    if data.etapa_id is not None:
        if data.etapa_id == "":
            lead.etapa_id = None
        else:
            try:
                etapa_uuid = uuid.UUID(data.etapa_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="etapa_id inválido")

            result_etapa = await db.execute(
                select(CRMEtapa)
                .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
                .where(CRMEtapa.id == etapa_uuid, CRMFunil.empresa_id == emp_uuid)
            )
            etapa = result_etapa.scalars().first()
            if not etapa:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada para esta empresa")
            lead.etapa_id = etapa.id

    if data.nome_contato is not None:
        lead.nome_contato = data.nome_contato
    if data.telefone_contato is not None:
        lead.telefone_contato = data.telefone_contato
    if data.historico_resumo is not None:
        lead.historico_resumo = data.historico_resumo
    if data.tags is not None:
        lead.tags = [str(tag).strip() for tag in data.tags if str(tag).strip()]
    if data.dados_adicionais is not None:
        lead.dados_adicionais = data.dados_adicionais

    try:
        await db.commit()
        await db.refresh(lead)
        return {
            "id": str(lead.id),
            "nome_contato": lead.nome_contato,
            "telefone_contato": lead.telefone_contato,
            "historico_resumo": lead.historico_resumo,
            "etapa_id": str(lead.etapa_id) if lead.etapa_id else None,
            "tags": lead.tags or [],
            "dados_adicionais": lead.dados_adicionais or {}
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar lead: {str(e)}")


@router.get("/{empresa_id}/exportar-leads")
async def exportar_leads_csv(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Exporta os leads em CSV (UTF-8-SIG), expandindo dados_adicionais em colunas extras.
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(CRMLead)
        .where(CRMLead.empresa_id == emp_uuid)
        .options(selectinload(CRMLead.etapa))
        .order_by(CRMLead.criado_em.desc())
    )
    leads = result.scalars().all()

    extra_keys: set[str] = set()
    for lead in leads:
        if isinstance(lead.dados_adicionais, dict):
            extra_keys.update(str(k) for k in lead.dados_adicionais.keys())

    base_columns = [
        "id",
        "nome_contato",
        "telefone_contato",
        "historico_resumo",
        "etapa_id",
        "etapa_nome",
        "etapa_tipo",
        "tags",
        "criado_em",
    ]
    fieldnames = base_columns + sorted(extra_keys)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for lead in leads:
        row = {
            "id": str(lead.id),
            "nome_contato": lead.nome_contato,
            "telefone_contato": lead.telefone_contato or "",
            "historico_resumo": lead.historico_resumo or "",
            "etapa_id": str(lead.etapa_id) if lead.etapa_id else "",
            "etapa_nome": lead.etapa.nome if lead.etapa else "",
            "etapa_tipo": lead.etapa.tipo if lead.etapa else "",
            "tags": ", ".join(lead.tags or []),
            "criado_em": lead.criado_em.isoformat() if lead.criado_em else "",
        }
        if isinstance(lead.dados_adicionais, dict):
            for key in extra_keys:
                value = lead.dados_adicionais.get(key, "")
                row[key] = "" if value is None else str(value)
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=leads_{empresa_id}.csv"},
    )


@router.get("/{empresa_id}/agenda")
async def obter_agenda(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna as configurações da agenda e a lista de agendamentos futuros da empresa.
    """
    try:
        # Busca config da agenda
        result_config = await db.execute(
            select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == empresa_id)
        )
        config = result_config.scalars().first()

        # Busca os agendamentos futuros (onde data_hora_inicio >= now, mas aqui para mockup pegaremos todos)
        # e dá load do Lead associado para mostrar o nome
        result_agendamentos = await db.execute(
            select(AgendamentoLocal)
            .where(AgendamentoLocal.empresa_id == empresa_id)
            .options(selectinload(AgendamentoLocal.lead))
            .order_by(AgendamentoLocal.data_hora_inicio)
        )
        agendamentos_db = result_agendamentos.scalars().all()

        resposta_config = None
        if config:
            resposta_config = {
                "dias": config.dias_funcionamento.get("dias", []),
                "inicio": config.horario_inicio.strftime("%H:%M") if config.horario_inicio else "00:00",
                "fim": config.horario_fim.strftime("%H:%M") if config.horario_fim else "23:59",
                "duracao_minutos": config.duracao_slot_minutos
            }

        lista_agendamentos = []
        for ag in agendamentos_db:
            lista_agendamentos.append({
                "id": str(ag.id),
                "data_hora_inicio": ag.data_hora_inicio.isoformat() if ag.data_hora_inicio else None,
                "data_hora_fim": ag.data_hora_fim.isoformat() if ag.data_hora_fim else None,
                "status": ag.status,
                "lead": {
                    "nome_contato": ag.lead.nome_contato if ag.lead else "Desconhecido"
                }
            })

        return {
            "config": resposta_config,
            "agendamentos": lista_agendamentos
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao carregar dados da Agenda: {str(e)}"
        )


@router.put("/{empresa_id}/credenciais")
async def atualizar_credenciais(empresa_id: str, credenciais: EvolutionCredentials, db: AsyncSession = Depends(get_db)):
    """
    Atualiza as credenciais da Evolution API para uma determinada empresa (Super Admin).
    """
    try:
        result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
        empresa = result.scalars().first()
        
        if not empresa:
            raise HTTPException(status_code=404, detail="Empresa não encontrada")
            
        novas_credenciais = {
            "evolution_url": credenciais.evolution_url,
            "evolution_apikey": credenciais.evolution_apikey,
            "evolution_instance": credenciais.evolution_instance,
            "openai_api_key": credenciais.openai_api_key
        }
        
        # Merge de credenciais caso haja (no momento subscreve tudo do WhatsApp channel)
        # O model JSON admite dicionário
        empresa.credenciais_canais = novas_credenciais
        
        await db.commit()
        await db.refresh(empresa)
        
        return {"mensagem": "Credenciais atualizadas com sucesso", "credenciais": empresa.credenciais_canais}
        
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao atualizar credenciais: {str(e)}"
        )


class SimuladorRequest(BaseModel):
    mensagem: str
    sessao_id: str

@router.post("/{empresa_id}/simulador", status_code=status.HTTP_202_ACCEPTED)
async def simulador_chat(
    empresa_id: str,
    payload: SimuladorRequest,
    background_tasks: BackgroundTasks
):
    """
    Fire-and-Forget: salva a mensagem no Redis via handle_debouncer em background
    e retorna 202 imediatamente. A resposta da IA fica disponível via GET /simulador/resposta/{sessao_id}.
    """
    from app.api.schemas import StandardMessage
    from app.api.utils import handle_debouncer
    from app.api.main import redis_client

    # Limpa qualquer resposta anterior desta sessão antes de iniciar novo ciclo
    await redis_client.delete(f"sim_resp:{payload.sessao_id}")

    msg = StandardMessage(
        empresa_id=empresa_id,
        canal="simulador",
        identificador_origem=payload.sessao_id,
        texto_mensagem=payload.mensagem,
        is_human_agent=False
    )
    background_tasks.add_task(handle_debouncer, msg)
    return {"status": "received", "sessao_id": payload.sessao_id}


@router.get("/{empresa_id}/simulador/resposta/{sessao_id}")
async def simulador_poll_resposta(empresa_id: str, sessao_id: str):
    """
    Long-polling: o frontend chama esta rota a cada 2s até receber status='concluido'.
    Quando o LangGraph finaliza, ele grava a resposta no Redis com chave sim_resp:{sessao_id}.
    """
    from app.api.main import redis_client

    resposta_raw = await redis_client.get(f"sim_resp:{sessao_id}")
    if resposta_raw is None:
        return {"status": "processando", "resposta": None}

    resposta = resposta_raw if isinstance(resposta_raw, str) else resposta_raw.decode("utf-8")
    # Consome a entrada após leitura para não poluir Redis
    await redis_client.delete(f"sim_resp:{sessao_id}")
    return {"status": "concluido", "resposta": resposta}

@router.delete("/{empresa_id}/simulador/reset", status_code=status.HTTP_204_NO_CONTENT)
async def resetar_simulador(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Remove fisicamente o Lead Mock do simulador e todo o seu histórico.
    """
    import uuid
    from db.models import CRMLead
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(select(CRMLead).where(
        CRMLead.empresa_id == emp_uuid,
        CRMLead.telefone_contato == "ID_TESTE_SIMULADOR"
    ))
    lead = result.scalars().first()
    
    if not lead:
        return
        
    try:
        await db.delete(lead)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao resetar simulador: {str(e)}")

@router.delete("/{empresa_id}/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deletar_lead(empresa_id: str, lead_id: str, db: AsyncSession = Depends(get_db)):
    """
    Remove fisicamente um Lead e todo o seu histórico de mensagens do banco de dados (Hard Delete).
    """
    import uuid
    from db.models import CRMLead
    try:
        emp_uuid = uuid.UUID(empresa_id)
        l_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa ou lead inválido")

    result = await db.execute(select(CRMLead).where(CRMLead.id == l_uuid, CRMLead.empresa_id == emp_uuid))
    lead = result.scalars().first()
    
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado ou não pertence a esta empresa")
        
    try:
        await db.delete(lead)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao deletar lead: {str(e)}")

