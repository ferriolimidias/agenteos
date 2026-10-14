import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { BarChart3, ExternalLink, RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { PageHeader, SectionCard, MetricCard, InfoBanner, LoadingState, ErrorState, Select, Input, StatusBadge, Table } from "../../components/ui";
import "../../styles/ux05-analytics.css";
import "./QualityDashboard.css";

const STATE_LABELS = { observed: "Observado", partial: "Parcial", unknown: "Desconhecido", not_applicable: "Não aplicável" };
const STATE_TONES = { observed: "success", partial: "warning", unknown: "default", not_applicable: "default" };
const metricLabels = {
  question_without_observed_response: "Perguntas sem resposta observada",
  price_offer_without_observed_continuity: "Sem continuidade após preço/oferta",
  response_coverage: "Cobertura de resposta",
};

const isoDay = (date) => date.toISOString().slice(0, 10);
const nextUtcDay = (day) => {
  const date = new Date(`${day}T00:00:00.000Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString();
};
const initialFilters = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 30 * 86400000);
  return { start: isoDay(start), end: isoDay(end), response_window_hours: 24, continuity_window_days: 7, channel: "", stage: "", ad_id: "", offer_signal: "", composition_version: "" };
};
const paramsFor = (filters) => ({
  start: `${filters.start}T00:00:00Z`,
  end: nextUtcDay(filters.end),
  response_window_hours: filters.response_window_hours,
  continuity_window_days: filters.continuity_window_days,
  ...(filters.channel ? { channel: filters.channel } : {}),
  ...(filters.stage ? { stage: filters.stage } : {}),
  ...(filters.ad_id ? { ad_id: filters.ad_id } : {}),
  ...(filters.offer_signal ? { offer_signal: filters.offer_signal } : {}),
  ...(filters.composition_version ? { composition_version: filters.composition_version } : {}),
});
const count = (value) => value === null || value === undefined ? "—" : new Intl.NumberFormat("pt-BR").format(value);
const percent = (metric) => metric?.value === null || metric?.value === undefined ? "—" : `${(metric.value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const time = (value) => value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }) : "—";
const stateBadge = (state) => <StatusBadge tone={STATE_TONES[state] || "default"}>{STATE_LABELS[state] || "Desconhecido"}</StatusBadge>;

function MetricBox({ metricKey, metric, onEvidence }) {
  return <div className="quality-metric-box">
    <div className="quality-metric-heading"><span>{metricLabels[metricKey]}</span>{stateBadge(metric?.state)}</div>
    <strong>{percent(metric)}</strong>
    <p className="fm-helper">{count(metric?.numerator)} / {count(metric?.denominator)} · numerador / denominador observado</p>
    {metric?.definition ? <p className="fm-helper">{metric.definition}</p> : null}
    {(metricKey === "question_without_observed_response" || metricKey === "price_offer_without_observed_continuity") && <button type="button" className="fm-button fm-button-secondary" onClick={() => onEvidence(metricKey)}><Search size={15} aria-hidden="true" />Ver evidências</button>}
  </div>;
}

export default function QualityDashboard() {
  const [tenant, setTenant] = useState(getActiveEmpresaId);
  const [draft, setDraft] = useState(initialFilters);
  const [filters, setFilters] = useState(initialFilters);
  const [refresh, setRefresh] = useState(0);
  const [view, setView] = useState({ scope: null, loading: true, data: null, error: "" });
  const [evidenceCategory, setEvidenceCategory] = useState("");
  const [evidenceView, setEvidenceView] = useState({ scope: null, loading: false, data: null, error: "" });
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const evidenceControllerRef = useRef(null);
  const evidenceScopeRef = useRef("");
  const scope = JSON.stringify([tenant, filters, refresh]);
  const evidenceScope = JSON.stringify([tenant, filters, refresh]);

  useLayoutEffect(() => {
    evidenceScopeRef.current = evidenceScope;
    evidenceControllerRef.current?.abort();
  }, [evidenceScope]);

  useEffect(() => {
    const update = () => setTenant(getActiveEmpresaId());
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const requestedScope = scope;
    if (!tenant) {
      Promise.resolve().then(() => { if (!controller.signal.aborted) setView({ scope: requestedScope, loading: false, data: null, error: "Selecione um tenant autenticado para consultar a qualidade." }); });
      return () => controller.abort();
    }
    api.get(`/empresas/${tenant}/analytics/attendance-quality`, { params: paramsFor(filters), signal: controller.signal })
      .then(response => { if (!controller.signal.aborted && getActiveEmpresaId() === tenant) setView({ scope: requestedScope, loading: false, data: response.data, error: "" }); })
      .catch(caught => { if (!controller.signal.aborted && getActiveEmpresaId() === tenant) setView({ scope: requestedScope, loading: false, data: null, error: caught?.response?.data?.detail || "Não foi possível carregar o diagnóstico de qualidade." }); });
    return () => controller.abort();
  }, [scope, tenant, filters]);

  const currentEvidenceScope = JSON.stringify([tenant, filters, refresh, evidenceCategory, evidenceOffset]);
  const visibleEvidence = evidenceView.scope === currentEvidenceScope;
  const evidence = visibleEvidence ? evidenceView.data : null;
  const evidenceLoading = visibleEvidence && evidenceView.loading;
  const evidenceError = visibleEvidence ? evidenceView.error : "";

  const fetchEvidence = (category, requestedOffset = 0) => {
    if (!tenant || evidenceLoading) return;
    evidenceControllerRef.current?.abort();
    const requestScope = JSON.stringify([tenant, filters, refresh, category, requestedOffset]);
    setEvidenceCategory(category); setEvidenceOffset(requestedOffset); setEvidenceView({ scope: requestScope, loading: true, data: null, error: "" });
    const controller = new AbortController();
    evidenceControllerRef.current = controller;
    api.get(`/empresas/${tenant}/analytics/attendance-quality/evidence`, { params: { ...paramsFor(filters), category, limit: 50, offset: requestedOffset }, signal: controller.signal })
      .then(response => { if (!controller.signal.aborted && getActiveEmpresaId() === tenant && evidenceScopeRef.current === JSON.stringify([tenant, filters, refresh])) setEvidenceView({ scope: requestScope, loading: false, data: response.data, error: "" }); })
      .catch(caught => { if (!controller.signal.aborted && evidenceScopeRef.current === JSON.stringify([tenant, filters, refresh])) setEvidenceView({ scope: requestScope, loading: false, data: null, error: caught?.response?.data?.detail || "Não foi possível carregar as evidências." }); });
  };
  useEffect(() => () => evidenceControllerRef.current?.abort(), []);
  const activeView = view.scope === scope ? view : null;
  const data = activeView?.data || null;
  const loading = activeView ? activeView.loading : true;
  const error = activeView?.error || "";
  const options = data?.options || {};
  const coverage = data?.coverage || {};
  const promptRows = data?.composition_traces?.items || [];
  const objectionRows = data?.objections?.items || [];
  const submit = (event) => { event.preventDefault(); setFilters({ ...draft }); setRefresh(value => value + 1); };
  const reset = () => { const next = initialFilters(); setDraft(next); setFilters(next); setRefresh(value => value + 1); };
  const update = (field, value) => setDraft(current => ({ ...current, [field]: value }));

  return <main className="ux05-analytics quality-dashboard" data-testid="attendance-quality-dashboard">
    <PageHeader eyebrow="Diagnóstico A08 · somente leitura" title="Qualidade do atendimento" icon={BarChart3} description="Sinais observados no histórico para orientar melhoria. Ausência de resposta não classifica, sozinha, objeção ou abandono." actions={<button type="button" className="fm-button fm-button-secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} aria-hidden="true" />Atualizar</button>} />
    <InfoBanner tone="info">O painel usa mensagens e tags persistidas no tenant. Texto de clientes, telefones e nomes não são retornados. Métricas com janela incompleta ficam como <strong>parciais</strong>; sem base observável ficam como <strong>desconhecidas</strong>. A ausência de resposta não classifica, por si só, uma objeção ou abandono.</InfoBanner>
    <SectionCard title="Período e filtros" description="Todas as datas são interpretadas em UTC; o fim do período é inclusivo no dia selecionado.">
      <form className="quality-filter-grid" onSubmit={submit}>
        <label>Início (UTC)<Input type="date" value={draft.start} required onChange={event => update("start", event.target.value)} /></label>
        <label>Fim (UTC)<Input type="date" value={draft.end} required onChange={event => update("end", event.target.value)} /></label>
        <label>Janela de resposta (h)<Input type="number" min="1" max="168" value={draft.response_window_hours} onChange={event => update("response_window_hours", Number(event.target.value))} /></label>
        <label>Janela após oferta (dias)<Input type="number" min="1" max="30" value={draft.continuity_window_days} onChange={event => update("continuity_window_days", Number(event.target.value))} /></label>
        <label>Canal<Select value={draft.channel} onChange={event => update("channel", event.target.value)}><option value="">Todos</option>{(options.channels || []).map(value => <option key={value} value={value}>{value}</option>)}</Select></label>
        <label>Etapa atual do CRM<Select value={draft.stage} onChange={event => update("stage", event.target.value)}><option value="">Todas</option>{(options.stages || []).map(stage => <option key={stage.id} value={stage.id}>{stage.label}</option>)}</Select></label>
        <label>Anúncio ligado ao contato (coorte)<Select value={draft.ad_id} onChange={event => update("ad_id", event.target.value)}><option value="">Todos</option>{(options.ads || []).map(value => <option key={value} value={value}>{value}</option>)}</Select></label>
        <label>Sinal de oferta<Select value={draft.offer_signal} onChange={event => update("offer_signal", event.target.value)}><option value="">Preço e oferta</option>{(options.offer_signals || []).map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select></label>
        <label>Composição A07 (somente traces)<Select value={draft.composition_version} onChange={event => update("composition_version", event.target.value)}><option value="">Todas</option>{(options.composition_versions || []).map(value => <option key={value} value={value}>{value}</option>)}</Select></label>
        <div className="quality-filter-actions"><button type="submit" className="fm-button fm-button-primary">Aplicar filtros</button><button type="button" className="fm-button fm-button-ghost" onClick={reset}>Limpar</button></div>
      </form>
    </SectionCard>
    {loading && <LoadingState label="Consultando histórico e cobertura..." />}
    {error && <ErrorState title="Diagnóstico indisponível" description={error} action={<button type="button" className="fm-button fm-button-secondary" onClick={() => setRefresh(value => value + 1)}>Tentar novamente</button>} />}
    {data && <>
      <p className="fm-helper quality-filter-scope">Escopo dos filtros: canal — {data.filter_scope?.channel} Etapa — {data.filter_scope?.stage} Anúncio — {data.filter_scope?.ad} Composição — {data.filter_scope?.composition_version}</p>
      <div className="quality-window fm-helper">Período UTC: {filters.start} a {filters.end} (dias inclusivos) · horizonte {time(data.window?.observed_at)} · respostas em {data.window?.response_window_hours}h · continuidade em {data.window?.continuity_window_days} dias.</div>
      <section className="quality-metric-grid" aria-label="Indicadores de qualidade">
        <MetricBox metricKey="question_without_observed_response" metric={data.metrics?.question_without_observed_response} onEvidence={fetchEvidence} />
        <MetricBox metricKey="price_offer_without_observed_continuity" metric={data.metrics?.price_offer_without_observed_continuity} onEvidence={fetchEvidence} />
        <MetricBox metricKey="response_coverage" metric={data.metrics?.response_coverage} onEvidence={fetchEvidence} />
        <MetricCard label="Mediana até primeira resposta" value={data.metrics?.median_response_seconds?.value == null ? "—" : `${Math.round(data.metrics.median_response_seconds.value / 60)} min`} helper={`${STATE_LABELS[data.metrics?.median_response_seconds?.state] || "Desconhecido"} · ${count(data.metrics?.median_response_seconds?.denominator)} pares respondidos; ${count(data.metrics?.median_response_seconds?.mature_inbound_denominator)} inbounds maduros`} icon={BarChart3} />
      </section>
      <SectionCard title="Cobertura e limites" description="O denominador é mostrado para separar ausência de evidência de zero observado.">
        <div className="quality-coverage-grid"><div><strong>{count(coverage.distinct_leads_by_crm_id)}</strong><span>leads CRM distintos (UUID)</span></div><div><strong>{count(coverage.messages_in_period)}</strong><span>mensagens no período</span></div><div><strong>{count(coverage.questions_total)}</strong><span>perguntas com marcador '?'</span></div><div><strong>{count(coverage.price_offer_signals_total)}</strong><span>sinais textuais de preço/oferta</span></div><div><strong>{count(coverage.missing_conversation_messages)}</strong><span>sem conversa vinculada</span></div><div><strong>{count(coverage.unknown_channel_messages)}</strong><span>canal desconhecido no filtro</span></div><div><strong>{count(coverage.questions_pending_window + coverage.price_offer_pending_window)}</strong><span>janelas ainda incompletas</span></div></div>
        <p className="fm-helper">Eventos de objeção sem associação para os filtros: canal {count(coverage.unknown_objection_channel_association_events)}, etapa {count(coverage.unknown_objection_stage_association_events)}. O filtro de anúncio é uma coorte do contato, sem associação temporal com a mensagem.</p>
        <p className="fm-helper">Estado geral: {stateBadge(coverage.state)}{coverage.messages_truncated ? " Limite técnico atingido; cobertura parcial." : ""}</p>
        {(coverage.limitations || []).map(limitation => <p className="fm-helper" key={limitation}>{limitation}</p>)}
      </SectionCard>
      <SectionCard title="Sinais observados" description="Visualização agregada; as barras representam taxas observadas e não avaliação causal."><div className="quality-bars" aria-label="Gráfico de taxas observadas">{["question_without_observed_response", "price_offer_without_observed_continuity", "response_coverage"].map(key => { const metric = data.metrics?.[key]; const width = metric?.value == null ? 0 : Math.max(0, Math.min(100, metric.value * 100)); return <div className="quality-bar-row" key={key}><span>{metricLabels[key]}</span><div className="quality-bar-track"><div className="quality-bar-fill" style={{ width: `${width}%` }} /></div><strong>{percent(metric)}</strong></div>; })}</div><div className="quality-example-grid">{(data.examples || []).map(example => <div key={example.kind}><strong>{count(example.count)}</strong><span>{example.label}</span><small>Exemplo sanitizado · {example.privacy}</small></div>)}</div></SectionCard>
      <div className="quality-two-column">
        <SectionCard title="Objeções recorrentes" description={data.objections?.definition}><p className="fm-helper">{stateBadge(data.objections?.state)} · somente tags CRM explícitas; não inferimos objeções do texto.</p>{objectionRows.length ? <Table label="Tags de objeção"><thead><tr><th>Tag</th><th>Contatos</th><th>Conversas</th><th>Eventos</th></tr></thead><tbody>{objectionRows.map(row => <tr key={row.tag_id}><td>{row.label}</td><td>{count(row.distinct_leads)}</td><td>{count(row.distinct_conversations)}</td><td>{count(row.events)}</td></tr>)}</tbody></Table> : <p className="fm-helper">Nenhuma tag explícita de objeção foi observada neste período.</p>}</SectionCard>
            <SectionCard title="Composições A07 observadas" description={data.composition_traces?.definition}><p className="fm-helper">{stateBadge(data.composition_traces?.state)} · versão exata do prompt: {STATE_LABELS[data.composition_traces?.exact_prompt_version_state] || "Desconhecida"}. Traces agregados, retidos por até 7 dias e sem vínculo com conversa.</p>{promptRows.length ? <Table label="Execuções A07"><thead><tr><th>Composição</th><th>Modo / origem</th><th>Escopo medido</th><th>Execuções</th><th>Concluídas</th><th>Tokens observados (amostras / execuções com provider)</th><th>Latência mediana</th><th>Custo observado (amostras / execuções com provider)</th></tr></thead><tbody>{promptRows.map(row => <tr key={`${row.version}:${row.mode}:${row.source}`}><td>{row.version}</td><td>{row.mode} · {row.source}</td><td>{row.metric_scope} · {row.measurement_source}</td><td>{count(row.runs)}</td><td>{count(row.completed)} ({percent(row.completion_rate)})</td><td>{count(row.input_tokens_observed)} / {count(row.output_tokens_observed)} ({count(row.input_token_samples)} / {count(row.output_token_samples)} / {count(row.provider_usage_runs)} · {STATE_LABELS[row.token_state] || "Desconhecido"})</td><td>{row.median_latency_ms == null ? "—" : `${Math.round(row.median_latency_ms)} ms`}</td><td>{row.observed_cost_usd == null ? (row.cost_state === "unknown" ? `Desconhecido (${count(row.cost_samples)} / ${count(row.provider_usage_runs)})` : "N/A") : `US$ ${row.observed_cost_usd.toFixed(4)} (${count(row.cost_samples)} / ${count(row.provider_usage_runs)} · ${STATE_LABELS[row.cost_state] || "Desconhecido"})`}</td></tr>)}</tbody></Table> : <p className="fm-helper">Nenhum trace A07 observável no período. A composição e a versão de prompt ficam desconhecidas.</p>}</SectionCard>
      </div>
      <SectionCard title="Oportunidades de melhoria" description="Hipóteses de trabalho derivadas apenas dos sinais acima; valide-as na operação antes de alterar regras.">{data.opportunities?.length ? <div className="quality-opportunity-list">{data.opportunities.map(item => <div key={item.key}><div className="quality-metric-heading"><strong>{item.title}</strong><StatusBadge tone={item.kind === "fact" ? "info" : "warning"}>{item.kind === "fact" ? "Fato observado" : "Hipótese"}</StatusBadge></div><p className="fm-helper">{item.basis}</p></div>)}</div> : <p className="fm-helper">Nenhuma oportunidade baseada em evidência foi gerada neste filtro.</p>}</SectionCard>
      {evidenceCategory && (evidenceLoading || evidenceError || evidence) && <SectionCard title={`Evidências · ${metricLabels[evidenceCategory]}`} actions={<button type="button" className="fm-button fm-button-ghost" onClick={() => { evidenceControllerRef.current?.abort(); setEvidenceCategory(""); setEvidenceView({ scope: null, loading: false, data: null, error: "" }); }}>Fechar</button>}><p className="fm-helper">IDs abaixo servem apenas para abrir a conversa autenticada; nenhum texto ou dado de contato é exposto nesta tela.</p>{evidenceLoading && <LoadingState label="Consultando evidências..." />}{evidenceError && <p role="alert" className="fm-helper">{evidenceError}</p>}{evidence && <><Table label="Evidências do atendimento"><thead><tr><th>Observado em (UTC)</th><th>Lead</th><th>Conversa</th><th>Estado</th><th>Ação</th></tr></thead><tbody>{(evidence.items || []).map(item => <tr key={item.message_id}><td>{time(item.observed_at)}</td><td><code>{item.lead_id}</code></td><td><code>{item.conversation_id}</code></td><td>{stateBadge(item.state)}</td><td><Link className="quality-link" to={`/painel/chat?lead=${encodeURIComponent(item.lead_id)}&journey=1`}><ExternalLink size={14} aria-hidden="true" />Abrir Inbox</Link></td></tr>)}</tbody></Table><div className="quality-evidence-pagination"><span className="fm-helper">{count(evidence.total)} evidências · página {Math.floor(evidenceOffset / 50) + 1}</span><div><button type="button" className="fm-button fm-button-secondary" disabled={evidenceOffset === 0 || evidenceLoading} onClick={() => fetchEvidence(evidenceCategory, Math.max(0, evidenceOffset - 50))}>Anterior</button><button type="button" className="fm-button fm-button-secondary" disabled={evidenceOffset + 50 >= Number(evidence.total || 0) || evidenceLoading} onClick={() => fetchEvidence(evidenceCategory, evidenceOffset + 50)}>Próxima</button></div></div></>}{evidence && !evidence.items?.length && <p className="fm-helper">Nenhuma evidência corresponde a este filtro.</p>}</SectionCard>}
      <p className="fm-helper quality-footer">Painel somente leitura. Marcadores textuais são heurísticas determinísticas documentadas; a ausência de resposta não é diagnóstico de intenção, abandono ou satisfação.</p>
    </>}
  </main>;
}
