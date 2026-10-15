import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    event,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from .database import Base

ROOT_ADMIN_EMAIL = "admin@ferriolimidias.com"
ROOT_ADMIN_ROLE = "super_admin"
ADMIN_EMPRESA_ROLE = "admin_empresa"
META_APP_REVIEWER_ROLE = "meta_app_reviewer"


def normalize_user_email(email: str | None) -> str:
    return (email or "").strip().lower()


def normalize_user_role(role: str | None) -> str:
    return (role or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_root_admin_email(email: str | None) -> bool:
    return normalize_user_email(email) == ROOT_ADMIN_EMAIL


def is_super_admin_role(role: str | None) -> bool:
    return normalize_user_role(role) == ROOT_ADMIN_ROLE


def is_admin_empresa_role(role: str | None) -> bool:
    return normalize_user_role(role) == ADMIN_EMPRESA_ROLE


def is_meta_app_reviewer_role(role: str | None) -> bool:
    return normalize_user_role(role) == META_APP_REVIEWER_ROLE

# N:N Association Table for Agentes and Ferramentas_API
agente_ferramentas = Table(
    "agente_ferramentas",
    Base.metadata,
    Column("agente_id", UUID(as_uuid=True), ForeignKey("agentes.id", ondelete="CASCADE"), primary_key=True),
    Column("ferramenta_id", UUID(as_uuid=True), ForeignKey("ferramentas_api.id", ondelete="CASCADE"), primary_key=True),
)

class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome_empresa = Column(String, nullable=False)
    area_atuacao = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    # Campo legado mantido durante a migracao para a tabela conexoes.
    credenciais_canais = Column(JSONB, default={})
    openai_api_key = Column(String, nullable=True)
    telefone_notificacao = Column(String, nullable=True)
    status_openai = Column(String, nullable=False, default="ok", server_default=sa_text("'ok'"))
    informacoes_adicionais = Column(Text, nullable=True)
    # Fonte oficial: usa a coluna legada "ia_identidade" com o novo nome semântico.
    ia_personalidade = Column("ia_identidade", Text, nullable=True)
    ia_regras_negocio = Column(Text, nullable=True)
    atendente_prompt = Column(Text, nullable=True)
    # Prompt comercial separado; permanece inativo até revisão/ativação explícita.
    conversion_monitor_prompt = Column(Text, nullable=True)
    # Deprecated por Prompt Bloat:
    # ia_estrategia_vendas = Column(Text, nullable=True)
    # ia_formatacao_whatsapp = Column(Text, nullable=True)
    # ia_tom_voz = Column(String, nullable=True)
    # Agent Configuration
    nome_agente = Column(String, default="Assistente Virtual")
    mensagem_saudacao = Column(String, nullable=True)
    modelo_ia = Column(String, default="gpt-4o-mini")
    # Request profile for a model explicitly validated against the tenant's
    # OpenAI credential. Existing rows keep the legacy provider parameters.
    modelo_ia_request_profile = Column(String(32), nullable=False, default="legacy", server_default=sa_text("'legacy'"))
    limite_certeza = Column(Float, nullable=False, default=0.65)
    limite_duvida = Column(Float, nullable=False, default=0.45)
    max_agentes_desempate = Column(Integer, nullable=False, default=3)
    conexao_disparo_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="SET NULL"), nullable=True)
    disparo_delay_min = Column(Integer, nullable=False, default=3)
    disparo_delay_max = Column(Integer, nullable=False, default=7)
    # Configurações de Follow-up Automático
    followup_ativo = Column(Boolean, default=False)
    followup_espera_nivel_1_minutos = Column(Integer, default=20)
    followup_espera_nivel_2_minutos = Column(Integer, default=10)
    meta_capi_ativo = Column(Boolean, default=False)
    meta_pixel_id = Column(String, nullable=True)
    meta_access_token = Column(String, nullable=True)
    # Coleta de dados do lead
    coletar_nome = Column(Boolean, default=True)

    # Relationships
    contatos = relationship("Contato", back_populates="empresa", cascade="all, delete-orphan")
    agentes = relationship("Agente", back_populates="empresa", cascade="all, delete-orphan")
    ferramentas = relationship("FerramentaAPI", back_populates="empresa", cascade="all, delete-orphan")
    parametros_cadencia = relationship("ParametrosCadencia", uselist=False, back_populates="empresa", cascade="all, delete-orphan")
    usuarios = relationship("Usuario", back_populates="empresa", cascade="all, delete-orphan")
    conhecimentos_rag = relationship("ConhecimentoRAG", back_populates="empresa", cascade="all, delete-orphan")
    crm_funis = relationship("CRMFunil", back_populates="empresa", cascade="all, delete-orphan")
    crm_leads = relationship("CRMLead", back_populates="empresa", cascade="all, delete-orphan")
    agenda_config = relationship("AgendaConfiguracao", uselist=False, back_populates="empresa", cascade="all, delete-orphan")
    agendamentos_locais = relationship("AgendamentoLocal", back_populates="empresa", cascade="all, delete-orphan")
    integracoes_externas = relationship("IntegracaoExterna", back_populates="empresa", cascade="all, delete-orphan")
    webhooks_saida = relationship("WebhookSaida", back_populates="empresa", cascade="all, delete-orphan")
    conexoes = relationship("Conexao", back_populates="empresa", cascade="all, delete-orphan", foreign_keys="Conexao.empresa_id")
    conexao_disparo = relationship("Conexao", back_populates="empresas_como_canal_disparo", foreign_keys=[conexao_disparo_id], post_update=True)
    tags_grupos = relationship("TagGroup", back_populates="empresa", cascade="all, delete-orphan")
    tags_crm = relationship("TagCRM", back_populates="empresa", cascade="all, delete-orphan")
    templates_mensagem = relationship("TemplateMensagem", back_populates="empresa", cascade="all, delete-orphan")
    campanhas_disparo = relationship("CampanhaDisparo", back_populates="empresa", cascade="all, delete-orphan")
    destinos_transferencia = relationship("DestinosTransferencia", back_populates="empresa", cascade="all, delete-orphan")
    historicos_transferencia = relationship("HistoricoTransferencia", back_populates="empresa", cascade="all, delete-orphan")
    unidades = relationship("EmpresaUnidade", back_populates="empresa", cascade="all, delete-orphan")
    followups_config = relationship("ConfigFollowUp", back_populates="empresa", cascade="all, delete-orphan")
    meta_conversion_events = relationship("MetaConversionEvent", back_populates="empresa", cascade="all, delete-orphan")
    campaign_recipients = relationship("CampaignRecipient", back_populates="empresa", cascade="all, delete-orphan")
    message_outbox = relationship("MessageOutbox", back_populates="empresa", cascade="all, delete-orphan")
    followup_executions = relationship("FollowupExecution", back_populates="empresa", cascade="all, delete-orphan")
    communication_consents = relationship("CommunicationConsent", back_populates="empresa", cascade="all, delete-orphan")
    meta_message_templates = relationship("MetaMessageTemplate", back_populates="empresa", cascade="all, delete-orphan")
    meta_template_sync_runs = relationship("MetaTemplateSyncRun", back_populates="empresa", cascade="all, delete-orphan")
    traffic_meta_configs = relationship("TrafficMetaConfig", back_populates="empresa", cascade="all, delete-orphan")
    payment_providers = relationship("EmpresaPaymentProvider", back_populates="empresa", cascade="all, delete-orphan")


