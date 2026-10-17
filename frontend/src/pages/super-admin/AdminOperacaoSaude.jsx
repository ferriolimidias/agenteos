import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import api from "../../services/api";
import { ActionButton, ErrorState, LoadingState, MetricCard, PageHeader, SectionCard, StatusBadge } from "../../components/ui";

const workerLabels = { followup: "Follow-ups", campaign: "Campanhas de mensagens" };
const workerStatus = (worker) => {
  if (!worker) return { label: "Não informado", tone: "warning" };
  if (worker.running && worker.status === "RUNNING") return { label: "Em execução", tone: "success" };
  if (worker.status === "DEGRADED") return { label: "Degradado", tone: "danger" };
  if (worker.enabled === false || worker.status === "STOPPED") return { label: "Parado", tone: "warning" };
  return { label: String(worker.status || "Desconhecido"), tone: "warning" };
};

function health(value) {
  if (value === true) return { label: "Saudável", tone: "success" };
  if (value === false) return { label: "Indisponível", tone: "danger" };
  return { label: "Não informado", tone: "warning" };
}

export default function AdminOperacaoSaude() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError(false);
    try {
      const response = await api.get("/admin/workers/status");
      setStatus(response.data || null);
      setUpdatedAt(new Date());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = window.setInterval(() => refresh({ silent: true }), 30_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const database = health(status?.database?.healthy);
  const redis = health(status?.redis?.healthy);

  return <div className="fm-admin-page">
    <PageHeader eyebrow="Plataforma Ferrioli" title="Operação e saúde" icon={Activity} description="Estado observado de banco, Redis, workers e filas. Informações ausentes são mostradas como não informadas." actions={<ActionButton type="button" variant="secondary" onClick={() => refresh()} disabled={loading} icon={RefreshCw}>Atualizar</ActionButton>} />
    {loading && !status ? <LoadingState label="Consultando os serviços operacionais..." /> : null}
    {error && !status ? <ErrorState title="Status operacional indisponível" description="Não foi possível consultar os indicadores operacionais agora." action={<ActionButton type="button" onClick={() => refresh()}>Tentar novamente</ActionButton>} /> : null}
    {status ? <>
      {error ? <p role="status" className="fm-helper">A atualização falhou. Os dados abaixo são da consulta anterior{updatedAt ? ` (${updatedAt.toLocaleTimeString("pt-BR")})` : ""}.</p> : null}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <MetricCard label="PostgreSQL" value={database.label} helper="Verificação realizada pelo endpoint operacional." icon={Activity} tone={database.tone} />
        <MetricCard label="Redis" value={redis.label} helper="Sinal de saúde disponível para o painel operacional." icon={Activity} tone={redis.tone} />
        <MetricCard label="Lock de follow-up" value={status.status_lock || "Não informado"} helper="Estado informado pelo serviço operacional." icon={Activity} />
      </div>
      <SectionCard title="Workers e filas" description={updatedAt ? `Última consulta: ${updatedAt.toLocaleString("pt-BR")}.` : "Consulta ainda não concluída."}>
        <div className="grid gap-4 lg:grid-cols-2">
          {Object.entries(workerLabels).map(([key, label]) => {
            const worker = status.workers?.[key];
            const state = workerStatus(worker);
            return <article key={key} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold text-slate-900">{label}</h2><StatusBadge tone={state.tone}>{state.label}</StatusBadge></div>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-slate-500">Habilitado</dt><dd className="font-medium">{worker?.enabled === true ? "Sim" : worker?.enabled === false ? "Não" : "Não informado"}</dd></div>
                <div><dt className="text-slate-500">Instâncias ativas</dt><dd className="font-medium">{Number.isFinite(Number(worker?.instance_count)) ? worker.instance_count : "Não informado"}</dd></div>
                <div><dt className="text-slate-500">Pendentes</dt><dd className="font-medium">{worker?.pending ?? "Não informado"}</dd></div>
                <div><dt className="text-slate-500">Retentativas</dt><dd className="font-medium">{worker?.retry ?? "Não informado"}</dd></div>
              </dl>
              <p className="mt-3 text-xs text-slate-500">O status reflete a resposta operacional atual; fila vazia e dado indisponível são estados distintos.</p>
            </article>;
          })}
        </div>
      </SectionCard>
    </> : null}
  </div>;
}
