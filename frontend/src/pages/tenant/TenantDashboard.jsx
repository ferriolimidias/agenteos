import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  KeyRound,
  MessageSquare,
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
import { EmptyState, MetricCard, PageHeader, SectionCard, StatusBadge } from "../../components/ui";
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
    lead?.agente_nome,
    lead?.dados_adicionais?.agente_responsavel,
  ];
  const selected = possible.find((v) => String(v || "").trim());
  return selected ? String(selected).trim() : "AtendimentoAgent";
}

function formatProviderLabel(provider, fallback) {
  const normalized = String(provider || "").toLowerCase();
  if (normalized === "meta_whatsapp" || normalized === "meta" || normalized === "meta_official") {
    return "API Oficial da Meta";
  }
  if (normalized === "evolution" || normalized === "evolution_legacy") {
    return "Evolution - legado";
  }
  return fallback || "Sem provider definido";
}

function formatChannelStatusLabel(status, connected) {
  const normalized = String(status || "").toLowerCase();
  if (connected || normalized === "connected") return "Conectado";
  if (normalized === "pending") return "Pendente";
  if (normalized === "degraded") return "Atenção";
  if (normalized === "error") return "Erro";
  if (normalized === "not_configured") return "Não configurado";
  return "Desconectado";
}

function channelTone(status, connected) {
  const normalized = String(status || "").toLowerCase();
  if (connected || normalized === "connected") return "active";
  if (normalized === "pending" || normalized === "degraded" || normalized === "not_configured") return "warning";
  return "danger";
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
    <div className="animate-pulse rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="h-4 w-28 rounded bg-slate-200" />
      <div className="mt-4 h-8 w-20 rounded bg-slate-200" />
      <div className="mt-3 h-3 w-36 rounded bg-slate-200" />
    </div>
  );
}

