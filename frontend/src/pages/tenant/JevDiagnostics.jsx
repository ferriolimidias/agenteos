import "../../styles/ux05-tools.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Brain, RefreshCw } from "lucide-react";
import api from "../../services/api";
import { PageHeader, StatusBadge } from "../../components/ui";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import { journeyInboxPath } from "../../utils/commercialFunnel";
import { JEV_INTENTS, JEV_STATUSES, jevConfidence, jevError, jevLabel, jevLeadId, jevMetric, jevNumber, jevParams, jevScopeIsCurrent, jevSignal, jevTime } from "../../utils/jevDiagnostics";
import "./JevDiagnostics.css";

function Decisions({ title, items }) {
  return <section className="ux05-jev-decisions"><h3>{title}</h3>{items?.length ? <ul>{items.map((item, index) => <li key={`${item.key}:${index}`}><strong>{jevLabel(item)}</strong> · {jevConfidence(item.confidence)}<small> Evidências: {(item.evidence_ids || []).join(", ") || "não fornecidas"}</small></li>)}</ul> : <p className="jev-muted">Nenhuma classificação sustentada neste grupo.</p>}</section>;
}
function RunDetail({ run, onClose }) {
  const classification = ["demo", "completed"].includes(run.status) ? run.classification : null;
  const path = journeyInboxPath(run.lead_id);
  return <section className="jev-card" aria-label="Resultado do diagnóstico" data-testid="jev-detail">
    <div className="jev-toolbar"><h2>Resultado SHADOW</h2><button type="button" onClick={onClose}>Fechar resultado</button></div>
    <p><span className="jev-badge">{JEV_STATUSES[run.status] || "Estado desconhecido"}</span> · {run.source === "synthetic" ? run.status === "demo" ? "Exemplo sintético; demonstração lexical local, sem chamada externa" : "Exemplo sintético; solicitação explícita ao provider" : "Contexto de conversa existente"}</p>
    <p className="jev-muted">{jevTime(run.created_at)}{run.deduplicated ? " · Resultado reutilizado para o mesmo contexto" : ""}</p>
    {run.status === "demo" && <p className="jev-notice">Esta demonstração usa regras lexicais locais com exemplos sintéticos. Ela não valida o provider real nem mede precisão de classificação.</p>}
    {run.ledger_persisted === false && <p className="jev-notice">Resultado local não persistido: ledger indisponível. Esta execução não integra a cobertura retida.</p>}
    {!classification && <p className="jev-notice">Não avaliado. Nenhuma classificação foi inventada para substituir a análise indisponível.</p>}
    {run.error_code && <p className="jev-muted">Código de diagnóstico: {run.error_code}</p>}
    {classification && <>
      <div className="jev-stage"><h3>Etapa comercial principal</h3><strong>{classification.primary_stage?.abstained ? "Indeterminada (abstenção)" : jevLabel(classification.primary_stage)}</strong> · {jevConfidence(classification.primary_stage?.confidence)}<p>Assuntos ativos simultâneos: {(classification.active_topics || []).map(jevLabel).join(" · ") || "Não determinados"}</p></div>
      <div className="jev-columns"><Decisions title="Intenções simultâneas" items={classification.intents} /><Decisions title="Objeções" items={classification.objections} /></div>
      <h3>Sinais da conversa</h3><div className="jev-chips">{["interest", "readiness", "human_request", "risk", "unanswered_questions", "interruption_hypothesis"].map(key => <span key={key}>{jevLabel(key)}: {jevSignal(classification.signals?.[key])}</span>)}</div>
      <p className="jev-muted">Dúvida sem resposta e motivo de interrupção permanecem desconhecidos quando o contexto limitado não os comprova.</p>
      <p className={classification.abstention?.abstained ? "jev-notice" : "jev-muted"}>Abstenção: {classification.abstention?.abstained ? "Sim" : "Não registrada"}{classification.abstention?.reason ? ` · ${classification.abstention.reason}` : ""}</p>
      <h3>Evidências limitadas</h3>{classification.evidence?.length ? <ul className="jev-evidence">{classification.evidence.map((item, index) => <li key={`${item.message_id}:${index}`}><small>Mensagem {item.message_id}</small><blockquote>{item.excerpt || "Trecho não disponibilizado"}</blockquote></li>)}</ul> : <p className="jev-muted">Nenhuma evidência disponibilizada.</p>}
      <h3>Sugestões de tags existentes · hipotéticas</h3><p className="jev-muted">Tags do tenant. Nenhuma tag é aplicada nesta fase.</p>{classification.tag_suggestions?.length ? <ul>{classification.tag_suggestions.map((tag, index) => <li key={`${tag.tag_id}:${index}`}><strong>{tag.tag_name}</strong> · {tag.reason} <span className="jev-badge">Hipotética</span></li>)}</ul> : <p className="jev-muted">Sem correspondência sustentada com tags existentes.</p>}
    </>}
    <div className="jev-chips"><span>Latência: {jevMetric(run, "latency_ms", " ms")}</span><span>Custo: {jevMetric(run, "cost_usd", " USD")}</span><span>Tokens de entrada: {jevMetric(run, "input_tokens")}</span></div>
    {path && <Link to={path}>Abrir jornada A04 no Inbox</Link>}
  </section>;
}