class OpenAIModelCatalogSnapshot(Base):
    __tablename__ = "openai_model_catalog_snapshots"
    __table_args__ = (UniqueConstraint("empresa_id", "credential_fingerprint", "provider", "project_context_fingerprint", name="uq_openai_catalog_scope"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_fingerprint = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False, default="openai", server_default=sa_text("'openai'"))
    project_context_fingerprint = Column(String(64), nullable=False, default="default", server_default=sa_text("'default'"))
    models_json = Column(JSONB, nullable=False, default=list)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class OpenAIModelValidation(Base):
    __tablename__ = "openai_model_validations"
    __table_args__ = (
        UniqueConstraint("empresa_id", "credential_fingerprint", "model_id", name="uq_openai_model_validation_scope"),
        CheckConstraint("validation_status IN ('compatible','incompatible')", name="ck_openai_model_validation_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_fingerprint = Column(String(64), nullable=False)
    model_id = Column(String(255), nullable=False)
    endpoint = Column(String(64), nullable=False, default="chat.completions", server_default=sa_text("'chat.completions'"))
    validation_status = Column(String(24), nullable=False)
    validated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class A11PromptVersion(Base):
    __tablename__ = "a11_prompt_versions"
    __table_args__ = (
        UniqueConstraint("empresa_id", "module_key", "version_number", name="uq_a11_prompt_version"),
        Index("ix_a11_prompt_versions_tenant_module", "empresa_id", "module_key", "version_number"),
        CheckConstraint("version_number > 0", name="ck_a11_prompt_version_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    module_key = Column(String(64), nullable=False)
    version_number = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    source_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="SET NULL"), nullable=True)
    change_summary = Column(Text, nullable=False)
    hypothesis = Column(Text, nullable=False)
    primary_metric = Column(String(64), nullable=False)
    author_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class A11PromptModuleState(Base):
    __tablename__ = "a11_prompt_module_states"
    __table_args__ = (
        UniqueConstraint("empresa_id", "module_key", name="uq_a11_module_state"),
        CheckConstraint("state IN ('OFFLINE','SHADOW','READY_FOR_PILOT','LIVE_DISABLED','PAUSED','ROLLED_BACK')", name="ck_a11_module_state"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    module_key = Column(String(64), nullable=False)
    stable_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=True)
    active_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=True)
    state = Column(String(24), nullable=False, default="OFFLINE", server_default=sa_text("'OFFLINE'"))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class A11OptimizerSettings(Base):
    __tablename__ = "a11_optimizer_settings"
    __table_args__ = (UniqueConstraint("empresa_id", name="uq_a11_optimizer_settings_tenant"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    evaluator_model_id = Column(String(255), nullable=True)
    evaluator_enabled = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    updated_by = Column(String(128), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class A11PromptExperiment(Base):
    __tablename__ = "a11_prompt_experiments"
    __table_args__ = (
        Index("ix_a11_experiment_tenant_state", "empresa_id", "status"),
        Index("uq_a11_one_running_experiment_per_tenant", "empresa_id", unique=True, postgresql_where=sa_text("status = 'RUNNING'")),
        CheckConstraint("assignment_unit = 'conversation'", name="ck_a11_assignment_unit"),
        CheckConstraint("candidate_percent BETWEEN 1 AND 99", name="ck_a11_candidate_percent"),
        CheckConstraint("status IN ('DRAFT','READY_FOR_PILOT','RUNNING','PAUSED','COMPLETED','ROLLED_BACK')", name="ck_a11_experiment_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    module_key = Column(String(64), nullable=False)
    control_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    candidate_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    assignment_unit = Column(String(32), nullable=False, default="conversation", server_default=sa_text("'conversation'"))
    candidate_percent = Column(Integer, nullable=False, default=50, server_default=sa_text("50"))
    status = Column(String(24), nullable=False, default="DRAFT", server_default=sa_text("'DRAFT'"))
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    criteria_json = Column(JSONB, nullable=False, default=dict)
    last_auto_evaluated_at = Column(DateTime, nullable=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class A11PromptAssignment(Base):
    __tablename__ = "a11_prompt_assignments"
    __table_args__ = (
        UniqueConstraint("empresa_id", "experiment_id", "conversation_id", name="uq_a11_assignment_unit"),
        Index("ix_a11_assignments_tenant_state", "empresa_id", "status", "assigned_at"),
        Index("ix_a11_assignments_tenant_experiment_variant", "empresa_id", "experiment_id", "variant"),
        CheckConstraint("variant IN ('control','candidate')", name="ck_a11_assignment_variant"),
        CheckConstraint("status IN ('assigned','exposed','generation_failed','guard_blocked')", name="ck_a11_assignment_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_experiments.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    variant = Column(String(16), nullable=False)
    prompt_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    assignment_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="assigned", server_default=sa_text("'assigned'"))
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))
    completed_at = Column(DateTime, nullable=True)
    generation_attempt_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    generation_failure_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    guard_block_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))


class A11PromptExposure(Base):
    __tablename__ = "a11_prompt_exposures"
    __table_args__ = (
        UniqueConstraint("empresa_id", "experiment_id", "conversation_id", name="uq_a11_exposure_unit"),
        Index("ix_a11_exposures_tenant_time", "empresa_id", "exposed_at"),
        Index("ix_a11_exposures_tenant_experiment_time", "empresa_id", "experiment_id", "exposed_at"),
        CheckConstraint("variant IN ('control','candidate')", name="ck_a11_exposure_variant"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_experiments.id", ondelete="CASCADE"), nullable=False)
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_assignments.id", ondelete="CASCADE"), nullable=False, unique=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    variant = Column(String(16), nullable=False)
    prompt_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    assignment_hash = Column(String(64), nullable=False)
    exposed_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))
    generation_latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    guard_allowed = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))


class A11PromptEvaluation(Base):
    __tablename__ = "a11_prompt_evaluations"
    __table_args__ = (
        Index("ix_a11_eval_tenant_version", "empresa_id", "prompt_version_id"),
        Index("ix_a11_eval_tenant_version_time", "empresa_id", "prompt_version_id", "evaluated_at"),
        CheckConstraint("source_kind IN ('synthetic','anonymized')", name="ck_a11_eval_source_kind"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    prompt_version_id = Column(UUID(as_uuid=True), ForeignKey("a11_prompt_versions.id", ondelete="CASCADE"), nullable=False)
    suite_version = Column(String(32), nullable=False)
    source_kind = Column(String(24), nullable=False, default="synthetic", server_default=sa_text("'synthetic'"))
    result_json = Column(JSONB, nullable=False)
    passed = Column(Boolean, nullable=False)
    evaluated_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class A11PromptAudit(Base):
    __tablename__ = "a11_prompt_audits"
    __table_args__ = (
        Index("ix_a11_audit_tenant_time", "empresa_id", "created_at"),
        Index("ix_a11_audit_tenant_version_approval", "empresa_id", "object_type", "action", "object_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    actor_id = Column(String(128), nullable=True)
    action = Column(String(48), nullable=False)
    object_type = Column(String(32), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=True)
    details_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))


class EmpresaPaymentProvider(Base):
    __tablename__ = "empresa_payment_providers"
    __table_args__ = (
        UniqueConstraint("empresa_id", "provider", name="uq_empresa_payment_providers_empresa_provider"),
        CheckConstraint("provider IN ('mercado_pago')", name="ck_empresa_payment_providers_provider"),
        CheckConstraint("environment IN ('test', 'production')", name="ck_empresa_payment_providers_environment"),
        Index("ix_empresa_payment_providers_empresa_enabled", "empresa_id", "enabled"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(40), nullable=False)
    environment = Column(String(20), nullable=False, default="test", server_default=sa_text("'test'"))
    enabled = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    access_token_encrypted = Column(Text, nullable=True)
    webhook_secret_encrypted = Column(Text, nullable=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))
    atualizado_em = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=sa_text("CURRENT_TIMESTAMP"))

    empresa = relationship("Empresa", back_populates="payment_providers")


class EmpresaUnidade(Base):
    __tablename__ = "empresa_unidades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome_unidade = Column(String, nullable=False)
    endereco_completo = Column(Text, nullable=False)
    link_google_maps = Column(String, nullable=True)
    horario_funcionamento = Column(String, nullable=True)
    is_matriz = Column(Boolean, nullable=False, default=False)

    empresa = relationship("Empresa", back_populates="unidades")


class TipoConexao(str, enum.Enum):
    EVOLUTION = "evolution"
    META = "meta"
    INSTAGRAM = "instagram"


class Conexao(Base):
    __tablename__ = "conexoes"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_conexoes_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(Enum(TipoConexao, name="tipo_conexao_enum"), nullable=False)
    channel = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    nome_instancia = Column(String, nullable=False)
    credenciais = Column(JSONB, default={})
    status = Column(String, nullable=False, default="ativo")
    is_default = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    external_account_id = Column(String, nullable=True)
    waba_id = Column(String, nullable=True)
    phone_number_id = Column(String, nullable=True)
    instagram_business_account_id = Column(String, nullable=True)
    page_id = Column(String, nullable=True)
    credential_source = Column(String, nullable=False, default="tenant_connection", server_default=sa_text("'tenant_connection'"))
    display_number = Column(String, nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="conexoes", foreign_keys=[empresa_id])
    empresas_como_canal_disparo = relationship("Empresa", back_populates="conexao_disparo", foreign_keys="Empresa.conexao_disparo_id")
    mensagens_historico = relationship(
        "MensagemHistorico", back_populates="conexao", foreign_keys="MensagemHistorico.conexao_id"
    )
    channel_contacts = relationship(
        "ChannelContact", back_populates="conexao", cascade="all, delete-orphan", foreign_keys="ChannelContact.conexao_id"
    )
    channel_conversations = relationship(
        "ChannelConversation",
        back_populates="conexao",
        cascade="all, delete-orphan",
        foreign_keys="ChannelConversation.conexao_id",
    )
    campaign_recipients = relationship("CampaignRecipient", back_populates="conexao", foreign_keys="CampaignRecipient.conexao_id")
    message_outbox = relationship("MessageOutbox", back_populates="conexao", foreign_keys="MessageOutbox.conexao_id")
    followup_executions = relationship("FollowupExecution", back_populates="conexao", foreign_keys="FollowupExecution.conexao_id")
    meta_message_templates = relationship("MetaMessageTemplate", back_populates="conexao", foreign_keys="MetaMessageTemplate.conexao_id")
    meta_template_sync_runs = relationship("MetaTemplateSyncRun", back_populates="conexao", foreign_keys="MetaTemplateSyncRun.conexao_id")
    traffic_meta_configs = relationship("TrafficMetaConfig", back_populates="whatsapp_connection", foreign_keys="TrafficMetaConfig.whatsapp_connection_id")


class TrafficMetaConfig(Base):
    __tablename__ = "traffic_meta_configs"
    __table_args__ = (
        UniqueConstraint("empresa_id", name="uq_traffic_meta_configs_empresa_id"),
        UniqueConstraint("empresa_id", "id", name="uq_traffic_meta_configs_empresa_id_id"),
        UniqueConstraint("ad_account_id", name="uq_traffic_meta_configs_ad_account_id"),
        UniqueConstraint("empresa_id", "ad_account_id", name="uq_traffic_meta_configs_empresa_ad_account"),
        ForeignKeyConstraint(
            ["empresa_id", "whatsapp_connection_id"],
            ["conexoes.empresa_id", "conexoes.id"],
            name="fk_traffic_meta_config_empresa_whatsapp_connection",
        ),
        Index("ix_traffic_meta_configs_empresa_ad_account", "empresa_id", "ad_account_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    business_id = Column(String, nullable=True)
    ad_account_id = Column(String, nullable=True)
    page_id = Column(String, nullable=True)
    instagram_account_id = Column(String, nullable=True)
    whatsapp_connection_id = Column(UUID(as_uuid=True), nullable=True)
    waba_id = Column(String, nullable=True)
    phone_number_id = Column(String, nullable=True)
    dataset_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft", server_default=sa_text("'draft'"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    empresa = relationship("Empresa", back_populates="traffic_meta_configs")
    whatsapp_connection = relationship("Conexao", back_populates="traffic_meta_configs", foreign_keys=[whatsapp_connection_id])


class MetaMarketingTenantAccountGrant(Base):
    """Explicit tenant/account authorization for the read-only admin Marketing credential."""

    __tablename__ = "meta_marketing_tenant_account_grants"
    __table_args__ = (
        UniqueConstraint("empresa_id", "ad_account_id", name="uq_meta_marketing_grant_empresa_account"),
        ForeignKeyConstraint(
            ["empresa_id", "ad_account_id"],
            ["traffic_meta_configs.empresa_id", "traffic_meta_configs.ad_account_id"],
            name="fk_meta_marketing_grant_tenant_account",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "credential_source = 'ADMIN_MANAGED_MARKETING_TOKEN'",
            name="ck_meta_marketing_grant_credential_source",
        ),
        CheckConstraint(
            "verification_method IN ('business_owner', 'delegated_client_ad_account')",
            name="ck_meta_marketing_grant_verification_method",
        ),
        CheckConstraint("length(trim(business_id)) > 0", name="ck_meta_marketing_grant_business_id"),
        CheckConstraint("length(trim(ad_account_id)) > 0", name="ck_meta_marketing_grant_ad_account_id"),
        Index("ix_meta_marketing_grants_empresa_enabled", "empresa_id", "read_enabled"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    business_id = Column(String(100), nullable=False)
    ad_account_id = Column(String(100), nullable=False)
    credential_source = Column(String(48), nullable=False, server_default=sa_text("'ADMIN_MANAGED_MARKETING_TOKEN'"))
    read_enabled = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    verification_method = Column(String(40), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class TrafficMetaCampaignCreation(Base):
    __tablename__ = "traffic_meta_campaign_creations"
    __table_args__ = (
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_traffic_meta_campaign_creations_idempotency"),
        ForeignKeyConstraint(
            ["empresa_id", "traffic_meta_config_id"],
            ["traffic_meta_configs.empresa_id", "traffic_meta_configs.id"],
            name="fk_traffic_meta_creation_empresa_config",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "whatsapp_connection_id"],
            ["conexoes.empresa_id", "conexoes.id"],
            name="fk_traffic_meta_creation_empresa_whatsapp_connection",
        ),
        Index("ix_traffic_meta_campaign_creations_empresa_status", "empresa_id", "status"),
        Index("ix_traffic_meta_campaign_creations_meta_campaign", "empresa_id", "meta_campaign_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    traffic_meta_config_id = Column(UUID(as_uuid=True), nullable=False)
    ad_account_id = Column(String, nullable=True)
    business_id = Column(String, nullable=True)
    whatsapp_connection_id = Column(UUID(as_uuid=True), nullable=True)
    idempotency_key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    adset_name = Column(String, nullable=False)
    daily_budget_cents = Column(Integer, nullable=False)
    location_type = Column(String, nullable=False)
    page_id = Column(String, nullable=False)
    instagram_account_id = Column(String, nullable=True)
    phone_number_id = Column(String, nullable=False)
    whatsapp_phone_number = Column(String, nullable=True)
    waba_id = Column(String, nullable=True)
    dataset_id = Column(String, nullable=True)
    campaign_sequence = Column(Integer, nullable=True)
    meta_campaign_id = Column(String, nullable=True)
    meta_adset_id = Column(String, nullable=True)
    campaign_status = Column(String, nullable=True)
    adset_status = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", server_default=sa_text("'pending'"))
    payload_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    response_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    error_json = Column(JSONB, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class InstagramCommentAutomation(Base):
    __tablename__ = "instagram_comment_automations"
    __table_args__ = (Index("ix_ig_comment_automations_empresa_active", "empresa_id", "active"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="SET NULL"), nullable=True)
    instagram_account_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    media_id = Column(String, nullable=True)
    keywords = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    public_reply_enabled = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    public_reply_mode = Column(String, nullable=False, default="fixed", server_default=sa_text("'fixed'"))
    fixed_reply = Column(Text, nullable=True)
    ai_examples = Column(Text, nullable=True)
    tone_instructions = Column(Text, nullable=True)
    fallback_reply = Column(Text, nullable=False)
    private_reply_enabled = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    private_message = Column(Text, nullable=True)
    private_link = Column(Text, nullable=True)
    private_reply_config = Column(JSONB, nullable=True)
    recent_public_replies = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class InstagramCommentExecution(Base):
    __tablename__ = "instagram_comment_executions"
    __table_args__ = (
        UniqueConstraint("empresa_id", "instagram_account_id", "comment_id", "automation_id", name="uq_ig_comment_execution_idempotency"),
        Index("ix_ig_comment_executions_empresa_created", "empresa_id", "criado_em"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="SET NULL"), nullable=True)
    automation_id = Column(UUID(as_uuid=True), ForeignKey("instagram_comment_automations.id", ondelete="CASCADE"), nullable=False)
    instagram_account_id = Column(String, nullable=False)
    comment_id = Column(String, nullable=False)
    media_id = Column(String, nullable=True)
    username = Column(String, nullable=True)
    comment_text = Column(Text, nullable=True)
    public_reply = Column(Text, nullable=True)
    public_reply_origin = Column(String, nullable=True)
    public_reply_status = Column(String, nullable=False, default="public_reply_pending")
    private_reply_status = Column(String, nullable=False, default="private_reply_pending")
    private_reply = Column(Text, nullable=True)
    webhook_event_id = Column(UUID(as_uuid=True), ForeignKey("instagram_webhook_events.id", ondelete="SET NULL"), nullable=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("mensagens_historico.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="pending")
    error_detail = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class InstagramWebhookEvent(Base):
    __tablename__ = "instagram_webhook_events"
    __table_args__ = (
        UniqueConstraint("connection_id", "comment_id", name="uq_instagram_webhook_event_comment"),
        Index("ix_instagram_webhook_events_empresa_created", "empresa_id", "criado_em"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(String, nullable=False)
    media_id = Column(String, nullable=True)
    sender_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="received", server_default=sa_text("'received'"))
    error_detail = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ChannelContact(Base):
    __tablename__ = "channel_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["empresa_id", "conexao_id"],
            ["conexoes.empresa_id", "conexoes.id"],
            name="fk_channel_contacts_empresa_conexao",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "lead_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_channel_contacts_empresa_lead",
        ),
        UniqueConstraint(
            "empresa_id",
            "channel",
            "provider",
            "conexao_id",
            "external_contact_id",
            name="uq_channel_contacts_identity",
        ),
        UniqueConstraint("empresa_id", "id", name="uq_channel_contacts_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    external_contact_id = Column(String, nullable=False)
    normalized_address = Column(String, nullable=True)
    phone_e164 = Column(String, nullable=True)
    username = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    profile_photo_url = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa")
    conexao = relationship("Conexao", back_populates="channel_contacts", foreign_keys=[conexao_id])
    lead = relationship("CRMLead", back_populates="channel_contacts", foreign_keys=[lead_id])
    conversations = relationship(
        "ChannelConversation",
        back_populates="contact",
        cascade="all, delete-orphan",
        foreign_keys="ChannelConversation.contact_id",
    )
    campaign_recipients = relationship("CampaignRecipient", back_populates="contact", foreign_keys="CampaignRecipient.contact_id")
    followup_executions = relationship("FollowupExecution", back_populates="contact", foreign_keys="FollowupExecution.contact_id")


class ChannelConversation(Base):
    __tablename__ = "channel_conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["empresa_id", "conexao_id"],
            ["conexoes.empresa_id", "conexoes.id"],
            name="fk_channel_conversations_empresa_conexao",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "contact_id"],
            ["channel_contacts.empresa_id", "channel_contacts.id"],
            name="fk_channel_conversations_empresa_contact",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "lead_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_channel_conversations_empresa_lead",
        ),
        UniqueConstraint(
            "empresa_id",
            "channel",
            "provider",
            "conexao_id",
            "conversation_key",
            name="uq_channel_conversations_identity",
        ),
        UniqueConstraint("empresa_id", "id", name="uq_channel_conversations_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("channel_contacts.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    conversation_key = Column(String, nullable=False)
    external_conversation_id = Column(String, nullable=False)
    external_thread_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open", server_default=sa_text("'open'"))
    last_message_at = Column(DateTime, nullable=True)
    last_inbound_at = Column(DateTime, nullable=True)
    last_outbound_at = Column(DateTime, nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa")
    conexao = relationship("Conexao", back_populates="channel_conversations", foreign_keys=[conexao_id])
    contact = relationship("ChannelContact", back_populates="conversations", foreign_keys=[contact_id])
    lead = relationship("CRMLead", back_populates="channel_conversations", foreign_keys=[lead_id])
    mensagens = relationship(
        "MensagemHistorico",
        back_populates="channel_conversation",
        foreign_keys="MensagemHistorico.channel_conversation_id",
    )
    campaign_recipients = relationship("CampaignRecipient", back_populates="conversation", foreign_keys="CampaignRecipient.conversation_id")
    followup_executions = relationship("FollowupExecution", back_populates="conversation", foreign_keys="FollowupExecution.conversation_id")


class CampanhaDisparoStatus(str, enum.Enum):
    PENDENTE = "pendente"
    EXECUTANDO = "executando"
    CONCLUIDO = "concluido"
    ERRO = "erro"


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

class ParametrosCadencia(Base):
    __tablename__ = "parametros_cadencia"

    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), primary_key=True)
    debouncer_segundos = Column(Integer, default=5)
    veloc_caracteres_seg = Column(Integer, default=50)
    timer_followup_min = Column(Integer, default=1440)  # 24 hours

    empresa = relationship("Empresa", back_populates="parametros_cadencia")


class Conhecimento(Base):
    __tablename__ = "conhecimento"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conteudo = Column(Text, nullable=False)
    source_name = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    embedding = Column(Vector(1536))
    
    empresa = relationship("Empresa")


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


def _enforce_root_admin_permissions(target: "Usuario") -> None:
    if not is_root_admin_email(getattr(target, "email", None)):
        return

    target.role = ROOT_ADMIN_ROLE
    target.empresa_id = None
    target.ativo = True

    if hasattr(target, "is_superuser"):
        target.is_superuser = True


@event.listens_for(Usuario, "before_insert")
def _usuario_before_insert(_mapper, _connection, target: "Usuario") -> None:
    _enforce_root_admin_permissions(target)


@event.listens_for(Usuario, "before_update")
def _usuario_before_update(_mapper, _connection, target: "Usuario") -> None:
    _enforce_root_admin_permissions(target)


class ConhecimentoRAG(Base):
    __tablename__ = "conhecimento_rag"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(String, nullable=False)  # 'texto', 'url', 'pdf'
    conteudo = Column(Text, nullable=False)
    source_name = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
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
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_crm_leads_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    etapa_id = Column(UUID(as_uuid=True), ForeignKey("crm_etapas.id", ondelete="SET NULL"), nullable=True)
    nome_contato = Column(String, nullable=False)
    telefone_contato = Column(String, nullable=True)
    historico_resumo = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=False, default=list)
    dados_adicionais = Column(JSONB, default={})  # campos extras captados pela IA
    status_atendimento = Column(String, nullable=False, default="aberto")
    foto_url = Column(String, nullable=True)
    foto_atualizada_em = Column(DateTime, nullable=True)
    gclid = Column(String, nullable=True)
    fbclid = Column(String, nullable=True)
    valor_conversao = Column(Float, nullable=True)
    bot_pausado_ate = Column(DateTime, nullable=True)
    ia_ativa = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="crm_leads")
    etapa = relationship("CRMEtapa", back_populates="leads")
    agendamentos = relationship("AgendamentoLocal", back_populates="lead")
    mensagens = relationship(
        "MensagemHistorico",
        back_populates="lead",
        cascade="all, delete-orphan",
        foreign_keys="MensagemHistorico.lead_id",
    )
    channel_contacts = relationship("ChannelContact", back_populates="lead", foreign_keys="ChannelContact.lead_id")
    channel_conversations = relationship(
        "ChannelConversation", back_populates="lead", foreign_keys="ChannelConversation.lead_id"
    )
    historicos_transferencia = relationship("HistoricoTransferencia", back_populates="lead", cascade="all, delete-orphan")
    followup_logs = relationship("LeadFollowUpLog", back_populates="lead", cascade="all, delete-orphan")
    campaign_recipients = relationship("CampaignRecipient", back_populates="lead", foreign_keys="CampaignRecipient.lead_id")
    followup_executions = relationship("FollowupExecution", back_populates="lead", foreign_keys="FollowupExecution.lead_id")
    communication_consents = relationship("CommunicationConsent", back_populates="lead", foreign_keys="CommunicationConsent.lead_id")


class ConfigFollowUp(Base):
    __tablename__ = "config_followups"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_config_followups_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    tempo_gatilho_minutos = Column(Integer, nullable=False)
    objetivo_prompt = Column(Text, nullable=False)
    tag_aplicar_final = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="SET NULL"), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="followups_config")
    logs = relationship("LeadFollowUpLog", back_populates="config_followup", cascade="all, delete-orphan")
    tag_final = relationship("TagCRM")
    executions = relationship(
        "FollowupExecution",
        back_populates="followup",
        cascade="all, delete-orphan",
        foreign_keys="FollowupExecution.followup_id",
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id"], ["empresas.id"], name="fk_media_assets_empresa"),
        UniqueConstraint("empresa_id", "content_hash", name="uq_media_assets_empresa_hash"),
        Index("ix_media_assets_empresa_status", "empresa_id", "status", "media_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), nullable=False)
    media_type = Column(String, nullable=False, default="audio", server_default=sa_text("'audio'"))
    original_name = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    byte_size = Column(Integer, nullable=False)
    content_hash = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="ready", server_default=sa_text("'ready'"))
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa")


class LeadFollowUpLog(Base):
    __tablename__ = "lead_followup_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    config_followup_id = Column(UUID(as_uuid=True), ForeignKey("config_followups.id", ondelete="CASCADE"), nullable=False)
    data_envio = Column(DateTime, nullable=False, default=datetime.utcnow)
    status_envio = Column(String, nullable=False, default="enviado")
    criado_em = Column(DateTime, default=datetime.utcnow)

    lead = relationship("CRMLead", back_populates="followup_logs")
    config_followup = relationship("ConfigFollowUp", back_populates="logs")


class CRMTagEvent(Base):
    __tablename__ = "crm_tag_events"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_crm_tag_events_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "tag_id"], ["tags_crm.empresa_id", "tags_crm.id"], name="fk_crm_tag_events_empresa_tag"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_crm_tag_events_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conversation_id"], ["channel_conversations.empresa_id", "channel_conversations.id"], name="fk_crm_tag_events_empresa_conversation"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_crm_tag_events_idempotency"),
        UniqueConstraint("empresa_id", "id", name="uq_crm_tag_events_empresa_id_id"),
        Index("ix_crm_tag_events_lookup", "empresa_id", "tag_id", "lead_id", "action", "occurred_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("channel_contacts.id", ondelete="SET NULL"), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("channel_conversations.id", ondelete="SET NULL"), nullable=True)
    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="CASCADE"), nullable=False)
    action = Column(String, nullable=False)
    source = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    source_reference_id = Column(String, nullable=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    idempotency_key = Column(String, nullable=False)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactImportBatch(Base):
    __tablename__ = "contact_import_batches"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "tag_id"], ["tags_crm.empresa_id", "tags_crm.id"], name="fk_contact_import_batches_empresa_tag"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_contact_import_batches_idempotency"),
        UniqueConstraint("empresa_id", "id", name="uq_contact_import_batches_empresa_id_id"),
        Index("ix_contact_import_batches_lookup", "empresa_id", "tag_id", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String, nullable=False)
    source = Column(String, nullable=False, default="importacao_manual")
    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="RESTRICT"), nullable=False)
    status = Column(String, nullable=False, default="completed", server_default=sa_text("'completed'"))
    total_rows = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    inserted_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    updated_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    duplicate_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    invalid_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    conflict_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    blocked_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    opted_out_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    unknown_consent_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    opted_in_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    initiated_by = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String, nullable=False)
    mapping_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    error_summary = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ContactImportError(Base):
    __tablename__ = "contact_import_errors"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "batch_id"], ["contact_import_batches.empresa_id", "contact_import_batches.id"], name="fk_contact_import_errors_empresa_batch"),
        Index("ix_contact_import_errors_batch", "empresa_id", "batch_id", "row_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("contact_import_batches.id", ondelete="CASCADE"), nullable=False)
    row_number = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    sanitized_row_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow)


class SavedAudience(Base):
    __tablename__ = "saved_audiences"
    __table_args__ = (
        UniqueConstraint("empresa_id", "name", name="uq_saved_audiences_empresa_name"),
        UniqueConstraint("empresa_id", "id", name="uq_saved_audiences_empresa_id_id"),
        Index("ix_saved_audiences_lookup", "empresa_id", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active", server_default=sa_text("'active'"))
    rules_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    preview_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FollowupSequenceStep(Base):
    __tablename__ = "followup_sequence_steps"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "followup_id"], ["config_followups.empresa_id", "config_followups.id"], name="fk_followup_sequence_steps_empresa_followup"),
        ForeignKeyConstraint(["empresa_id", "trigger_tag_id"], ["tags_crm.empresa_id", "tags_crm.id"], name="fk_followup_sequence_steps_empresa_trigger_tag"),
        ForeignKeyConstraint(["empresa_id", "template_id"], ["meta_message_templates.empresa_id", "meta_message_templates.id"], name="fk_followup_sequence_steps_empresa_template"),
        ForeignKeyConstraint(["empresa_id", "post_send_tag_id"], ["tags_crm.empresa_id", "tags_crm.id"], name="fk_followup_sequence_steps_empresa_post_tag"),
        UniqueConstraint("empresa_id", "followup_id", "step_order", name="uq_followup_sequence_steps_order"),
        UniqueConstraint("empresa_id", "id", name="uq_followup_sequence_steps_empresa_id_id"),
        Index("ix_followup_sequence_steps_lookup", "empresa_id", "followup_id", "trigger_tag_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    followup_id = Column(UUID(as_uuid=True), ForeignKey("config_followups.id", ondelete="CASCADE"), nullable=False)
    step_order = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="active", server_default=sa_text("'active'"))
    trigger_tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="RESTRICT"), nullable=True)
    delay_minutes = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    timezone = Column(String, nullable=False, default="America/Sao_Paulo", server_default=sa_text("'America/Sao_Paulo'"))
    ad_window_mode = Column(String, nullable=False, default="template_after_expiry", server_default=sa_text("'template_after_expiry'"))
    message_mode = Column(String, nullable=False, default="fixed_text", server_default=sa_text("'fixed_text'"))
    message_free_text = Column(Text, nullable=True)
    content_type = Column(String, nullable=False, default="text", server_default=sa_text("'text'"))
    media_asset_id = Column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    send_as_voice = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    audio_caption = Column(Text, nullable=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("meta_message_templates.id", ondelete="SET NULL"), nullable=True)
    variable_mapping_json = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    execute_conditions_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    cancel_conditions_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    post_send_tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="SET NULL"), nullable=True)
    actions_json = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    send_window_enabled = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    send_window_start = Column(Time, nullable=True)
    send_window_end = Column(Time, nullable=True)
    limit_to_free_entry_window = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TagGroup(Base):
    __tablename__ = "tags_grupos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    cor = Column(String, nullable=True)
    ordem = Column(Integer, nullable=False, default=0)

    empresa = relationship("Empresa", back_populates="tags_grupos")
    tags = relationship("TagCRM", back_populates="grupo")