export default function TenantDashboard() {
  const user = getStoredUser();
  const hasUser = Boolean(user);
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState({
    whatsappConnected: false,
    whatsappStatus: "not_configured",
    whatsappStatusLabel: "Não configurado",
    whatsappProviderLabel: "Sem conexão configurada",
    whatsappHelper: "Nenhuma conexão WhatsApp operacional encontrada.",
    agentStatusLabel: "Ativo",
    agentStatusTone: "active",
    leads24h: 0,
    mensagensMes: 0,
  });
  const [activitySeries, setActivitySeries] = useState(buildLast7DaysSeries());
  const [recentLeads, setRecentLeads] = useState([]);
  const [aiStatus, setAiStatus] = useState("ok");
  const [aiConfigurada, setAiConfigurada] = useState(false);
  const [telefoneNotificacao, setTelefoneNotificacao] = useState("");
  const initialLoadDoneRef = useRef(false);

  useEffect(() => {
    let mounted = true;

    const carregarDashboard = async ({ silent = false } = {}) => {
      if (!hasUser || !empresaId) {
        if (mounted) setLoading(false);
        return;
      }

      if (!silent && !initialLoadDoneRef.current && mounted) {
        setLoading(true);
      }
      try {
        const [resDashboard, resWhatsapp, resCrm, resInbox, resIaConfig] = await Promise.all([
          api.get(`/empresas/${empresaId}/dashboard/stats`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/whatsapp/status`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/crm`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/inbox`).catch(() => ({ data: [] })),
          api.get(`/empresas/${empresaId}/ia-config`).catch(() => ({ data: {} })),
        ]);

        const whatsappStatus = String(resWhatsapp?.data?.status || "not_configured").toLowerCase();
        const whatsappConnected = Boolean(resWhatsapp?.data?.conectado) || Boolean(resWhatsapp?.data?.online);
        const whatsappStatusLabel = formatChannelStatusLabel(whatsappStatus, whatsappConnected);
        const whatsappProviderLabel = formatProviderLabel(
          resWhatsapp?.data?.provider,
          resWhatsapp?.data?.provider_label,
        );
        const displayNumber = String(resWhatsapp?.data?.display_phone_number || resWhatsapp?.data?.display_number || "").trim();
        const coexistence = Boolean(resWhatsapp?.data?.coexistence);
        const whatsappHelper = [
          whatsappProviderLabel,
          displayNumber ? `Numero ${displayNumber}` : "",
          coexistence ? "WhatsApp Business no celular ativo" : "",
        ].filter(Boolean).join(" · ");

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
          whatsappConnected,
          whatsappStatus,
          whatsappStatusLabel,
          whatsappProviderLabel,
          whatsappHelper: whatsappHelper || "Canal WhatsApp sem detalhes operacionais.",
          agentStatusLabel: "Ativo",
          agentStatusTone: "active",
          leads24h,
          mensagensMes,
        });
        setAiStatus(String(resIaConfig?.data?.status_openai || "ok").toLowerCase());
        setAiConfigurada(Boolean(resIaConfig?.data?.openai_configurada));
        setTelefoneNotificacao(String(resIaConfig?.data?.telefone_notificacao || ""));
        setRecentLeads(recent);
        setActivitySeries(baseSeries);
      } catch (err) {
        console.error("Erro ao carregar dashboard do tenant:", err);
        if (!mounted) return;
        setMetrics({
          whatsappConnected: false,
          whatsappStatus: "error",
          whatsappStatusLabel: "Erro",
          whatsappProviderLabel: "Sem provider definido",
          whatsappHelper: "Não foi possível carregar o estado do canal.",
          agentStatusLabel: "Indisponível",
          agentStatusTone: "warning",
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
  }, [empresaId, hasUser]);

  const aiConnection = useMemo(() => {
    // Regra: a ausência de chave configurada tem prioridade sobre o status reativo,
    // que só pode ser confiável quando há chave para verificar.
    if (!aiConfigurada) {
      return {
        label: "Não Configurada",
        helper: "Solicite ativação ou revisão para a equipe AgenteOS.",
        className:
          "border-amber-500/40 bg-amber-500/10 text-amber-300 shadow-[0_0_24px_rgba(245,158,11,0.22)]",
        icon: KeyRound,
      };
    }
    if (aiStatus === "sem_credito") {
      return {
        label: "Sem saldo",
        helper: "Creditos OpenAI esgotados",
        className:
          "border-red-500/40 bg-red-500/10 text-red-300 shadow-[0_0_24px_rgba(239,68,68,0.22)]",
        icon: AlertTriangle,
      };
    }
    if (aiStatus === "chave_invalida") {
      return {
        label: "Erro de autenticação",
        helper: "Chave rejeitada. Solicite revisão para a equipe AgenteOS.",
        className:
          "border-red-500/40 bg-red-500/10 text-red-300 shadow-[0_0_24px_rgba(239,68,68,0.22)]",
        icon: WalletCards,
      };
    }
    return {
      label: "Operacional",
      helper: "Chave configurada e ativa",
      className:
        "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 shadow-[0_0_24px_rgba(16,185,129,0.22)]",
      icon: CheckCircle2,
    };
  }, [aiStatus, aiConfigurada]);
  const AiConnectionIcon = aiConnection.icon;

  if (!user) {
    return (
      <EmptyState title="Sessão expirada" description="Faça login novamente para acessar o dashboard." />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Visão rápida da operação do tenant: atendimento, leads, mensagens e saúde da IA."
        icon={Activity}
        eyebrow="Painel cliente"
      />

      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
          <MetricCard
            label="Canal WhatsApp"
            value={
              <StatusBadge tone={channelTone(metrics.whatsappStatus, metrics.whatsappConnected)}>
                {metrics.whatsappStatusLabel}
              </StatusBadge>
            }
            helper={metrics.whatsappHelper}
            icon={MessageSquare}
            tone={channelTone(metrics.whatsappStatus, metrics.whatsappConnected)}
          />
          <MetricCard
            label="Agente de atendimento"
            value={<StatusBadge tone={metrics.agentStatusTone}>{metrics.agentStatusLabel}</StatusBadge>}
            helper="Estado da automação de atendimento, separado do canal e da IA."
            icon={Bot}
            tone={metrics.agentStatusTone}
          />
          <MetricCard label="Leads capturados (24h)" value={metrics.leads24h} icon={UserPlus} tone="active" />
          <MetricCard label="Mensagens trocadas (mês)" value={metrics.mensagensMes} icon={MessageSquare} tone="info" />
          <MetricCard
            label="Conexão com IA"
            value={<StatusBadge tone={!aiConfigurada || aiStatus !== "ok" ? "warning" : "active"}>{aiConnection.label}</StatusBadge>}
            helper={aiConnection.helper}
            icon={AiConnectionIcon}
            tone={!aiConfigurada || aiStatus !== "ok" ? "warning" : "active"}
          />
        </div>
      )}

      <SectionCard title="Alertas operacionais" description="Notificações de saldo e disponibilidade da IA." icon={KeyRound}>
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-slate-900">Telefone para alertas de crédito</h2>
          <p className="mt-1 text-sm text-slate-500">
            Canal cadastrado pela equipe AgenteOS para avisos operacionais do agente.
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          {telefoneNotificacao ? telefoneNotificacao : "Nenhum telefone de alerta cadastrado."}
        </div>
        <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-800">
          Configurações de IA, chaves, modelos e alertas técnicos são gerenciados pelo Super Admin.
        </div>
      </SectionCard>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <SectionCard title="Atividade de mensagens (7 dias)" icon={Activity} className="xl:col-span-2">
          <div className="h-64">
            {loading ? (
              <div className="h-full animate-pulse rounded bg-slate-100" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={activitySeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="label" tick={{ fill: "#64748b", fontSize: 12 }} axisLine={{ stroke: "#e2e8f0" }} />
                  <YAxis allowDecimals={false} tick={{ fill: "#64748b", fontSize: 12 }} axisLine={{ stroke: "#e2e8f0" }} />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid #e2e8f0",
                      backgroundColor: "#ffffff",
                      color: "#0f172a",
                    }}
                    labelStyle={{ color: "#0f172a" }}
                  />
                  <Line type="monotone" dataKey="mensagens" stroke="#818cf8" strokeWidth={3} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </SectionCard>

        <SectionCard title="Atividade recente">
          <div className="overflow-hidden rounded-xl border border-slate-200">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Nome</th>
                  <th className="px-3 py-2 font-medium">Data</th>
                  <th className="px-3 py-2 font-medium">Agente</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  [...Array(5)].map((_, idx) => (
                    <tr key={`sk-${idx}`} className="border-t border-slate-100">
                      <td className="px-3 py-3">
                        <div className="h-3 w-16 animate-pulse rounded bg-slate-200" />
                      </td>
                      <td className="px-3 py-3">
                        <div className="h-3 w-14 animate-pulse rounded bg-slate-200" />
                      </td>
                      <td className="px-3 py-3">
                        <div className="h-3 w-20 animate-pulse rounded bg-slate-200" />
                      </td>
                    </tr>
                  ))
                ) : recentLeads.length > 0 ? (
                  recentLeads.map((lead) => (
                    <tr key={lead.id} className="border-t border-slate-100 text-slate-700">
                      <td className="px-3 py-2.5 font-medium">{lead.nome}</td>
                      <td className="px-3 py-2.5">{formatDateTime(lead.data)}</td>
                      <td className="px-3 py-2.5">{lead.agente}</td>
                    </tr>
                  ))
                ) : (
                  <tr className="border-t border-slate-100">
                    <td colSpan={3} className="px-3 py-6 text-center text-slate-500">
                      Sem atividade recente.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
