import "../../styles/ux05-analytics.css";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { BarChart3, Download, RefreshCw } from "lucide-react";
import api from "../../services/api";
import { PageHeader } from "../../components/ui";
import { getActiveEmpresaId } from "../../utils/auth";
import { aggregateCsvIsSafe, allowlistedFunnelFilters, defaultFunnelFilters, durationLabel, FUNNEL_STATUS, funnelCount, funnelError, funnelParams, funnelPercent, funnelRate, funnelRevenue, funnelTime, inputUtcTime, journeyInboxPath, optionLabel, optionValue, utcInputValue } from "../../utils/commercialFunnel";
import "./FunnelDashboard.css";

function SelectFilter({ label, field, filters, setFilters, options = [] }) {
  const unique = [...new Map(options.map(option => [optionValue(option), option])).values()];
  return <label>{label}<select aria-label={label} value={filters[field] || ""} onChange={event => setFilters(current => ({ ...current, [field]: event.target.value }))}><option value="">Todos</option>{unique.map(option => <option key={optionValue(option)} value={optionValue(option)}>{optionLabel(option)}</option>)}</select></label>;
}
const EVIDENCE_STATES = { observed: "Observado", unknown: "Desconhecido", partial: "Parcial", exact: "Identificado", resolved: "Resolvido", no_event: "Sem evento registrado", not_instrumented: "Não instrumentado", temporal_same_conversation_not_causal: "Retorno posterior na mesma conversa; relação temporal sem causalidade", temporal_not_causal: "Relação temporal sem causalidade" };
const COVERAGE_LABELS = { state: "Estado", status: "Estado", limitation: "Limitação", definition: "Definição", basis: "Base", numerator: "Numerador", denominator: "Denominador", coverage: "Cobertura", instrumented_leads: "Contatos instrumentados", unknown_leads: "Contatos desconhecidos", unconfirmed_purchase_signals: "Sinais de compra sem comprovação", snapshot_only: "Somente snapshot", history_complete: "Histórico completo", observed_leads: "Contatos com evidência", eligible_leads: "Contatos elegíveis", without_eligible_entry: "Sem entrada elegível", confirmed_leads: "Convertidos comprovados", unverified_purchase_signal_leads: "Sinais de compra sem comprovação financeira", crm_snapshot_is_not_history: "Snapshot CRM não é histórico", closed_is_not_lost: "Encerramento não comprova perda" };
function safeNotes(value) {
  if (typeof value === "string") return [EVIDENCE_STATES[value] || value];
  if (Array.isArray(value)) return value.filter(item => typeof item === "string");
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).filter(([key, item]) => Object.hasOwn(COVERAGE_LABELS, key) && (typeof item === "string" || typeof item === "boolean" || typeof item === "number")).map(([key, item]) => `${COVERAGE_LABELS[key]}: ${typeof item === "boolean" ? item ? "sim" : "não" : EVIDENCE_STATES[item] || item}`);
}
export default function FunnelDashboard() {
  const location = useLocation();
  const [tenant, setTenant] = useState(getActiveEmpresaId);
  const remembered = location.state?.funnelContext;
  const initial = useRef(remembered?.tenant === tenant ? { ...defaultFunnelFilters(), ...allowlistedFunnelFilters(remembered.filters) } : defaultFunnelFilters());
  const [draft, setDraft] = useState(initial.current);
  const [filters, setFilters] = useState(initial.current);
  const [refresh, setRefresh] = useState(0);
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState(null);
  const [offset, setOffset] = useState(0);
  const [drill, setDrill] = useState(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillError, setDrillError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const scope = JSON.stringify([tenant, filters, refresh]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const exportController = useRef(null);
  useEffect(() => {
    const update = () => setTenant(getActiveEmpresaId());
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  useEffect(() => {
    exportController.current?.abort(); setExporting(false); setExportError("");
    const controller = new AbortController();
    const requestedScope = scope;
    setDashboard(null); setLoading(true); setError(""); setDrill(null); setSelection(null); setOffset(0);
    if (!tenant) { setLoading(false); setError("Selecione um tenant autenticado para visualizar as evidências."); return () => controller.abort(); }
    api.get(`/empresas/${tenant}/analytics/commercial-funnel`, { params: funnelParams(filters), signal: controller.signal }).then(response => {
      if (controller.signal.aborted || scopeRef.current !== requestedScope || getActiveEmpresaId() !== tenant) return;
      setDashboard({ tenant, scope: requestedScope, data: response.data });
    }).catch(caught => { if (!controller.signal.aborted && scopeRef.current === requestedScope) setError(funnelError(caught)); }).finally(() => { if (!controller.signal.aborted && scopeRef.current === requestedScope) setLoading(false); });
    return () => controller.abort();
  }, [scope, tenant]);
  const data = dashboard?.scope === scope && dashboard?.tenant === tenant ? dashboard.data : null;
  const observedFilters = { ...filters, ...(data?.window?.observed_at ? { observed_at: data.window.observed_at } : {}) };
  const drillScope = JSON.stringify([scope, selection, offset, data?.window?.observed_at]);
  const drillScopeRef = useRef(drillScope); drillScopeRef.current = drillScope;
  useEffect(() => {
    const controller = new AbortController(); setDrill(null); setDrillError("");
    if (!data || !selection || !tenant) { setDrillLoading(false); return () => controller.abort(); }
    const requestedScope = drillScope;
    setDrillLoading(true);
    api.get(`/empresas/${tenant}/analytics/commercial-funnel/leads`, { params: funnelParams(observedFilters, { ...selection.params, offset, limit: 25 }), signal: controller.signal }).then(response => {
      if (!controller.signal.aborted && drillScopeRef.current === requestedScope && getActiveEmpresaId() === tenant) setDrill({ scope: requestedScope, data: response.data });
    }).catch(caught => { if (!controller.signal.aborted && drillScopeRef.current === requestedScope) setDrillError(funnelError(caught)); }).finally(() => { if (!controller.signal.aborted && drillScopeRef.current === requestedScope) setDrillLoading(false); });
    return () => controller.abort();
  }, [drillScope]);
  useEffect(() => () => exportController.current?.abort(), []);
  const rows = drill?.scope === drillScope ? drill.data : null;
  const openDrill = (title, params = {}) => { setOffset(0); setSelection({ title, params }); };
  const exportCsv = async () => {
    if (!data || exporting) return;
    const requestedScope = scope; const controller = new AbortController(); exportController.current = controller;
    setExporting(true); setExportError("");
    try {
      const response = await api.get(`/empresas/${tenant}/analytics/commercial-funnel/export`, { params: funnelParams(observedFilters), responseType: "blob", signal: controller.signal });
      if (controller.signal.aborted || scopeRef.current !== requestedScope || getActiveEmpresaId() !== tenant) return;
      if (!aggregateCsvIsSafe(await response.data.text())) throw new Error("Invalid aggregated CSV schema");
      const url = URL.createObjectURL(response.data); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "funil-comercial-agregado.csv"; anchor.click(); URL.revokeObjectURL(url);
    } catch (caught) { if (!controller.signal.aborted && scopeRef.current === requestedScope) setExportError(funnelError(caught)); }
    finally { if (!controller.signal.aborted && scopeRef.current === requestedScope) setExporting(false); }
  };
  const applyFilters = event => { event.preventDefault(); const next = { ...draft }; delete next.observed_at; setFilters(next); setRefresh(value => value + 1); };
  const maxReached = Math.max(1, ...(data?.stages || []).map(stage => Number(stage.reached_leads || 0)));
  const maxSeries = Math.max(1, ...(data?.series || []).map(row => Number(row.leads || 0)));
  const options = data?.options || {};
  const statusOptions = Object.entries(FUNNEL_STATUS).map(([key, label]) => ({ key, label }));
  return <div className="ux05-analytics funnel-dashboard" data-testid="funnel-dashboard">
    <PageHeader className="funnel-heading" eyebrow="Inteligência comercial · evidências persistidas" title="Funil e gargalos" icon={BarChart3} description="Contatos únicos por coorte; nenhum clique é contado como lead. Disponível sem JEV." actions={<><button type="button" className="funnel-btn" disabled={loading} onClick={() => { const next = { ...filters }; delete next.observed_at; setFilters(next); setRefresh(value => value + 1); }}><RefreshCw size={14} className="inline mr-1" />Atualizar dados</button><button type="button" className="funnel-btn" disabled={!data || exporting} onClick={exportCsv}><Download size={14} className="inline mr-1" />{exporting ? "Exportando..." : "Exportar agregado CSV"}</button></>} />
    <form className="funnel-panel ux05-funnel-filter-panel" onSubmit={applyFilters}><h2>Coorte e filtros</h2><div className="funnel-filters">
      <label>Entrada a partir de (UTC)<input type="datetime-local" aria-label="Entrada a partir de (UTC)" value={inputUtcTime(draft.start)} required onChange={event => setDraft(current => ({ ...current, start: utcInputValue(event.target.value) }))} /></label>
      <label>Entrada antes de (UTC)<input type="datetime-local" aria-label="Entrada antes de (UTC)" value={inputUtcTime(draft.end)} required onChange={event => setDraft(current => ({ ...current, end: utcInputValue(event.target.value) }))} /></label>
      <label>Inatividade em horas<input type="number" aria-label="Inatividade em horas" value={draft.inactivity_hours} min="1" max="8760" required onChange={event => setDraft(current => ({ ...current, inactivity_hours: Number(event.target.value) }))} /></label>
      <label>Janela de retorno em dias<input type="number" aria-label="Janela de retorno em dias" value={draft.return_window_days} min="1" max="90" required onChange={event => setDraft(current => ({ ...current, return_window_days: Number(event.target.value) }))} /></label>
      <SelectFilter label="Etapa comercial" field="stage" filters={draft} setFilters={setDraft} options={options.stages || data?.stages || []} />
      <SelectFilter label="Origem comprovada" field="origin" filters={draft} setFilters={setDraft} options={options.origins || []} />
      <SelectFilter label="Campanha Ads identificada" field="campaign_id" filters={draft} setFilters={setDraft} options={options.campaigns || []} />
      <SelectFilter label="Anúncio identificado" field="ad_id" filters={draft} setFilters={setDraft} options={options.ads || []} />
      <SelectFilter label="Estado do contato" field="status" filters={draft} setFilters={setDraft} options={statusOptions} />
    </div><div className="funnel-toolbar mt-4"><p className="funnel-muted">Início inclusivo, fim exclusivo; período máximo de 366 dias. Padrão: últimos 90 dias. Horizonte e horários em UTC.</p><button type="submit" className="funnel-btn funnel-primary">Aplicar filtros</button></div></form>
    {loading && <div role="status" className="funnel-panel">Consultando coorte e cobertura...</div>}
    {(error || exportError) && <div role="alert" className="funnel-alert">{error || exportError}</div>}
    {data && <>
      <div className="funnel-panel funnel-muted ux05-observation-window" data-testid="funnel-window">Entrada: {funnelTime(data.window?.start)} até {funnelTime(data.window?.end)} exclusivo · observação até {funnelTime(data.window?.observed_at)} UTC.<br />Unidade: contato CRM único no tenant. Base: {data.window?.entry_definition || "primeira conversa/inbound elegível em toda a história"}. Inatividade: {data.window?.inactivity_hours} h.</div>
      {data.summary?.total_leads === 0 && <div className="funnel-panel" role="status">Nenhum contato nesta coorte. Isso não comprova que as etapas não ocorreram em outros períodos.</div>}
      <div className="funnel-grid mb-5" data-testid="funnel-summary"><div className="funnel-metric"><span>Contatos únicos</span><strong data-testid="funnel-total">{funnelCount(data.summary?.total_leads)}</strong><button className="funnel-btn" onClick={() => openDrill("Todos os contatos")}>Ver contatos</button><p className="funnel-muted">{funnelCount(data.summary?.conversations)} conversas · {funnelCount(data.summary?.inbound_leads)} com inbound</p></div>{Object.entries(FUNNEL_STATUS).map(([key, label]) => <div className="funnel-metric" key={key}><span>{label}</span><strong data-testid={`funnel-state-${key}`}>{funnelCount(data.summary?.[key])}</strong><button className="funnel-btn" onClick={() => openDrill(label, { status: key })}>Ver {label.toLowerCase()}</button><p className="funnel-muted">Denominador: {funnelCount(data.summary?.total_leads)} contatos da coorte</p></div>)}</div>
      <section className="funnel-panel"><h2>Etapas atingidas por evidência</h2><p className="funnel-muted mb-4">Cada contato aparece uma vez por etapa. Saltos e retornos são permitidos; barras não precisam ser decrescentes. Snapshot CRM não comprova transições.</p><div data-testid="funnel-stages">{(data.stages || []).map(stage => <div key={stage.key} className="funnel-bar-row" data-stage-key={stage.key}><button className="funnel-link text-left" onClick={() => openDrill(stage.label, { stage: stage.key })}>{stage.label}</button><div className="funnel-track" role="img" aria-label={`${stage.label}: ${funnelCount(stage.reached_leads)} contatos atingiram; ${stage.state || "cobertura parcial"}`}><div className="funnel-bar" style={{ width: stage.reached_leads === null ? "0%" : `${Number(stage.reached_leads || 0) / maxReached * 100}%` }} /></div><div><strong>{funnelCount(stage.reached_leads)}</strong><p className="funnel-muted">{funnelRate(stage.rate)}</p><p className="funnel-muted">{stage.state === "unknown" || stage.reached_leads === null ? "Não instrumentado / desconhecido" : EVIDENCE_STATES[stage.state] || "Observado"}</p></div></div>)}</div>{!(data.stages || []).length && <p className="funnel-muted">Etapas comerciais não configuradas ou sem instrumentação identificada. Os demais indicadores continuam disponíveis.</p>}</section>
      <section className="funnel-panel"><h2>Transições observadas</h2><p className="funnel-muted mb-2">Somente evidências ordenadas. Contatos únicos e ocorrências são separados; retorno não representa uma nova aquisição.</p><div className="funnel-scroll" tabIndex={0} role="region" aria-label="Tabela do funil"><table><thead><tr><th>De → para</th><th>Tipo</th><th>Contatos</th><th>Ocorrências</th><th>Taxa · numerador / denominador</th></tr></thead><tbody>{(data.transitions || []).map((row, index) => <tr key={`${row.from_stage}:${row.to_stage}:${index}`}><td>{data.stages?.find(stage => stage.key === row.from_stage)?.label || row.from_stage} → {data.stages?.find(stage => stage.key === row.to_stage)?.label || row.to_stage}</td><td>{({ forward: "Avanço", skip: "Salto", return: "Retorno", backward: "Retorno" })[row.kind] || row.kind}</td><td>{funnelCount(row.lead_count)}</td><td>{funnelCount(row.occurrences)}</td><td>{funnelRate(row.rate)}</td></tr>)}</tbody></table></div>{!(data.transitions || []).length && <p className="funnel-muted mt-3">Nenhuma transição registrada nesta coorte. Histórico parcial não significa ausência de avanço.</p>}</section>
      <section className="funnel-panel"><h2>Gargalos mensuráveis</h2><p className="funnel-muted mb-4">Inatividade após etapa observável, sem avanço posterior registrado. Não indica motivo de abandono. Duração baseada em timestamps comprovados.</p><div className="funnel-grid">{(data.bottlenecks || []).map(row => <div className="funnel-metric" key={row.key}><h3>{row.title}</h3><strong>{funnelCount(row.count)}</strong><p className="funnel-muted">{funnelPercent(typeof row.rate === "object" ? row.rate?.value : row.rate)} · {funnelCount(row.count)} / {funnelCount(row.denominator)}</p><p className="funnel-muted">{durationLabel(row.duration_hours)}</p><p className="funnel-muted">{safeNotes(row.coverage).join(" · ")}</p><button type="button" className="funnel-btn mt-3" onClick={() => openDrill(row.title, { bottleneck: row.key })}>Investigar gargalo</button></div>)}</div>{!(data.bottlenecks || []).length && <p className="funnel-muted">Nenhum gargalo comprovado neste filtro. Consulte a cobertura de etapas e atividade.</p>}</section>
      {selection && <section className="funnel-panel" data-testid="funnel-drill"><div className="funnel-toolbar"><h2>Contatos · {selection.title}</h2><button className="funnel-btn" onClick={() => setSelection(null)}>Fechar contatos</button></div><p className="funnel-muted">Mesma coorte e horizonte do dashboard. Dados de identificação disponíveis somente nesta consulta autenticada.</p>{drillLoading && <p role="status">Consultando contatos...</p>}{drillError && <p role="alert" className="funnel-alert">{drillError}</p>}{rows && <><div className="funnel-scroll" tabIndex={0} role="region" aria-label="Contatos da coorte"><table><thead><tr><th>Contato</th><th>Entrada UTC</th><th>Última atividade UTC</th><th>Estado</th><th>Última etapa observada</th><th>Atribuição</th><th>Jornada</th></tr></thead><tbody>{(rows.items || []).map(item => { const path = journeyInboxPath(item.lead_id); return <tr key={item.lead_id} data-testid="funnel-lead-row"><td>{item.display_name || "Contato sem nome registrado"}</td><td>{funnelTime(item.entry_at)}</td><td>{funnelTime(item.last_activity_at)}</td><td><span className="funnel-status">{FUNNEL_STATUS[item.status] || "Indeterminado"}</span></td><td>{item.current_stage_label || "Etapa desconhecida"}</td><td>{EVIDENCE_STATES[typeof item.attribution === "string" ? item.attribution : item.attribution?.state || item.attribution?.status] || "Desconhecida"}</td><td>{path ? <Link className="funnel-link" to={path} state={{ funnelContext: { tenant, filters: observedFilters } }}>Abrir jornada A04</Link> : "Jornada indisponível"}</td></tr>; })}</tbody></table></div>{!rows.items?.length && <p className="funnel-muted mt-3">Nenhum contato corresponde a este filtro.</p>}<div className="funnel-toolbar mt-4"><span className="funnel-muted">{funnelCount(rows.total)} contatos · página {Math.floor(offset / 25) + 1} · {funnelCount(rows.items?.length)} nesta página</span><div className="flex gap-2"><button className="funnel-btn" disabled={offset === 0 || drillLoading} onClick={() => setOffset(value => Math.max(0, value - 25))}>Página anterior</button><button className="funnel-btn" disabled={offset + 25 >= Number(rows.total || 0) || drillLoading} onClick={() => setOffset(value => value + 25)}>Próxima página</button></div></div></>}</section>}
      <section className="funnel-panel"><h2>Evolução das coortes de entrada</h2><p className="funnel-muted">Estados observados até o mesmo horizonte; sem confundir mensagens com contatos.</p><div className="funnel-series">{(data.series || []).map(row => <div key={row.period} className="funnel-series-item"><strong>{funnelCount(row.leads)}</strong><div className="funnel-series-bar" style={{ height: `${Math.max(1, Number(row.leads || 0) / maxSeries * 85)}px` }} /><span>{row.period}</span></div>)}</div><details><summary>Ver números por período</summary><div className="funnel-scroll" tabIndex={0} role="region" aria-label="Contatos da coorte"><table><thead><tr><th>Período</th><th>Contatos</th><th>Ativos</th><th>Inativos</th><th>Convertidos</th><th>Indeterminados</th></tr></thead><tbody>{(data.series || []).map(row => <tr key={row.period}><td>{row.period}</td>{["leads", "active", "inactive", "converted", "indeterminate"].map(key => <td key={key}>{funnelCount(row[key])}</td>)}</tr>)}</tbody></table></div></details></section>
      <div className="funnel-grid"><section className="funnel-panel"><h2>Retorno após follow-up</h2><strong className="text-2xl">{funnelCount(data.followup?.returned_leads)} / {funnelCount(data.followup?.sent_leads)}</strong><p>{funnelPercent(typeof data.followup?.rate === "object" ? data.followup?.rate?.value : data.followup?.rate)} de retorno · janela {data.followup?.window_days} dias</p><p className="funnel-muted">Contatos únicos com envio comprovado e inbound posterior. Relação temporal, sem afirmar causalidade. {safeNotes(data.followup?.relation).join(" · ")}</p></section><section className="funnel-panel"><h2>Pagamentos e receita</h2><p data-testid="funnel-revenue">{funnelRevenue(data.summary?.revenue)}</p><p className="funnel-muted">Compra somente com pagamento real verificado. Tag de compra, checkout e valor CRM não comprovam receita.</p><p className="funnel-muted">Sinais de compra sem comprovação financeira: {funnelCount(data.coverage?.financial?.unverified_purchase_signal_leads)}.</p><p className="funnel-muted">Encerrados sem conversão comprovada: {funnelCount(data.summary?.closed_without_conversion)}. Encerramento não comprova perda.</p></section></div>
      <section className="funnel-panel"><h2>Cobertura de atribuição</h2><div className="funnel-grid"><div><strong>{funnelCount(data.attribution?.exact_leads)}</strong> com Ads identificados</div><div><strong>{funnelCount(data.attribution?.partial_leads)}</strong> parciais</div><div><strong>{funnelCount(data.attribution?.unknown_leads)}</strong> desconhecidos</div></div><p className="funnel-muted mt-3">Vínculo anterior à entrada: {funnelCount(data.attribution?.coverage?.before_entry_leads)} · posterior: {funnelCount(data.attribution?.coverage?.after_entry_leads)} · mesmo instante: {funnelCount(data.attribution?.coverage?.same_time_leads)}. Denominador: {funnelCount(data.attribution?.denominator)} · cobertura: {funnelPercent(typeof data.attribution?.coverage === "object" ? data.attribution.coverage.value : data.attribution?.coverage)}. Vínculos persistidos; múltiplos anúncios não formam segmentos aditivos e não comprovam causalidade da entrada.</p></section>
      <section className="funnel-panel"><h2>Snapshot CRM atual</h2><p className="funnel-muted mb-2">Distribuição atual, separada do histórico de evidências; não comprova quando ocorreu uma mudança.</p><div className="funnel-grid">{(data.crm_snapshot || []).map(row => <div key={row.key}><strong>{funnelCount(row.current_leads)}</strong> · {row.label}</div>)}</div></section>
      <section className="funnel-panel"><h2>Ads Insights · comparação separada</h2><p className="funnel-muted">Estado: {EVIDENCE_STATES[data.ads_comparison?.state] || "Desconhecido"} · nível: {({ ad: "anúncio", adset: "conjunto de anúncios", campaign: "campanha" })[data.ads_comparison?.level] || "não identificado"}. Cliques e conversas Meta não são contatos locais. Sem ROAS quando a receita não é comprovada.</p><div className="funnel-scroll" tabIndex={0} role="region" aria-label="Contatos da coorte"><table><thead><tr><th>Identificação</th><th>Conta / moeda</th><th>Janela</th><th>Gasto</th><th>Impressões</th><th>Cliques</th><th>Conversas Meta</th><th>Atribuição Meta</th></tr></thead><tbody>{(data.ads_comparison?.rows || []).map((row, index) => <tr key={index}><td>{row.ad_name || row.campaign_name || row.ad_id || row.campaign_id || "Não identificado"}</td><td>{row.account_id || row.ad_account_id || "Conta não identificada"} / {row.account_currency || row.currency || "desconhecida"}</td><td>{row.date_start || row.period_start || row.start || "?"} → {row.date_stop || row.date_end || row.period_end || row.end || "?"}</td><td>{funnelCount(row.spend)}</td><td>{funnelCount(row.impressions)}</td><td>{funnelCount(row.clicks)}</td><td>{funnelCount(row.messaging_conversations)}</td><td>{typeof row.attribution_setting === "string" ? row.attribution_setting : "Não registrado"}</td></tr>)}</tbody></table></div>{!data.ads_comparison?.rows?.length && <p className="funnel-muted mt-3">Sem Ads Insights identificados e compatíveis com esta janela.</p>}{safeNotes(data.ads_comparison?.limitations).map(note => <p key={note} className="funnel-muted">{note}</p>)}</section>
      <section className="funnel-panel" data-testid="funnel-coverage"><h2>Cobertura e limites das evidências</h2>{["entry", "stage_history", "financial", "loss", "prompt_exposure"].map(key => <details key={key} className="mb-3"><summary>{({ entry: "Entrada da coorte", stage_history: "Histórico de etapas", financial: "Pagamento e receita", loss: "Perdas confirmadas", prompt_exposure: "Exposição de prompt" })[key]}</summary><div className="funnel-evidence">{safeNotes(data.coverage?.[key]).length ? safeNotes(data.coverage?.[key]).map(note => <p key={note}>{note}</p>) : "Sem cobertura adicional registrada nesta fonte."}</div></details>)}{safeNotes(data.coverage?.limitations).map(note => <p className="funnel-muted" key={note}>{note}</p>)}</section>
    </>}
    <p className="funnel-muted">Consulta somente leitura. Exportação contém somente agregados, sem nomes, telefones ou IDs de contatos. <Link className="funnel-link" to="/painel">Voltar ao Dashboard operacional</Link></p>
  </div>;
}
