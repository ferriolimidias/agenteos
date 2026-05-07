import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BrainCircuit,
  Building2,
  CheckCircle2,
  Link2,
  MessageSquare,
  Save,
  Settings2,
  Sparkles,
  Users,
} from "lucide-react";

import api from "../../services/api";
import Orquestrador from "./Orquestrador";
import EmpresaConexoesManager from "../../components/EmpresaConexoesManager";

const TABS = [
  { id: "visao-geral", label: "Visão Geral", icon: Building2 },
  { id: "agentes", label: "Agentes", icon: BrainCircuit },
  { id: "configuracoes", label: "Configurações", icon: Settings2 },
  { id: "conexoes", label: "Conexões", icon: Link2 },
];

export default function EmpresaDetalhes() {
  const navigate = useNavigate();
  const { empresaId } = useParams();
  const [empresa, setEmpresa] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tabAtiva, setTabAtiva] = useState("visao-geral");
  const [savingEmpresa, setSavingEmpresa] = useState(false);
  const [savingCredenciais, setSavingCredenciais] = useState(false);
  const [toast, setToast] = useState("");
  const [editEmpresa, setEditEmpresa] = useState({ nome_empresa: "", area_atuacao: "" });
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overview, setOverview] = useState({
    whatsappStatus: "DISCONNECTED",
    whatsappOnline: false,
    totalEspecialistas: 0,
    especialistaPrincipal: "",
    totalInteracoes: 0,
  });

  const empresaNome = useMemo(
    () => String(empresa?.nome_empresa || "Empresa"),
    [empresa?.nome_empresa],
  );

  const carregarEmpresa = async () => {
    if (!empresaId) return;
    setLoading(true);
    try {
      const res = await api.get("/empresas/");
      const atual = (res.data || []).find((e) => String(e.id) === String(empresaId)) || null;
      setEmpresa(atual);
      setEditEmpresa({
        nome_empresa: String(atual?.nome_empresa || ""),
        area_atuacao: String(atual?.area_atuacao || ""),
      });
      setOpenaiApiKey(String(atual?.credenciais_canais?.openai_api_key || ""));
    } catch (err) {
      console.error("Erro ao carregar empresa:", err);
      setEmpresa(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    carregarEmpresa();
  }, [empresaId]);

  useEffect(() => {
    const carregarOverview = async () => {
      if (!empresaId) return;
      setOverviewLoading(true);
      try {
        const [resConexoes, resEspecialistas, resDashboard] = await Promise.all([
          api.get(`/empresas/${empresaId}/conexoes/`),
          api.get(`/admin/orquestrador/empresas/${empresaId}/especialistas`),
          api.get(`/empresas/${empresaId}/dashboard/stats`).catch(() => null),
        ]);

        const conexoes = Array.isArray(resConexoes.data) ? resConexoes.data : [];
        const especialistas = Array.isArray(resEspecialistas.data) ? resEspecialistas.data : [];
        const conexaoEvolution = conexoes.find((c) => String(c.tipo).toLowerCase() === "evolution");
        let whatsappStatus = "DISCONNECTED";
        let whatsappOnline = false;

        if (conexaoEvolution?.id) {
          try {
            const resStatus = await api.get(`/conexoes/status/${conexaoEvolution.id}`);
            whatsappStatus = String(resStatus.data?.status || "disconnected").toUpperCase();
            whatsappOnline = Boolean(resStatus.data?.online) || whatsappStatus === "OPEN" || whatsappStatus === "CONNECTED";
          } catch (err) {
            console.error("Erro ao carregar status da Evolution:", err);
          }
        }

        const principal = String(especialistas?.[0]?.nome || "").trim();
        const totalInteracoesReais = Number(resDashboard?.data?.total_mensagens);
        setOverview({
          whatsappStatus,
          whatsappOnline,
          totalEspecialistas: especialistas.length,
          especialistaPrincipal: principal,
          totalInteracoes: Number.isFinite(totalInteracoesReais) ? totalInteracoesReais : 0,
        });
      } catch (err) {
        console.error("Erro ao carregar visão geral:", err);
        setOverview((prev) => ({ ...prev, totalInteracoes: 0, totalEspecialistas: 0, especialistaPrincipal: "" }));
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

  const irParaTesteAgente = () => {
    localStorage.setItem("impersonated_empresa_id", String(empresaId));
    const especialista = String(overview.especialistaPrincipal || "").trim();
    const params = new URLSearchParams();
    params.set("empresaId", String(empresaId));
    if (especialista) params.set("especialista", especialista);
    navigate(`/painel/simulador?${params.toString()}`);
  };

  if (loading) {
    return <div className="text-sm text-gray-400">Carregando dados da empresa...</div>;
  }

  if (!empresa) {
    return (
      <div className="rounded-xl border border-[#2d2d2d] bg-[#121212] p-6 text-gray-300">
        Empresa não encontrada.
      </div>
    );
  }

  return (
    <div className="space-y-6 text-gray-100">
      <div className="rounded-xl border border-[#2d2d2d] bg-[#131313] p-5 shadow-sm">
        <Link
          to="/admin"
          className="inline-flex items-center gap-2 text-xs text-gray-400 transition-colors hover:text-white"
        >
          <ArrowLeft size={14} />
          Voltar para Gestão de Clientes
        </Link>
        <div className="mt-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-white">{empresaNome}</h1>
            <p className="mt-1 text-sm text-gray-400">Gestão centralizada do tenant selecionado.</p>
          </div>
          <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
            Ativo
          </span>
        </div>
      </div>

      <div className="rounded-xl border border-[#2d2d2d] bg-[#121212] p-2 shadow-sm">
        <div className="flex flex-wrap gap-2">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTabAtiva(id)}
              className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-all ${
                tabAtiva === id
                  ? "border-indigo-500/50 bg-indigo-600/20 text-indigo-200"
                  : "border-[#2d2d2d] bg-[#171717] text-gray-400 hover:text-gray-100"
              }`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {tabAtiva === "visao-geral" && (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div
              className={`rounded-xl border border-[#2d2d2d] bg-[#1a1a1b] p-4 shadow-sm ${
                overviewLoading
                  ? ""
                  : overview.whatsappOnline
                  ? "shadow-[0_0_24px_rgba(34,197,94,0.15)]"
                  : "shadow-[0_0_24px_rgba(239,68,68,0.16)]"
              }`}
            >
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Conexão WhatsApp</p>
              {overviewLoading ? (
                <div className="mt-3 h-6 w-28 animate-pulse rounded bg-[#242426]" />
              ) : (
                <div className="mt-3 flex items-center gap-2">
                  {overview.whatsappOnline ? (
                    <CheckCircle2 size={16} className="text-emerald-400" />
                  ) : (
                    <AlertTriangle size={16} className="text-amber-400" />
                  )}
                  <span className="text-sm font-medium text-gray-100">
                    {overview.whatsappOnline ? "CONNECTED" : overview.whatsappStatus}
                  </span>
                </div>
              )}
              <p className="mt-2 text-xs text-gray-500">Estado atual da instância Evolution deste tenant.</p>
              {!overviewLoading && !overview.whatsappOnline ? (
                <button
                  type="button"
                  onClick={() => setTabAtiva("conexoes")}
                  className="mt-3 inline-flex items-center gap-1 rounded-md border border-red-500/40 bg-red-600/10 px-2.5 py-1 text-[11px] font-semibold text-red-300 transition-colors hover:bg-red-600/20"
                >
                  Reconectar
                </button>
              ) : null}
            </div>

            <div className="rounded-xl border border-[#2d2d2d] bg-[#1a1a1b] p-4 shadow-sm">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Especialistas</p>
              {overviewLoading ? (
                <div className="mt-3 h-6 w-16 animate-pulse rounded bg-[#242426]" />
              ) : (
                <div className="mt-3 flex items-center gap-2">
                  <Users size={16} className="text-indigo-300" />
                  <span className="text-lg font-semibold text-white">{overview.totalEspecialistas}</span>
                </div>
              )}
              <p className="mt-2 text-xs text-gray-500">Total de agentes especialistas configurados.</p>
            </div>

            <div className="rounded-xl border border-[#2d2d2d] bg-[#1a1a1b] p-4 shadow-sm">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Uso (simulado)</p>
              {overviewLoading ? (
                <div className="mt-3 h-6 w-24 animate-pulse rounded bg-[#242426]" />
              ) : (
                <div className="mt-3 flex items-center gap-2">
                  <MessageSquare size={16} className="text-cyan-300" />
                  <span className="text-lg font-semibold text-white">{overview.totalInteracoes}</span>
                </div>
              )}
              <p className="mt-2 text-xs text-gray-500">Interações acumuladas (histórico real do tenant).</p>
            </div>

            <div className="rounded-xl border border-[#2d2d2d] bg-[#1a1a1b] p-4 shadow-sm">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Acesso rápido</p>
              <button
                type="button"
                onClick={irParaTesteAgente}
                className="mt-3 inline-flex items-center gap-2 rounded-lg border border-indigo-500/30 bg-indigo-600/15 px-3 py-2 text-xs font-semibold text-indigo-200 transition-colors hover:bg-indigo-600/25"
              >
                Testar Agente
              </button>
              <p className="mt-2 text-xs text-gray-500">
                {overview.especialistaPrincipal
                  ? `Abrirá com contexto da empresa e especialista principal: ${overview.especialistaPrincipal}.`
                  : "Abrirá com contexto da empresa para validação rápida do fluxo."}
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-[#2d2d2d] bg-[#131313] p-4">
              <p className="text-xs uppercase tracking-wide text-gray-500">Nome</p>
              <p className="mt-2 text-sm text-gray-200">{empresa.nome_empresa}</p>
            </div>
            <div className="rounded-xl border border-[#2d2d2d] bg-[#131313] p-4">
              <p className="text-xs uppercase tracking-wide text-gray-500">Tenant ID</p>
              <p className="mt-2 break-all text-xs font-mono text-gray-300">{empresa.id}</p>
            </div>
          </div>
        </div>
      )}

      {tabAtiva === "agentes" && <Orquestrador empresaId={String(empresa.id)} embedded />}

      {tabAtiva === "configuracoes" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <form
            onSubmit={salvarEmpresa}
            className="space-y-4 rounded-xl border border-[#2d2d2d] bg-[#131313] p-5 shadow-sm"
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
              <Settings2 size={16} />
              Dados da Empresa
            </h3>
            <div>
              <label className="mb-1 block text-xs text-gray-400">Nome</label>
              <input
                value={editEmpresa.nome_empresa}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, nome_empresa: e.target.value }))}
                className="w-full rounded-lg border border-[#2d2d2d] bg-[#181818] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">Área de atuação</label>
              <input
                value={editEmpresa.area_atuacao}
                onChange={(e) => setEditEmpresa((p) => ({ ...p, area_atuacao: e.target.value }))}
                className="w-full rounded-lg border border-[#2d2d2d] bg-[#181818] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={savingEmpresa}
              className="inline-flex items-center gap-2 rounded-lg border border-[#2d2d2d] bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-700 disabled:opacity-60"
            >
              <Save size={14} />
              {savingEmpresa ? "Salvando..." : "Salvar dados"}
            </button>
          </form>

          <form
            onSubmit={salvarOpenAI}
            className="space-y-4 rounded-xl border border-[#2d2d2d] bg-[#131313] p-5 shadow-sm"
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
              <Sparkles size={16} />
              Credencial OpenAI por Tenant
            </h3>
            <p className="text-xs text-gray-500">
              Opcional. Se vazio, o sistema usa fallback configurado no ambiente/plataforma.
            </p>
            <div>
              <label className="mb-1 block text-xs text-gray-400">OPENAI API Key</label>
              <input
                type="password"
                value={openaiApiKey}
                onChange={(e) => setOpenaiApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full rounded-lg border border-[#2d2d2d] bg-[#181818] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={savingCredenciais}
              className="inline-flex items-center gap-2 rounded-lg border border-[#2d2d2d] bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-700 disabled:opacity-60"
            >
              <Save size={14} />
              {savingCredenciais ? "Salvando..." : "Salvar credencial"}
            </button>
          </form>
        </div>
      )}

      {tabAtiva === "conexoes" && (
        <div className="rounded-xl border border-[#2d2d2d] bg-[#131313] p-4 shadow-sm">
          <EmpresaConexoesManager
            empresaId={empresa.id}
            empresaNome={empresa.nome_empresa}
            conexaoDisparoId={empresa.conexao_disparo_id}
            disparoDelayMin={empresa.disparo_delay_min}
            disparoDelayMax={empresa.disparo_delay_max}
            onConexaoDisparoUpdated={carregarEmpresa}
          />
        </div>
      )}

      {toast ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg border border-[#2d2d2d] bg-[#151515] px-4 py-3 text-xs text-gray-200 shadow-lg">
          {toast}
        </div>
      ) : null}
    </div>
  );
}