function TenantDiagnostics({ tenant, initialLead, isAdminView = false }) {
  const base = `/empresas/${tenant}/jev`;
  const [status, setStatus] = useState(null);
  const [examples, setExamples] = useState([]);
  const [exampleId, setExampleId] = useState("");
  const [useProvider, setUseProvider] = useState(false);
  const [leadId, setLeadId] = useState(initialLead || "");
  const [filters, setFilters] = useState({ status: "", intent: "" });
  const [runs, setRuns] = useState(null);
  const [run, setRun] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [listRefresh, setListRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [actionError, setActionError] = useState("");
  const actionController = useRef(null);
  const current = useCallback(controller => {
    const activeTenant = isAdminView ? tenant : getActiveEmpresaId();
    return jevScopeIsCurrent(tenant, activeTenant, controller.signal) && (isAdminView || getStoredUser()?.role !== "meta_app_reviewer");
  }, [tenant, isAdminView]);
  useEffect(() => () => actionController.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setStatus(null); setExamples([]); setExampleId("");
    if (!current(controller)) { setLoading(false); return () => controller.abort(); }
    Promise.allSettled([api.get(`${base}/status`, { signal: controller.signal }), api.get(`${base}/examples`, { signal: controller.signal })]).then(results => {
      if (!current(controller)) return;
      if (results[0].status === "fulfilled") setStatus(results[0].value.data);
      else setError(jevError(results[0].reason));
      if (results[1].status === "fulfilled") { const items = results[1].value.data.items || []; setExamples(items); setExampleId(items[0]?.id || ""); }
      else setError(jevError(results[1].reason));
      setLoading(false);
    });
    return () => controller.abort();
  }, [base, current, refresh]);
  useEffect(() => {
    const controller = new AbortController(); setRuns(null); setListError("");
    if (!current(controller)) return () => controller.abort();
    api.get(`${base}/runs`, { params: jevParams(filters), signal: controller.signal }).then(response => {
      if (current(controller)) setRuns(response.data);
    }).catch(caught => { if (current(controller)) setListError(jevError(caught)); });
    return () => controller.abort();
  }, [base, current, filters, listRefresh]);
  useEffect(() => { actionController.current?.abort(); setRun(null); setBusy(false); setActionError(""); }, [filters]);
  async function requestRun(body, id) {
    actionController.current?.abort();
    const controller = new AbortController(); actionController.current = controller;
    if (!current(controller)) return;
    setRun(null); setActionError(""); setBusy(true);
    try {
      const response = id ? await api.get(`${base}/runs/${encodeURIComponent(id)}`, { signal: controller.signal }) : await api.post(`${base}/analyze`, body, { signal: controller.signal });
      if (!current(controller)) return;
      setRun(response.data); setListRefresh(value => value + 1);
      if (!id) setRefresh(value => value + 1);
    } catch (caught) { if (current(controller)) setActionError(jevError(caught)); }
    finally { if (current(controller)) setBusy(false); }
  }
  const example = examples.find(item => item.id === exampleId);
  const allowed = Boolean(status?.enabled);
  return <div className="ux05-tools ux05-jev jev-diagnostics" data-testid="jev-diagnostics">
    <PageHeader className="jev-heading" eyebrow="JEV Core · A06" title={<>Inteligência conversacional <StatusBadge tone="info">SHADOW</StatusBadge></>} icon={Brain} description="Diagnóstico com evidências. Atendimento, respostas, funil e tags permanecem sob os fluxos existentes." actions={<button type="button" disabled={loading} onClick={() => { setRefresh(value => value + 1); setListRefresh(value => value + 1); }}><RefreshCw size={15} /> Atualizar diagnóstico</button>} />
    <p className="jev-links"><Link to="/painel/funil">Abrir funil e gargalos A05</Link>{jevLeadId(leadId) && <Link to={journeyInboxPath(leadId.trim())}>Abrir jornada A04 no Inbox</Link>}</p>
    {loading && <p role="status" className="ux05-tools-state">Consultando configuração do tenant...</p>}{error && <p role="alert" className="jev-notice">{error}</p>}
    {status && <section className="jev-card" aria-label="Disponibilidade e cobertura">
      <h2>Disponibilidade e cobertura</h2><div className="jev-metrics">
        <div><small>Diagnóstico</small><strong>{status.enabled ? "SHADOW disponível" : "Desativado"}</strong></div>
        <div><small>Provider externo</small><strong>{status.provider?.configured && status.provider?.enabled ? "Configurado e habilitado" : "Não habilitado"}</strong><small>Disponibilidade: {status.provider?.availability || "Desconhecida"}</small></div>
        <div><small>Autorização de dados reais</small><strong>{status.provider?.authorized_real ? "Configurada no servidor" : "Ausente"}</strong></div>
        <div><small>Ledger</small><strong>{status.ledger?.available ? "Disponível" : "Indisponível"}</strong><small>Retenção: {jevNumber(status.ledger?.retention_days, " dias")}</small></div>
      </div><p>Execuções solicitadas: {jevNumber(status.coverage?.requested)} · avaliadas: {jevNumber(status.coverage?.evaluated)} · abstenções: {jevNumber(status.coverage?.abstained)} · erros: {jevNumber(status.coverage?.errors)}</p>
      <p className="jev-muted">Denominador: execuções retidas no ledger ({status.coverage?.denominator || "desconhecido"}); não representa a cobertura de todas as conversas do tenant. Demonstrações sintéticas não comprovam avaliação pelo provider.</p>
      <p className="jev-muted">Demonstrações locais: {jevNumber(status.coverage?.synthetic_demos)} · conversas reais solicitadas/avaliadas: {jevNumber(status.coverage?.real_requested)}/{jevNumber(status.coverage?.real_evaluated)}</p>
      <div className="jev-chips"><span>Orçamento diário: {jevNumber(status.budget?.daily_usd, " USD")}</span><span>Reservado: {jevNumber(status.budget?.reserved_usd, " USD")}</span><span>Custo medido: {jevNumber(status.budget?.measured_usd, " USD")}</span></div>
      <p className="jev-muted">Precisão real, p50/p95 e custo por conversa: desconhecidos enquanto não houver amostra suficiente e medição comprovada.</p>
      {status.limitations?.length > 0 && <details><summary>Limitações observadas</summary><ul>{status.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
    </section>}
    <div className="jev-columns ux05-jev-requests">
      <section className="jev-card"><h2>Exemplos sintéticos PT-BR</h2><p className="jev-muted">Demonstração local independente. Escolha um exemplo e solicite explicitamente a análise.</p><label>Exemplo<select aria-label="Exemplo" value={exampleId} onChange={event => setExampleId(event.target.value)} disabled={!examples.length || busy}>{examples.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        {example && <div className="jev-example" tabIndex={0} role="region" aria-label="Mensagens do exemplo sintético">{example.messages.map(message => <p key={message.id}>{message.text}</p>)}</div>}
        {status?.provider?.configured && status?.provider?.enabled && <label className="jev-checkbox"><input type="checkbox" checked={useProvider} onChange={event => setUseProvider(event.target.checked)} disabled={busy} />Usar provider externo somente neste exemplo sintético (consome orçamento)</label>}
        <button type="button" className="jev-primary" disabled={!allowed || !exampleId || busy} onClick={() => requestRun({ example_id: exampleId, ...(useProvider && status?.provider?.configured && status?.provider?.enabled ? { use_provider: true } : {}) })}>Analisar exemplo sintético</button>
      </section>
      <section className="jev-card"><h2>Diagnóstico de conversa existente</h2><p className="jev-muted">Somente leitura de contexto limitado. O servidor exige configuração e autorização por tenant antes de qualquer envio externo; este formulário não habilita o provider.</p><label>ID do contato (UUID)<input value={leadId} onChange={event => setLeadId(event.target.value)} placeholder="ID do contato deste tenant" autoComplete="off" /></label><button type="button" disabled={!allowed || !jevLeadId(leadId) || busy} onClick={() => requestRun({ lead_id: jevLeadId(leadId) })}>Solicitar diagnóstico SHADOW</button><p className="jev-muted">Sem provider ou autorização, o resultado permanece não avaliado.</p></section>
    </div>
    {busy && <p role="status" className="ux05-tools-state">Consultando análise SHADOW...</p>}{actionError && <p role="alert" className="jev-notice">{actionError}</p>}
    {run && <RunDetail run={run} onClose={() => setRun(null)} />}
    <section className="jev-card" aria-label="Execuções de diagnóstico"><h2>Execuções retidas</h2><div className="jev-filters"><label>Estado<select aria-label="Estado" value={filters.status} onChange={event => setFilters(previous => ({ ...previous, status: event.target.value }))}><option value="">Todos os estados</option>{Object.entries(JEV_STATUSES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>Intenção<select aria-label="Intenção" value={filters.intent} onChange={event => setFilters(previous => ({ ...previous, intent: event.target.value }))}><option value="">Todas as intenções</option>{Object.entries(JEV_INTENTS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      {listError && <p role="alert" className="jev-notice">{listError}</p>}{!runs && !listError && <p role="status">Consultando execuções...</p>}{runs && <><p className="jev-muted">{jevNumber(runs.total)} execuções neste filtro · até {jevNumber(runs.limit)} resultados</p>{!runs.items?.length ? <p>Nenhuma execução retida neste filtro. Isso não comprova ausência de intenções.</p> : <div className="jev-scroll" tabIndex={0} role="region" aria-label="Execuções retidas"><table><thead><tr><th>Horário UTC</th><th>Fonte</th><th>Estado</th><th>Intenções</th><th>Resultado</th></tr></thead><tbody>{runs.items.map(item => <tr key={item.id}><td>{jevTime(item.created_at)}</td><td>{item.source === "synthetic" ? "Sintética" : "Conversa"}</td><td>{JEV_STATUSES[item.status] || "Desconhecido"}</td><td>{(item.classification?.intents || []).map(jevLabel).join(" · ") || "Não avaliado"}</td><td><button type="button" disabled={busy} onClick={() => requestRun(null, item.id)}>Ver resultado</button></td></tr>)}</tbody></table></div>}</>}
    </section>
  </div>;
}
export default function JevDiagnostics() {
  const location = useLocation();
  const { empresaId: routeTenant } = useParams();
  const [session, setSession] = useState(() => ({ tenant: routeTenant || getActiveEmpresaId(), reviewer: !routeTenant && getStoredUser()?.role === "meta_app_reviewer" }));
  useEffect(() => {
    if (routeTenant) return undefined;
    const update = () => setSession({ tenant: getActiveEmpresaId(), reviewer: getStoredUser()?.role === "meta_app_reviewer" });
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, [routeTenant]);
  const tenant = routeTenant || session.tenant;
  const reviewer = !routeTenant && session.reviewer;
  if (reviewer) return <p role="alert">Diagnóstico não disponível para este perfil.</p>;
  if (!tenant) return <p role="alert">Selecione um tenant autenticado para consultar o diagnóstico.</p>;
  return <TenantDiagnostics key={tenant} tenant={tenant} isAdminView={Boolean(routeTenant)} initialLead={jevLeadId(new URLSearchParams(location.search).get("lead_id"))} />;
}
