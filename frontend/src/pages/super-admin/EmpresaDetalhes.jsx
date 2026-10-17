import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  BrainCircuit,
  Building2,
  CalendarDays,
  Columns3,
  CreditCard,
  Database,
  Image as ImageIcon,
  KeyRound,
  Link2,
  MessageSquare,
  Pencil,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Search,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  UserCog,
} from "lucide-react";

import { ActionButton, EmptyState, ErrorState, InfoBanner, LoadingState, MetricCard, PageHeader, SectionCard, StatusBadge, Tabs } from "../../components/ui";
import AdminDialog from "../../components/admin/AdminDialog";
import "./EmpresaDetalhes.css";

import api from "../../services/api";
import EmpresaConexoesManager from "../../components/EmpresaConexoesManager";
import EmpresaCrmEtapasManager from "../../components/EmpresaCrmEtapasManager";
import EmpresaPaymentManager from "../../components/EmpresaPaymentManager";
import RAGManager from "../../components/RAGManager";

const TABS = [
  { id: "visao-geral", label: "Visão Geral", icon: Building2 },
  { id: "usuarios", label: "Usuários", icon: UserCog },
  { id: "agente", label: "Atendimento e IA", icon: BrainCircuit },
  { id: "regras", label: "Regras Comerciais", icon: Columns3 },
  { id: "conhecimento", label: "Base de Conhecimento", icon: Database },
  { id: "canais", label: "Canais e Credenciais", icon: Link2 },
  { id: "pagamentos", label: "Pagamentos", icon: CreditCard },
  { id: "agenda-automacoes", label: "Agenda e Automações", icon: CalendarDays },
  { id: "imagens", label: "Imagens / Visão", icon: ImageIcon },
  { id: "diagnostico", label: "Diagnóstico", icon: ShieldCheck },
];

const IA_CONFIG_DEFAULT = {
  ia_personalidade: "",
  ia_regras_negocio: "",
  nome_agente: "",
  mensagem_saudacao: "",
  conversion_monitor_prompt: "",
  modelo_ia: "gpt-4o-mini",
  modelo_ia_reasoning_effort: "default",
  reasoning_effort_capabilities: { status: "unknown", options: ["default"], levels: [] },
  followup_ativo: false,
  limite_certeza: 0.65,
  limite_duvida: 0.45,
  max_agentes_desempate: 3,
  informacoes_adicionais: "",
  coletar_nome: true,
  telefone_notificacao: "",
  tags_ia_habilitadas: [],
};

const USUARIO_DEFAULT = {
  nome: "",
  email: "",
  role: "admin_empresa",
  ativo: true,
  senha_temporaria: "",
};

const USUARIO_ROLES = [
  { value: "admin_empresa", label: "Admin empresa" },
];

function formatWhatsappStatus(status, online) {
  const normalized = String(status || "").toLowerCase();
  if (online || normalized === "connected") return "Conectado";
  if (normalized === "pending") return "Pendente";
  if (normalized === "degraded") return "Atenção";
  if (normalized === "error") return "Erro";
  if (normalized === "not_configured") return "Não configurado";
  return "Desconectado";
}

function formatWhatsappProvider(provider, fallback) {
  const normalized = String(provider || "").toLowerCase();
  if (normalized === "meta_whatsapp" || normalized === "meta" || normalized === "meta_official") return "API Oficial da Meta";
  if (normalized === "evolution" || normalized === "evolution_legacy") return "Evolution - legado";
  return fallback || "Sem provider definido";
}

function MiniBadge({ children, tone = "slate" }) {
  const tones = { slate: "default", active: "active", internal: "info", warning: "warning" };
  return <StatusBadge tone={tones[tone] || "default"}>{children}</StatusBadge>;
}

