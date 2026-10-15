import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ClipboardList, FlaskConical, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { ActionButton, ErrorState, InfoBanner, LoadingState, PageHeader, SectionCard, StatusBadge } from "../../components/ui";
import "../../styles/ux05-analytics.css";

const emptyCandidate = { module_key: "qualification", source_version_id: "", content: "", change_summary: "", hypothesis: "", primary_metric: "response_after_7d" };
const EMPTY_MODULES = Object.freeze([]);
const fmt = (value) => value == null ? "—" : new Intl.NumberFormat("pt-BR").format(value);
const percent = (value) => value == null ? "—" : `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const textError = (error) => error?.response?.data?.detail?.code || error?.response?.data?.detail || "Não foi possível concluir a operação.";

export default function PromptOptimizer() {
  const [tenant, setTenant] = useState(getActiveEmpresaId);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [candidate, setCandidate] = useState(emptyCandidate);
  const [experimentForm, setExperimentForm] = useState({ module_key: "qualification", control_version_id: "", candidate_version_id: "", candidate_percent: 50, sample_min_per_variant: 100, maturity_days: 7 });
  const [notice, setNotice] = useState("");
  const [decisionResults, setDecisionResults] = useState({});
  const [evaluatorModels, setEvaluatorModels] = useState([]);
  const [evaluatorModelId, setEvaluatorModelId] = useState("");
  const [useModelEvaluation, setUseModelEvaluation] = useState(false);
  const [suggestions, setSuggestions] = useState(null);

  const load = useCallback(async () => {
    if (!tenant) { setLoading(false); setError("Selecione um tenant autenticado."); return; }
    setLoading(true); setError("");
    try {
      const [dashboardResponse, modelResponse] = await Promise.all([
        api.get(`/empresas/${tenant}/prompt-optimizer`),
        api.get(`/empresas/${tenant}/prompt-optimizer/evaluator/models`).catch(() => ({ data: { items: [] } })),
      ]);
      if (getActiveEmpresaId() === tenant) {
        setData(dashboardResponse.data);
        setEvaluatorModels(Array.isArray(modelResponse.data?.items) ? modelResponse.data.items : []);
        setEvaluatorModelId((current) => current || dashboardResponse.data?.evaluator?.model_id || "");
      }
    } catch (caught) {
      if (getActiveEmpresaId() === tenant) setError(textError(caught));
    } finally { if (getActiveEmpresaId() === tenant) setLoading(false); }
  }, [tenant]);

  useEffect(() => {
    const update = () => setTenant(getActiveEmpresaId());
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  useEffect(() => { load(); }, [load]);

  const modules = data?.modules || EMPTY_MODULES;
  const selectedModule = useMemo(() => modules.find((item) => item.id === candidate.module_key), [modules, candidate.module_key]);
  const candidateSource = selectedModule?.versions?.find((item) => item.id === candidate.source_version_id);
  const experimentModule = useMemo(() => modules.find((item) => item.id === experimentForm.module_key), [modules, experimentForm.module_key]);
  const approvedVersions = (experimentModule?.versions || []).filter((item) => item.approved);
  const controlVersions = (experimentModule?.versions || []).filter((item) => item.id === experimentModule?.stable_version_id);

  async function action(key, fn, successMessage) {
    setBusy(key); setNotice(""); setError("");
    try { const response = await fn(); if (response?.data?.decision) setDecisionResults((current) => ({ ...current, [response.data.metrics?.experiment_id || "last"]: response.data })); setNotice(successMessage); await load(); }
    catch (caught) { setError(textError(caught)); }
    finally { setBusy(""); }
  }

  function chooseCandidateModule(moduleKey) {
    const row = modules.find((item) => item.id === moduleKey);
    const source = row?.versions?.[0];
    setCandidate({ ...emptyCandidate, module_key: moduleKey, source_version_id: source?.id || "", content: source?.content || "" });
  }

  function chooseExperimentModule(moduleKey) {
    const row = modules.find((item) => item.id === moduleKey);
    const approved = (row?.versions || []).filter((item) => item.approved);
    const stableId = row?.stable_version_id;
    setExperimentForm((current) => ({ ...current, module_key: moduleKey, control_version_id: approved.find((item) => item.id === stableId)?.id || approved[0]?.id || "", candidate_version_id: approved.find((item) => item.id !== stableId)?.id || "" }));
  }

  async function detectSuggestions() {
    setBusy("suggestions"); setError("");
    try { const response = await api.get(`/empresas/${tenant}/prompt-optimizer/suggestions`); setSuggestions(response.data); }
    catch (caught) { setError(textError(caught)); }
    finally { setBusy(""); }
  }

  if (loading && !data) return <main className="ux05-analytics p-4 md:p-6"><LoadingState label="Carregando Prompt Optimizer..." /></main>;

  return <main className="ux05-analytics mx-auto max-w-7xl space-y-5 p-4 md:p-6" data-testid="a11-prompt-optimizer">
    <PageHeader title="Prompt Optimizer · A11" description="Versionamento, avaliação offline e experimentos controlados por tenant." icon={Sparkles} actions={<ActionButton variant="secondary" disabled={Boolean(busy)} onClick={load}><RefreshCw size={15} />Atualizar</ActionButton>} />
    <InfoBanner tone="warning">
      <strong>Atendimento LIVE permanece desligado.</strong> Versões e avaliações offline não alteram o prompt ativo. Experimentos só recebem tráfego com flags explícitas, tenant piloto autorizado e uma conversa ligada. Esta interface não envia mensagens.
    </InfoBanner>
    {error && <ErrorState title="Não foi possível carregar ou salvar" description={String(error)} />}
    {notice && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}

    <SectionCard title="Estado de ativação" icon={ShieldCheck}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[["Exposição A/B", data?.live?.ab_live], ["Promoção automática", data?.live?.automatic_promotion_live], ["Rollback automático", data?.live?.automatic_rollback_live], ["Alteração automática", data?.live?.automatic_prompt_mutation]].map(([label, enabled]) => <div key={label} className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">{label}</p><StatusBadge tone={enabled ? "success" : "warning"}>{enabled ? "Habilitado" : "OFF"}</StatusBadge></div>)}
      </div>
      <p className="mt-3 text-sm text-slate-600">{data?.live?.reason || "Nenhum tenant está inscrito em piloto por padrão."}</p>
    </SectionCard>

    <SectionCard title="Módulos e versões imutáveis" icon={ClipboardList}>
      {!modules.some((item) => item.versions?.length) && <div className="space-y-3"><p className="text-sm text-slate-600">Inicialize snapshots das instruções de referência A07 para criar a primeira base comparável. Esta ação apenas grava versões OFFLINE para este tenant.</p><ActionButton disabled={Boolean(busy)} onClick={() => action("bootstrap", () => api.post(`/empresas/${tenant}/prompt-optimizer/bootstrap`), "Versões de referência inicializadas.")}>{busy === "bootstrap" ? "Inicializando..." : "Inicializar versões de referência"}</ActionButton></div>}
      <div className="grid gap-3 md:grid-cols-2">
        {modules.filter((item) => item.versions?.length).map((module) => <article key={module.id} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold text-slate-900">{module.title}</h3><p className="text-xs text-slate-500">{module.id}</p></div><StatusBadge tone="default">{module.state}</StatusBadge></div>
          <div className="mt-3 space-y-2">{module.versions.map((version) => <details key={version.id} className="rounded-lg bg-slate-50 p-3"><summary className="cursor-pointer text-sm font-medium">v{version.version} · {version.approved ? "Aprovada" : "Rascunho"} · SHA {version.content_hash.slice(0, 10)}</summary><p className="mt-2 text-sm text-slate-700">{version.change_summary}</p><p className="mt-1 text-xs text-slate-500">Hipótese: {version.hypothesis}</p><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-xs">{version.content}</pre><div className="mt-2 flex flex-wrap gap-2"><ActionButton variant="secondary" disabled={Boolean(busy)} onClick={() => { if (useModelEvaluation && !window.confirm("Executar nove casos sintéticos no modelo avaliador configurado? A chamada pode gerar custo e não usará dados de clientes.")) return; action(`eval-${version.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/versions/${version.id}/evaluate`, { use_model: useModelEvaluation }), useModelEvaluation ? "Casos sintéticos com modelo registrados; sem exposição A/B." : "Avaliação offline determinística registrada; sem custo de modelo e sem exposição A/B."); }}>{busy === `eval-${version.id}` ? "Avaliando..." : useModelEvaluation ? "Avaliar com modelo" : "Avaliar offline"}</ActionButton>{!version.approved && <ActionButton variant="secondary" disabled={Boolean(busy)} onClick={() => action(`approve-${version.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/versions/${version.id}/approve`), "Aprovação humana registrada.")}>{busy === `approve-${version.id}` ? "Salvando..." : "Aprovar versão"}</ActionButton>}</div></details>)}</div>
        </article>)}
      </div>
    </SectionCard>

    <SectionCard title="Modelo opcional para avaliação sintética" icon={FlaskConical}>
      <p className="mb-3 text-sm text-slate-600">A suíte determinística local é o padrão e não chama provedores. Opcionalmente, um modelo compatível pode gerar respostas para nove casos sintéticos; verificações determinísticas cobrem preço conhecido/desconhecido, política, objeção, continuidade, disponibilidade, transferência, segurança e contexto incompleto. As chamadas podem gerar custo e nunca contam como exposições A/B.</p>
      <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
        <label className="space-y-1 text-sm">Modelo avaliador<select className="fm-field" value={evaluatorModelId} onChange={(event) => setEvaluatorModelId(event.target.value)}><option value="">Sem modelo avaliador</option>{evaluatorModels.filter((model) => model.compatibility !== "incompatible").map((model) => <option key={model.id} value={model.id}>{model.id} · {model.compatibility === "verified_compatible" || model.compatibility === "known_compatible" ? "compatível" : "validar primeiro"}</option>)}</select></label>
        <div className="flex items-end"><ActionButton variant="secondary" disabled={!evaluatorModelId || Boolean(busy)} onClick={() => { if (!window.confirm(`Validar ${evaluatorModelId} com uma chamada sintética? A OpenAI pode cobrar.`)) return; action("evaluator-validate", () => api.post(`/empresas/${tenant}/prompt-optimizer/evaluator/models/validate`, { model_id: evaluatorModelId }), "Compatibilidade validada com a credencial deste tenant."); }}>Validar modelo</ActionButton></div>
        <div className="flex items-end"><ActionButton disabled={Boolean(busy)} onClick={() => action("evaluator-save", () => api.put(`/empresas/${tenant}/prompt-optimizer/evaluator`, { model_id: evaluatorModelId || null }), evaluatorModelId ? "Modelo avaliador salvo para uso explícito." : "Modelo avaliador removido.")}>Salvar seleção</ActionButton></div>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={useModelEvaluation} onChange={(event) => setUseModelEvaluation(event.target.checked)} disabled={!data?.evaluator?.enabled} />Usar modelo nos próximos testes offline (nove chamadas sintéticas; pode gerar custo)</label>
      <p className="mt-2 text-xs text-slate-500">Modelo salvo: {data?.evaluator?.model_id || "nenhum"} · custo observado: {data?.evaluator?.cost_state || "unknown"}. Modelos novos exigem validação antes do uso.</p>
    </SectionCard>

    <SectionCard title="Hipóteses baseadas em evidência A08" icon={FlaskConical}>
      <p className="mb-3 text-sm text-slate-600">Lê agregados persistidos A08, sem texto de conversas. Ausência de resposta é uma observação temporal e não comprova objeção, abandono ou causa.</p>
      <ActionButton variant="secondary" disabled={Boolean(busy)} onClick={detectSuggestions}>{busy === "suggestions" ? "Analisando agregados..." : "Detectar gargalos observáveis"}</ActionButton>
      {suggestions && <><p className="mt-3 text-xs text-slate-500">Estado: {suggestions.state} · fonte: {suggestions.source}. {suggestions.limitation}</p><div className="mt-3 grid gap-3 md:grid-cols-2">{(suggestions.items || []).map((item) => <article key={item.id} className="rounded-lg border border-slate-200 bg-white p-3"><h3 className="font-semibold">{item.title}</h3><p className="mt-1 text-sm">Hipótese: {item.hypothesis}</p><p className="mt-1 text-xs text-slate-500">A08: {fmt(item.numerator)} / {fmt(item.denominator)} · {item.state} · sem inferência causal.</p><ActionButton variant="secondary" className="mt-2" onClick={() => { chooseCandidateModule(item.module_key); setCandidate((current) => ({ ...current, module_key: item.module_key, change_summary: item.title, hypothesis: item.hypothesis })); }}>Usar como hipótese</ActionButton></article>)}</div></>}
    </SectionCard>

    <SectionCard title="Criar versão candidata" icon={Sparkles}>
      <form className="grid gap-3 md:grid-cols-2" onSubmit={(event) => { event.preventDefault(); action("candidate", async () => { await api.post(`/empresas/${tenant}/prompt-optimizer/versions`, candidate); setCandidate(emptyCandidate); }, "Candidata criada em estado OFFLINE."); }}>
        <label className="space-y-1 text-sm">Módulo<select className="fm-field" value={candidate.module_key} onChange={(event) => chooseCandidateModule(event.target.value)}>{modules.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label className="space-y-1 text-sm">Versão base<select className="fm-field" value={candidate.source_version_id} onChange={(event) => { const source = selectedModule?.versions?.find((item) => item.id === event.target.value); setCandidate((current) => ({ ...current, source_version_id: event.target.value, content: source?.content || "" })); }}>{(selectedModule?.versions || []).map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.content_hash.slice(0, 10)}</option>)}</select></label>
        <label className="space-y-1 text-sm md:col-span-2">Conteúdo candidato<textarea className="fm-field min-h-36" maxLength={8000} value={candidate.content} onChange={(event) => setCandidate((current) => ({ ...current, content: event.target.value }))} /></label>
        <label className="space-y-1 text-sm">Alteração proposta<input className="fm-field" maxLength={1000} value={candidate.change_summary} onChange={(event) => setCandidate((current) => ({ ...current, change_summary: event.target.value }))} required /></label>
        <label className="space-y-1 text-sm">Hipótese verificável<input className="fm-field" maxLength={1000} value={candidate.hypothesis} onChange={(event) => setCandidate((current) => ({ ...current, hypothesis: event.target.value }))} required /></label>
        <p className="text-xs text-slate-500 md:col-span-2">Métrica: continuidade observada em até 7 dias, com denominador e maturação explícitos. Alterações comerciais, factuais ou de segurança exigem análise humana e nunca são aplicadas automaticamente.</p>
        <div className="md:col-span-2"><ActionButton type="submit" disabled={Boolean(busy) || !candidateSource}>{busy === "candidate" ? "Criando..." : "Criar candidata OFFLINE"}</ActionButton></div>
      </form>
    </SectionCard>

    <SectionCard title="Experimentos e evidências persistidas" icon={FlaskConical}>
      <form className="grid gap-3 md:grid-cols-4" onSubmit={(event) => { event.preventDefault(); action("experiment", () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments`, experimentForm), "Rascunho de experimento salvo; nenhuma variante foi exposta."); }}>
        <label className="space-y-1 text-sm">Módulo<select className="fm-field" value={experimentForm.module_key} onChange={(event) => chooseExperimentModule(event.target.value)}>{modules.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label className="space-y-1 text-sm">Controle estável<select className="fm-field" value={experimentForm.control_version_id} onChange={(event) => setExperimentForm((current) => ({ ...current, control_version_id: event.target.value }))}>{controlVersions.map((item) => <option key={item.id} value={item.id}>v{item.version}{item.id === experimentModule?.stable_version_id ? " · estável" : ""}</option>)}</select></label>
        <label className="space-y-1 text-sm">Candidata aprovada<select className="fm-field" value={experimentForm.candidate_version_id} onChange={(event) => setExperimentForm((current) => ({ ...current, candidate_version_id: event.target.value }))}><option value="">Selecione</option>{approvedVersions.filter((item) => item.id !== experimentForm.control_version_id).map((item) => <option key={item.id} value={item.id}>v{item.version}</option>)}</select></label>
        <label className="space-y-1 text-sm">Distribuição candidata (%)<input type="number" min="1" max="99" className="fm-field" value={experimentForm.candidate_percent} onChange={(event) => setExperimentForm((current) => ({ ...current, candidate_percent: Number(event.target.value) }))} /></label>
        <label className="space-y-1 text-sm">Amostra mínima / variante<input type="number" min="20" max="10000" className="fm-field" value={experimentForm.sample_min_per_variant} onChange={(event) => setExperimentForm((current) => ({ ...current, sample_min_per_variant: Number(event.target.value) }))} /></label>
        <label className="space-y-1 text-sm">Maturação (dias)<input type="number" min="1" max="90" className="fm-field" value={experimentForm.maturity_days} onChange={(event) => setExperimentForm((current) => ({ ...current, maturity_days: Number(event.target.value) }))} /></label>
        <div className="flex items-end"><ActionButton type="submit" disabled={Boolean(busy) || !experimentForm.control_version_id || !experimentForm.candidate_version_id}>{busy === "experiment" ? "Salvando..." : "Criar rascunho"}</ActionButton></div>
      </form>
      {!data?.experiments?.length && <p className="mt-4 text-sm text-slate-500">Sem experimentos ou exposições persistidas. Dados sintéticos e previews não são contados.</p>}
      <div className="mt-4 space-y-3">{(data?.experiments || []).map((experiment) => <article key={experiment.id} className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{modules.find((item) => item.id === experiment.module_key)?.title || experiment.module_key}</h3><StatusBadge tone={experiment.status === "RUNNING" ? "warning" : "default"}>{experiment.status}</StatusBadge></div>
        <p className="mt-1 text-xs text-slate-500">Atribuição por conversa · {experiment.metrics.distribution.control_percent}% controle / {experiment.metrics.distribution.candidate_percent}% candidata · {experiment.metrics.maturity_days} dias de maturação</p>
        {experiment.metrics.truncated && <p role="status" className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-sm text-amber-800">Resultados parciais: um limite de volume foi atingido ({(experiment.metrics.truncation_reasons || []).join(", ") || "limite de avaliação"}). As métricas afetadas não representam o conjunto completo; promoção bloqueada.</p>}
        <div className="mt-3 grid gap-2 sm:grid-cols-2">{["control", "candidate"].map((variant) => { const metric = experiment.metrics.by_variant?.[variant]; const response = metric?.response_after_window; const purchase = metric?.verified_purchase_after_30d; const stage = metric?.commercial_stage_advance_after_30d; return <div key={variant} className="rounded-lg bg-slate-50 p-3"><strong className="capitalize">{variant === "control" ? "Controle" : "Candidata"}</strong><p>Exposições: {fmt(metric?.exposed)} · atribuições: {fmt(metric?.assigned)}</p><p>Respostas posteriores: {fmt(response?.numerator)} / {fmt(response?.denominator)} · {percent(response?.denominator ? response.numerator / response.denominator : null)} · {response?.state || "unknown"}</p><p>Avanços comerciais após 30 dias: {fmt(stage?.numerator)} / {fmt(stage?.denominator)} · {stage?.state || "unknown"}</p><p>Compras verificadas após 30 dias: {fmt(purchase?.numerator)} / {fmt(purchase?.denominator)} · {purchase?.state || "unknown"}</p><p>Latência P50: {metric?.latency_p50_ms?.value == null ? "unknown" : `${fmt(metric.latency_p50_ms.value)} ms`} · tokens de entrada médios: {fmt(metric?.input_tokens_mean?.value)}</p><p>Custo medido: {metric?.cost_usd?.state || "unknown"} · erros: {fmt(metric?.generation_failed)} · guard blocks: {fmt(metric?.guard_blocked)}</p></div>; })}</div>
        <p className="mt-2 text-xs text-slate-500">Resposta posterior indica associação temporal, não causalidade. Aprovação estatística exige amostra madura, dados observados e guardrails válidos.</p>
        <div className="mt-3 flex flex-wrap gap-2"><ActionButton variant="secondary" disabled={Boolean(busy)} onClick={() => action(`decision-${experiment.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments/${experiment.id}/decision`), "Decisão calculada com base somente em exposições persistidas.")}>Avaliar promoção</ActionButton>{["DRAFT", "READY_FOR_PILOT", "PAUSED"].includes(experiment.status) && <ActionButton variant="secondary" disabled={Boolean(busy) || !data?.live?.ab_live} onClick={() => action(`start-${experiment.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments/${experiment.id}/start`), "Experimento iniciado conforme flags do tenant.")}>{busy === `start-${experiment.id}` ? "Iniciando..." : data?.live?.ab_live ? experiment.status === "PAUSED" ? "Retomar piloto" : "Iniciar piloto" : "Piloto desativado"}</ActionButton>}{experiment.status === "RUNNING" && <><ActionButton variant="secondary" disabled={Boolean(busy)} onClick={() => action(`pause-${experiment.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments/${experiment.id}/pause`), "Novas atribuições foram pausadas.")}>Pausar</ActionButton><ActionButton variant="secondary" disabled={Boolean(busy)} onClick={() => { if (window.confirm("Encerrar este experimento? As evidências existentes serão mantidas e novas atribuições serão interrompidas.")) action(`complete-${experiment.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments/${experiment.id}/complete`), "Experimento encerrado; histórico e exposições preservados."); }}>Encerrar</ActionButton></>}{["RUNNING", "PAUSED"].includes(experiment.status) && <ActionButton variant="danger" disabled={Boolean(busy)} onClick={() => { const reason = window.prompt("Motivo do rollback (registrado na auditoria):"); if (reason) action(`rollback-${experiment.id}`, () => api.post(`/empresas/${tenant}/prompt-optimizer/experiments/${experiment.id}/rollback`, { reason }), "Rollback registrado."); }}>Rollback</ActionButton>}</div>
        {decisionResults[experiment.id] && <p role="status" className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-sm">Decisão: {decisionResults[experiment.id].reason} · p={decisionResults[experiment.id].p_value == null ? "unknown" : decisionResults[experiment.id].p_value.toFixed(4)} · lift observado={percent(decisionResults[experiment.id].observed_lift)}. Métricas pós-exposição não provam causalidade.</p>}
        {!data?.live?.ab_live && <p className="mt-2 text-xs text-amber-700">Início bloqueado: A/B LIVE está OFF ou este tenant não consta na lista de piloto.</p>}
      </article>)}</div>
    </SectionCard>

    <SectionCard title="Auditoria recente" icon={ClipboardList}>
      {data?.audit?.length ? <ol className="space-y-2">{data.audit.slice(0, 20).map((item) => <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 py-2 text-sm"><span>{item.action} · {item.object_type}</span><time className="text-xs text-slate-500">{item.created_at ? new Date(item.created_at).toLocaleString() : ""}</time></li>)}</ol> : <p className="text-sm text-slate-500">Nenhum evento registrado.</p>}
    </SectionCard>
    {data?.live?.automatic_prompt_mutation && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"><AlertTriangle size={15} className="mr-1 inline" />A alteração automática de prompt permanece proibida por este painel.</p>}
  </main>;
}
