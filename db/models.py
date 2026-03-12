import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    ForeignKey,
    Text,
    Table,
    DateTime,
    Time
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from .database import Base

# N:N Association Table for Agentes and Ferramentas_API
agente_ferramentas = Table(
    "agente_ferramentas",
    Base.metadata,
    Column("agente_id", UUID(as_uuid=True), ForeignKey("agentes.id", ondelete="CASCADE"), primary_key=True),
    Column("ferramenta_id", UUID(as_uuid=True), ForeignKey("ferramentas_api.id", ondelete="CASCADE"), primary_key=True),
)

especialista_tools = Table(
    "especialista_tools",
    Base.metadata,
    Column("especialista_id", UUID(as_uuid=True), ForeignKey("especialistas.id", ondelete="CASCADE"), primary_key=True),
    Column("api_connection_id", UUID(as_uuid=True), ForeignKey("api_connections.id", ondelete="CASCADE"), primary_key=True),
)

especialista_ferramentas = Table(
    "especialista_ferramentas",
    Base.metadata,
    Column("especialista_id", UUID(as_uuid=True), ForeignKey("especialistas.id", ondelete="CASCADE"), primary_key=True),
    Column("ferramenta_id", UUID(as_uuid=True), ForeignKey("ferramentas_api.id", ondelete="CASCADE"), primary_key=True),
)

class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome_empresa = Column(String, nullable=False)
    area_atuacao = Column(String, nullable=True)
    credenciais_canais = Column(JSONB, default={})
    informacoes_adicionais = Column(Text, nullable=True)
    ia_instrucoes_personalizadas = Column(Text, nullable=True)
    ia_tom_voz = Column(String, nullable=True)
    # Agent Configuration
    nome_agente = Column(String, default="Assistente Virtual")
    mensagem_saudacao = Column(String, nullable=True)
    modelo_ia = Column(String, default="gpt-4o-mini")
    modelo_roteador = Column(String, default="gpt-4o-mini")
    # Configurações de Follow-up Automático
    followup_ativo = Column(Boolean, default=False)
    followup_espera_nivel_1_minutos = Column(Integer, default=20)
    followup_espera_nivel_2_minutos = Column(Integer, default=10)
    # Coleta de dados do lead
    coletar_nome = Column(Boolean, default=True)

    # Relationships
    contatos = relationship("Contato", back_populates="empresa", cascade="all, delete-orphan")
    agentes = relationship("Agente", back_populates="empresa", cascade="all, delete-orphan")
    ferramentas = relationship("FerramentaAPI", back_populates="empresa", cascade="all, delete-orphan")
    parametros_cadencia = relationship("ParametrosCadencia", uselist=False, back_populates="empresa", cascade="all, delete-orphan")
    documentos = relationship("DocumentoBase", back_populates="empresa", cascade="all, delete-orphan")
    especialistas = relationship("Especialista", back_populates="empresa", cascade="all, delete-orphan")
    api_connections = relationship("APIConnection", back_populates="empresa", cascade="all, delete-orphan")
    usuarios = relationship("Usuario", back_populates="empresa", cascade="all, delete-orphan")
    conhecimentos_rag = relationship("ConhecimentoRAG", back_populates="empresa", cascade="all, delete-orphan")
    crm_funis = relationship("CRMFunil", back_populates="empresa", cascade="all, delete-orphan")
    crm_leads = relationship("CRMLead", back_populates="empresa", cascade="all, delete-orphan")
    agenda_config = relationship("AgendaConfiguracao", uselist=False, back_populates="empresa", cascade="all, delete-orphan")
    agendamentos_locais = relationship("AgendamentoLocal", back_populates="empresa", cascade="all, delete-orphan")
    integracoes_externas = relationship("IntegracaoExterna", back_populates="empresa", cascade="all, delete-orphan")
    webhooks_saida = relationship("WebhookSaida", back_populates="empresa", cascade="all, delete-orphan")


class Contato(Base):
    __tablename__ = "contatos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=True)
    identificador_origem = Column(String, nullable=False)  # Example: phone number or external ID
    canal_preferencial = Column(String, nullable=False)
    data_primeiro_contato = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="contatos")


class Agente(Base):
    __tablename__ = "agentes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    modelo_ia = Column(String, nullable=False)
    prompt_sistema = Column(Text, nullable=False)
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="agentes")
    ferramentas = relationship("FerramentaAPI", secondary=agente_ferramentas, back_populates="agentes")


class FerramentaAPI(Base):
    __tablename__ = "ferramentas_api"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome_ferramenta = Column(String, nullable=False)
    descricao_ia = Column(Text, nullable=False)
    schema_parametros = Column(JSONB, default={})
    url = Column(String, nullable=True)
    metodo = Column(String, nullable=True, default="GET")
    headers = Column(Text, nullable=True) # JSON formatado como string para simplicidade
    payload = Column(Text, nullable=True) # JSON formatado como string com variáveis {{var}}
    regra_retorno = Column(String, nullable=True)

    empresa = relationship("Empresa", back_populates="ferramentas")
    agentes = relationship("Agente", secondary=agente_ferramentas, back_populates="ferramentas")
    especialistas_vinculados = relationship("Especialista", secondary=especialista_ferramentas, back_populates="ferramentas")


class ParametrosCadencia(Base):
    __tablename__ = "parametros_cadencia"

    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), primary_key=True)
    debouncer_segundos = Column(Integer, default=5)
    veloc_caracteres_seg = Column(Integer, default=50)
    timer_followup_min = Column(Integer, default=1440)  # 24 hours

    empresa = relationship("Empresa", back_populates="parametros_cadencia")