export default function EmpresaDetalhes() {
  const navigate = useNavigate();
  const location = useLocation();
  const companiesReturn = /^\/admin(?:\?|$)/.test(location.state?.companiesReturn || "") ? location.state.companiesReturn : "/admin";
  const { empresaId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [empresa, setEmpresa] = useState(null);
  const [loading, setLoading] = useState(true);
  const requestedTab = searchParams.get("tab");
  const tabAtiva = TABS.some((tab) => tab.id === requestedTab) ? requestedTab : "visao-geral";
  const [loadError, setLoadError] = useState("");
  const [usuariosError, setUsuariosError] = useState("");
  const [usuarioError, setUsuarioError] = useState("");
  const [resetError, setResetError] = useState("");
  const [confirmarTeste, setConfirmarTeste] = useState(false);
  const [statusUsuario, setStatusUsuario] = useState(null);
  const [savingStatusUsuario, setSavingStatusUsuario] = useState(false);
  const [savingEmpresa, setSavingEmpresa] = useState(false);
  const [savingCredenciais, setSavingCredenciais] = useState(false);
  const [savingIaConfig, setSavingIaConfig] = useState(false);
  const [toast, setToast] = useState("");
  const [editEmpresa, setEditEmpresa] = useState({ nome_empresa: "", area_atuacao: "" });
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [openaiKeyConfigured, setOpenaiKeyConfigured] = useState(false);
  const [iaConfig, setIaConfig] = useState(IA_CONFIG_DEFAULT);
  const [modelOptions, setModelOptions] = useState([]);
  const [modelCatalog, setModelCatalog] = useState({ credential_status: "unknown", stale: false, updated_at: null, source: null });
  const [modelSearch, setModelSearch] = useState("");
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [validatingModel, setValidatingModel] = useState(false);
  const [tagsOficiais, setTagsOficiais] = useState([]);
  const [tagsIaLoading, setTagsIaLoading] = useState(false);
  const [tagsIaError, setTagsIaError] = useState("");
  const [funnelStatus, setFunnelStatus] = useState(null);
  const [funnelPreview, setFunnelPreview] = useState(null);
  const [funnelBusy, setFunnelBusy] = useState(false);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overview, setOverview] = useState({
    whatsappStatus: "unknown",
    whatsappStatusLabel: "Não disponível",
    whatsappProviderLabel: "Status não disponível",
    whatsappOnline: false,
    totalInteracoes: null,
  });
  const [usuarios, setUsuarios] = useState([]);
  const [usuariosLoading, setUsuariosLoading] = useState(false);
  const [showUsuarioModal, setShowUsuarioModal] = useState(false);
  const [editingUsuarioId, setEditingUsuarioId] = useState(null);
  const [savingUsuario, setSavingUsuario] = useState(false);
  const [usuarioForm, setUsuarioForm] = useState(USUARIO_DEFAULT);
  const [resetUsuario, setResetUsuario] = useState(null);
  const [resetSenhaTemporaria, setResetSenhaTemporaria] = useState("");
  const [savingResetSenha, setSavingResetSenha] = useState(false);
  const [senhaExibida, setSenhaExibida] = useState(null);

  const empresaNome = useMemo(
    () => String(empresa?.nome_empresa || "Empresa"),
    [empresa?.nome_empresa],
  );
  const selectedModelId = String(iaConfig?.modelo_ia || "");
  const selectedModelOption = modelOptions.find((item) => item.id === selectedModelId);
  const reasoningCapabilities = selectedModelOption?.reasoning_effort_capabilities
    || iaConfig?.reasoning_effort_capabilities
    || { status: "unknown", options: ["default"], levels: [] };
  const reasoningEffortOptions = Array.isArray(reasoningCapabilities.options) && reasoningCapabilities.options.length
    ? reasoningCapabilities.options
    : ["default"];
  const selectedReasoningEffort = String(iaConfig?.modelo_ia_reasoning_effort || "default");
  const reasoningEffortIncompatible = !reasoningEffortOptions.includes(selectedReasoningEffort);
  const currentModelMissing = Boolean(selectedModelId && !modelOptions.some((item) => item.id === selectedModelId));
  const visibleModelOptions = useMemo(() => {
    const normalizedQuery = modelSearch.trim().toLowerCase();
    const matching = modelOptions.filter((item) => !normalizedQuery || `${item.name || item.id} ${item.id}`.toLowerCase().includes(normalizedQuery));
    const selected = modelOptions.find((item) => item.id === selectedModelId);
    return selected && !matching.some((item) => item.id === selected.id) ? [selected, ...matching] : matching;
  }, [modelOptions, modelSearch, selectedModelId]);

  const alterarTab = (tabId) => {
    const next = new URLSearchParams(searchParams);
    if (tabId === "visao-geral") next.delete("tab");
    else next.set("tab", tabId);
    setSearchParams(next, { state: location.state });
  };

  const carregarEmpresa = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    setLoadError("");
    setTagsIaLoading(true);
    try {
      const res = await api.get("/empresas/");
      const atual = (res.data || []).find((e) => String(e.id) === String(empresaId)) || null;
      const [resIaConfig, resTags, resFunnel, resModels] = atual
        ? await Promise.all([
            api.get(`/empresas/${empresaId}/ia-config`).catch(() => ({ data: {} })),
            api.get(`/empresas/${empresaId}/crm/tags/oficiais`).catch(() => ({ data: null })),
            api.get(`/empresas/${empresaId}/monitor/funnel`).catch(() => ({ data: null })),
            api.get(`/admin/empresas/${empresaId}/openai-models`).catch(() => ({ data: { items: [], credential_status: "unknown", stale: false, load_error: true } })),
          ])
        : [{ data: {} }, { data: [] }, { data: null }, { data: null }];
      setEmpresa(atual);
      setEditEmpresa({
        nome_empresa: String(atual?.nome_empresa || ""),
        area_atuacao: String(atual?.area_atuacao || ""),
      });
      setOpenaiApiKey("");
      setOpenaiKeyConfigured(Boolean(atual?.openai_api_key_configurada || resIaConfig.data?.openai_configurada));
      setIaConfig({
        ...IA_CONFIG_DEFAULT,
        ...(resIaConfig.data || {}),
        telefone_notificacao: String(resIaConfig.data?.telefone_notificacao || ""),
        tags_ia_habilitadas: Array.isArray(resIaConfig.data?.tags_ia_habilitadas) ? resIaConfig.data.tags_ia_habilitadas : [],
      });
      setFunnelStatus(resFunnel?.data || null);
      setModelOptions(Array.isArray(resModels.data?.items) ? resModels.data.items : []);
      setModelCatalog(resModels.data || { credential_status: "unknown", stale: false, updated_at: null, source: null });
      if (resTags.data === null) {
        setTagsIaError("Não foi possível carregar as tags oficiais deste tenant.");
        setTagsOficiais([]);
      } else {
        setTagsIaError("");
        setTagsOficiais(Array.isArray(resTags.data) ? resTags.data : []);
      }
    } catch (err) {
      console.error("Erro ao carregar empresa:", err);
      setEmpresa(null);
      setLoadError("Não foi possível carregar os dados da empresa. Tente novamente.");
    } finally {
      setLoading(false);
      setTagsIaLoading(false);
    }
  }, [empresaId]);

  const previewFunnel = async () => {
    setFunnelBusy(true);
    try {
      const { data } = await api.post(`/empresas/${empresaId}/monitor/funnel/preview`);
      setFunnelPreview(data);
    } catch { setToast("Falha ao gerar prévia do funil."); }
    finally { setFunnelBusy(false); }
  };

  const syncFunnel = async () => {
    setFunnelBusy(true);
    try {
      const { data } = await api.post(`/empresas/${empresaId}/monitor/funnel/sync`);
      setFunnelPreview(null);
      setFunnelStatus({ spec: data.spec, stale: false });
      setToast("Funil e tags sincronizados.");
      await carregarEmpresa();
    } catch { setToast("Falha ao sincronizar funil e tags."); }
    finally { setFunnelBusy(false); }
  };

  const toggleTagIa = (tagId) => {
    const normalizedId = String(tagId || "").trim();
    if (!normalizedId) return;
    setIaConfig((current) => {
      const selected = Array.isArray(current.tags_ia_habilitadas) ? current.tags_ia_habilitadas.map(String) : [];
      const next = selected.includes(normalizedId)
        ? selected.filter((id) => id !== normalizedId)
        : [...selected, normalizedId];
      return { ...current, tags_ia_habilitadas: next };
    });
  };

  useEffect(() => {
    carregarEmpresa();
  }, [carregarEmpresa]);

  const gerarSenhaTemporaria = useCallback(() => {
    const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";
    const bytes = new Uint32Array(18);
    window.crypto?.getRandomValues?.(bytes);
    const suffix = Array.from(bytes, (value, index) => {
      const fallback = Math.floor(Math.random() * alphabet.length);
      return alphabet[(value || fallback + index) % alphabet.length];
    }).join("");
    return `AgOS-${suffix}`;
  }, []);

  const carregarUsuarios = useCallback(async () => {
    if (!empresaId) return [];
    try {
      setUsuariosLoading(true);
      setUsuariosError("");
      const res = await api.get(`/admin/empresas/${empresaId}/usuarios`);
      const lista = Array.isArray(res.data) ? res.data : [];
      setUsuarios(lista);
      return lista;
    } catch (err) {
      console.error("Erro ao carregar usuários da empresa:", err);
      setUsuarios([]);
      setUsuariosError("Não foi possível carregar os usuários da empresa.");
      return [];
    } finally {
      setUsuariosLoading(false);
    }
  }, [empresaId]);

  useEffect(() => {
    if (tabAtiva === "usuarios") {
      carregarUsuarios();
    }
  }, [carregarUsuarios, tabAtiva]);

  useEffect(() => {
    const carregarOverview = async () => {
      if (!empresaId) return;
      setOverviewLoading(true);
      try {
        const [resDashboard, resWhatsapp] = await Promise.all([
          api.get(`/empresas/${empresaId}/dashboard/stats`).catch(() => null),
          api.get(`/empresas/${empresaId}/whatsapp/status`).catch(() => null),
        ]);

        const whatsappStatus = String(resWhatsapp?.data?.status || "unknown").toLowerCase();
        const whatsappOnline = Boolean(resWhatsapp?.data?.online) || Boolean(resWhatsapp?.data?.conectado);
        const whatsappStatusLabel = resWhatsapp?.data?.status ? formatWhatsappStatus(whatsappStatus, whatsappOnline) : "Não disponível";
        const whatsappProviderLabel = resWhatsapp ? formatWhatsappProvider(resWhatsapp?.data?.provider, resWhatsapp?.data?.provider_label) : "Não foi possível consultar a conexão";

        const totalInteracoesReais = resDashboard?.data?.total_mensagens == null ? NaN : Number(resDashboard.data.total_mensagens);
        setOverview((prev) => ({
          ...prev,
          whatsappStatus,
          whatsappStatusLabel,
          whatsappProviderLabel,
          whatsappOnline,
          totalInteracoes: Number.isFinite(totalInteracoesReais) ? totalInteracoesReais : null,
        }));
      } catch (err) {
        console.error("Erro ao carregar visão geral:", err);
      } finally {
        setOverviewLoading(false);
      }
    };

    carregarOverview();
  }, [empresaId]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = window.setTimeout(() => setToast(""), 2500);
    return () => window.clearTimeout(t);
  }, [toast]);

  const salvarEmpresa = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    try {
      setSavingEmpresa(true);
      await api.put(`/empresas/${empresaId}`, editEmpresa);
      setToast("Dados da empresa atualizados.");
      await carregarEmpresa();
    } catch (err) {
      console.error("Erro ao salvar empresa:", err);
      setToast("Falha ao salvar dados da empresa.");
    } finally {
      setSavingEmpresa(false);
    }
  };

  const salvarOpenAI = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    if (!openaiApiKey.trim()) {
      setToast("Informe uma nova chave para substituir a credencial atual. Campo vazio preserva a configuração existente.");
      return;
    }
    try {
      setSavingCredenciais(true);
      await api.put(`/empresas/${empresaId}/credenciais`, { openai_api_key: openaiApiKey });
      setToast("Credenciais salvas.");
      await carregarEmpresa();
    } catch {
      setToast("Falha ao salvar credenciais.");
    } finally {
      setSavingCredenciais(false);
    }
  };

  const atualizarCatalogoModelos = async () => {
    if (!empresaId) return;
    try {
      setRefreshingModels(true);
      const response = await api.get(`/admin/empresas/${empresaId}/openai-models?refresh=true`);
      const catalog = response.data || {};
      setModelCatalog(catalog);
      setModelOptions(Array.isArray(catalog.items) ? catalog.items : []);
      setToast(catalog.stale ? "A atualização falhou; o último catálogo válido está sendo exibido." : "Catálogo OpenAI atualizado.");
    } catch (error) {
      const stale = error?.response?.data?.detail?.code === "catalog_refresh_failed";
      setModelCatalog((previous) => ({ ...previous, stale: true, refresh_error: error?.response?.data?.detail?.provider_status || "provider_unavailable" }));
      setToast(stale ? "Não foi possível atualizar o catálogo agora. Confira a conexão e tente novamente." : "Não foi possível carregar o catálogo OpenAI deste tenant.");
    } finally {
      setRefreshingModels(false);
    }
  };

  const validarModeloSelecionado = async () => {
    if (!empresaId || !selectedModelId) return;
    if (!window.confirm(`O AgentOS fará uma chamada de teste explícita a ${selectedModelId}. A OpenAI pode cobrar pelo uso. O teste enviará apenas o texto sintético “Reply with OK.”. Continuar?`)) return;
    try {
      setValidatingModel(true);
      await api.post(`/admin/empresas/${empresaId}/openai-models/validate`, { model_id: selectedModelId });
      setModelOptions((items) => items.map((item) => item.id === selectedModelId ? { ...item, compatibility: "verified_compatible", compatibility_known: true, validation_required: false, compatibility_explanation: "Validado com a credencial deste tenant" } : item));
      setToast("Modelo validado. A seleção só será ativada ao salvar a configuração.");
    } catch (error) {
      const code = error?.response?.data?.detail?.provider_status || error?.response?.data?.detail?.code || "validation_failed";
      setToast(`Não foi possível validar este modelo (${code}). O modelo atual foi preservado.`);
    } finally {
      setValidatingModel(false);
    }
  };

  const salvarIaConfig = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    if (reasoningEffortIncompatible) {
      setToast("Escolha explicitamente um nível de raciocínio compatível com o modelo.");
      return;
    }
    try {
      setSavingIaConfig(true);
      await api.put(`/empresas/${empresaId}/ia-config`, {
        ...iaConfig,
        limite_certeza: Number(iaConfig.limite_certeza),
        limite_duvida: Number(iaConfig.limite_duvida),
        max_agentes_desempate: Number(iaConfig.max_agentes_desempate),
        coletar_nome: Boolean(iaConfig.coletar_nome),
        followup_ativo: Boolean(iaConfig.followup_ativo),
      });
      setToast("Configuração do agente salva.");
      await carregarEmpresa();
    } catch (err) {
      console.error("Erro ao salvar configuração do agente:", err);
      const detail = err?.response?.data?.detail;
      setToast(typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : "Falha ao salvar configuração do agente.");
    } finally {
      setSavingIaConfig(false);
    }
  };

  const formatarDataUsuario = (value) => {
    if (!value) return "Não registrado";
    const data = new Date(value);
    if (Number.isNaN(data.getTime())) return "Não registrado";
    return data.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  };

  const abrirNovoUsuario = () => {
    setUsuarioError("");
    setEditingUsuarioId(null);
    setSenhaExibida(null);
    setUsuarioForm({ ...USUARIO_DEFAULT, senha_temporaria: gerarSenhaTemporaria() });
    setShowUsuarioModal(true);
  };

  const abrirEdicaoUsuario = (usuario) => {
    setUsuarioError("");
    setEditingUsuarioId(usuario.id);
    setSenhaExibida(null);
    setUsuarioForm({
      nome: usuario.nome || "",
      email: usuario.email || "",
      role: usuario.role || "admin_empresa",
      ativo: Boolean(usuario.ativo),
      senha_temporaria: "",
    });
    setShowUsuarioModal(true);
  };

  const salvarUsuario = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    const payload = {
      nome: usuarioForm.nome,
      email: usuarioForm.email,
      role: usuarioForm.role,
      ativo: Boolean(usuarioForm.ativo),
    };
    try {
      setSavingUsuario(true);
      setUsuarioError("");
      if (editingUsuarioId) {
        await api.put(`/admin/empresas/${empresaId}/usuarios/${editingUsuarioId}`, payload);
        setShowUsuarioModal(false);
        setToast("Usuário atualizado.");
      } else {
        const res = await api.post(`/admin/empresas/${empresaId}/usuarios`, {
          ...payload,
          senha_temporaria: usuarioForm.senha_temporaria,
        });
        setShowUsuarioModal(false);
        setSenhaExibida({
          nome: res.data?.usuario?.nome || payload.nome,
          email: res.data?.usuario?.email || payload.email,
          senha: res.data?.senha_temporaria || "",
        });
        setToast("Usuário criado ou atualizado.");
      }
      await carregarUsuarios();
    } catch (err) {
      console.error("Erro ao salvar usuário:", err);
      setUsuarioError(typeof err?.response?.data?.detail === "string" ? err.response.data.detail : "Falha ao salvar usuário. Revise os dados e tente novamente.");
    } finally {
      setSavingUsuario(false);
    }
  };

  const alternarStatusUsuario = async (usuario) => {
    if (!empresaId || !usuario?.id) return;
    const acao = usuario.ativo ? "desativar" : "ativar";
    try {
      await api.post(`/admin/empresas/${empresaId}/usuarios/${usuario.id}/${acao}`);
      await carregarUsuarios();
      setToast(usuario.ativo ? "Usuário desativado." : "Usuário ativado.");
    } catch (err) {
      console.error("Erro ao alterar status do usuário:", err);
      setToast("Falha ao alterar status do usuário.");
    }
  };

  const abrirResetSenha = (usuario) => {
    setResetError("");
    setSenhaExibida(null);
    setResetUsuario(usuario);
    setResetSenhaTemporaria(gerarSenhaTemporaria());
  };

  const resetarSenhaUsuario = async (e) => {
    e.preventDefault();
    if (!empresaId || !resetUsuario?.id) return;
    try {
      setSavingResetSenha(true);
      setResetError("");
      const res = await api.post(`/admin/empresas/${empresaId}/usuarios/${resetUsuario.id}/reset-senha`, {
        senha_temporaria: resetSenhaTemporaria,
      });
      setSenhaExibida({
        nome: res.data?.usuario?.nome || resetUsuario.nome,
        email: res.data?.usuario?.email || resetUsuario.email,
        senha: res.data?.senha_temporaria || "",
      });
      setResetUsuario(null);
      setResetSenhaTemporaria("");
      setToast("Senha temporária redefinida.");
    } catch (err) {
      console.error("Erro ao resetar senha:", err);
      setResetError(typeof err?.response?.data?.detail === "string" ? err.response.data.detail : "Falha ao resetar senha. Tente novamente.");
    } finally {
      setSavingResetSenha(false);
    }
  };

  const copiarSenhaExibida = async () => {
    if (!senhaExibida?.senha) return;
    try {
      await navigator.clipboard.writeText(senhaExibida.senha);
      setToast("Senha temporária copiada.");
    } catch {
      setToast("Não foi possível copiar automaticamente.");
    }
  };

  const irParaTesteAgente = () => {
    localStorage.setItem("impersonated_empresa_id", String(empresaId));
    const params = new URLSearchParams();
    params.set("empresaId", String(empresaId));
    navigate(`/painel/simulador?${params.toString()}`);
  };

  if (loading) return <LoadingState label="Carregando dados da empresa..." />;
  if (loadError) return <ErrorState title="Falha ao carregar empresa" description={loadError} action={<ActionButton type="button" onClick={carregarEmpresa} icon={RefreshCw}>Tentar novamente</ActionButton>} />;
  if (!empresa) return <EmptyState title="Empresa não encontrada" description="A empresa solicitada não está disponível nesta listagem." action={<Link to="/admin" className="fm-button fm-button-secondary">Voltar para empresas</Link>} />;

  return (
    <div className="admin-company-details space-y-6">
      <Link to={companiesReturn} className="admin-company-back"><ArrowLeft size={16} aria-hidden="true" /> Voltar para empresas</Link>
      <PageHeader title={empresaNome} icon={Building2} eyebrow="Gestão de empresas" description="Gerencie acessos, agente, canais e configurações desta empresa." actions={<StatusBadge tone="info">Empresa selecionada</StatusBadge>} />
      <Tabs items={TABS.map((tab) => ({ ...tab, panelId: `company-panel-${tab.id}` }))} value={tabAtiva} onChange={alterarTab} label="Seções da empresa" className="admin-company-tabs" />
      <div role="tabpanel" id={`company-panel-${tabAtiva}`} aria-label={TABS.find((tab) => tab.id === tabAtiva)?.label} tabIndex={0} className="admin-company-panel">
      {tabAtiva === "visao-geral" && (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <MetricCard label="Conexão WhatsApp" value={overviewLoading ? "Consultando…" : overview.whatsappStatusLabel} helper={overviewLoading ? "Consultando a conexão da empresa." : overview.whatsappProviderLabel} icon={MessageSquare} tone={overview.whatsappOnline ? "success" : "warning"} />
            <MetricCard label="Interações registradas" value={overviewLoading ? "Consultando…" : overview.totalInteracoes ?? "Não disponível"} helper="Histórico real de mensagens da empresa." icon={Database} />
            <SectionCard title="Acesso rápido" description="Configure a empresa ou teste o atendimento no contexto do cliente.">
              <div className="fm-actions">
                <ActionButton type="button" onClick={() => setConfirmarTeste(true)} icon={BrainCircuit}>Testar Agente</ActionButton>
                <ActionButton type="button" variant="secondary" onClick={() => alterarTab("canais")} icon={Link2}>Gerenciar canais</ActionButton>
              </div>
            </SectionCard>
          </div>
          {!overviewLoading && (overview.whatsappStatus === "unknown" || overview.totalInteracoes === null) ? <InfoBanner tone="warning">Parte dos indicadores não pôde ser consultada. A informação indisponível não representa ausência de conexão ou de mensagens.</InfoBanner> : null}

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Nome</p>
              <p className="mt-2 text-sm text-slate-700">{empresa.nome_empresa}</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Tenant ID</p>
              <p className="mt-2 break-all text-xs font-mono text-slate-600">{empresa.id}</p>
            </div>
          </div>
        </div>
      )}

      {tabAtiva === "usuarios" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                  <UserCog size={16} />
                  Usuários da empresa
                </h3>
                <p className="mt-2 max-w-3xl text-sm text-slate-500">
                  Gestão de acesso administrada pelo Super Admin. Senhas temporárias aparecem apenas na criação ou reset.
                </p>
              </div>
              <ActionButton
                type="button"
                onClick={abrirNovoUsuario}
              >
                <Plus size={14} />
                Novo usuário
              </ActionButton>
            </div>
          </div>

          {usuariosLoading ? (
            <LoadingState label="Carregando usuários..." />
          ) : usuariosError ? (
            <ErrorState title="Falha ao carregar usuários" description={usuariosError} action={<ActionButton type="button" onClick={carregarUsuarios} icon={RefreshCw}>Tentar novamente</ActionButton>} />
          ) : usuarios.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
              <p className="text-sm font-semibold text-slate-900">Nenhum usuário vinculado a esta empresa.</p>
              <p className="mt-2 text-sm text-slate-500">Crie o primeiro admin do tenant pelo fluxo Super Admin.</p>
              <ActionButton
                type="button"
                onClick={abrirNovoUsuario}
              >
                <Plus size={14} />
                Criar usuário
              </ActionButton>
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="overflow-x-auto" role="region" aria-label="Usuários da empresa" tabIndex={0}>
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <caption className="sr-only">Usuários e acessos de {empresaNome}</caption>
                  <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Nome</th>
                      <th className="px-4 py-3 font-semibold">E-mail</th>
                      <th className="px-4 py-3 font-semibold">Perfil de acesso</th>
                      <th className="px-4 py-3 font-semibold">Status</th>
                      <th className="px-4 py-3 font-semibold">Último login</th>
                      <th className="px-4 py-3 text-right font-semibold">Ações</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {usuarios.map((usuario) => (
                      <tr key={usuario.id} className="align-middle">
                        <td className="px-4 py-3 font-medium text-slate-900">{usuario.nome}</td>
                        <td className="px-4 py-3 text-slate-600">{usuario.email}</td>
                        <td className="px-4 py-3">
                          <MiniBadge tone="internal">{usuario.role}</MiniBadge>
                        </td>
                        <td className="px-4 py-3">
                          <MiniBadge tone={usuario.ativo ? "active" : "warning"}>
                            {usuario.ativo ? "Ativo" : "Inativo"}
                          </MiniBadge>
                        </td>
                        <td className="px-4 py-3 text-slate-500">{formatarDataUsuario(usuario.ultimo_login)}</td>
                        <td className="px-4 py-3">
                          <div className="flex justify-end gap-1">
                            <ActionButton
                              type="button"
                              onClick={() => abrirEdicaoUsuario(usuario)}
                              variant="secondary"
                              title="Editar usuário"
                              aria-label={`Editar usuário ${usuario.nome}`}
                            >
                              <Pencil size={15} />
                            </ActionButton>
                            <ActionButton
                              type="button"
                              onClick={() => abrirResetSenha(usuario)}
                              variant="secondary"
                              title="Resetar senha temporária"
                              aria-label={`Resetar senha de ${usuario.nome}`}
                            >
                              <KeyRound size={15} />
                            </ActionButton>
                            <ActionButton
                              type="button"
                              onClick={() => setStatusUsuario(usuario)}
                              variant="secondary"
                              title={usuario.ativo ? "Desativar usuário" : "Ativar usuário"}
                              aria-label={`${usuario.ativo ? "Desativar" : "Ativar"} usuário ${usuario.nome}`}
                            >
                              {usuario.ativo ? <PowerOff size={15} /> : <Power size={15} />}
                            </ActionButton>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {tabAtiva === "agente" && (
        <form onSubmit={salvarIaConfig} className="space-y-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                <BrainCircuit size={16} />
                Agente de Atendimento
              </h3>
              <p className="mt-2 max-w-3xl text-sm text-slate-500">
                Configuração crítica administrada pelo Super Admin. Estes campos alimentam o agente principal do tenant e
                não são exibidos para edição no painel cliente.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge tone="warning">Apenas Super Admin</StatusBadge>
              <Link
                to={`/admin/empresas/${empresaId}/atendimento-ia`}
                className="inline-flex min-h-11 items-center rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-sm font-semibold text-indigo-800 transition-colors hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700"
              >
                Prompts e diagnósticos
              </Link>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <label htmlFor="company-field-1" className="mb-1 block text-xs text-slate-500">Nome do agente</label>
              <input id="company-field-1"
                value={iaConfig.nome_agente || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, nome_agente: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div>
              <label htmlFor="company-field-2" className="mb-1 block text-xs text-slate-500">Mensagem de saudação</label>
              <textarea id="company-field-2"
                rows={6}
                value={iaConfig.mensagem_saudacao || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, mensagem_saudacao: e.target.value }))}
                className="fm-field"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <ActionButton type="button" onClick={previewFunnel} disabled={funnelBusy}>
                  <RefreshCw size={14} /> Sincronizar funil e tags
                </ActionButton>
                {funnelStatus?.stale && <MiniBadge tone="warning">Funil desatualizado</MiniBadge>}
                {funnelStatus?.spec && !funnelStatus?.stale && <MiniBadge tone="active">Funil sincronizado · {funnelStatus.spec.version}</MiniBadge>}
              </div>
              {funnelPreview && (
                <div className="fm-feature-card mt-3">
                  <div className="mb-2 font-semibold text-slate-700">Prévia: {funnelPreview.stage_count} etapas · {funnelPreview.new_stage_count} novas</div>
                  <div className="grid gap-1 md:grid-cols-2">{(funnelPreview.spec?.stages || []).map((stage) => <div key={stage.key}>#{stage.order} {stage.display_name} → {stage.meta_event}</div>)}</div>
                  <ActionButton type="button" onClick={syncFunnel} disabled={funnelBusy}>Confirmar sincronização</ActionButton>
                </div>
              )}
            </div>
            <div className="lg:col-span-2">
              <label htmlFor="company-field-3" className="mb-1 block text-xs text-slate-500">Personalidade / tom / persona</label>
              <textarea id="company-field-3"
                rows={4}
                value={iaConfig.ia_personalidade || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, ia_personalidade: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div className="lg:col-span-2">
              <label htmlFor="company-field-4" className="mb-1 block text-xs text-slate-500">Regras obrigatórias / regras de negócio</label>
              <textarea id="company-field-4"
                rows={5}
                value={iaConfig.ia_regras_negocio || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, ia_regras_negocio: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div className="lg:col-span-2">
              <label htmlFor="company-field-5" className="mb-1 block text-xs text-slate-500">Monitor de conversão</label>
              <p className="mb-2 text-xs text-slate-400">
                Orienta a IA que acompanha a conversa e identifica avanços comerciais para aplicação das tags configuradas.
              </p>
              <textarea id="company-field-5"
                rows={6}
                value={iaConfig.conversion_monitor_prompt || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, conversion_monitor_prompt: e.target.value }))}
                placeholder="Descreva como interpretar avanço comercial, evidências, sinais negativos e o momento de aplicar tags."
                className="fm-field"
              />
            </div>
            <div className="lg:col-span-2 space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <label htmlFor="company-field-6" className="mb-1 block text-xs font-semibold text-slate-700">Modelo principal</label>
                  <p className="text-xs text-slate-500">Este modelo é compartilhado pelo atendimento e pelo Monitor de Conversão.</p>
                </div>
                <ActionButton type="button" variant="secondary" disabled={refreshingModels || modelCatalog.credential_status === "not_configured"} onClick={atualizarCatalogoModelos}>
                  <RefreshCw size={14} className={refreshingModels ? "animate-spin" : ""} />
                  {refreshingModels ? "Atualizando..." : "Atualizar modelos"}
                </ActionButton>
              </div>
              <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input aria-label="Buscar modelos OpenAI" value={modelSearch} onChange={(event) => setModelSearch(event.target.value)} placeholder="Buscar por nome ou ID" className="fm-field pl-9" />
              </div>
              <select id="company-field-6"
                value={selectedModelId}
                onChange={(event) => setIaConfig((previous) => ({ ...previous, modelo_ia: event.target.value }))}
                className="fm-field"
              >
                {currentModelMissing && <option value={selectedModelId}>{selectedModelId} — Modelo atual — não confirmado</option>}
                {!selectedModelId && <option value="">Selecione um modelo disponível</option>}
                {visibleModelOptions.map((option) => {
                  const compatibilityLabel = option.compatibility === "incompatible" ? "Não compatível" : option.compatibility === "verified_compatible" ? "Validado" : option.compatibility === "known_compatible" ? "Compatibilidade conhecida" : "Verificação necessária";
                  const availabilityLabel = option.availability === "listed" ? "Listado na OpenAI" : "Disponibilidade desconhecida";
                  return <option key={option.id} value={option.id} disabled={option.compatibility === "incompatible"}>{option.name || option.id} — {availabilityLabel} · {compatibilityLabel}</option>;
                })}
              </select>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <span>
                  {modelCatalog.credential_status === "not_configured" ? "Credencial deste tenant não configurada. Cadastre uma chave para consultar modelos." : modelCatalog.load_error ? "Não foi possível carregar o catálogo deste tenant. O modelo salvo continua preservado." : modelCatalog.stale ? "Catálogo antigo: a atualização mais recente falhou; o último resultado válido foi preservado." : modelCatalog.updated_at ? `Catálogo consultado em ${new Date(modelCatalog.updated_at).toLocaleString()}.` : "O catálogo é consultado com a credencial OpenAI desta empresa."}
                </span>
                {modelOptions.find((item) => item.id === selectedModelId)?.validation_required && <ActionButton type="button" variant="secondary" disabled={validatingModel} onClick={validarModeloSelecionado}>{validatingModel ? "Validando..." : "Validar modelo"}</ActionButton>}
              </div>
              {modelOptions.find((item) => item.id === selectedModelId)?.validation_required && <p className="text-xs text-amber-700">Este modelo ainda não tem compatibilidade comprovada. A validação faz uma chamada explícita à API e pode gerar custo; nenhum dado de cliente será enviado.</p>}
              {currentModelMissing && <p className="text-xs text-amber-700">O catálogo atual não confirmou o modelo salvo. Ele permanece selecionado e não será substituído automaticamente.</p>}
              <div className="mt-3 border-t border-slate-200 pt-3">
                <label htmlFor="company-field-reasoning-effort" className="mb-1 block text-xs font-semibold text-slate-700">Nível de raciocínio</label>
                <select
                  id="company-field-reasoning-effort"
                  value={selectedReasoningEffort}
                  onChange={(event) => setIaConfig((previous) => ({ ...previous, modelo_ia_reasoning_effort: event.target.value }))}
                  className="fm-field"
                  aria-describedby="reasoning-effort-help"
                  aria-invalid={reasoningEffortIncompatible}
                >
                  {reasoningEffortIncompatible && <option value={selectedReasoningEffort} disabled>{selectedReasoningEffort} — incompatível; escolha um nível permitido</option>}
                  {reasoningEffortOptions.map((effort) => {
                    const labels = { default: "Padrão do modelo", none: "Nenhum", minimal: "Mínimo", low: "Baixo", medium: "Médio", high: "Alto", xhigh: "Muito alto", max: "Máximo" };
                    return <option key={effort} value={effort}>{labels[effort] || effort}</option>;
                  })}
                </select>
                <p id="reasoning-effort-help" className="mt-2 text-xs text-slate-500">
                  Níveis mais altos podem aumentar latência, tokens de raciocínio e custo. “Padrão do modelo” omite o parâmetro e preserva o padrão do provider.
                </p>
                {reasoningEffortIncompatible && <p role="alert" className="mt-2 text-xs text-amber-700">O nível atual não é compatível com o modelo selecionado. Escolha uma opção permitida antes de salvar.</p>}
                {!reasoningEffortIncompatible && reasoningCapabilities.status === "not_supported" && <p className="mt-2 text-xs text-slate-500">Este modelo não oferece níveis explícitos de raciocínio; o padrão do modelo será usado.</p>}
                {!reasoningEffortIncompatible && reasoningCapabilities.status === "unknown" && <p className="mt-2 text-xs text-amber-700">Compatibilidade não confirmada para este modelo. Apenas o padrão do provider pode ser selecionado.</p>}
              </div>
            </div>
            <div>
              <label htmlFor="company-field-7" className="mb-1 block text-xs text-slate-500">Limite de certeza</label>
              <input id="company-field-7"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={iaConfig.limite_certeza}
                onChange={(e) => setIaConfig((p) => ({ ...p, limite_certeza: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div>
              <label htmlFor="company-field-8" className="mb-1 block text-xs text-slate-500">Limite de dúvida</label>
              <input id="company-field-8"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={iaConfig.limite_duvida}
                onChange={(e) => setIaConfig((p) => ({ ...p, limite_duvida: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div>
              <label htmlFor="company-field-9" className="mb-1 block text-xs text-slate-500">Máx. agentes no desempate</label>
              <input id="company-field-9"
                type="number"
                min="1"
                value={iaConfig.max_agentes_desempate}
                onChange={(e) => setIaConfig((p) => ({ ...p, max_agentes_desempate: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div>
              <label htmlFor="company-field-10" className="mb-1 block text-xs text-slate-500">Telefone de notificação</label>
              <input id="company-field-10"
                value={iaConfig.telefone_notificacao || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, telefone_notificacao: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div className="lg:col-span-2">
              <label htmlFor="company-field-11" className="mb-1 block text-xs text-slate-500">Informações adicionais</label>
              <textarea id="company-field-11"
                rows={3}
                value={iaConfig.informacoes_adicionais || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, informacoes_adicionais: e.target.value }))}
                className="fm-field"
              />
            </div>
            <label className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={Boolean(iaConfig.coletar_nome)}
                onChange={(e) => setIaConfig((p) => ({ ...p, coletar_nome: e.target.checked }))}
              />
              Coletar nome do cliente
            </label>
            <label className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={Boolean(iaConfig.followup_ativo)}
                onChange={(e) => setIaConfig((p) => ({ ...p, followup_ativo: e.target.checked }))}
              />
              Follow-up ativo
            </label>
          </div>

          <section className="fm-feature-card" aria-labelledby="tags-ia-title">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 id="tags-ia-title" className="text-sm font-semibold text-slate-950">Tags disponíveis para a IA</h4>
                <p className="mt-1 max-w-3xl text-sm text-slate-600">
                  Selecione quais tags o atendente de IA poderá consultar e aplicar. Essa configuração não altera as conversões vinculadas às tags.
                </p>
              </div>
              <StatusBadge tone="info">
                {Array.isArray(iaConfig.tags_ia_habilitadas) ? iaConfig.tags_ia_habilitadas.length : 0} selecionada(s)
              </StatusBadge>
            </div>

            {tagsIaLoading ? (
              <p className="mt-4 text-sm text-slate-500">Carregando tags oficiais...</p>
            ) : tagsIaError ? (
              <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{tagsIaError}</p>
            ) : tagsOficiais.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-slate-300 bg-white px-3 py-3 text-sm text-slate-600">
                Nenhuma tag oficial cadastrada para este tenant. Cadastre tags antes de disponibilizá-las para a IA.
              </p>
            ) : (
              <div className="mt-4 grid gap-2 md:grid-cols-2">
                {tagsOficiais.map((tag) => {
                  const tagId = String(tag.id || "");
                  const selected = Array.isArray(iaConfig.tags_ia_habilitadas) && iaConfig.tags_ia_habilitadas.map(String).includes(tagId);
                  return (
                    <label key={tagId} className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-700 hover:border-indigo-300">
                      <input
                        type="checkbox"
                        value={tagId}
                        checked={selected}
                        onChange={() => toggleTagIa(tagId)}
                      />
                      <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: tag.cor || "#64748b" }} aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate font-medium">{tag.nome || "Tag sem nome"}</span>
                    </label>
                  );
                })}
              </div>
            )}
          </section>

          <ActionButton
            type="submit"
            disabled={savingIaConfig || reasoningEffortIncompatible}
          >
            <Save size={14} />
            {savingIaConfig ? "Salvando..." : "Salvar configuração do agente"}
          </ActionButton>
        </form>
      )}

      {tabAtiva === "regras" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <Columns3 size={16} />
              Regras comerciais
            </h3>
            <p className="mt-2 text-sm text-slate-500">
              Regras de orçamento, condições comerciais e avisos obrigatórios devem ser configurados pelo Super Admin.
            </p>
          </div>
          <EmpresaCrmEtapasManager empresaId={String(empresa.id)} onNotify={setToast} />
        </div>
      )}

      {tabAtiva === "conhecimento" && (
        <SectionCard title="Base de conhecimento" description="Gerencie as informações que apoiam as respostas do agente desta empresa." icon={Database}>
          <RAGManager empresaId={String(empresa.id)} />
        </SectionCard>
      )}

      {tabAtiva === "canais" && (
        <div className="space-y-4">
          <SectionCard title="Campanhas e integrações Meta" description="A equipe Ferrioli administra credenciais e ativos de anúncios desta empresa." icon={Link2} actions={<Link to={`/admin/empresas/${empresaId}/meta`} className="inline-flex min-h-11 items-center rounded-lg bg-indigo-700 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700">Abrir canais e campanhas</Link>} />
          <SectionCard title="Automação do Instagram" description="Credencial técnica administrada pelo Super Admin; publicações e regras ficam na área de campanhas do cliente." icon={Link2} actions={<Link to={`/admin/empresas/${empresaId}/instagram`} className="inline-flex min-h-11 items-center rounded-lg border border-indigo-200 bg-white px-4 py-2 text-sm font-semibold text-indigo-800 hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700">Gerenciar conexão Instagram</Link>} />
          <div className="grid gap-4 lg:grid-cols-2">
          <form
            onSubmit={salvarEmpresa}
            className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <Settings2 size={16} />
              Dados da Empresa
            </h3>
            <div>
              <label htmlFor="company-field-12" className="mb-1 block text-xs text-slate-500">Nome</label>
              <input id="company-field-12"
                value={editEmpresa.nome_empresa}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, nome_empresa: e.target.value }))}
                className="fm-field"
              />
            </div>
            <div>
              <label htmlFor="company-field-13" className="mb-1 block text-xs text-slate-500">Área de atuação</label>
              <input id="company-field-13"
                value={editEmpresa.area_atuacao}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, area_atuacao: e.target.value }))}
                className="fm-field"
              />
            </div>
            <ActionButton
              type="submit"
              disabled={savingEmpresa}
            >
              <Save size={14} />
              {savingEmpresa ? "Salvando..." : "Salvar dados"}
            </ActionButton>
          </form>

          <form
            onSubmit={salvarOpenAI}
            className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <Sparkles size={16} />
              Credencial OpenAI por Tenant
            </h3>
            <p className="text-xs text-slate-500">
              {openaiKeyConfigured ? "Credencial OpenAI configurada para este tenant. Digite uma nova chave somente para substituí-la; o campo vazio preserva a atual." : "Nenhuma credencial OpenAI configurada para este tenant. O catálogo exige uma chave associada a esta empresa."}
            </p>
            <div>
              <label htmlFor="company-field-14" className="mb-1 block text-xs text-slate-500">OPENAI API Key</label>
              <input id="company-field-14"
                type="password"
                value={openaiApiKey}
                onChange={(e) => setOpenaiApiKey(e.target.value)}
                placeholder={openaiKeyConfigured ? "Nova chave para substituição explícita" : "Informe a chave OpenAI deste tenant"}
                className="fm-field"
              />
            </div>
            <ActionButton
              type="submit"
              disabled={savingCredenciais}
            >
              <Save size={14} />
              {savingCredenciais ? "Salvando..." : "Salvar credencial"}
            </ActionButton>
          </form>
        </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <EmpresaConexoesManager
              empresaId={empresa.id}
              empresaNome={empresa.nome_empresa}
              conexaoDisparoId={empresa.conexao_disparo_id}
              disparoDelayMin={empresa.disparo_delay_min}
              disparoDelayMax={empresa.disparo_delay_max}
              onConexaoDisparoUpdated={carregarEmpresa}
            />
          </div>
        </div>
      )}

      {tabAtiva === "pagamentos" && (
        <div className="space-y-4"><SectionCard title="Pagamentos" description="Configure o provedor de pagamentos e suas credenciais para esta empresa." icon={CreditCard} /><EmpresaPaymentManager empresaId={String(empresa.id)} onNotify={setToast} /></div>
      )}

      {tabAtiva === "agenda-automacoes" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <CalendarDays size={16} />
              Agenda
            </h3>
            <p className="mt-2 text-sm text-slate-500">
              A agenda operacional permanece no painel cliente, mas políticas de como o agente coleta dados e confirma
              visitas ficam na configuração do agente pelo Super Admin.
            </p>
            <ActionButton type="button" variant="secondary" onClick={() => alterarTab("agente")}>Configurar agente</ActionButton>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <Sparkles size={16} />
              Automações
            </h3>
            <p className="mt-2 text-sm text-slate-500">
              Follow-ups e campanhas podem ser operados pelo cliente. Configurações técnicas e roteamento permanecem sob
              responsabilidade do Super Admin.
            </p>
            <ActionButton type="button" variant="secondary" onClick={() => alterarTab("agente")}>Configurar automações do agente</ActionButton>
          </div>
        </div>
      )}

      {tabAtiva === "imagens" && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
            <ImageIcon size={16} />
            Imagens / Visão
          </h3>
          <p className="mt-2 text-sm text-slate-500">
            A capacidade de análise de imagem é genérica da plataforma. Provider, modelo, BYOK, fallback e confirmação
            humana devem ser administrados aqui pelo Super Admin quando houver UI dedicada.
          </p>
        </div>
      )}

      {tabAtiva === "diagnostico" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <ShieldCheck size={16} />
              Diagnóstico do tenant
            </h3>
            <p className="mt-2 text-sm text-slate-500">
              Use esta área para validar status, simular atendimento e investigar falhas sem expor detalhes técnicos ao
              cliente.
            </p>
            <ActionButton
              type="button"
              onClick={() => setConfirmarTeste(true)}
            >
              Testar Agente
            </ActionButton>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <MessageSquare size={16} />
              Indicadores
            </h3>
            <p className="mt-2 text-sm text-slate-500">Mensagens registradas: {overviewLoading ? "Consultando…" : overview.totalInteracoes ?? "Não disponível"}</p>
            <p className="mt-1 text-sm text-slate-500">WhatsApp: {overviewLoading ? "Consultando…" : overview.whatsappStatusLabel}</p>
          </div>
        </div>
      )}

      </div>
      {toast ? <div className="admin-company-toast" role={/^(Falha|Não foi possível)/.test(toast) ? "alert" : "status"} aria-live="polite">{toast}</div> : null}

      <AdminDialog open={Boolean(statusUsuario)} title={statusUsuario?.ativo ? "Desativar usuário" : "Ativar usuário"} busy={savingStatusUsuario} onClose={() => setStatusUsuario(null)} footer={<><ActionButton type="button" variant="secondary" disabled={savingStatusUsuario} onClick={() => setStatusUsuario(null)}>Cancelar</ActionButton><ActionButton type="button" disabled={savingStatusUsuario} onClick={async () => { setSavingStatusUsuario(true); try { await alternarStatusUsuario(statusUsuario); } finally { setSavingStatusUsuario(false); setStatusUsuario(null); } }}>{savingStatusUsuario ? "Aguarde..." : "Confirmar alteração de acesso"}</ActionButton></>}>
        <InfoBanner tone="warning">{statusUsuario?.ativo ? "O acesso será desativado para" : "O acesso será reativado para"} <strong>{statusUsuario?.nome}</strong> nesta empresa. O perfil de acesso será preservado.</InfoBanner>
      </AdminDialog>

      <AdminDialog open={confirmarTeste} title="Acessar o contexto da empresa" onClose={() => setConfirmarTeste(false)} footer={<><ActionButton type="button" variant="secondary" onClick={() => setConfirmarTeste(false)}>Cancelar</ActionButton><ActionButton type="button" onClick={irParaTesteAgente}>Continuar para o simulador</ActionButton></>}>
        <InfoBanner tone="warning">Você acessará o simulador como <strong>{empresaNome}</strong>. As ações no painel usam o contexto desta empresa. Use “Encerrar Teste” para retornar à administração.</InfoBanner>
      </AdminDialog>

      <AdminDialog open={Boolean(senhaExibida)} title="Senha temporária" onClose={() => setSenhaExibida(null)} footer={<><ActionButton type="button" variant="secondary" onClick={copiarSenhaExibida}>Copiar</ActionButton><ActionButton type="button" onClick={() => setSenhaExibida(null)}>Entendi</ActionButton></>}>
        <InfoBanner tone="warning">A senha temporária será exibida apenas uma vez. Não há como consultar a senha atual depois.</InfoBanner>
        <div className="admin-company-password"><p>{senhaExibida?.nome} · {senhaExibida?.email}</p><p className="font-mono mt-2">{senhaExibida?.senha}</p></div>
      </AdminDialog>

      <AdminDialog open={showUsuarioModal} title={editingUsuarioId ? "Editar usuário" : "Novo usuário"} onClose={() => setShowUsuarioModal(false)} busy={savingUsuario} className="admin-company-form-dialog">
        <form onSubmit={salvarUsuario} className="space-y-4">
          <p className="fm-helper">Administre o acesso à empresa selecionada. Campos marcados com * são obrigatórios.</p>
          {usuarioError ? <div role="alert"><InfoBanner tone="danger">{usuarioError}</InfoBanner></div> : null}
          <fieldset disabled={savingUsuario} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div><label htmlFor="company-user-name">Nome *</label><input className="fm-field" id="company-user-name" data-autofocus required autoComplete="name" value={usuarioForm.nome} onChange={(e) => setUsuarioForm((p) => ({ ...p, nome: e.target.value }))} /></div>
              <div><label htmlFor="company-user-email">E-mail *</label><input className="fm-field" id="company-user-email" required type="email" autoComplete="email" value={usuarioForm.email} onChange={(e) => setUsuarioForm((p) => ({ ...p, email: e.target.value }))} /></div>
              <div><label htmlFor="company-user-role">Perfil de acesso</label><select className="fm-field" id="company-user-role" value={usuarioForm.role} onChange={(e) => setUsuarioForm((p) => ({ ...p, role: e.target.value }))}>{USUARIO_ROLES.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></div>
              <label className="flex items-center gap-3"><input type="checkbox" checked={Boolean(usuarioForm.ativo)} onChange={(e) => setUsuarioForm((p) => ({ ...p, ativo: e.target.checked }))} />Usuário ativo</label>
            </div>
            {!editingUsuarioId ? <div><label htmlFor="company-user-password">Senha temporária *</label><div className="flex gap-2"><input className="fm-field" id="company-user-password" required minLength={8} autoComplete="new-password" value={usuarioForm.senha_temporaria} onChange={(e) => setUsuarioForm((p) => ({ ...p, senha_temporaria: e.target.value }))} /><ActionButton type="button" variant="secondary" onClick={() => setUsuarioForm((p) => ({ ...p, senha_temporaria: gerarSenhaTemporaria() }))}>Gerar</ActionButton></div><p className="fm-helper">Compartilhe a senha temporária somente com o usuário.</p></div> : null}
          </fieldset>
          <div className="fm-actions justify-end"><ActionButton type="button" variant="secondary" disabled={savingUsuario} onClick={() => setShowUsuarioModal(false)}>Cancelar</ActionButton><ActionButton type="submit" disabled={savingUsuario} icon={Save}>{savingUsuario ? "Salvando..." : "Salvar usuário"}</ActionButton></div>
        </form>
      </AdminDialog>

      <AdminDialog open={Boolean(resetUsuario)} title="Resetar senha temporária" onClose={() => setResetUsuario(null)} busy={savingResetSenha} className="admin-company-form-dialog">
        <form onSubmit={resetarSenhaUsuario} className="space-y-4">
          <p className="fm-helper">{resetUsuario?.nome} · {resetUsuario?.email}</p>
          <InfoBanner tone="warning">Ao confirmar, a senha anterior será substituída. A nova senha temporária será exibida apenas uma vez.</InfoBanner>
          {resetError ? <div role="alert"><InfoBanner tone="danger">{resetError}</InfoBanner></div> : null}
          <fieldset disabled={savingResetSenha}><label htmlFor="company-reset-password">Nova senha temporária *</label><div className="flex gap-2"><input className="fm-field" id="company-reset-password" data-autofocus required minLength={8} autoComplete="new-password" value={resetSenhaTemporaria} onChange={(e) => setResetSenhaTemporaria(e.target.value)} /><ActionButton type="button" variant="secondary" onClick={() => setResetSenhaTemporaria(gerarSenhaTemporaria())}>Gerar</ActionButton></div></fieldset>
          <div className="fm-actions justify-end"><ActionButton type="button" variant="secondary" disabled={savingResetSenha} onClick={() => setResetUsuario(null)}>Cancelar</ActionButton><ActionButton type="submit" disabled={savingResetSenha} icon={KeyRound}>{savingResetSenha ? "Resetando..." : "Resetar senha"}</ActionButton></div>
        </form>
      </AdminDialog>
    </div>
  );
}
