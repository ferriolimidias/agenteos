import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BrainCircuit,
  Building2,
  CalendarDays,
  CheckCircle2,
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
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCog,
  UserRoundCheck,
  Users,
  X,
} from "lucide-react";

import api from "../../services/api";
import EmpresaConexoesManager from "../../components/EmpresaConexoesManager";
import EmpresaCrmEtapasManager from "../../components/EmpresaCrmEtapasManager";
import EmpresaPaymentManager from "../../components/EmpresaPaymentManager";
import RAGManager from "../../components/RAGManager";

const TABS = [
  { id: "visao-geral", label: "Visão Geral", icon: Building2 },
  { id: "usuarios", label: "Usuários", icon: UserCog },
  { id: "agente", label: "Agente de Atendimento", icon: BrainCircuit },
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
  const tones = {
    slate: "border-slate-200 bg-slate-50 text-slate-600",
    active: "border-emerald-200 bg-emerald-50 text-emerald-700",
    internal: "border-indigo-200 bg-indigo-50 text-indigo-700",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
  };
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tones[tone] || tones.slate}`}>
      {children}
    </span>
  );
}

export default function EmpresaDetalhes() {
  const navigate = useNavigate();
  const { empresaId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [empresa, setEmpresa] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tabAtiva, setTabAtiva] = useState("visao-geral");
  const [savingEmpresa, setSavingEmpresa] = useState(false);
  const [savingCredenciais, setSavingCredenciais] = useState(false);
  const [savingIaConfig, setSavingIaConfig] = useState(false);
  const [toast, setToast] = useState("");
  const [editEmpresa, setEditEmpresa] = useState({ nome_empresa: "", area_atuacao: "" });
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [iaConfig, setIaConfig] = useState(IA_CONFIG_DEFAULT);
  const [modelOptions, setModelOptions] = useState([]);
  const [tagsOficiais, setTagsOficiais] = useState([]);
  const [tagsIaLoading, setTagsIaLoading] = useState(false);
  const [tagsIaError, setTagsIaError] = useState("");
  const [funnelStatus, setFunnelStatus] = useState(null);
  const [funnelPreview, setFunnelPreview] = useState(null);
  const [funnelBusy, setFunnelBusy] = useState(false);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overview, setOverview] = useState({
    whatsappStatus: "not_configured",
    whatsappStatusLabel: "Não configurado",
    whatsappProviderLabel: "Sem conexão configurada",
    whatsappOnline: false,
    totalInteracoes: 0,
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

  useEffect(() => {
    const requestedTab = searchParams.get("tab");
    if (requestedTab && TABS.some((tab) => tab.id === requestedTab)) {
      setTabAtiva(requestedTab);
    }
  }, [searchParams]);

  const alterarTab = (tabId) => {
    setTabAtiva(tabId);
    setSearchParams(tabId === "visao-geral" ? {} : { tab: tabId });
  };

  const carregarEmpresa = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    setTagsIaLoading(true);
    try {
      const res = await api.get("/empresas/");
      const atual = (res.data || []).find((e) => String(e.id) === String(empresaId)) || null;
      const [resIaConfig, resTags, resFunnel, resModels] = atual
        ? await Promise.all([
            api.get(`/empresas/${empresaId}/ia-config`).catch(() => ({ data: {} })),
            api.get(`/empresas/${empresaId}/crm/tags/oficiais`).catch(() => ({ data: null })),
            api.get(`/empresas/${empresaId}/monitor/funnel`).catch(() => ({ data: null })),
            api.get("/admin/modelos-ia").catch(() => ({ data: [] })),
          ])
        : [{ data: {} }, { data: [] }, { data: null }, { data: [] }];
      setEmpresa(atual);
      setEditEmpresa({
        nome_empresa: String(atual?.nome_empresa || ""),
        area_atuacao: String(atual?.area_atuacao || ""),
      });
      setOpenaiApiKey(String(atual?.credenciais_canais?.openai_api_key || ""));
      setIaConfig({
        ...IA_CONFIG_DEFAULT,
        ...(resIaConfig.data || {}),
        telefone_notificacao: String(resIaConfig.data?.telefone_notificacao || ""),
        tags_ia_habilitadas: Array.isArray(resIaConfig.data?.tags_ia_habilitadas) ? resIaConfig.data.tags_ia_habilitadas : [],
      });
      setFunnelStatus(resFunnel?.data || null);
      setModelOptions(Array.isArray(resModels.data) ? resModels.data : []);
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
    } catch (err) { setToast("Falha ao gerar prévia do funil."); }
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
    } catch (err) { setToast("Falha ao sincronizar funil e tags."); }
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
      const res = await api.get(`/admin/empresas/${empresaId}/usuarios`);
      const lista = Array.isArray(res.data) ? res.data : [];
      setUsuarios(lista);
      return lista;
    } catch (err) {
      console.error("Erro ao carregar usuários da empresa:", err);
      setUsuarios([]);
      setToast("Falha ao carregar usuários da empresa.");
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
          api.get(`/empresas/${empresaId}/whatsapp/status`).catch(() => ({ data: {} })),
        ]);

        const whatsappStatus = String(resWhatsapp.data?.status || "not_configured").toLowerCase();
        const whatsappOnline = Boolean(resWhatsapp.data?.online) || Boolean(resWhatsapp.data?.conectado);
        const whatsappStatusLabel = formatWhatsappStatus(whatsappStatus, whatsappOnline);
        const whatsappProviderLabel = formatWhatsappProvider(resWhatsapp.data?.provider, resWhatsapp.data?.provider_label);

        const totalInteracoesReais = Number(resDashboard?.data?.total_mensagens);
        setOverview((prev) => ({
          ...prev,
          whatsappStatus,
          whatsappStatusLabel,
          whatsappProviderLabel,
          whatsappOnline,
          totalInteracoes: Number.isFinite(totalInteracoesReais) ? totalInteracoesReais : 0,
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
    try {
      setSavingCredenciais(true);
      await api.put(`/empresas/${empresaId}/credenciais`, { openai_api_key: openaiApiKey });
      setToast("Credenciais salvas.");
      await carregarEmpresa();
    } catch (err) {
      console.error("Erro ao salvar credenciais:", err);
      setToast("Falha ao salvar credenciais.");
    } finally {
      setSavingCredenciais(false);
    }
  };

  const salvarIaConfig = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
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
      setToast("Falha ao salvar configuração do agente.");
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
    setEditingUsuarioId(null);
    setSenhaExibida(null);
    setUsuarioForm({ ...USUARIO_DEFAULT, senha_temporaria: gerarSenhaTemporaria() });
    setShowUsuarioModal(true);
  };

  const abrirEdicaoUsuario = (usuario) => {
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
      if (editingUsuarioId) {
        await api.put(`/admin/empresas/${empresaId}/usuarios/${editingUsuarioId}`, payload);
        setShowUsuarioModal(false);
        setToast("Usuário atualizado.");
      } else {
        const res = await api.post(`/admin/empresas/${empresaId}/usuarios`, {
          ...payload,
          senha_temporaria: usuarioForm.senha_temporaria,
        });
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
      setToast(err?.response?.data?.detail || "Falha ao salvar usuário.");
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
    setSenhaExibida(null);
    setResetUsuario(usuario);
    setResetSenhaTemporaria(gerarSenhaTemporaria());
  };

  const resetarSenhaUsuario = async (e) => {
    e.preventDefault();
    if (!empresaId || !resetUsuario?.id) return;
    try {
      setSavingResetSenha(true);
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
      setToast(err?.response?.data?.detail || "Falha ao resetar senha.");
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

  if (loading) {
    return <div className="text-sm text-slate-500">Carregando dados da empresa...</div>;
  }

  if (!empresa) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-slate-600">
        Empresa não encontrada.
      </div>
    );
  }

  return (
    <div className="space-y-6 text-slate-950">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <Link
          to="/admin"
          className="inline-flex items-center gap-2 text-xs text-slate-500 transition-colors hover:text-slate-950"
        >
          <ArrowLeft size={14} />
          Voltar para Gestão de Clientes
        </Link>
        <div className="mt-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">{empresaNome}</h1>
            <p className="mt-1 text-sm text-slate-500">Gestão centralizada do tenant selecionado.</p>
          </div>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            Ativo
          </span>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-2 shadow-sm">
        <div className="flex flex-wrap gap-2">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
            <button
              key={tab.id}
              type="button"
              onClick={() => alterarTab(tab.id)}
              className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-all ${
                tabAtiva === tab.id
                  ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                  : "border-slate-200 bg-white text-slate-500 hover:text-slate-950"
              }`}
            >
              <Icon size={14} />
              {tab.label}
            </button>
            );
          })}
        </div>
      </div>

      {tabAtiva === "visao-geral" && (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div
              className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${
                overviewLoading
                  ? ""
                  : overview.whatsappOnline
                  ? "shadow-[0_0_24px_rgba(34,197,94,0.15)]"
                  : "shadow-[0_0_24px_rgba(239,68,68,0.16)]"
              }`}
            >
              <p className="text-[11px] uppercase tracking-wide text-slate-500">Conexão WhatsApp</p>
              {overviewLoading ? (
                <div className="mt-3 h-6 w-28 animate-pulse rounded bg-slate-200" />
              ) : (
                <div className="mt-3 flex items-center gap-2">
                  {overview.whatsappOnline ? (
                    <CheckCircle2 size={16} className="text-emerald-600" />
                  ) : (
                    <AlertTriangle size={16} className="text-amber-600" />
                  )}
                  <span className="text-sm font-medium text-slate-950">
                    {overview.whatsappStatusLabel}
                  </span>
                </div>
              )}
              <p className="mt-2 text-xs text-slate-500">{overview.whatsappProviderLabel}</p>
              {!overviewLoading && !overview.whatsappOnline ? (
                <button
                  type="button"
                  onClick={() => alterarTab("canais")}
                  className="mt-3 inline-flex items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-semibold text-red-700 transition-colors hover:bg-red-100"
                >
                  Reconectar
                </button>
              ) : null}
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-[11px] uppercase tracking-wide text-slate-500">Uso (simulado)</p>
              {overviewLoading ? (
                <div className="mt-3 h-6 w-24 animate-pulse rounded bg-slate-200" />
              ) : (
                <div className="mt-3 flex items-center gap-2">
                  <MessageSquare size={16} className="text-cyan-600" />
                  <span className="text-lg font-semibold text-slate-950">{overview.totalInteracoes}</span>
                </div>
              )}
              <p className="mt-2 text-xs text-slate-500">Interações acumuladas (histórico real do tenant).</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-[11px] uppercase tracking-wide text-slate-500">Acesso rápido</p>
              <button
                type="button"
                onClick={irParaTesteAgente}
                className="mt-3 inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-100"
              >
                Testar Agente
              </button>
              <p className="mt-2 text-xs text-slate-500">
                Abrirá com contexto da empresa para validação rápida do fluxo.
              </p>
            </div>
          </div>

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
              <button
                type="button"
                onClick={abrirNovoUsuario}
                className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-100"
              >
                <Plus size={14} />
                Novo usuário
              </button>
            </div>
          </div>

          {usuariosLoading ? (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white p-8 text-sm text-slate-500">
              <RefreshCw size={16} className="animate-spin" />
              Carregando usuários...
            </div>
          ) : usuarios.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
              <p className="text-sm font-semibold text-slate-900">Nenhum usuário vinculado a esta empresa.</p>
              <p className="mt-2 text-sm text-slate-500">Crie o primeiro admin do tenant pelo fluxo Super Admin.</p>
              <button
                type="button"
                onClick={abrirNovoUsuario}
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
              >
                <Plus size={14} />
                Criar usuário
              </button>
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Nome</th>
                      <th className="px-4 py-3 font-semibold">E-mail</th>
                      <th className="px-4 py-3 font-semibold">Role</th>
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
                            <button
                              type="button"
                              onClick={() => abrirEdicaoUsuario(usuario)}
                              className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 hover:text-slate-900"
                              title="Editar usuário"
                            >
                              <Pencil size={15} />
                            </button>
                            <button
                              type="button"
                              onClick={() => abrirResetSenha(usuario)}
                              className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-indigo-50 hover:text-indigo-700"
                              title="Resetar senha temporária"
                            >
                              <KeyRound size={15} />
                            </button>
                            <button
                              type="button"
                              onClick={() => alternarStatusUsuario(usuario)}
                              className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-amber-50 hover:text-amber-700"
                              title={usuario.ativo ? "Desativar usuário" : "Ativar usuário"}
                            >
                              {usuario.ativo ? <PowerOff size={15} /> : <Power size={15} />}
                            </button>
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
            <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
              Apenas Super Admin
            </span>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-slate-500">Nome do agente</label>
              <input
                value={iaConfig.nome_agente || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, nome_agente: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Mensagem de saudação</label>
              <textarea
                rows={6}
                value={iaConfig.mensagem_saudacao || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, mensagem_saudacao: e.target.value }))}
                className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button type="button" onClick={previewFunnel} disabled={funnelBusy} className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
                  <RefreshCw size={14} className={funnelBusy ? "animate-spin" : ""} /> Sincronizar funil e tags
                </button>
                {funnelStatus?.stale && <MiniBadge tone="warning">Funil desatualizado</MiniBadge>}
                {funnelStatus?.spec && !funnelStatus?.stale && <MiniBadge tone="active">Funil sincronizado · {funnelStatus.spec.version}</MiniBadge>}
              </div>
              {funnelPreview && (
                <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3 text-xs text-slate-600">
                  <div className="mb-2 font-semibold text-slate-700">Prévia: {funnelPreview.stage_count} etapas · {funnelPreview.new_stage_count} novas</div>
                  <div className="grid gap-1 md:grid-cols-2">{(funnelPreview.spec?.stages || []).map((stage) => <div key={stage.key}>#{stage.order} {stage.display_name} → {stage.meta_event}</div>)}</div>
                  <button type="button" onClick={syncFunnel} disabled={funnelBusy} className="mt-3 rounded-lg bg-indigo-600 px-3 py-2 font-semibold text-white disabled:opacity-50">Confirmar sincronização</button>
                </div>
              )}
            </div>
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs text-slate-500">Personalidade / tom / persona</label>
              <textarea
                rows={4}
                value={iaConfig.ia_personalidade || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, ia_personalidade: e.target.value }))}
                className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs text-slate-500">Regras obrigatórias / regras de negócio</label>
              <textarea
                rows={5}
                value={iaConfig.ia_regras_negocio || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, ia_regras_negocio: e.target.value }))}
                className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs text-slate-500">Monitor de conversão</label>
              <p className="mb-2 text-xs text-slate-400">
                Orienta a IA que acompanha a conversa e identifica avanços comerciais para aplicação das tags configuradas.
              </p>
              <textarea
                rows={6}
                value={iaConfig.conversion_monitor_prompt || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, conversion_monitor_prompt: e.target.value }))}
                placeholder="Descreva como interpretar avanço comercial, evidências, sinais negativos e o momento de aplicar tags."
                className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Modelo principal</label>
              <select
                value={iaConfig.modelo_ia || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, modelo_ia: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              >
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Limite de certeza</label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={iaConfig.limite_certeza}
                onChange={(e) => setIaConfig((p) => ({ ...p, limite_certeza: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Limite de dúvida</label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={iaConfig.limite_duvida}
                onChange={(e) => setIaConfig((p) => ({ ...p, limite_duvida: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Máx. agentes no desempate</label>
              <input
                type="number"
                min="1"
                value={iaConfig.max_agentes_desempate}
                onChange={(e) => setIaConfig((p) => ({ ...p, max_agentes_desempate: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Telefone de notificação</label>
              <input
                value={iaConfig.telefone_notificacao || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, telefone_notificacao: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs text-slate-500">Informações adicionais</label>
              <textarea
                rows={3}
                value={iaConfig.informacoes_adicionais || ""}
                onChange={(e) => setIaConfig((p) => ({ ...p, informacoes_adicionais: e.target.value }))}
                className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
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

          <section className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4" aria-labelledby="tags-ia-title">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 id="tags-ia-title" className="text-sm font-semibold text-slate-950">Tags disponíveis para a IA</h4>
                <p className="mt-1 max-w-3xl text-sm text-slate-600">
                  Selecione quais tags o atendente de IA poderá consultar e aplicar. Essa configuração não altera as conversões vinculadas às tags.
                </p>
              </div>
              <span className="rounded-full border border-indigo-200 bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700">
                {Array.isArray(iaConfig.tags_ia_habilitadas) ? iaConfig.tags_ia_habilitadas.length : 0} selecionada(s)
              </span>
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

          <button
            type="submit"
            disabled={savingIaConfig}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-700 disabled:opacity-60"
          >
            <Save size={14} />
            {savingIaConfig ? "Salvando..." : "Salvar configuração do agente"}
          </button>
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
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-slate-900 shadow-sm">
          <RAGManager empresaId={String(empresa.id)} />
        </div>
      )}

      {tabAtiva === "canais" && (
        <div className="space-y-4">
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
              <label className="mb-1 block text-xs text-slate-500">Nome</label>
              <input
                value={editEmpresa.nome_empresa}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, nome_empresa: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-500">Área de atuação</label>
              <input
                value={editEmpresa.area_atuacao}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, area_atuacao: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={savingEmpresa}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-700 disabled:opacity-60"
            >
              <Save size={14} />
              {savingEmpresa ? "Salvando..." : "Salvar dados"}
            </button>
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
              Opcional. Se vazio, o sistema usa fallback configurado no ambiente/plataforma.
            </p>
            <div>
              <label className="mb-1 block text-xs text-slate-500">OPENAI API Key</label>
              <input
                type="password"
                value={openaiApiKey}
                onChange={(e) => setOpenaiApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={savingCredenciais}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-700 disabled:opacity-60"
            >
              <Save size={14} />
              {savingCredenciais ? "Salvando..." : "Salvar credencial"}
            </button>
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
        <EmpresaPaymentManager empresaId={String(empresa.id)} onNotify={setToast} />
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
            <button
              type="button"
              onClick={irParaTesteAgente}
              className="mt-4 inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-100"
            >
              Testar Agente
            </button>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <MessageSquare size={16} />
              Indicadores
            </h3>
            <p className="mt-2 text-sm text-slate-500">Mensagens registradas: {overview.totalInteracoes}</p>
            <p className="mt-1 text-sm text-slate-500">WhatsApp: {overview.whatsappStatusLabel}</p>
          </div>
        </div>
      )}

      {toast ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg border border-slate-200 bg-white px-4 py-3 text-xs text-slate-700 shadow-lg">
          {toast}
        </div>
      ) : null}

      {senhaExibida ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-2 text-base font-semibold text-slate-950">
                  <KeyRound size={17} />
                  Senha temporária
                </h3>
                <p className="mt-2 text-sm text-amber-700">
                  A senha temporária será exibida apenas uma vez. Não há como consultar a senha atual depois.
                </p>
              </div>
              <button type="button" onClick={() => setSenhaExibida(null)} className="rounded-lg p-1 text-slate-500 hover:bg-slate-100">
                <X size={18} />
              </button>
            </div>
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <p className="text-xs text-slate-500">{senhaExibida.nome} · {senhaExibida.email}</p>
              <p className="mt-2 break-all font-mono text-sm font-semibold text-slate-950">{senhaExibida.senha}</p>
            </div>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={copiarSenhaExibida}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Copiar
              </button>
              <button
                type="button"
                onClick={() => setSenhaExibida(null)}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
              >
                Entendi
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showUsuarioModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
          <div className="w-full max-w-xl rounded-xl border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h3 className="text-base font-semibold text-slate-950">
                  {editingUsuarioId ? "Editar usuário" : "Novo usuário"}
                </h3>
                <p className="mt-1 text-xs text-slate-500">Fluxo restrito ao Super Admin.</p>
              </div>
              <button type="button" onClick={() => setShowUsuarioModal(false)} className="rounded-lg p-1 text-slate-500 hover:bg-slate-100">
                <X size={18} />
              </button>
            </div>
            <form onSubmit={salvarUsuario} className="space-y-4 p-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-slate-500">Nome</label>
                  <input required value={usuarioForm.nome} onChange={(e) => setUsuarioForm((p) => ({ ...p, nome: e.target.value }))} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500" />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-slate-500">E-mail</label>
                  <input required type="email" value={usuarioForm.email} onChange={(e) => setUsuarioForm((p) => ({ ...p, email: e.target.value }))} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500" />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-slate-500">Role</label>
                  <select value={usuarioForm.role} onChange={(e) => setUsuarioForm((p) => ({ ...p, role: e.target.value }))} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500">
                    {USUARIO_ROLES.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}
                  </select>
                </div>
                <label className="flex items-center gap-3 self-end rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
                  <input type="checkbox" checked={Boolean(usuarioForm.ativo)} onChange={(e) => setUsuarioForm((p) => ({ ...p, ativo: e.target.checked }))} />
                  Usuário ativo
                </label>
              </div>
              {!editingUsuarioId ? (
                <div>
                  <label className="mb-1 block text-xs text-slate-500">Senha temporária</label>
                  <div className="flex gap-2">
                    <input required minLength={8} value={usuarioForm.senha_temporaria} onChange={(e) => setUsuarioForm((p) => ({ ...p, senha_temporaria: e.target.value }))} className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-700 outline-none focus:border-indigo-500" />
                    <button type="button" onClick={() => setUsuarioForm((p) => ({ ...p, senha_temporaria: gerarSenhaTemporaria() }))} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Gerar</button>
                  </div>
                  <p className="mt-2 text-xs text-amber-700">A senha temporária será exibida apenas uma vez.</p>
                </div>
              ) : null}
              <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
                <button type="button" onClick={() => setShowUsuarioModal(false)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Cancelar</button>
                <button type="submit" disabled={savingUsuario} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-60">
                  <Save size={14} />
                  {savingUsuario ? "Salvando..." : "Salvar usuário"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {resetUsuario ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h3 className="text-base font-semibold text-slate-950">Resetar senha temporária</h3>
                <p className="mt-1 text-xs text-slate-500">{resetUsuario.nome} · {resetUsuario.email}</p>
              </div>
              <button type="button" onClick={() => setResetUsuario(null)} className="rounded-lg p-1 text-slate-500 hover:bg-slate-100"><X size={18} /></button>
            </div>
            <form onSubmit={resetarSenhaUsuario} className="space-y-4 p-5">
              <div>
                <label className="mb-1 block text-xs text-slate-500">Nova senha temporária</label>
                <div className="flex gap-2">
                  <input required minLength={8} value={resetSenhaTemporaria} onChange={(e) => setResetSenhaTemporaria(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-700 outline-none focus:border-indigo-500" />
                  <button type="button" onClick={() => setResetSenhaTemporaria(gerarSenhaTemporaria())} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Gerar</button>
                </div>
                <p className="mt-2 text-xs text-amber-700">A senha temporária será exibida apenas uma vez.</p>
              </div>
              <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
                <button type="button" onClick={() => setResetUsuario(null)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Cancelar</button>
                <button type="submit" disabled={savingResetSenha} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-60">
                  <KeyRound size={14} />
                  {savingResetSenha ? "Resetando..." : "Resetar senha"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

    </div>
  );
}
