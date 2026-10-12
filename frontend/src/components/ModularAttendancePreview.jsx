import { useEffect, useRef, useState } from "react";
import { FlaskConical } from "lucide-react";
import api from "../services/api";
import { getActiveEmpresaId, getStoredToken } from "../utils/auth";
import { buildPreviewRequest, canPreviewProvider, metricValue, modularError, moduleVersion, previewClassification, previewModules, previewResponse, previewScopeCurrent, previewStage, previewTopics } from "../utils/modularAttendance";
import { ActionButton, InfoBanner, LoadingState, SectionCard, StatusBadge } from "./ui";

export default function ModularAttendancePreview() {
  const [scope, setScope] = useState(() => ({ tenant: getActiveEmpresaId(), token: getStoredToken(), generation: 0 }));
  useEffect(() => {
    const refresh = () => setScope(old => {
      const tenant = getActiveEmpresaId(), token = getStoredToken();
      return tenant === old.tenant && token === old.token ? old : { tenant, token, generation: old.generation + 1 };
    });
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.removeEventListener("storage", refresh); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, []);
  return <PreviewEditor key={`${scope.tenant}:${scope.generation}`} scope={scope} />;
}

function PreviewEditor({ scope }) {
  const [status, setStatus] = useState(null), [examples, setExamples] = useState([]), [loading, setLoading] = useState(true);
  const [source, setSource] = useState("example"), [exampleId, setExampleId] = useState("");
  const [message, setMessage] = useState(""), [contextMode, setContextMode] = useState("incomplete"), [channel, setChannel] = useState("whatsapp");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [result, setResult] = useState(null), [compare, setCompare] = useState(true), [reload, setReload] = useState(0);
  const busyRef = useRef(false), controllerRef = useRef(null), mounted = useRef(false), errorRef = useRef(null), resultRef = useRef(null);
  const current = signal => previewScopeCurrent(scope, getActiveEmpresaId(), getStoredToken(), signal) && mounted.current;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controllerRef.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const load = async () => {
      try {
        if (!scope.tenant || !scope.token) throw new Error("NO_SESSION");
        const base = `/empresas/${scope.tenant}/atendimento-modular`;
        const [info, catalog] = await Promise.all([api.get(base + "/status", { signal: controller.signal, timeout: 10000 }), api.get(base + "/examples", { signal: controller.signal, timeout: 10000 })]);
        if (!active || !previewScopeCurrent(scope, getActiveEmpresaId(), getStoredToken(), controller.signal)) return;
        if (info.data?.schema_version !== "a07.v1" || !Array.isArray(catalog.data?.items)) throw new Error("INVALID_RESPONSE");
        const items = catalog.data.items.filter(item => typeof item.id === "string" && /^[a-z0-9_]{1,64}$/.test(item.id) && typeof item.title === "string" && typeof item.message === "string").slice(0, 30);
        setStatus(info.data); setExamples(items); setExampleId(items[0]?.id || ""); setError("");
      } catch (failure) {
        if (active && previewScopeCurrent(scope, getActiveEmpresaId(), getStoredToken(), controller.signal)) setError(modularError(failure));
      } finally { if (active && !controller.signal.aborted) setLoading(false); }
    };
    void load();
    return () => { active = false; controller.abort(); };
  }, [scope, reload]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (result) resultRef.current?.focus({ preventScroll: true }); }, [result]);
  const simulate = async (useJev = false) => {
    if (busyRef.current || !current()) return;
    busyRef.current = true; setBusy(true); setError(""); setResult(null);
    const controller = new AbortController(); controllerRef.current = controller;
    try {
      const body = buildPreviewRequest({ source, exampleId, message, contextMode, channel, useJev }, status, examples);
      const response = await api.post(`/empresas/${scope.tenant}/atendimento-modular/preview`, body, { signal: controller.signal, timeout: 15000 });
      if (current(controller.signal)) setResult(previewResponse(response.data));
    } catch (failure) { if (current(controller.signal)) setError(modularError(failure)); }
    finally { if (current(controller.signal)) { busyRef.current = false; setBusy(false); } }
  };
  const chosen = examples.find(item => item.id === exampleId);
  const modules = previewModules(result?.selected_modules);
  const metrics = result?.metrics || {};
  return <SectionCard title="Pré-visualização do atendimento modular" description="Combine assuntos em uma resposta simulada e compare com o legado." icon={FlaskConical} className="a07-preview">
    <InfoBanner>Simulação local com fatos sintéticos. Preços, garantias e disponibilidade deste cenário não representam a oferta real da empresa. Nenhuma mensagem ou ação comercial é enviada.</InfoBanner>
    {loading ? <LoadingState label="Carregando simulação" /> : null}
    {error ? <div ref={errorRef} role="alert" tabIndex={-1} className="fm-banner fm-tone-danger mt-4"><div>{error}</div></div> : null}
    {!loading && !status ? <ActionButton type="button" variant="secondary" className="mt-3" onClick={() => { setLoading(true); setError(""); setReload(old => old + 1); }}>Tentar carregar novamente</ActionButton> : null}
    {status ? <>
      <div className="mt-4 flex flex-wrap gap-2"><StatusBadge tone="info">Produção: {status.mode === "MODULAR" ? "MODULAR" : "LEGACY"}</StatusBadge><StatusBadge>Composição {moduleVersion(status.composition_version)}</StatusBadge><StatusBadge>{status.provider?.jev_configured ? "JEV configurado" : "JEV não configurado"}</StatusBadge></div>
      <p className="fm-helper mt-2">Modo de produção somente leitura. A pré-visualização não ativa atendimento modular nem altera autorizações.</p>
      <details className="mt-3 text-sm"><summary className="cursor-pointer font-medium">Catálogo de módulos versionados</summary><ul className="mt-2 grid gap-2 sm:grid-cols-2">{previewModules(status.modules).map(item => <li key={item.id}>{item.title} · {item.version}</li>)}</ul></details>
      <form className="mt-4 grid min-w-0 gap-4 md:grid-cols-2" onSubmit={event => { event.preventDefault(); void simulate(); }} aria-busy={busy}>
        <div><label htmlFor="a07-source" className="mb-1 block text-sm font-medium">Entrada da simulação</label><select id="a07-source" className="fm-field w-full" value={source} disabled={busy} onChange={event => { setSource(event.target.value); setResult(null); }}><option value="example">Exemplo sintético</option><option value="custom">Mensagem personalizada local</option></select></div>
        <div><label htmlFor="a07-channel" className="mb-1 block text-sm font-medium">Canal do cenário</label><select id="a07-channel" className="fm-field w-full" value={channel} disabled={busy} onChange={event => { setChannel(event.target.value); setResult(null); }}><option value="whatsapp">WhatsApp</option><option value="instagram">Instagram</option><option value="simulator">Simulador</option></select></div>
        {source === "example" ? <div className="md:col-span-2"><label htmlFor="a07-example" className="mb-1 block text-sm font-medium">Exemplo</label><select id="a07-example" className="fm-field w-full" value={exampleId} disabled={busy} onChange={event => { setExampleId(event.target.value); setResult(null); }} required><option value="" disabled>Selecione</option>{examples.map(item => <option key={item.id} value={item.id}>{item.title.slice(0, 100)}</option>)}</select><p className="fm-helper mt-2 break-words">{chosen?.message?.slice(0, 2000)}</p></div> : <div className="md:col-span-2"><label htmlFor="a07-message" className="mb-1 block text-sm font-medium">Mensagem personalizada</label><textarea id="a07-message" className="fm-field w-full resize-y" rows={3} maxLength={2000} required value={message} disabled={busy} onChange={event => { setMessage(event.target.value); setResult(null); }} placeholder="Ex.: quanto custa, tem garantia e quando começa?" /><p className="fm-helper mt-1">Somente simulação local. Não use dados pessoais ou conversas reais.</p></div>}
        <div><label htmlFor="a07-context" className="mb-1 block text-sm font-medium">Contexto sintético</label><select id="a07-context" className="fm-field w-full" value={contextMode} disabled={busy} onChange={event => { setContextMode(event.target.value); setResult(null); }}><option value="complete">Completo</option><option value="incomplete">Incompleto</option><option value="conflicting">Conflitante</option></select></div>
        <div className="flex items-center"><label className="flex gap-2 text-sm"><input type="checkbox" checked={compare} onChange={event => setCompare(event.target.checked)} /> Comparar resposta legada</label></div>
        <div className="flex flex-wrap gap-2 md:col-span-2"><ActionButton type="submit" disabled={busy || (source === "example" ? !exampleId : !message.trim())}>{busy ? "Simulando..." : "Simular resposta"}</ActionButton>{source === "example" && canPreviewProvider(status, exampleId, examples) ? <ActionButton type="button" variant="secondary" disabled={busy} onClick={() => void simulate(true)}>Consultar JEV com este exemplo sintético</ActionButton> : null}</div>
      </form>
      {busy ? <div role="status" aria-live="polite" className="fm-helper mt-3">Simulação em andamento. Aguarde o resultado.</div> : null}
      {result ? <div ref={resultRef} tabIndex={-1} role="region" aria-label="Resultado da simulação modular" className="mt-5 min-w-0 space-y-4">
        <h3 className="font-semibold">Resultado da simulação</h3>
        <p className="fm-helper">Etapa principal: {previewStage(result.primary_stage)}</p>
        <p className="fm-helper">Assuntos simultâneos: {previewTopics(result.active_topics).join(", ") || "Não identificados"}</p>
        <div aria-label="Módulos selecionados" className="flex flex-wrap gap-2">{modules.length ? modules.map(item => <StatusBadge key={item.id} tone="info">{item.title} · {item.version}</StatusBadge>) : <p className="fm-helper">Sem módulos selecionados; resposta segura ou legado.</p>}</div>
        <p className="fm-helper">{previewClassification(result.jev)}</p>
        {result.fallback_reason ? <InfoBanner tone="warning">Fallback seguro para o legado. Motivo: {moduleVersion(result.fallback_reason)}</InfoBanner> : null}
        {result.guard?.allowed === false ? <InfoBanner tone="warning">A política de segurança limitou a resposta simulada.</InfoBanner> : null}
        {result.conflicts?.length ? <InfoBanner tone="warning">O cenário contém fatos conflitantes. Confirme as condições antes de responder.</InfoBanner> : null}
        {result.missing_facts?.length ? <InfoBanner tone="warning">Faltam fatos comerciais no cenário. A resposta pede esclarecimento.</InfoBanner> : null}
        <div className={`grid min-w-0 gap-4 ${compare ? "lg:grid-cols-2" : ""}`}><div className="min-w-0 rounded-lg border p-4"><h4 className="font-semibold">Resposta modular simulada</h4><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{result.response_text}</p></div>{compare ? <div className="min-w-0 rounded-lg border p-4"><h4 className="font-semibold">Resposta legada simulada</h4><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{result.legacy_response_text}</p></div> : null}</div>
        <div className="fm-table-scroll" role="region" aria-label="Métricas da simulação" tabIndex={0}><table className="fm-table"><thead><tr><th>Métrica</th><th>Legado</th><th>Modular</th></tr></thead><tbody><tr><th>Estimativa de tokens de entrada</th><td>{metricValue(metrics.legacy?.estimated_input_tokens)}</td><td>{metricValue(metrics.modular?.estimated_input_tokens)}</td></tr><tr><th>Tempo local observado</th><td>{metricValue(metrics.legacy?.latency_ms, " ms")}</td><td>{metricValue(metrics.modular?.latency_ms, " ms")}</td></tr><tr><th>Tokens de entrada do provider</th><td>{metricValue(metrics.legacy?.input_tokens)}</td><td>{metricValue(metrics.modular?.input_tokens)}</td></tr><tr><th>Tokens de saída do provider</th><td>{metricValue(metrics.legacy?.output_tokens)}</td><td>{metricValue(metrics.modular?.output_tokens)}</td></tr><tr><th>Custo observado (USD)</th><td>{metricValue(metrics.legacy?.cost_usd)}</td><td>{metricValue(metrics.modular?.cost_usd)}</td></tr></tbody></table></div>
        <p className="fm-helper">Estimativas por caracteres e latência do renderer local. Não demonstram economia, custo ou qualidade de um provider real.</p>
        <p className="fm-helper break-all">Referência: {moduleVersion(result.run_id)} · Composição {moduleVersion(result.composition_version)} · {result.trace?.persisted === true ? "Trilha sanitizada registrada" : "Trilha não persistida"}</p>
      </div> : null}
    </> : null}
  </SectionCard>;
}
