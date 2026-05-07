import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  MessageSquare,
  Save,
  UserPlus,
  WalletCards,
} from "lucide-react";
import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

function formatDayLabel(dateObj) {
  return dateObj.toLocaleDateString("pt-BR", { weekday: "short" });
}

function formatDateTime(dateStr) {
  if (!dateStr) return "-";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function resolveAgentName(lead) {
  const possible = [
    lead?.agente_responsavel,
    lead?.especialista_nome,
    lead?.especialista_atual,
    lead?.agente_nome,
    lead?.dados_adicionais?.agente_responsavel,
    lead?.dados_adicionais?.especialista_nome,
  ];
  const selected = possible.find((v) => String(v || "").trim());
  return selected ? String(selected).trim() : "IA Maestro";
}

function buildLast7DaysSeries() {
  const base = startOfToday();
  const items = [];
  for (let i = 6; i >= 0; i -= 1) {
    const d = new Date(base);
    d.setDate(base.getDate() - i);
    const iso = d.toISOString().slice(0, 10);
    items.push({
      date: iso,
      label: formatDayLabel(d),
      mensagens: 0,
    });
  }
  return items;
}

function CardSkeleton() {
  return (
    <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6 animate-pulse">
      <div className="h-4 w-28 rounded bg-[#2a2a2c]" />
      <div className="mt-4 h-8 w-20 rounded bg-[#2a2a2c]" />
      <div className="mt-3 h-3 w-36 rounded bg-[#2a2a2c]" />
    </div>
  );
}

export default function TenantDashboard() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState({
    roboOnline: false,
    roboStatus: "OFFLINE",
    leads24h: 0,
    mensagensMes: 0,
  });
  const [activitySeries, setActivitySeries] = useState(buildLast7DaysSeries());
  const [recentLeads, setRecentLeads] = useState([]);
  const [aiStatus, setAiStatus] = useState("ok");
  const [telefoneNotificacao, setTelefoneNotificacao] = useState("");
  const [savingPhone, setSavingPhone] = useState(false);
  const initialLoadDoneRef = useRef(false);

  useEffect(() => {
    let mounted = true;

    const carregarDashboard = async ({ silent = false } = {}) => {
      if (!user || !empresaId) {
        if (mounted) setLoading(false);
        return;
      }

      if (!silent && !initialLoadDoneRef.current && mounted) {
        setLoading(true);
      }
      try {
        const [resDashboard, resConexoes, resCrm, resInbox, resIaConfig] = await Promise.all([
          api.get(`/empresas/${empresaId}/dashboard/stats`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/conexoes/`).catch(() => ({ data: [] })),
          api.get(`/empresas/${empresaId}/crm`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/inbox`).catch(() => ({ data: [] })),
          api.get(`/empresas/${empresaId}/ia-config`).catch(() => ({ data: {} })),
        ]);

        const conexoes = Array.isArray(resConexoes.data) ? resConexoes.data : [];
        const conexaoEvolution = conexoes.find(
          (c) => String(c.tipo || "").toLowerCase() === "evolution",
        );

        let roboStatus = "OFFLINE";
        let roboOnline = false;
        if (conexaoEvolution?.id) {
          try {
            const statusRes = await api.get(`/conexoes/status/${conexaoEvolution.id}`);
            roboStatus = String(statusRes?.data?.status || "DISCONNECTED").toUpperCase();
            roboOnline = Boolean(statusRes?.data?.online) || ["OPEN", "CONNECTED"].includes(roboStatus);
          } catch {
            roboStatus = "OFFLINE";
            roboOnline = false;
          }
        }

        const etapas = Array.isArray(resCrm?.data?.etapas) ? resCrm.data.etapas : [];
        const allLeads = etapas.flatMap((etapa) =>
          Array.isArray(etapa?.leads)
            ? etapa.leads.map((lead) => ({
                ...lead,
                etapa_nome: etapa?.nome || "",
              }))
            : [],
        );

        const dayAgo = new Date();
        dayAgo.setHours(dayAgo.getHours() - 24);
        const leads24h = allLeads.filter((lead) => {
          const d = new Date(lead?.criado_em);
          return !Number.isNaN(d.getTime()) && d >= dayAgo;
        }).length;

        const recent = allLeads
          .filter((lead) => lead?.criado_em)
          .sort((a, b) => new Date(b.criado_em) - new Date(a.criado_em))
          .slice(0, 5)
          .map((lead) => ({
            id: String(lead.id || ""),
            nome: String(lead.nome_contato || "Contato sem nome"),
            data: lead.criado_em,
            agente: resolveAgentName(lead),
          }));

        const inboxLeads = Array.isArray(resInbox.data) ? resInbox.data : [];
        const leadsParaHistorico = inboxLeads.slice(0, 20).filter((lead) => lead?.telefone_contato);
        const historicos = await Promise.all(
          leadsParaHistorico.map((lead) =>
            api
              .get(`/empresas/${empresaId}/inbox/${lead.telefone_contato}`)
              .then((r) => (Array.isArray(r.data) ? r.data : []))
              .catch(() => []),
          ),
        );

        const baseSeries = buildLast7DaysSeries();
        const today = new Date();
        const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);

        let mensagensMes = 0;
        for (const lista of historicos) {
          for (const msg of lista) {
            const d = new Date(msg?.criado_em);
            if (Number.isNaN(d.getTime())) continue;

            if (d >= monthStart) {
              mensagensMes += 1;
            }

            const dayKey = d.toISOString().slice(0, 10);
            const target = baseSeries.find((item) => item.date === dayKey);
            if (target) target.mensagens += 1;
          }
        }

        if (mensagensMes === 0) {
          mensagensMes = Number(resDashboard?.data?.total_mensagens || 0);
        }

        if (!mounted) return;
        setMetrics({
          roboOnline,
          roboStatus,
          leads24h,
          mensagensMes,
        });
        setAiStatus(String(resIaConfig?.data?.status_openai || "ok").toLowerCase());
        setTelefoneNotificacao(String(resIaConfig?.data?.telefone_notificacao || ""));
        setRecentLeads(recent);
        setActivitySeries(baseSeries);
      } catch (err) {
        console.error("Erro ao carregar dashboard do tenant:", err);
        if (!mounted) return;
        setMetrics({
          roboOnline: false,
          roboStatus: "OFFLINE",
          leads24h: 0,
          mensagensMes: 0,
        });
        setRecentLeads([]);
        setActivitySeries(buildLast7DaysSeries());
      } finally {
        if (!silent && mounted) {
          setLoading(false);
          initialLoadDoneRef.current = true;
        }
      }
    };

    carregarDashboard({ silent: false });
    const intervalId = window.setInterval(() => {
      carregarDashboard({ silent: true });
    }, 30000);

    return () => {
      mounted = false;
      window.clearInterval(intervalId);
    };
  }, [empresaId, user]);

  const robotBadgeClass = useMemo(
    () =>
      metrics.roboOnline
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 shadow-[0_0_24px_rgba(16,185,129,0.22)]"
        : "border-red-500/40 bg-red-500/10 text-red-300 shadow-[0_0_24px_rgba(239,68,68,0.22)]",
    [metrics.roboOnline],
  );

  const aiConnection = useMemo(() => {
    if (aiStatus === "sem_credito") {
      return {
        label: "Sem saldo",
        helper: "Creditos OpenAI esgotados",
        className: "border-red-500/40 bg-red-500/10 text-red-300 shadow-[0_0_24px_rgba(239,68,68,0.22)]",
        icon: AlertTriangle,
      };
    }
    if (aiStatus === "chave_invalida") {
      return {
        label: "Erro de autenticacao",
        helper: "Chave invalida ou nao configurada",
        className: "border-amber-500/40 bg-amber-500/10 text-amber-300 shadow-[0_0_24px_rgba(245,158,11,0.22)]",
        icon: WalletCards,
      };
    }
    return {
      label: "Operacional",
      helper: "Chave configurada e ativa",
      className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 shadow-[0_0_24px_rgba(16,185,129,0.22)]",
      icon: CheckCircle2,
    };
  }, [aiStatus]);
  const AiConnectionIcon = aiConnection.icon;

  const salvarTelefoneNotificacao = async () => {
    if (!empresaId) return;
    setSavingPhone(true);
    try {
      await api.put(`/empresas/${empresaId}/ia-config`, {
        telefone_notificacao: telefoneNotificacao,
      });
    } catch (err) {
      console.error("Erro ao salvar telefone de notificacao:", err);
    } finally {
      setSavingPhone(false);
    }
  };

  if (!user) {
    return (
      <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6 text-sm text-gray-300">
        Faça login novamente para acessar a dashboard.
      </div>
    );
  }

  return (
    <div className="space-y-6 text-gray-100">
      <div>
        <h1 className="text-2xl font-semibold text-white">Dashboard do Cliente</h1>
        <p className="mt-1 text-sm font-medium text-gray-400">
          Visão rápida da operação do seu tenant em tempo real.
        </p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
            <div className="flex items-start justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">Status do Robô</p>
              <Bot size={18} className="text-gray-300" />
            </div>
            <div className="mt-4">
              <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${robotBadgeClass}`}>
                {metrics.roboOnline ? "Online" : "Offline"} ({metrics.roboStatus})
              </span>
            </div>
          </div>

          <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
            <div className="flex items-start justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">Leads Capturados (24h)</p>
              <UserPlus size={18} className="text-cyan-300" />
            </div>
            <p className="mt-4 text-3xl font-semibold text-white">{metrics.leads24h}</p>
          </div>

          <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
            <div className="flex items-start justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">Mensagens Trocadas (mês)</p>
              <MessageSquare size={18} className="text-indigo-300" />
            </div>
            <p className="mt-4 text-3xl font-semibold text-white">{metrics.mensagensMes}</p>
          </div>

          <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
            <div className="flex items-start justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">Conexao com IA</p>
              <AiConnectionIcon size={18} className="text-gray-300" />
            </div>
            <div className="mt-4">
              <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${aiConnection.className}`}>
                {aiConnection.label}
              </span>
              <p className="mt-2 text-xs text-gray-400">{aiConnection.helper}</p>
            </div>
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-gray-100">Telefone para alertas de credito</h2>
          <p className="mt-1 text-xs text-gray-400">
            Recebe aviso no WhatsApp quando a IA detectar falta de saldo da OpenAI.
          </p>
        </div>
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <input
            value={telefoneNotificacao}
            onChange={(e) => setTelefoneNotificacao(e.target.value)}
            placeholder="Ex: 5511999999999"
            className="w-full rounded-xl border border-[#2d2d2d] bg-[#101012] px-4 py-2.5 text-sm text-gray-100 outline-none focus:border-indigo-500"
          />
          <button
            type="button"
            onClick={salvarTelefoneNotificacao}
            disabled={savingPhone}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-indigo-500/40 bg-indigo-500/15 px-4 py-2.5 text-sm font-semibold text-indigo-200 transition hover:bg-indigo-500/25 disabled:opacity-60"
          >
            <Save size={16} />
            {savingPhone ? "Salvando..." : "Salvar"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6 xl:col-span-2">
          <div className="mb-4 flex items-center gap-2">
            <Activity size={18} className="text-indigo-300" />
            <h2 className="text-sm font-semibold text-gray-100">Atividade de Mensagens (7 dias)</h2>
          </div>
          <div className="h-64">
            {loading ? (
              <div className="h-full animate-pulse rounded bg-[#222224]" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={activitySeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2d2d2d" />
                  <XAxis dataKey="label" tick={{ fill: "#a3a3a3", fontSize: 12 }} axisLine={{ stroke: "#2d2d2d" }} />
                  <YAxis allowDecimals={false} tick={{ fill: "#a3a3a3", fontSize: 12 }} axisLine={{ stroke: "#2d2d2d" }} />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid #2d2d2d",
                      backgroundColor: "#141416",
                      color: "#e5e7eb",
                    }}
                    labelStyle={{ color: "#e5e7eb" }}
                  />
                  <Line type="monotone" dataKey="mensagens" stroke="#818cf8" strokeWidth={3} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
          <h2 className="text-sm font-semibold text-gray-100">Atividade Recente</h2>
          <div className="mt-4 overflow-hidden rounded-xl border border-[#2d2d2d]">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#141416] text-gray-400">
                <tr>
                  <th className="px-3 py-2 font-medium">Nome</th>
                  <th className="px-3 py-2 font-medium">Data</th>
                  <th className="px-3 py-2 font-medium">Agente</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  [...Array(5)].map((_, idx) => (
                    <tr key={`sk-${idx}`} className="border-t border-[#2d2d2d]">
                      <td className="px-3 py-3">
                        <div className="h-3 w-16 animate-pulse rounded bg-[#2a2a2c]" />
                      </td>
                      <td className="px-3 py-3">
                        <div className="h-3 w-14 animate-pulse rounded bg-[#2a2a2c]" />
                      </td>
                      <td className="px-3 py-3">
                        <div className="h-3 w-20 animate-pulse rounded bg-[#2a2a2c]" />
                      </td>
                    </tr>
                  ))
                ) : recentLeads.length > 0 ? (
                  recentLeads.map((lead) => (
                    <tr key={lead.id} className="border-t border-[#2d2d2d] text-gray-200">
                      <td className="px-3 py-2.5 font-medium">{lead.nome}</td>
                      <td className="px-3 py-2.5">{formatDateTime(lead.data)}</td>
                      <td className="px-3 py-2.5">{lead.agente}</td>
                    </tr>
                  ))
                ) : (
                  <tr className="border-t border-[#2d2d2d]">
                    <td colSpan={3} className="px-3 py-6 text-center text-gray-500">
                      Sem atividade recente.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