class DocumentoBase(Base):
    __tablename__ = "documentos_base"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome_arquivo = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PROCESSING")

    empresa = relationship("Empresa", back_populates="documentos")
    vetores = relationship("VetorConhecimento", back_populates="documento", cascade="all, delete-orphan")


class VetorConhecimento(Base):
    __tablename__ = "vetores_conhecimento"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    documento_id = Column(UUID(as_uuid=True), ForeignKey("documentos_base.id", ondelete="CASCADE"), nullable=False)
    conteudo_texto = Column(Text, nullable=False)
    # Required: CREATE EXTENSION IF NOT EXISTS vector in your db
    # 1536 is the common dimension for OpenAI's text-embedding-ada-002
    embedding = Column(Vector(1536))

    documento = relationship("DocumentoBase", back_populates="vetores")


class Conhecimento(Base):
    __tablename__ = "conhecimento"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conteudo = Column(Text, nullable=False)
    embedding = Column(Vector(1536))
    
    empresa = relationship("Empresa")

class Especialista(Base):
    __tablename__ = "especialistas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    descricao_missao = Column(String, nullable=True)
    prompt_sistema = Column(Text, nullable=False)
    modelo_ia = Column(String, default="gpt-4o-mini")
    usar_rag = Column(Boolean, default=False)
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="especialistas")
    api_connections = relationship("APIConnection", secondary=especialista_tools, back_populates="especialistas")
    ferramentas = relationship("FerramentaAPI", secondary=especialista_ferramentas, back_populates="especialistas_vinculados")


class APIConnection(Base):
    __tablename__ = "api_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    descricao = Column(String, nullable=True)
    url = Column(String, nullable=False)
    metodo = Column(String, nullable=False, default="GET")
    headers_json = Column(JSONB, default={})
    params_schema_json = Column(JSONB, default={})

    empresa = relationship("Empresa", back_populates="api_connections")
    especialistas = relationship("Especialista", secondary=especialista_tools, back_populates="api_connections")


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=True)
    nome = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    senha_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'super_admin' or 'admin_empresa'
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="usuarios")


class ConhecimentoRAG(Base):
    __tablename__ = "conhecimento_rag"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(String, nullable=False)  # 'texto', 'url', 'pdf'
    conteudo = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="PROCESSING")

    empresa = relationship("Empresa", back_populates="conhecimentos_rag")


class CRMFunil(Base):
    __tablename__ = "crm_funis"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)

    empresa = relationship("Empresa", back_populates="crm_funis")
    etapas = relationship("CRMEtapa", back_populates="funil", cascade="all, delete-orphan")


class CRMEtapa(Base):
    __tablename__ = "crm_etapas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    funil_id = Column(UUID(as_uuid=True), ForeignKey("crm_funis.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    tipo = Column(String, nullable=True)
    ordem = Column(Integer, nullable=False, default=0)

    funil = relationship("CRMFunil", back_populates="etapas")
    leads = relationship("CRMLead", back_populates="etapa")


class CRMLead(Base):
    __tablename__ = "crm_leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    etapa_id = Column(UUID(as_uuid=True), ForeignKey("crm_etapas.id", ondelete="SET NULL"), nullable=True)
    nome_contato = Column(String, nullable=False)
    telefone_contato = Column(String, nullable=True)
    historico_resumo = Column(Text, nullable=True)
    dados_adicionais = Column(JSONB, default={})  # campos extras captados pela IA
    bot_pausado_ate = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="crm_leads")
    etapa = relationship("CRMEtapa", back_populates="leads")
    agendamentos = relationship("AgendamentoLocal", back_populates="lead")
    mensagens = relationship("MensagemHistorico", back_populates="lead", cascade="all, delete-orphan")

class MensagemHistorico(Base):
    __tablename__ = "mensagens_historico"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    texto = Column(Text, nullable=False)
    from_me = Column(Boolean, nullable=False, default=False)
    criado_em = Column(DateTime, default=datetime.utcnow)

    lead = relationship("CRMLead", back_populates="mensagens")



class AgendaConfiguracao(Base):
    __tablename__ = "agenda_configuracoes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, unique=True)
    dias_funcionamento = Column(JSONB, default={})  # ex: seg a sex
    horario_inicio = Column(Time, nullable=False)
    horario_fim = Column(Time, nullable=False)
    duracao_slot_minutos = Column(Integer, nullable=False, default=30)

    empresa = relationship("Empresa", back_populates="agenda_config")


class AgendamentoLocal(Base):
    __tablename__ = "agendamentos_locais"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    data_hora_inicio = Column(DateTime, nullable=False)
    data_hora_fim = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="agendado")

    empresa = relationship("Empresa", back_populates="agendamentos_locais")
    lead = relationship("CRMLead", back_populates="agendamentos")


class IntegracaoExterna(Base):
    __tablename__ = "integracoes_externas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    provedor = Column(String, nullable=False)  # ex: 'google_calendar', 'cal_com', 'webhook_custom'
    credenciais_json = Column(JSONB, default={})
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="integracoes_externas")

class ConfiguracoesGlobais(Base):
    __tablename__ = "configuracoes_globais"

    id = Column(Integer, primary_key=True, default=1)
    nome_sistema = Column(String, nullable=False, default="ANTIGRAVITY")
    cor_primaria = Column(String, nullable=False, default="#6366f1")
    openai_key_global = Column(String, nullable=True)

class WebhookSaida(Base):
    __tablename__ = "webhooks_saida"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, unique=True)
    url = Column(String, nullable=False)
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="webhooks_saida")

