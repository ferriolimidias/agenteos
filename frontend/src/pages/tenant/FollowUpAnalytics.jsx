import "../../styles/ux05-analytics.css";
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, RefreshCw, Timer } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { ErrorState, InfoBanner, Input, LoadingState, PageHeader, SectionCard, Select, StatusBadge, Table } from "../../components/ui";
import "./QualityDashboard.css";

const STATES = { observed: "Observado", partial: "Parcial", unknown: "Desconhecido", not_applicable: "Não aplicável" };
const TONES = { observed: "success", partial: "warning", unknown: "default", not_applicable: "default" };
const LABELS = {
  scheduled_executions: "Ciclos criados no período",
  currently_scheduled: "Ainda aguardando execução",
  eligible_contacts: "Contatos elegíveis observados",
  executions_started: "Execuções iniciadas",
  messages_sent: "Mensagens enviadas",
  deliveries_confirmed: "Entregas confirmadas",
  responses_observed: "Respostas posteriores",
  reactivations_observed: "Reativações observadas",
  stage_advances_observed: "Avanços comerciais posteriores",
  confirmed_conversions: "Conversões comprovadas",
  cancelled_or_stopped: "Cancelados ou interrompidos",
  failed: "Falhas registradas",
};
const DRILL_KIND = {
  scheduled_executions: "all", currently_scheduled: "scheduled", eligible_contacts: "eligible", executions_started: "initiated",
  messages_sent: "sent", deliveries_confirmed: "delivered", confirmed_conversions: "converted", failed: "failed",
  responses_observed: "replied", reactivations_observed: "reactivated",
  stage_advances_observed: "stage_advanced", cancelled_or_stopped: "stopped",
};
const isoDay = (date) => date.toISOString().slice(0, 10);
const nextDay = (day) => {
  const value = new Date(`${day}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + 1);
  return value.toISOString();
};
const initialFilters = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 86400000);
  return { start: isoDay(start), end: isoDay(end), response_window_days: 7, reactivation_days: 7, mode: "", channel: "", status: "", stop_reason: "" };
};
const paramsFor = (filters) => ({
  start: `${filters.start}T00:00:00Z`, end: nextDay(filters.end),
  response_window_days: filters.response_window_days, reactivation_days: filters.reactivation_days,
  ...(filters.mode ? { mode: filters.mode } : {}), ...(filters.channel ? { channel: filters.channel } : {}),
  ...(filters.status ? { status: filters.status } : {}), ...(filters.stop_reason ? { stop_reason: filters.stop_reason } : {}),
});
const count = (value) => value === null || value === undefined ? "—" : new Intl.NumberFormat("pt-BR").format(value);
const percent = (metric) => metric?.value == null ? "—" : `${(metric.value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const time = (value) => value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }) : "—";
const state = (value) => <StatusBadge tone={TONES[value] || "default"}>{STATES[value] || "Desconhecido"}</StatusBadge>;
const modeLabel = (mode) => mode === "conversation_resume" ? "Retomada automática" : mode === "scheduled_message" ? "Mensagem programada" : "Desconhecido";
const statusLabel = (status) => ({ scheduled: "Agendado", retry: "Nova tentativa", processing: "Processando", locked: "Em processamento", sent: "Enviado", delivered: "Entregue", read: "Lido", cancelled: "Cancelado", stopped: "Interrompido", failed: "Falhou" })[status] || "Desconhecido";

function Metric({ metricKey, metric, onEvidence }) {
  const drillKind = DRILL_KIND[metricKey];
  const noDenominator = metric?.denominator === 0 && metric?.state === "unknown";
  return <article className="quality-metric-box" data-testid={`followup-metric-${metricKey}`}>
    <div className="quality-metric-heading"><span>{LABELS[metricKey]}</span>{state(metric?.state)}</div>
    <strong>{noDenominator ? "—" : metricKey === "scheduled_executions" ? count(metric?.numerator) : `${count(metric?.numerator)} / ${count(metric?.denominator)}`}</strong>
    <p className="fm-helper">{metricKey === "scheduled_executions" ? "execuções persistidas no período" : `${percent(metric)} · numerador / denominador`}</p>
    {metric?.definition ? <p className="fm-helper">{metric.definition}</p> : null}
    {metric?.coverage?.pending_window > 0 ? <p className="fm-helper">Janela pendente: {count(metric.coverage.pending_window)}</p> : null}
    {drillKind && <button type="button" className="fm-button fm-button-secondary" onClick={() => onEvidence(drillKind)}>Ver registros</button>}
  </article>;
}

export default function FollowUpAnalytics() {
  const [tenant, setTenant] = useState(getActiveEmpresaId);
  const [draft, setDraft] = useState(initialFilters);
  const [filters, setFilters] = useState(initialFilters);
  const [refresh, setRefresh] = useState(0);
  const [view, setView] = useState({ scope: "", loading: true, data: null, error: "" });
  const [drill, setDrill] = useState({ kind: "", loading: false, data: null, error: "", offset: 0 });
  const scope = JSON.stringify([tenant, filters, refresh]);

  useEffect(() => {
    const onStorage = () => setTenant(getActiveEmpresaId());
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const requestedScope = scope;
    if (!tenant) {
      Promise.resolve().then(() => {
        if (!controller.signal.aborted) setView({ scope: requestedScope, loading: false, data: null, error: "Selecione um tenant autenticado para consultar os follow-ups." });
      });
      return () => controller.abort();
    }
    api.get(`/empresas/${tenant}/analytics/followup-analytics`, { params: paramsFor(filters), signal: controller.signal })
      .then(response => { if (!controller.signal.aborted && getActiveEmpresaId() === tenant) setView({ scope: requestedScope, loading: false, data: response.data, error: "" }); })
      .catch(error => { if (!controller.signal.aborted && getActiveEmpresaId() === tenant) setView({ scope: requestedScope, loading: false, data: null, error: error?.response?.data?.detail || "Não foi possível carregar a análise de follow-ups." }); });
    return () => controller.abort();
  }, [scope, tenant, filters]);

  const data = view.scope === scope ? view.data : null;
  const loading = view.scope !== scope || view.loading;
  const error = view.scope === scope ? view.error : "";
  const update = (field, value) => setDraft(current => ({ ...current, [field]: value }));
  const submit = (event) => { event.preventDefault(); setFilters({ ...draft }); setDrill({ kind: "", loading: false, data: null, error: "", offset: 0 }); setRefresh(value => value + 1); };
  const reset = () => { const next = initialFilters(); setDraft(next); setFilters(next); setDrill({ kind: "", loading: false, data: null, error: "", offset: 0 }); setRefresh(value => value + 1); };
  const fetchEvidence = async (kind, offset = 0) => {
    if (!tenant || drill.loading) return;
    setDrill({ kind, loading: true, data: null, error: "", offset });
    try {
      const response = await api.get(`/empresas/${tenant}/analytics/followup-analytics/evidence`, { params: { ...paramsFor(filters), kind, limit: 25, offset } });
      if (getActiveEmpresaId() === tenant) setDrill({ kind, loading: false, data: response.data, error: "", offset });
    } catch (caught) {
      setDrill({ kind, loading: false, data: null, error: caught?.response?.data?.detail || "Não foi possível carregar os registros.", offset });
    }
  };
  const metrics = data?.summary || {};
  const metricKeys = Object.keys(LABELS);
  const stopReasons = useMemo(() => data?.stop_reasons || [], [data]);

  return <main className="ux05-analytics quality-dashboard followup-analytics" data-testid="followup-analytics-dashboard">
    <PageHeader eyebrow="A09 · análise somente leitura" title="Desempenho dos follow-ups" icon={Timer} description="Acompanhe o que foi agendado e o que tem evidência de execução, envio, entrega e retorno. Eventos posteriores não comprovam causalidade." actions={<button type="button" className="fm-button fm-button-secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} aria-hidden="true" />Atualizar</button>} />
    <InfoBanner tone="info">O painel não cria nem altera follow-ups. Entrega exige confirmação persistida do provider. Elegibilidade, respostas e avanços usam somente eventos registrados; dados ausentes aparecem como parciais ou desconhecidos.</InfoBanner>
    <SectionCard title="Período e filtros" description="Período UTC com início inclusivo e dia final inclusivo; execução agrupada por data de criação.">
      <form className="quality-filter-grid" onSubmit={submit}>
        <label>Início (UTC)<Input type="date" value={draft.start} required onChange={event => update("start", event.target.value)} /></label>
        <label>Fim (UTC)<Input type="date" value={draft.end} required onChange={event => update("end", event.target.value)} /></label>
        <label>Janela de resposta (dias)<Input type="number" min="1" max="90" value={draft.response_window_days} onChange={event => update("response_window_days", Number(event.target.value))} /></label>
        <label>Inatividade para reativação (dias)<Input type="number" min="1" max="90" value={draft.reactivation_days} onChange={event => update("reactivation_days", Number(event.target.value))} /></label>
        <label>Tipo<Select value={draft.mode} onChange={event => update("mode", event.target.value)}><option value="">Todos</option><option value="scheduled_message">Mensagem programada</option><option value="conversation_resume">Retomada automática</option></Select></label>
        <label>Canal<Select value={draft.channel} onChange={event => update("channel", event.target.value)}><option value="">Todos</option>{(data?.options?.channels || ["whatsapp", "instagram", "web", "sms", "telegram"]).map(channel => <option key={channel} value={channel}>{channel}</option>)}</Select></label>
        <label>Estado atual<Select value={draft.status} onChange={event => update("status", event.target.value)}><option value="">Todos</option>{(data?.options?.statuses || []).map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}</Select></label>
        <label>Motivo de parada<Select value={draft.stop_reason} onChange={event => update("stop_reason", event.target.value)}><option value="">Todos</option>{(data?.options?.stop_reasons || []).map(reason => <option key={reason} value={reason}>{reason.replaceAll("_", " ")}</option>)}</Select></label>
        <div className="quality-filter-actions"><button type="submit" className="fm-button fm-button-primary">Aplicar filtros</button><button type="button" className="fm-button fm-button-ghost" onClick={reset}>Limpar</button></div>
      </form>
    </SectionCard>
    {loading && <LoadingState label="Consultando registros de follow-up e cobertura..." />}
    {error && <ErrorState title="Análise indisponível" description={error} action={<button type="button" className="fm-button fm-button-secondary" onClick={() => setRefresh(value => value + 1)}>Tentar novamente</button>} />}
    {data && <>
      <p className="fm-helper">Período: {time(data.window?.start)} a {time(data.window?.end)} (fim exclusivo) · observação: {time(data.window?.observed_at)} UTC · resposta: {data.window?.response_window_days} dias · execução: {data.coverage?.execution_rows_read}/{data.coverage?.execution_limit} registros · {state(data.coverage?.state)}</p>
      <section className="quality-metric-grid" aria-label="Métricas de follow-up">
        {metricKeys.map(key => <Metric key={key} metricKey={key} metric={metrics[key]} onEvidence={kind => fetchEvidence(kind)} />)}
      </section>
      <SectionCard title="Programado vs. automático" description="Ciclos criados no período por tipo persistido no snapshot. O primeiro total inclui execuções concluídas; ‘ainda aguardando execução’ conta scheduled/retry. Respostas posteriores são uma associação temporal na mesma conversa.">
        <Table label="Comparação de follow-ups"><thead><tr><th>Tipo</th><th>Ciclos criados</th><th>Iniciados</th><th>Enviados</th><th>Entregues</th><th>Respostas</th><th>Taxa de resposta</th></tr></thead><tbody>{Object.entries(data.by_mode || {}).map(([mode, row]) => <tr key={mode}><th scope="row">{modeLabel(mode)}</th><td>{count(row.scheduled)}</td><td>{count(row.executed)}</td><td>{count(row.sent)}</td><td>{count(row.delivered)}</td><td>{count(row.responded)}</td><td>{percent(row.response_rate)} · {count(row.response_rate?.numerator)}/{count(row.response_rate?.denominator)} · {STATES[row.response_rate?.state] || "Desconhecido"}</td></tr>)}</tbody></Table>
      </SectionCard>
      <SectionCard title="Cancelamentos, interrupções e falhas" description="Motivos livres são agrupados como ‘outro motivo registrado’ para não expor detalhes internos ou dados pessoais.">
        {stopReasons.length ? <Table label="Motivos de parada"><thead><tr><th>Motivo</th><th>Registros</th></tr></thead><tbody>{stopReasons.map(item => <tr key={item.reason}><td>{item.reason.replaceAll("_", " ")}</td><td>{count(item.count)}</td></tr>)}</tbody></Table> : <p className="fm-helper">Nenhum motivo de parada foi registrado no período.</p>}
        <p className="fm-helper">Envios legados: {count(data.legacy_send_logs?.send_log_rows)} registros em {count(data.legacy_send_logs?.distinct_contacts)} contatos distintos. Mantidos separados para evitar duplicidade com ciclos modernos; entrega e resposta legadas permanecem desconhecidas.</p>
      </SectionCard>
      <SectionCard title="Cobertura e limites" description="Estados parciais e desconhecidos preservam a diferença entre ausência de dados e zero observado.">
        {(data.coverage?.limitations || []).map((limitation, index) => <p className="fm-helper" key={index}>{limitation}</p>)}
        <p className="fm-helper">Decisões de elegibilidade registradas: {count(data.coverage?.eligibility_decision_coverage?.recorded)}; sem decisão: {count(data.coverage?.eligibility_decision_coverage?.unknown)}; denominador: {count(data.coverage?.eligibility_decision_coverage?.denominator)}.</p>
        <p className="fm-helper">Histórico de etapa limitado: {data.coverage?.stage_events_truncated ? "sim" : "não"}. Logs legados: {data.coverage?.legacy_logs?.reason}</p>
      </SectionCard>
      {drill.kind && <SectionCard title="Registros observados" description="IDs servem para abrir a conversa no Inbox autenticado. Nenhuma mensagem, telefone ou nome de contato é retornado." actions={<button className="fm-button fm-button-ghost" type="button" onClick={() => setDrill({ kind: "", loading: false, data: null, error: "", offset: 0 })}>Fechar</button>}>
        {drill.loading && <LoadingState label="Consultando registros..." />}{drill.error && <p role="alert" className="fm-helper">{drill.error}</p>}
        {drill.data && <>
          <Table label="Registros de follow-up"><thead><tr><th>Follow-up</th><th>Tipo</th><th>Agendado</th><th>Enviado</th><th>Resposta</th><th>Estado</th><th>Elegibilidade</th><th>Abrir</th></tr></thead><tbody>{drill.data.items.map(row => <tr key={row.execution_id}><td>{row.followup_id ? `Follow-up ${row.followup_id.slice(0, 8)}` : "Follow-up"}</td><td>{modeLabel(row.mode)}</td><td>{time(row.scheduled_at)}</td><td>{time(row.sent_at)}</td><td>{time(row.response_at)}</td><td>{statusLabel(row.status)}{row.stop_reason ? ` · ${row.stop_reason.replaceAll("_", " ")}` : ""}</td><td>{row.eligibility === true ? "Elegível registrado" : row.eligibility === false ? "Inelegível registrado" : "Desconhecido"}</td><td>{row.lead_id ? <Link className="quality-link" to={`/painel/chat?lead=${encodeURIComponent(row.lead_id)}&journey=1`}><ExternalLink size={14} aria-hidden="true" />Inbox</Link> : "Indisponível"}</td></tr>)}</tbody></Table>
          <div className="quality-evidence-pagination"><span className="fm-helper">{count(drill.data.total)} registros · página {Math.floor(drill.offset / 25) + 1} · cobertura {STATES[drill.data.coverage?.state] || "Desconhecida"}</span><div><button type="button" className="fm-button fm-button-secondary" disabled={drill.offset === 0 || drill.loading} onClick={() => fetchEvidence(drill.kind, Math.max(0, drill.offset - 25))}>Anterior</button><button type="button" className="fm-button fm-button-secondary" disabled={drill.offset + 25 >= Number(drill.data.total || 0) || drill.loading} onClick={() => fetchEvidence(drill.kind, drill.offset + 25)}>Próxima</button></div></div>
        </>}
      </SectionCard>}
      <p className="fm-helper">Consulta somente leitura. <Link className="quality-link" to="/painel/followups">Configurar follow-ups existentes</Link></p>
    </>}
  </main>;
}