class TagCRM(Base):
    __tablename__ = "tags_crm"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_tags_crm_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    grupo_id = Column(UUID(as_uuid=True), ForeignKey("tags_grupos.id", ondelete="SET NULL"), nullable=True)
    nome = Column(String, nullable=False)
    cor = Column(String, nullable=False, default="#2563eb")
    tipo = Column(String, nullable=False, default="comportamento")
    ordem = Column(Integer, nullable=True)
    ativa_no_funil = Column(Boolean, nullable=False, default=False)
    pausa_permanente = Column(Boolean, nullable=False, default=False, server_default=sa_text('false'))
    instrucao_ia = Column(Text, nullable=True)
    ai_can_apply = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    disparar_conversao_ads = Column(Boolean, nullable=False, default=False)
    acao_fechamento = Column(Boolean, nullable=False, default=False)
    acao_transferir_humano = Column(Boolean, default=False, server_default=sa_text('false'))
    mensagem_transferencia = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="tags_crm")
    grupo = relationship("TagGroup", back_populates="tags")
    meta_conversion_events = relationship("MetaConversionEvent", back_populates="tag")


class MetaConversionEvent(Base):
    __tablename__ = "meta_conversion_events"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_meta_conversion_events_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="SET NULL"), nullable=True)
    event_name = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    reservation_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", server_default=sa_text("'pending'"))
    payload_hash = Column(String, nullable=True)
    payload_preview = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    response_preview = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    last_attempt_at = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    test_event_code = Column(String, nullable=True)
    source = Column(String, nullable=False, default="tag", server_default=sa_text("'tag'"))
    conversion_context = Column(String, nullable=False, default="current", server_default=sa_text("'current'"))
    action_source = Column(String, nullable=True)
    messaging_channel = Column(String, nullable=True)
    credential_source = Column(String, nullable=True)
    waba_id_masked = Column(String, nullable=True)
    dataset_id_masked = Column(String, nullable=True)
    ctwa_present = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    trigger_type = Column(String, nullable=True)
    trigger_ref = Column(String, nullable=True)
    trigger_source = Column(String, nullable=True)
    trigger_source_event_id = Column(String, nullable=True)

    empresa = relationship("Empresa", back_populates="meta_conversion_events")
    lead = relationship("CRMLead")
    tag = relationship("TagCRM", back_populates="meta_conversion_events")


