import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import { emptyObservation, operationsError, operationsParams, OUTCOMES, outcomePresentation } from "../../utils/conversionOperations";
import { ActionButton, EmptyState, InfoBanner, LoadingState, MetricCard, PageHeader, RestrictedNotice, SectionCard, StatusBadge } from "../../components/ui";

const EMPTY_FILTERS = { outcome: "", transport: "", event_name: "", start_at: "", end_at: "" };
const INPUT = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800";
const LIMIT = 20;
function when(value) {
  if (!value) return "Nenhuma tentativa observada";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Data indisponível" : date.toLocaleString("pt-BR");
}
function DeliveryBadge({ value }) {
  const presentation = outcomePresentation(value);
  return <StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>;
}
function Field({ label, value }) {
  return <div><dt className="text-xs font-semibold text-slate-500">{label}</dt><dd className="mt-1 break-all text-sm text-slate-800">{value ?? "Não disponível"}</dd></div>;
}

export default function ConversionOperations() {
  const [tenant, setTenant] = useState(getActiveEmpresaId);
  const allowed = getStoredUser()?.role !== "meta_app_reviewer";
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [filterError, setFilterError] = useState("");
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const key = JSON.stringify([tenant, filters, offset, refresh]);
  const detailKey = JSON.stringify([key, selected]);
  const data = list?.key === key ? list.data : null;
  const failure = error?.key === key ? error.message : null;
  const attempt = detail?.key === detailKey ? detail.data : null;
  const detailFailure = detailError?.key === detailKey ? detailError.message : null;

  useEffect(() => {
    const sync = () => {
      const current = getActiveEmpresaId();
      if (tenant !== current) { setTenant(current); setSelected(null); setOffset(0); }
    };
    window.addEventListener("storage", sync);
    window.addEventListener("focus", sync);
    return () => { window.removeEventListener("storage", sync); window.removeEventListener("focus", sync); };
  }, [tenant]);

  useEffect(() => {
    if (!tenant || !allowed) return;
    const controller = new AbortController();
    let active = true;
    api.get(`/empresas/${tenant}/analytics/meta-sender-operations`, {
      params: operationsParams(filters, offset, LIMIT), signal: controller.signal,
    }).then(response => { if (active) setList({ key, data: response.data }); })
      .catch(caught => { if (active && !controller.signal.aborted) setError({ key, message: operationsError(caught) }); });
    return () => { active = false; controller.abort(); };
  }, [tenant, allowed, filters, offset, key]);

  useEffect(() => {
    if (!tenant || !allowed || !selected) return;
    const controller = new AbortController();
    let active = true;
    api.get(`/empresas/${tenant}/analytics/meta-sender-operations/${selected}`, { signal: controller.signal })
      .then(response => { if (active) setDetail({ key: detailKey, data: response.data }); })
      .catch(caught => { if (active && !controller.signal.aborted) setDetailError({ key: detailKey, message: operationsError(caught) }); });
    return () => { active = false; controller.abort(); };
  }, [tenant, allowed, selected, detailKey]);

  if (!allowed) return <RestrictedNotice title="Visão operacional restrita" description="A conta de revisão Meta mantém acesso somente às áreas autorizadas." />;
  if (!tenant) return <RestrictedNotice title="Selecione uma empresa" description="As evidências operacionais são consultadas somente no tenant ativo." />;

  const summary = data?.summary;
  const changeFilter = (name, value) => setDraft(previous => ({ ...previous, [name]: value }));
  const applyFilters = event => {
    event.preventDefault();
    try {
      operationsParams(draft);
      setFilterError(""); setFilters({ ...draft }); setOffset(0); setSelected(null); setRefresh(value => value + 1);
    } catch (caught) { setFilterError(caught.message); }
  };
  const movePage = next => { setOffset(next); setSelected(null); };

  return <div className="space-y-6" data-testid="sender-operations">
    <PageHeader title="Conversões Meta — operação" eyebrow="Evidências de envio" icon={Activity}
      description="Tentativas registradas pelos senders existentes. As tags continuam sendo a origem dos sinais de conversão."
      actions={<ActionButton variant="secondary" icon={RefreshCw} onClick={() => { setSelected(null); setRefresh(value => value + 1); }}>Atualizar evidências</ActionButton>} />
    <InfoBanner tone="warning">Aceitação pela Meta não comprova atribuição da conversão. Entrega real: <strong>NÃO VALIDADO</strong>. Nenhum envio ou replay pode ser iniciado nesta tela.</InfoBanner>
    {failure && <div role="alert"><InfoBanner tone="danger">{failure}</InfoBanner></div>}
    {!failure && !data && <LoadingState label="Consultando evidências dos senders..." />}
    {summary && <>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={emptyObservation(summary) ? "warning" : "info"}>{summary.observation_status}</StatusBadge>
        <StatusBadge tone="default">LIVE canônico: {summary.canonical_live_enabled ? "ATIVO — verificar configuração" : "desativado"}</StatusBadge>
        <StatusBadge tone="info">Ownership: SHADOW</StatusBadge>
        <span className="text-xs text-slate-500">Consultado em {when(data.checked_at)}</span>
      </div>
      {emptyObservation(summary) && <InfoBanner tone="warning">Nenhum evento natural observado nas evidências instrumentadas deste tenant. A ausência de tráfego não comprova falha nem sucesso de entrega.</InfoBanner>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Tentativas observadas" value={summary.total_attempts} helper={`Última: ${when(summary.last_attempt_at)}`} />
        <MetricCard label="Aceitos pela Meta" value={summary.outcome_counts?.accepted ?? 0} helper="Resposta do provedor; não equivale a atribuição" />
        <MetricCard label="Entrega desconhecida" value={summary.outcome_counts?.unknown ?? 0} helper="Timeout ou confirmação ambígua; não autoriza retry" tone="warning" />
        <MetricCard label="Históricos protegidos" value={summary.historical?.protected_commercial_events ?? 0} helper={`${summary.historical?.historical_outbox_blocked ?? 0} registros de outbox bloqueados`} />
      </div>
      <SectionCard title="Cobertura e configuração" description="Resumo do tenant independente dos filtros. Configuração presente não comprova prontidão de envio.">
        <p className="mb-3 text-sm text-slate-600">Caminhos instrumentados: {summary.coverage?.paths_instrumented ?? 0}/{summary.coverage?.paths_discovered ?? 0}. Configuração local Business Messaging: {summary.configuration?.status || "NÃO VALIDADO"}. Configuração CAPI: NÃO VALIDADO.</p>
        <div className="grid gap-4 md:grid-cols-2">{(summary.coverage?.paths || []).map(path => <div key={path.transport_path} className="rounded-lg border border-slate-200 p-4">
          <p className="text-sm font-semibold text-slate-800">{path.transport === "capi" ? "Conversions API" : "Business Messaging"}</p>
          <p className="mt-1 break-all text-xs text-slate-500">{path.transport_path}</p>
          <div className="mt-3"><StatusBadge tone={path.observation_status === "NO_EVENT_OBSERVED" ? "warning" : "info"}>{path.observation_status}</StatusBadge></div>
          <p className="mt-2 text-xs text-slate-600">{path.total_attempts} tentativas · {when(path.last_attempt_at)}</p>
        </div>)}</div>
      </SectionCard>
      <SectionCard title="Correspondência entre registros" description="Somente vínculos persistidos. Correspondência provável não comprova identidade exata nem atribuição.">
        <div className="grid gap-3 sm:grid-cols-4">{["LINKED_EXACT", "LINKED_PROBABLE", "AMBIGUOUS", "UNLINKED"].map(status => <div key={status}><p className="text-xs text-slate-500">{status}</p><p className="text-lg font-semibold">{summary.persisted_links?.[status] ?? 0}</p></div>)}</div>
        <p className="mt-4 text-sm text-slate-600">Outbox: {summary.outbox?.total ?? 0} registros. Atribuição: {summary.attribution?.touches ?? 0} touches e {summary.attribution?.resolutions ?? 0} resoluções. Reservas SHADOW: {summary.shadow_diagnostics?.reservations ?? 0}; enforcement {summary.ownership_enforced ? "ativo — verificar configuração" : "desativado"}.</p>
        <p className="mt-2 text-xs text-slate-500">Legados: {summary.correspondence?.legacy_events ?? 0}; sem vínculo persistido: {summary.correspondence?.legacy_without_persisted_link ?? 0}; Commercial Events: {summary.correspondence?.commercial_events ?? 0}; projeções: {summary.correspondence?.projections ?? 0}. Vínculos exatos inferidos por esta tela: 0.</p>
      </SectionCard>
    </>}
    <SectionCard title="Tentativas de entrega" description="Filtros aplicam-se à lista; cobertura e observação global continuam disponíveis.">
      <form onSubmit={applyFilters} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div><label htmlFor="sender-outcome" className="text-xs font-semibold text-slate-600">Estado</label><select id="sender-outcome" className={INPUT} value={draft.outcome} onChange={event => changeFilter("outcome", event.target.value)}><option value="">Todos os estados</option>{Object.entries(OUTCOMES).map(([value, presentation]) => <option key={value} value={value}>{presentation.label}</option>)}</select></div>
        <div><label htmlFor="sender-transport" className="text-xs font-semibold text-slate-600">Sender</label><select id="sender-transport" className={INPUT} value={draft.transport} onChange={event => changeFilter("transport", event.target.value)}><option value="">Todos os senders</option><option value="business_messaging">Business Messaging</option><option value="capi">Conversions API</option></select></div>
        <label className="text-xs font-semibold text-slate-600">Nome do evento<input className={INPUT} maxLength={64} value={draft.event_name} onChange={event => changeFilter("event_name", event.target.value)} placeholder="Ex.: Purchase" /></label>
        <label className="text-xs font-semibold text-slate-600">Início (horário local)<input className={INPUT} type="datetime-local" value={draft.start_at} onChange={event => changeFilter("start_at", event.target.value)} /></label>
        <label className="text-xs font-semibold text-slate-600">Fim (horário local)<input className={INPUT} type="datetime-local" value={draft.end_at} onChange={event => changeFilter("end_at", event.target.value)} /></label>
        <div className="flex items-end gap-2"><ActionButton type="submit">Aplicar filtros</ActionButton><ActionButton type="button" variant="secondary" onClick={() => { setDraft(EMPTY_FILTERS); setFilters(EMPTY_FILTERS); setOffset(0); setSelected(null); setFilterError(""); setRefresh(value => value + 1); }}>Limpar</ActionButton></div>
      </form>
      {filterError && <p className="mt-3 text-sm text-red-700" role="alert">{filterError}</p>}
      {data && <div className="mt-5">
        {!data.items?.length ? <EmptyState title={emptyObservation(summary) ? "Nenhum evento natural observado" : "Nenhuma tentativa corresponde aos filtros"} description={emptyObservation(summary) ? "NO_EVENT_OBSERVED. Aguardando tráfego natural; entrega real permanece NÃO VALIDADO." : "A observação global permanece acima. Altere os filtros para consultar outras tentativas."} /> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b text-xs text-slate-500"><tr><th className="p-3">Evento / sender</th><th className="p-3">Estado</th><th className="p-3">Início</th><th className="p-3">HTTP</th><th className="p-3">Detalhes</th></tr></thead><tbody>{data.items.map(item => <tr key={item.id} className="border-b border-slate-100"><td className="p-3"><p className="font-semibold">{item.event_name || "Não informado"}</p><p className="text-xs text-slate-500">{item.transport}</p></td><td className="p-3"><DeliveryBadge value={item.delivery_status} /></td><td className="p-3">{when(item.started_at)}</td><td className="p-3">{item.http_status ?? "—"}</td><td className="p-3"><ActionButton variant="secondary" onClick={() => setSelected(item.id)} aria-label={`Ver detalhes da tentativa ${item.id}`}>Ver detalhes</ActionButton></td></tr>)}</tbody></table></div>}
        <div className="mt-4 flex items-center justify-between gap-3"><p className="text-xs text-slate-500">{data.total} resultados · página {Math.floor(offset / LIMIT) + 1}</p><div className="flex gap-2"><ActionButton variant="secondary" disabled={offset === 0} onClick={() => movePage(Math.max(0, offset - LIMIT))}>Anterior</ActionButton><ActionButton variant="secondary" disabled={offset + LIMIT >= data.total || offset + LIMIT > 10000} onClick={() => movePage(offset + LIMIT)}>Próxima</ActionButton></div></div>
      </div>}
    </SectionCard>
    {selected && <SectionCard title="Detalhe da tentativa" actions={<ActionButton variant="secondary" onClick={() => setSelected(null)}>Fechar detalhes</ActionButton>}>
      {detailFailure ? <div role="alert"><InfoBanner tone="danger">{detailFailure}</InfoBanner></div> : !attempt ? <LoadingState label="Consultando detalhe seguro..." /> : <div className="space-y-4" data-testid="sender-attempt-detail">
        <DeliveryBadge value={attempt.delivery_status} />
        {attempt.delivery_status === "unknown" && <InfoBanner tone="warning">UNKNOWN_DELIVERY: não há confirmação de entrega. Esta evidência não autoriza retry nem replay.</InfoBanner>}
        <dl className="grid gap-4 sm:grid-cols-2"><Field label="Tentativa" value={attempt.id} /><Field label="Evento" value={attempt.event_name} /><Field label="Caminho" value={attempt.transport_path} /><Field label="Resultado registrado" value={attempt.outcome} /><Field label="Início" value={when(attempt.started_at)} /><Field label="Finalização" value={attempt.finished_at ? when(attempt.finished_at) : "Não finalizada"} /><Field label="Identidade externa (hash)" value={attempt.identity?.external_event_id_hash} /><Field label="Identidade externa segura" value={attempt.identity?.external_event_id_safe} /><Field label="Dataset (hash)" value={attempt.identity?.dataset_id_hash} /><Field label="WABA (hash)" value={attempt.identity?.waba_id_hash} /><Field label="Classificação da identidade" value={attempt.identity_classification} /><Field label="Resposta HTTP" value={attempt.http_status} /><Field label="Eventos aceitos pelo provedor" value={attempt.provider_response?.events_received} /><Field label="Código de erro do provedor" value={attempt.provider_response?.code} /><Field label="Commercial Event persistido" value={attempt.related?.commercial_event_id} /><Field label="Vínculo persistido" value={attempt.related?.persisted_link?.classification} /><Field label="Estado da atribuição" value={attempt.related?.attribution?.status} /><Field label="Tentativas com a mesma identidade" value={attempt.shadow_diagnostics?.same_identity_attempts} /></dl>
        {(attempt.related?.outbox || []).length > 0 && <div><h3 className="text-sm font-semibold">Outbox associada</h3>{attempt.related.outbox.map(row => <p key={row.id} className="mt-2 break-all text-xs text-slate-600">{row.id} · {row.status} · {row.dispatch_mode} · histórico bloqueado: {row.historical_no_send ? "sim" : "não"}</p>)}</div>}
        <InfoBanner>Diagnóstico SHADOW, sem enforcement. Tentativas repetidas não comprovam duplicidade. A correspondência exige identidade e destino completos; nenhum vínculo provável é convertido em exato.</InfoBanner>
      </div>}
    </SectionCard>}
  </div>;
}