class CanonicalCommercialEvent(Base):
    """Internal commercial fact; external Meta delivery is tracked separately."""

    __tablename__ = "commercial_events"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_commercial_events_empresa_id_id"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_commercial_events_idempotency"),
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_commercial_events_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_commercial_events_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conversation_id"], ["channel_conversations.empresa_id", "channel_conversations.id"], name="fk_commercial_events_empresa_conversation"),
        ForeignKeyConstraint(["empresa_id", "message_id"], ["mensagens_historico.empresa_id", "mensagens_historico.id"], name="fk_commercial_events_empresa_message"),
        ForeignKeyConstraint(["empresa_id", "tag_event_id"], ["crm_tag_events.empresa_id", "crm_tag_events.id"], name="fk_commercial_events_empresa_tag_event"),
        ForeignKeyConstraint(["empresa_id", "attribution_touch_id"], ["attribution_touches.empresa_id", "attribution_touches.id"], name="fk_commercial_events_empresa_attribution_touch"),
        ForeignKeyConstraint(["empresa_id", "attribution_resolution_id"], ["attribution_touch_resolutions.empresa_id", "attribution_touch_resolutions.id"], name="fk_commercial_events_empresa_attribution_resolution"),
        CheckConstraint("event_name IN ('ViewContent','LeadSubmitted','QualifiedLead','AddToCart','InitiateCheckout','OrderCreated','CartAbandoned','Purchase','Refund','OrderShipped','OrderDelivered','OrderCanceled','OrderReturned','RatingProvided','ReviewProvided')", name="ck_commercial_events_name"),
        CheckConstraint("external_dispatch_policy IN ('historical_no_send','disabled','not_applicable')", name="ck_commercial_events_dispatch_policy"),
        CheckConstraint("NOT is_backfilled OR external_dispatch_policy = 'historical_no_send'", name="ck_commercial_events_backfill_no_send"),
        CheckConstraint("(value IS NULL AND currency IS NULL) OR (value IS NOT NULL AND currency IS NOT NULL AND char_length(currency)=3)", name="ck_commercial_events_value_currency"),
        CheckConstraint("jsonb_typeof(evidence_json)='object' AND jsonb_typeof(metadata_json)='object' AND jsonb_typeof(attribution_snapshot)='object'", name="ck_commercial_events_json_objects"),
        Index("ix_commercial_events_tenant_time", "empresa_id", "occurred_at"),
        Index("ix_commercial_events_tenant_name_time", "empresa_id", "event_name", "occurred_at"),
        Index("ix_commercial_events_lead_time", "empresa_id", "lead_id", "occurred_at"),
        Index("ix_commercial_events_campaign_time", "empresa_id", "last_campaign_id", "occurred_at"),
        Index("ix_commercial_events_payment", "empresa_id", "provider", "payment_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    event_name = Column(String(80), nullable=False)
    event_category = Column(String(40), nullable=False)
    source_type = Column(String(60), nullable=False)
    source_id = Column(String(200), nullable=True)
    provider = Column(String(80), nullable=True)
    lead_id = Column(UUID(as_uuid=True), nullable=True)
    contact_id = Column(UUID(as_uuid=True), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    tag_event_id = Column(UUID(as_uuid=True), nullable=True)
    funnel_stage_id = Column(String(120), nullable=True)
    checkout_id = Column(String(200), nullable=True)
    payment_id = Column(String(200), nullable=True)
    attribution_touch_id = Column(UUID(as_uuid=True), nullable=True)
    attribution_resolution_id = Column(UUID(as_uuid=True), nullable=True)
    attribution_status = Column(String(24), nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    attribution_model = Column(String(24), nullable=False, default="last_touch", server_default=sa_text("'last_touch'"))
    first_touch_id = Column(UUID(as_uuid=True), nullable=True)
    last_touch_id = Column(UUID(as_uuid=True), nullable=True)
    first_campaign_id = Column(String(100), nullable=True)
    first_adset_id = Column(String(100), nullable=True)
    first_ad_id = Column(String(100), nullable=True)
    last_campaign_id = Column(String(100), nullable=True)
    last_adset_id = Column(String(100), nullable=True)
    last_ad_id = Column(String(100), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    value = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    revenue_verified = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    is_backfilled = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    external_dispatch_policy = Column(String(24), nullable=False, default="disabled", server_default=sa_text("'disabled'"))
    attribution_snapshot = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    evidence_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    idempotency_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class CommercialMetaEventLink(Base):
    """Read-only reconciliation from legacy Meta attempts to internal facts."""

    __tablename__ = "commercial_event_meta_links"
    __table_args__ = (
        UniqueConstraint("empresa_id", "meta_event_id", name="uq_commercial_event_meta_link_legacy"),
        ForeignKeyConstraint(["empresa_id", "commercial_event_id"], ["commercial_events.empresa_id", "commercial_events.id"], name="fk_commercial_event_meta_link_fact"),
        ForeignKeyConstraint(["empresa_id", "meta_event_id"], ["meta_conversion_events.empresa_id", "meta_conversion_events.id"], name="fk_commercial_event_meta_link_legacy"),
        CheckConstraint("classification IN ('LINKED_EXACT','LINKED_PROBABLE','AMBIGUOUS','UNLINKED')", name="ck_commercial_event_meta_link_classification"),
        CheckConstraint("classification <> 'LINKED_EXACT' OR commercial_event_id IS NOT NULL", name="ck_commercial_event_meta_link_exact_reference"),
        Index("ix_commercial_event_meta_link_class", "empresa_id", "classification"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    meta_event_id = Column(UUID(as_uuid=True), nullable=False)
    commercial_event_id = Column(UUID(as_uuid=True), nullable=True)
    classification = Column(String(24), nullable=False)
    match_reason = Column(String(80), nullable=False)
    candidate_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    reconciled_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class CommercialMetaEventPolicy(Base):
    """Per-tenant/event rollout mode for the canonical Meta signal pipeline."""

    __tablename__ = "commercial_meta_event_policies"
    __table_args__ = (
        UniqueConstraint("empresa_id", "event_name", name="uq_commercial_meta_policy_tenant_event"),
        CheckConstraint("mode IN ('disabled','shadow','live')", name="ck_commercial_meta_policy_mode"),
        Index("ix_commercial_meta_policy_mode", "empresa_id", "mode"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    event_name = Column(String(80), nullable=False)
    mode = Column(String(16), nullable=False, default="disabled", server_default=sa_text("'disabled'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class CanonicalMetaConversionProjection(Base):
    """Safe, durable shadow projection; never represents a Meta delivery attempt."""

    __tablename__ = "commercial_meta_conversion_projections"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_commercial_meta_projection_empresa_id_id"),
        UniqueConstraint("empresa_id", "commercial_event_id", "projection_version", name="uq_commercial_meta_projection_event_version"),
        UniqueConstraint("empresa_id", "canonical_event_id", name="uq_commercial_meta_projection_canonical_id"),
        ForeignKeyConstraint(
            ["empresa_id", "commercial_event_id"],
            ["commercial_events.empresa_id", "commercial_events.id"],
            name="fk_commercial_meta_projection_fact",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "legacy_event_id"],
            ["meta_conversion_events.empresa_id", "meta_conversion_events.id"],
            name="fk_commercial_meta_projection_exact_legacy",
        ),
        ForeignKeyConstraint(
            ["mapping_id"],
            ["business_messaging_conversion_mappings.id"],
            name="fk_commercial_meta_projection_mapping",
        ),
        CheckConstraint("mode IN ('disabled','shadow','live')", name="ck_commercial_meta_projection_mode"),
        CheckConstraint("projection_status IN ('disabled','valid','invalid','unsupported','live_blocked')", name="ck_commercial_meta_projection_status"),
        CheckConstraint("comparison_classification IN ('EXACT','SEMANTICALLY_EQUIVALENT','MISSING_ATTRIBUTION','MISSING_REQUIRED_FIELDS','EVENT_ID_MISMATCH','VALUE_MISMATCH','UNSUPPORTED_EVENT','NO_LEGACY_REFERENCE','EVENT_NAME_MISMATCH','CONTEXT_MISMATCH')", name="ck_commercial_meta_projection_comparison"),
        CheckConstraint("legacy_reference_classification IN ('EXACT_LINKED','PROBABLE_ONLY','AMBIGUOUS','UNLINKED')", name="ck_commercial_meta_projection_legacy_class"),
        CheckConstraint("delivery_state IN ('NOT_SENT','SHADOW_ONLY','ALREADY_DELIVERED_BY_LEGACY','LEGACY_ACCEPTED','LEGACY_FAILED','UNKNOWN_DELIVERY','NOT_APPLICABLE','BLOCKED')", name="ck_commercial_meta_projection_delivery_state"),
        CheckConstraint("jsonb_typeof(comparison_json)='object' AND jsonb_typeof(safe_summary_json)='object'", name="ck_commercial_meta_projection_json_objects"),
        CheckConstraint("length(canonical_event_id)=64", name="ck_commercial_meta_projection_canonical_id_length"),
        Index("ix_commercial_meta_projection_tenant_status", "empresa_id", "projection_status"),
        Index("ix_commercial_meta_projection_tenant_delivery", "empresa_id", "delivery_state"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    commercial_event_id = Column(UUID(as_uuid=True), nullable=False)
    legacy_event_id = Column(UUID(as_uuid=True), nullable=True)
    mapping_id = Column(UUID(as_uuid=True), nullable=True)
    projection_version = Column(Integer, nullable=False, default=1, server_default=sa_text("1"))
    canonical_event_id = Column(String(64), nullable=False)
    event_name = Column(String(80), nullable=False)
    mode = Column(String(16), nullable=False, default="shadow", server_default=sa_text("'shadow'"))
    projection_status = Column(String(24), nullable=False)
    comparison_classification = Column(String(40), nullable=False, default="NO_LEGACY_REFERENCE", server_default=sa_text("'NO_LEGACY_REFERENCE'"))
    legacy_reference_classification = Column(String(24), nullable=False, default="UNLINKED", server_default=sa_text("'UNLINKED'"))
    delivery_state = Column(String(40), nullable=False, default="SHADOW_ONLY", server_default=sa_text("'SHADOW_ONLY'"))
    event_time = Column(BigInteger, nullable=True)
    action_source = Column(String(40), nullable=True)
    dataset_id_masked = Column(String(32), nullable=True)
    waba_id_masked = Column(String(32), nullable=True)
    ctwa_present = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    value = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    payload_hash = Column(String(64), nullable=True)
    reason_code = Column(String(80), nullable=True)
    legacy_candidate_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    comparison_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    safe_summary_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    last_projected_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class CanonicalConversionOutbox(Base):
    """Durable canonical delivery intent; P1C.3 only operates in SHADOW."""

    __tablename__ = "canonical_conversion_outbox"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_canonical_conversion_outbox_empresa_id"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_canonical_conversion_outbox_idempotency"),
        UniqueConstraint("empresa_id", "commercial_event_id", name="uq_canonical_conversion_outbox_event"),
        ForeignKeyConstraint(
            ["empresa_id", "commercial_event_id"],
            ["commercial_events.empresa_id", "commercial_events.id"],
            name="fk_conversion_outbox_fact",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "projection_id"],
            ["commercial_meta_conversion_projections.empresa_id", "commercial_meta_conversion_projections.id"],
            name="fk_conversion_outbox_projection",
            ondelete="CASCADE",
        ),
        CheckConstraint("provider='meta'", name="ck_conversion_outbox_provider"),
        CheckConstraint("destination_type='business_messaging_dataset'", name="ck_conversion_outbox_destination"),
        CheckConstraint(
            "status IN ('SHADOW','BLOCKED','HISTORICAL_BLOCKED','READY','CLAIMED','RETRY','DELIVERED','FAILED_PERMANENT','UNKNOWN_DELIVERY')",
            name="ck_conversion_outbox_status",
        ),
        CheckConstraint("dispatch_mode IN ('disabled','shadow','live')", name="ck_conversion_outbox_mode"),
        CheckConstraint("dispatch_mode <> 'live' OR status='BLOCKED'", name="ck_conversion_outbox_live_stays_blocked"),
        CheckConstraint(
            "(historical_no_send AND status='HISTORICAL_BLOCKED') OR (NOT historical_no_send AND status<>'HISTORICAL_BLOCKED')",
            name="ck_conversion_outbox_history_no_send",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_conversion_outbox_attempt_count"),
        CheckConstraint(
            "jsonb_typeof(payload_snapshot)='object' AND jsonb_typeof(provider_response_metadata)='object'",
            name="ck_conversion_outbox_payload_object",
        ),
        Index("ix_conversion_outbox_claim", "dispatch_mode", "status", "next_attempt_at", "lease_expires_at", "created_at"),
        Index("ix_conversion_outbox_tenant_status", "empresa_id", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    commercial_event_id = Column(UUID(as_uuid=True), nullable=False)
    projection_id = Column(UUID(as_uuid=True), nullable=False)
    provider = Column(String(32), nullable=False, default="meta", server_default=sa_text("'meta'"))
    destination_type = Column(String(48), nullable=False, default="business_messaging_dataset", server_default=sa_text("'business_messaging_dataset'"))
    dataset_id_masked = Column(String(32), nullable=True)
    waba_id_masked = Column(String(32), nullable=True)
    event_name = Column(String(80), nullable=False)
    event_id = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    payload_snapshot = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    payload_version = Column(Integer, nullable=False, default=1, server_default=sa_text("1"))
    status = Column(String(32), nullable=False)
    dispatch_mode = Column(String(16), nullable=False, default="shadow", server_default=sa_text("'shadow'"))
    historical_no_send = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    eligible_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(120), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(String(80), nullable=True)
    last_error_code = Column(String(100), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    provider_response_metadata = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class CanonicalConversionDeliveryAttempt(Base):
    """Append-only external attempts or explicit non-delivery shadow simulations."""

    __tablename__ = "canonical_conversion_delivery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["empresa_id", "outbox_id"],
            ["canonical_conversion_outbox.empresa_id", "canonical_conversion_outbox.id"],
            name="fk_conversion_attempt_outbox",
            ondelete="CASCADE",
        ),
        UniqueConstraint("empresa_id", "outbox_id", "attempt_number", name="uq_conversion_attempt_number"),
        CheckConstraint("attempt_number > 0", name="ck_conversion_attempt_number"),
        CheckConstraint("attempt_kind IN ('SHADOW_SIMULATION','EXTERNAL')", name="ck_conversion_attempt_kind"),
        CheckConstraint(
            "status IN ('SIMULATED','FAILED_PRE_SEND','RETRY','DELIVERED','FAILED_PERMANENT','UNKNOWN_DELIVERY')",
            name="ck_conversion_attempt_status",
        ),
        CheckConstraint("provider_ack_status IN ('NOT_ATTEMPTED','ACKED','REJECTED','UNKNOWN')", name="ck_conversion_attempt_ack"),
        CheckConstraint("attempt_kind <> 'SHADOW_SIMULATION' OR provider_ack_status='NOT_ATTEMPTED'", name="ck_conversion_shadow_never_acked"),
        CheckConstraint("jsonb_typeof(response_metadata)='object'", name="ck_conversion_attempt_response_object"),
        Index("ix_conversion_attempt_tenant_outbox", "empresa_id", "outbox_id", "attempt_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    outbox_id = Column(UUID(as_uuid=True), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    attempt_kind = Column(String(32), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False)
    error_class = Column(String(80), nullable=True)
    provider_ack_status = Column(String(32), nullable=False, default="NOT_ATTEMPTED", server_default=sa_text("'NOT_ATTEMPTED'"))
    request_correlation_id = Column(String(80), nullable=True)
    response_metadata = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class LegacyConversionDeliveryEvidence(Base):
    """Durable legacy transport evidence; acceptance is not ad attribution."""
    __tablename__ = "legacy_conversion_delivery_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "commercial_event_id"], ["commercial_events.empresa_id", "commercial_events.id"], name="fk_legacy_delivery_evidence_fact"),
        ForeignKeyConstraint(["empresa_id", "legacy_event_id"], ["meta_conversion_events.empresa_id", "meta_conversion_events.id"], name="fk_legacy_delivery_evidence_legacy"),
        UniqueConstraint("request_correlation_id", "external_event_id_hash", name="uq_legacy_delivery_evidence_request_event"),
        CheckConstraint("request_outcome IN ('LOCAL_PREPARED','REQUEST_ATTEMPTED','PROVIDER_ACCEPTED','PROVIDER_REJECTED','DELIVERY_UNKNOWN','NOT_SENT','BLOCKED')", name="ck_legacy_delivery_evidence_outcome"),
        CheckConstraint("identity_classification IN ('EXACT_LINKED','UNLINKED')", name="ck_legacy_delivery_evidence_identity"),
        CheckConstraint("identity_classification <> 'EXACT_LINKED' OR (commercial_event_id IS NOT NULL AND legacy_event_id IS NOT NULL)", name="ck_legacy_delivery_evidence_exact"),
        CheckConstraint("http_status IS NULL OR http_status BETWEEN 100 AND 599", name="ck_legacy_delivery_evidence_http"),
        CheckConstraint("jsonb_typeof(provider_response)='object'", name="ck_legacy_delivery_evidence_response"),
        Index("ix_legacy_delivery_evidence_tenant_time", "empresa_id", "attempt_started_at"),
        Index("ix_legacy_delivery_evidence_identity", "empresa_id", "external_event_id_hash", "dataset_id_hash", "waba_id_hash"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    commercial_event_id = Column(UUID(as_uuid=True), nullable=True)
    legacy_event_id = Column(UUID(as_uuid=True), nullable=True)
    external_event_id_hash = Column(String(64), nullable=False)
    external_event_id_safe = Column(String(64), nullable=True)
    event_name = Column(String(80), nullable=False)
    dataset_id_hash = Column(String(64), nullable=False)
    waba_id_hash = Column(String(64), nullable=True)
    source_type = Column(String(60), nullable=False)
    source_id_hash = Column(String(64), nullable=True)
    transport_path = Column(String(120), nullable=False)
    identity_classification = Column(String(24), nullable=False, default="UNLINKED", server_default=sa_text("'UNLINKED'"))
    request_correlation_id = Column(UUID(as_uuid=True), nullable=False)
    attempt_started_at = Column(DateTime(timezone=True), nullable=False)
    attempt_finished_at = Column(DateTime(timezone=True), nullable=True)
    request_outcome = Column(String(32), nullable=False, default="LOCAL_PREPARED", server_default=sa_text("'LOCAL_PREPARED'"))
    http_status = Column(Integer, nullable=True)
    provider_response = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    delivery_uncertainty = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class ConversionDeliveryIdentity(Base):
    """Future shared ownership; only shadow validation is supported here."""
    __tablename__ = "conversion_delivery_identities"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "commercial_event_id"], ["commercial_events.empresa_id", "commercial_events.id"], name="fk_conversion_shared_identity_fact"),
        UniqueConstraint("empresa_id", "identity_key", name="uq_conversion_shared_identity"),
        CheckConstraint("owner IN ('legacy','canonical')", name="ck_conversion_shared_identity_owner"),
        CheckConstraint("ownership_mode='SHADOW_VALIDATION'", name="ck_conversion_shared_identity_shadow"),
        CheckConstraint("delivery_state IN ('RESERVED','PROVIDER_ACCEPTED','DELIVERY_UNKNOWN')", name="ck_conversion_shared_identity_state"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    commercial_event_id = Column(UUID(as_uuid=True), nullable=False)
    identity_key = Column(String(64), nullable=False)
    external_event_id_hash = Column(String(64), nullable=False)
    owner = Column(String(16), nullable=False)
    ownership_mode = Column(String(24), nullable=False, default="SHADOW_VALIDATION", server_default=sa_text("'SHADOW_VALIDATION'"))
    delivery_state = Column(String(24), nullable=False, default="RESERVED", server_default=sa_text("'RESERVED'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class MetaAdsInsightDaily(Base):
    """Tenant/account-scoped daily read-only Graph snapshot."""

    __tablename__ = "meta_ads_insights_daily"
    __table_args__ = (
        UniqueConstraint("empresa_id", "ad_account_id", "entity_level", "entity_id", "date_start", "date_stop", "attribution_setting", name="uq_meta_ads_insights_daily_key"),
        CheckConstraint("entity_level IN ('account','campaign','adset','ad')", name="ck_meta_ads_insights_daily_level"),
        CheckConstraint("date_stop >= date_start", name="ck_meta_ads_insights_daily_date"),
        CheckConstraint("jsonb_typeof(actions_json)='array' AND jsonb_typeof(action_values_json)='array' AND jsonb_typeof(raw_metrics_json)='object'", name="ck_meta_ads_insights_daily_json"),
        Index("ix_meta_ads_insights_daily_campaign_date", "empresa_id", "campaign_id", "date_start"),
        Index("ix_meta_ads_insights_daily_adset_date", "empresa_id", "adset_id", "date_start"),
        Index("ix_meta_ads_insights_daily_ad_date", "empresa_id", "ad_id", "date_start"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    ad_account_id = Column(String(100), nullable=False)
    account_currency = Column(String(3), nullable=True)
    entity_level = Column(String(16), nullable=False)
    entity_id = Column(String(100), nullable=False)
    account_name = Column(String(240), nullable=True)
    campaign_id = Column(String(100), nullable=True)
    campaign_name = Column(String(240), nullable=True)
    adset_id = Column(String(100), nullable=True)
    adset_name = Column(String(240), nullable=True)
    ad_id = Column(String(100), nullable=True)
    ad_name = Column(String(240), nullable=True)
    date_start = Column(Date, nullable=False)
    date_stop = Column(Date, nullable=False)
    attribution_setting = Column(String(120), nullable=False, default="unspecified", server_default=sa_text("'unspecified'"))
    spend = Column(Numeric(18, 6), nullable=True)
    impressions = Column(BigInteger, nullable=True)
    reach = Column(BigInteger, nullable=True)
    frequency = Column(Numeric(18, 8), nullable=True)
    clicks = Column(BigInteger, nullable=True)
    ctr = Column(Numeric(18, 8), nullable=True)
    cpc = Column(Numeric(18, 8), nullable=True)
    cpm = Column(Numeric(18, 8), nullable=True)
    messaging_conversations = Column(Numeric(18, 6), nullable=True)
    actions_json = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    action_values_json = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    raw_metrics_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    graph_api_version = Column(String(24), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
class BusinessMessagingConversionMapping(Base):
    __tablename__ = "business_messaging_conversion_mappings"
    __table_args__ = (
        UniqueConstraint("empresa_id", "trigger_type", "trigger_ref", name="uq_bm_conversion_mapping_empresa_trigger"),
        Index("ix_bm_conversion_mappings_empresa_active", "empresa_id", "ativo"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    trigger_type = Column(String, nullable=False)
    trigger_ref = Column(String, nullable=False)
    event_name = Column(String, nullable=False)
    ativo = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    send_mode = Column(String, nullable=False, default="dry_run", server_default=sa_text("'dry_run'"))
    default_value = Column(Float, nullable=True)
    currency = Column(String, nullable=False, default="BRL", server_default=sa_text("'BRL'"))
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)




class MensagemHistorico(Base):
    __tablename__ = "mensagens_historico"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_mensagens_historico_empresa_id_id"),
        ForeignKeyConstraint(
            ["empresa_id", "lead_id"],
            ["crm_leads.empresa_id", "crm_leads.id"],
            name="fk_mensagens_historico_empresa_lead",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "conexao_id"],
            ["conexoes.empresa_id", "conexoes.id"],
            name="fk_mensagens_historico_empresa_conexao",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "channel_conversation_id"],
            ["channel_conversations.empresa_id", "channel_conversations.id"],
            name="fk_mensagens_historico_empresa_conversation",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="SET NULL"), nullable=True)
    channel_conversation_id = Column(UUID(as_uuid=True), ForeignKey("channel_conversations.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    external_message_id = Column(String, nullable=True)
    external_conversation_id = Column(String, nullable=True)
    direction = Column(String, nullable=True)
    message_status = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)
    texto = Column(Text, nullable=False)
    tipo_mensagem = Column(String, nullable=False, default="text")
    media_url = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    origem_mensagem = Column(String, nullable=True)
    from_me = Column(Boolean, nullable=False, default=False)
    criado_em = Column(DateTime, default=datetime.utcnow)

    lead = relationship("CRMLead", back_populates="mensagens", foreign_keys=[lead_id])
    conexao = relationship("Conexao", back_populates="mensagens_historico", foreign_keys=[conexao_id])
    channel_conversation = relationship(
        "ChannelConversation", back_populates="mensagens", foreign_keys=[channel_conversation_id]
    )


class TemplateMensagem(Base):
    __tablename__ = "templates_mensagem"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    texto_template = Column(Text, nullable=False)
    variaveis_esperadas = Column(JSONB, nullable=False, default=list)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="templates_mensagem")
    campanhas = relationship("CampanhaDisparo", back_populates="template")


class MetaMessageTemplate(Base):
    __tablename__ = "meta_message_templates"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_meta_templates_empresa_conexao"),
        UniqueConstraint("empresa_id", "conexao_id", "remote_template_id", name="uq_meta_templates_remote_id"),
        UniqueConstraint("empresa_id", "conexao_id", "waba_id", "name", "language", name="uq_meta_templates_name_language_waba"),
        UniqueConstraint("empresa_id", "id", name="uq_meta_templates_empresa_id_id"),
        Index("ix_meta_templates_lookup", "empresa_id", "conexao_id", "waba_id", "normalized_status", "normalized_category"),
        Index("ix_meta_templates_search", "empresa_id", "name", "language"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=False)
    waba_id = Column(String, nullable=False)
    remote_template_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    language = Column(String, nullable=False)
    raw_category = Column(String, nullable=True)
    normalized_category = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    raw_status = Column(String, nullable=True)
    normalized_status = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    quality_rating = Column(String, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    parameter_format = Column(String, nullable=True)
    remote_created_at = Column(DateTime, nullable=True)
    remote_updated_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    stale_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    graph_api_version = Column(String, nullable=False, default="v25.0", server_default=sa_text("'v25.0'"))
    raw_metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="meta_message_templates")
    conexao = relationship("Conexao", back_populates="meta_message_templates", foreign_keys=[conexao_id])
    components = relationship(
        "MetaTemplateComponent",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="MetaTemplateComponent.component_order",
        foreign_keys="MetaTemplateComponent.template_id",
    )


class MetaTemplateComponent(Base):
    __tablename__ = "meta_template_components"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "template_id"], ["meta_message_templates.empresa_id", "meta_message_templates.id"], name="fk_meta_template_components_empresa_template"),
        UniqueConstraint("empresa_id", "template_id", "component_order", name="uq_meta_template_components_order"),
        Index("ix_meta_template_components_template", "empresa_id", "template_id", "component_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("meta_message_templates.id", ondelete="CASCADE"), nullable=False)
    component_type = Column(String, nullable=False)
    component_order = Column(Integer, nullable=False)
    format = Column(String, nullable=True)
    text = Column(Text, nullable=True)
    buttons_json = Column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    example_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    media_reference_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    raw_component_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    template = relationship("MetaMessageTemplate", back_populates="components", foreign_keys=[template_id])


class MetaTemplateSyncRun(Base):
    __tablename__ = "meta_template_sync_runs"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_meta_template_sync_runs_empresa_conexao"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_meta_template_sync_runs_idempotency"),
        Index("ix_meta_template_sync_runs_lookup", "empresa_id", "conexao_id", "waba_id", "started_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=False)
    waba_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="started", server_default=sa_text("'started'"))
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    initial_cursor = Column(Text, nullable=True)
    final_cursor = Column(Text, nullable=True)
    pages = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    found_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    created_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    updated_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    unchanged_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    stale_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    error_code = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    graph_api_version = Column(String, nullable=False, default="v25.0", server_default=sa_text("'v25.0'"))
    source = Column(String, nullable=False, default="manual", server_default=sa_text("'manual'"))
    initiated_by = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="meta_template_sync_runs")
    conexao = relationship("Conexao", back_populates="meta_template_sync_runs", foreign_keys=[conexao_id])


class CampanhaDisparo(Base):
    __tablename__ = "campanhas_disparo"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_campanhas_disparo_empresa_id_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome = Column(String, nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates_mensagem.id", ondelete="SET NULL"), nullable=True)
    tags_alvo = Column(JSONB, nullable=False, default=list)
    data_agendamento = Column(DateTime, nullable=True)
    status = Column(Enum(CampanhaDisparoStatus, name="campanha_disparo_status_enum"), nullable=False, default=CampanhaDisparoStatus.PENDENTE)
    central_status = Column(String, nullable=False, default="draft", server_default=sa_text("'draft'"))
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="SET NULL"), nullable=True)
    meta_template_id = Column(UUID(as_uuid=True), ForeignKey("meta_message_templates.id", ondelete="SET NULL"), nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    # Nullable keeps legacy rows compatible; application policy treats NULL as
    # the historical (enabled) behavior. New campaigns always persist True or
    # False explicitly.
    ai_reply_enabled = Column(Boolean, nullable=True, default=True, server_default=sa_text("true"))
    started_at = Column(DateTime, nullable=True)
    queued_at = Column(DateTime, nullable=True)
    paused_at = Column(DateTime, nullable=True)
    resumed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    error_code = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="campanhas_disparo")
    template = relationship("TemplateMensagem", back_populates="campanhas")
    recipients = relationship(
        "CampaignRecipient",
        back_populates="campanha",
        cascade="all, delete-orphan",
        foreign_keys="CampaignRecipient.campanha_id",
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "campanha_id"], ["campanhas_disparo.empresa_id", "campanhas_disparo.id"], name="fk_campaign_recipients_empresa_campanha"),
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_campaign_recipients_empresa_conexao"),
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_campaign_recipients_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_campaign_recipients_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conversation_id"], ["channel_conversations.empresa_id", "channel_conversations.id"], name="fk_campaign_recipients_empresa_conversation"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_campaign_recipients_idempotency"),
        UniqueConstraint("empresa_id", "id", name="uq_campaign_recipients_empresa_id_id"),
        Index("ix_campaign_recipients_campaign_status", "empresa_id", "campanha_id", "status"),
        Index("ix_campaign_recipients_external_message", "empresa_id", "provider", "external_message_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    campanha_id = Column(UUID(as_uuid=True), ForeignKey("campanhas_disparo.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("channel_contacts.id", ondelete="RESTRICT"), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("channel_conversations.id", ondelete="SET NULL"), nullable=True)
    recipient_address = Column(String, nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates_mensagem.id", ondelete="SET NULL"), nullable=True)
    meta_template_id = Column(UUID(as_uuid=True), ForeignKey("meta_message_templates.id", ondelete="SET NULL"), nullable=True)
    parameters_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    payload_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    status = Column(String, nullable=False, default="queued", server_default=sa_text("'queued'"))
    scheduled_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    external_message_id = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="campaign_recipients")
    campanha = relationship("CampanhaDisparo", back_populates="recipients", foreign_keys=[campanha_id])
    conexao = relationship("Conexao", back_populates="campaign_recipients", foreign_keys=[conexao_id])
    lead = relationship("CRMLead", back_populates="campaign_recipients", foreign_keys=[lead_id])
    contact = relationship("ChannelContact", back_populates="campaign_recipients", foreign_keys=[contact_id])
    conversation = relationship("ChannelConversation", back_populates="campaign_recipients", foreign_keys=[conversation_id])
    outbox_messages = relationship(
        "MessageOutbox",
        back_populates="campaign_recipient",
        foreign_keys="MessageOutbox.campaign_recipient_id",
    )


class AttributionTouch(Base):
    """Immutable, tenant-scoped facts used by the attribution ledger."""

    __tablename__ = "attribution_touches"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_attribution_touches_empresa_id_id"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_attribution_touches_idempotency"),
        ForeignKeyConstraint(["empresa_id", "connection_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_attribution_touches_empresa_connection"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_attribution_touches_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conversation_id"], ["channel_conversations.empresa_id", "channel_conversations.id"], name="fk_attribution_touches_empresa_conversation"),
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_attribution_touches_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "outbound_campaign_id"], ["campanhas_disparo.empresa_id", "campanhas_disparo.id"], name="fk_attribution_touches_empresa_campaign"),
        ForeignKeyConstraint(["empresa_id", "campaign_recipient_id"], ["campaign_recipients.empresa_id", "campaign_recipients.id"], name="fk_attribution_touches_empresa_recipient"),
        ForeignKeyConstraint(["empresa_id", "attribution_touch_id"], ["attribution_touches.empresa_id", "attribution_touches.id"], name="fk_attribution_touches_empresa_attribution_touch"),
        CheckConstraint("(amount IS NULL AND currency IS NULL) OR (amount IS NOT NULL AND currency IS NOT NULL AND char_length(currency) = 3)", name="ck_attribution_touches_amount_currency"),
        CheckConstraint("jsonb_typeof(metadata_json) = 'object'", name="ck_attribution_touches_metadata_object"),
        Index("ix_attribution_touches_lead_time", "empresa_id", "lead_id", "occurred_at"),
        Index("ix_attribution_touches_conversation_time", "empresa_id", "conversation_id", "occurred_at"),
        Index("ix_attribution_touches_campaign_time", "empresa_id", "outbound_campaign_id", "occurred_at"),
        Index("ix_attribution_touches_external_payment", "empresa_id", "provider", "payment_id"),
        Index("ix_attribution_touches_event_kind", "empresa_id", "event_kind", "occurred_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    channel = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    connection_id = Column(UUID(as_uuid=True), nullable=True)
    touch_type = Column(String, nullable=False)
    touch_source = Column(String, nullable=False)
    event_kind = Column(String, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    contact_id = Column(UUID(as_uuid=True), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    lead_id = Column(UUID(as_uuid=True), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    external_event_id = Column(String, nullable=True)
    ctwa_clid = Column(Text, nullable=True)
    source_id = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    source_type = Column(String, nullable=True)
    headline = Column(String(240), nullable=True)
    body = Column(Text, nullable=True)
    media_type = Column(String(40), nullable=True)
    outbound_campaign_id = Column(UUID(as_uuid=True), nullable=True)
    campaign_recipient_id = Column(UUID(as_uuid=True), nullable=True)
    checkout_id = Column(String, nullable=True)
    payment_id = Column(String, nullable=True)
    attribution_touch_id = Column(UUID(as_uuid=True), nullable=True)
    amount = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    idempotency_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class AttributionTouchResolution(Base):
    """Mutable, replaceable interpretation of immutable attribution facts."""

    __tablename__ = "attribution_touch_resolutions"
    __table_args__ = (
        UniqueConstraint("empresa_id", "id", name="uq_attribution_touch_resolutions_empresa_id_id"),
        UniqueConstraint("empresa_id", "touch_id", name="uq_attribution_touch_resolutions_touch"),
        ForeignKeyConstraint(["empresa_id", "touch_id"], ["attribution_touches.empresa_id", "attribution_touches.id"], name="fk_attribution_touch_resolutions_touch", ondelete="CASCADE"),
        CheckConstraint("resolution_status IN ('resolved','partial','unresolved','not_applicable','not_found','forbidden','error_retryable')", name="ck_attribution_touch_resolution_status"),
        Index("ix_attribution_touch_resolutions_campaign", "empresa_id", "campaign_id", "adset_id", "ad_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    touch_id = Column(UUID(as_uuid=True), nullable=False)
    resolution_status = Column(String, nullable=False, default="unresolved", server_default=sa_text("'unresolved'"))
    ad_id = Column(String, nullable=True)
    adset_id = Column(String, nullable=True)
    campaign_id = Column(String, nullable=True)
    creative_id = Column(String, nullable=True)
    ad_name_snapshot = Column(String, nullable=True)
    adset_name_snapshot = Column(String, nullable=True)
    campaign_name_snapshot = Column(String, nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(80), nullable=True)
    api_version = Column(String(24), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


class MessageOutbox(Base):
    __tablename__ = "message_outbox"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_message_outbox_empresa_conexao"),
        ForeignKeyConstraint(["empresa_id", "campaign_recipient_id"], ["campaign_recipients.empresa_id", "campaign_recipients.id"], name="fk_message_outbox_empresa_campaign_recipient"),
        ForeignKeyConstraint(["empresa_id", "followup_execution_id"], ["followup_executions.empresa_id", "followup_executions.id"], name="fk_message_outbox_empresa_followup_execution"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_message_outbox_idempotency"),
        UniqueConstraint("empresa_id", "id", name="uq_message_outbox_empresa_id_id"),
        Index("ix_message_outbox_claim", "status", "available_at", "priority", "locked_at"),
        Index("ix_message_outbox_external_message", "empresa_id", "provider", "external_message_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    operation_type = Column(String, nullable=False)
    payload_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    idempotency_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued", server_default=sa_text("'queued'"))
    priority = Column(Integer, nullable=False, default=100, server_default=sa_text("100"))
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default=sa_text("0"))
    max_attempts = Column(Integer, nullable=False, default=3, server_default=sa_text("3"))
    last_error_code = Column(String, nullable=True)
    last_error_detail = Column(Text, nullable=True)
    external_message_id = Column(String, nullable=True)
    campaign_recipient_id = Column(UUID(as_uuid=True), ForeignKey("campaign_recipients.id", ondelete="SET NULL"), nullable=True)
    followup_execution_id = Column(UUID(as_uuid=True), ForeignKey("followup_executions.id", ondelete="SET NULL"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="message_outbox")
    conexao = relationship("Conexao", back_populates="message_outbox", foreign_keys=[conexao_id])
    campaign_recipient = relationship("CampaignRecipient", back_populates="outbox_messages", foreign_keys=[campaign_recipient_id])
    followup_execution = relationship("FollowupExecution", back_populates="outbox_messages", foreign_keys=[followup_execution_id])
    attempts_rel = relationship(
        "MessageAttempt",
        back_populates="outbox",
        cascade="all, delete-orphan",
        foreign_keys="MessageAttempt.outbox_id",
    )


class MessageAttempt(Base):
    __tablename__ = "message_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "outbox_id"], ["message_outbox.empresa_id", "message_outbox.id"], name="fk_message_attempts_empresa_outbox"),
        UniqueConstraint("empresa_id", "outbox_id", "attempt_number", name="uq_message_attempts_attempt"),
        Index("ix_message_attempts_outbox", "empresa_id", "outbox_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outbox_id = Column(UUID(as_uuid=True), ForeignKey("message_outbox.id", ondelete="CASCADE"), nullable=False)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="RESTRICT"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    result_status = Column(String, nullable=False, default="started", server_default=sa_text("'started'"))
    external_message_id = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    outbox = relationship("MessageOutbox", back_populates="attempts_rel", foreign_keys=[outbox_id])


class FollowupExecution(Base):
    __tablename__ = "followup_executions"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "followup_id"], ["config_followups.empresa_id", "config_followups.id"], name="fk_followup_executions_empresa_followup"),
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_followup_executions_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_followup_executions_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conversation_id"], ["channel_conversations.empresa_id", "channel_conversations.id"], name="fk_followup_executions_empresa_conversation"),
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_followup_executions_empresa_conexao"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_followup_executions_idempotency"),
        UniqueConstraint("empresa_id", "id", name="uq_followup_executions_empresa_id_id"),
        Index("ix_followup_executions_status", "empresa_id", "status", "scheduled_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    followup_id = Column(UUID(as_uuid=True), ForeignKey("config_followups.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("channel_contacts.id", ondelete="RESTRICT"), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("channel_conversations.id", ondelete="RESTRICT"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    trigger = Column(String, nullable=False)
    trigger_event_id = Column(UUID(as_uuid=True), ForeignKey("crm_tag_events.id", ondelete="SET NULL"), nullable=True)
    triggered_at = Column(DateTime, nullable=True)
    trigger_tag_id = Column(UUID(as_uuid=True), ForeignKey("tags_crm.id", ondelete="SET NULL"), nullable=True)
    policy_version = Column(String, nullable=True)
    policy_snapshot_json = Column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    ad_entry_started_at = Column(DateTime, nullable=True)
    ad_entry_expires_at = Column(DateTime, nullable=True)
    ad_entry_window_state = Column(String, nullable=True)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="scheduled", server_default=sa_text("'scheduled'"))
    stop_reason = Column(String, nullable=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates_mensagem.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String, nullable=False)
    outbox_id = Column(UUID(as_uuid=True), nullable=True)
    external_message_id = Column(String, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="followup_executions")
    followup = relationship("ConfigFollowUp", back_populates="executions", foreign_keys=[followup_id])
    lead = relationship("CRMLead", back_populates="followup_executions", foreign_keys=[lead_id])
    contact = relationship("ChannelContact", back_populates="followup_executions", foreign_keys=[contact_id])
    conversation = relationship("ChannelConversation", back_populates="followup_executions", foreign_keys=[conversation_id])
    conexao = relationship("Conexao", back_populates="followup_executions", foreign_keys=[conexao_id])
    outbox_messages = relationship("MessageOutbox", back_populates="followup_execution", foreign_keys="MessageOutbox.followup_execution_id")


class CommunicationConsent(Base):
    __tablename__ = "communication_consents"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "lead_id"], ["crm_leads.empresa_id", "crm_leads.id"], name="fk_communication_consents_empresa_lead"),
        ForeignKeyConstraint(["empresa_id", "contact_id"], ["channel_contacts.empresa_id", "channel_contacts.id"], name="fk_communication_consents_empresa_contact"),
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_communication_consents_empresa_conexao"),
        UniqueConstraint("empresa_id", "channel", "provider", "contact_id", "conexao_id", name="uq_communication_consents_scope"),
        Index("ix_communication_consents_lookup", "empresa_id", "channel", "provider", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL"), nullable=True)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("channel_contacts.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="CASCADE"), nullable=True)
    channel = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    status = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    source = Column(String, nullable=False, default="unknown", server_default=sa_text("'unknown'"))
    proof_reference = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    recorded_by_user_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    consented_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    blocked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="communication_consents")
    lead = relationship("CRMLead", back_populates="communication_consents", foreign_keys=[lead_id])


class MessageStatusEvent(Base):
    __tablename__ = "message_status_events"
    __table_args__ = (
        ForeignKeyConstraint(["empresa_id", "conexao_id"], ["conexoes.empresa_id", "conexoes.id"], name="fk_message_status_events_empresa_conexao"),
        UniqueConstraint("empresa_id", "idempotency_key", name="uq_message_status_events_idempotency"),
        Index("ix_message_status_events_external", "empresa_id", "provider", "external_message_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    conexao_id = Column(UUID(as_uuid=True), ForeignKey("conexoes.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    external_message_id = Column(String, nullable=True)
    external_event_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=True)
    raw_status = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DestinosTransferencia(Base):
    __tablename__ = "destinos_transferencia"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    nome_destino = Column(String, nullable=False)
    contatos_destino = Column(JSONB, nullable=False, default=list)
    instrucoes_ativacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="destinos_transferencia")
    historicos_transferencia = relationship("HistoricoTransferencia", back_populates="destino")


class HistoricoTransferencia(Base):
    __tablename__ = "historico_transferencia"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    destino_id = Column(UUID(as_uuid=True), ForeignKey("destinos_transferencia.id", ondelete="SET NULL"), nullable=True)
    motivo_ia = Column(Text, nullable=True)
    resumo_enviado = Column(Text, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    empresa = relationship("Empresa", back_populates="historicos_transferencia")
    lead = relationship("CRMLead", back_populates="historicos_transferencia")
    destino = relationship("DestinosTransferencia", back_populates="historicos_transferencia")



class AgendaConfiguracao(Base):
    __tablename__ = "agenda_configuracoes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, unique=True)
    dias_funcionamento = Column(JSONB, default={})  # mapa por dia (seg..dom) com aberto/inicio/fim
    # Deprecated: mantidos para legado; a fonte de verdade passou a ser dias_funcionamento (JSONB).
    horario_inicio = Column(Time, nullable=True)
    horario_fim = Column(Time, nullable=True)
    excecoes = Column(JSONB, default=list)
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
    favicon_base64 = Column(Text, nullable=True)
    logo_base64 = Column(Text, nullable=True)
    instagram_app_id = Column(String, nullable=True)
    instagram_app_secret_encrypted = Column(Text, nullable=True)
    instagram_verify_token_encrypted = Column(Text, nullable=True)
    instagram_graph_api_version = Column(String, nullable=False, default="v26.0", server_default=sa_text("'v26.0'"))
    instagram_config_updated_at = Column(DateTime, nullable=True)

class WebhookSaida(Base):
    __tablename__ = "webhooks_saida"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, unique=True)
    url = Column(String, nullable=False)
    ativo = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="webhooks_saida")

# Temporary compatibility re-export. Definitions live in db.leoft_models.
from db.leoft_models import (
    LeoFTApprovalEvent,
    LeoFTCatalogAlias,
    LeoFTCatalogEntry,
    LeoFTCatalogImportRun,
    LeoFTCatalogMedia,
    LeoFTCatalogSignature,
    LeoFTCatalogVersion,
    LeoFTIntegrationEvent,
    LeoFTMediaApproval,
    LeoFTMediaCandidateRejection,
    LeoFTPaymentEvent,
    LeoFTSaleFulfillment,
    LeoFTManualWorkout,
    LeoFTManualWorkoutExercise,
    LeoFTStudent,
    LeoFTStudentAccess,
    LeoFTStudentExerciseProgress,
    LeoFTStudentWorkoutProgress,
    LeoFTStudentWorkoutAssignment,
    LeoFTTrainingExercise,
    LeoFTTrainingPlan,
    LeoFTTrainingPlanVersion,
    LeoFTTrainingReviewEvent,
    LeoFTTrainingReviewSession,
    LeoFTTrainingSession,
    LeoFTTrainingWeek,
)
