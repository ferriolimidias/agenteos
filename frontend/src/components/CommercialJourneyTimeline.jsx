import { useEffect, useRef, useState } from "react";
import { Activity, X } from "lucide-react";
import api from "../services/api";
import { getActiveEmpresaId } from "../utils/auth";
import { JOURNEY_CATEGORIES, journeyError, journeyState, journeyTime, mergeJourney, safeJourneyDetails } from "../utils/commercialJourney";
import { ActionButton, EmptyState, InfoBanner, LoadingState, StatusBadge } from "./ui";

export default function CommercialJourneyTimeline({ empresaId, leadId, onClose }) {
  const [activeTenant, setActiveTenant] = useState(getActiveEmpresaId);
  const [category, setCategory] = useState("");
  const [zone, setZone] = useState("UTC");
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [moreScope, setMoreScope] = useState(null);
  const moreController = useRef(null);
  const scope = JSON.stringify([activeTenant, empresaId, leadId, category, refresh]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const validTenant = activeTenant === empresaId;
  const data = result?.scope === scope && validTenant ? result.data : null;
  const failure = error?.scope === scope ? error.message : null;
  const zoneLabel = zone === "UTC" ? "UTC" : Intl.DateTimeFormat().resolvedOptions().timeZone;

  useEffect(() => {
    const sync = () => setActiveTenant(getActiveEmpresaId());
    window.addEventListener("storage", sync); window.addEventListener("focus", sync);
    return () => { window.removeEventListener("storage", sync); window.removeEventListener("focus", sync); };
  }, []);

  useEffect(() => {
    moreController.current?.abort();
    if (!empresaId || !leadId || !validTenant) return;
    const controller = new AbortController();
    let active = true;
    api.get(`/empresas/${empresaId}/inbox/leads/${leadId}/journey`, {
      params: { limit: 30, ...(category ? { category } : {}) }, signal: controller.signal,
    }).then(response => {
      if (active && scopeRef.current === scope && getActiveEmpresaId() === empresaId) setResult({ scope, data: response.data });
    }).catch(caught => {
      if (active && !controller.signal.aborted && scopeRef.current === scope) setError({ scope, message: journeyError(caught) });
    });
    return () => { active = false; controller.abort(); moreController.current?.abort(); };
  }, [empresaId, leadId, category, validTenant, scope]);

  const loadMore = async () => {
    if (!data?.has_more || !data.next_cursor || moreScope === scope) return;
    const controller = new AbortController(); moreController.current = controller;
    setMoreScope(scope);
    try {
      const response = await api.get(`/empresas/${empresaId}/inbox/leads/${leadId}/journey`, {
        params: { limit: 30, ...(category ? { category } : {}), cursor: data.next_cursor }, signal: controller.signal,
      });
      if (scopeRef.current === scope && !controller.signal.aborted && getActiveEmpresaId() === empresaId) {
        setResult({ scope, data: { ...response.data, items: mergeJourney(data.items, response.data.items) } });
        setError(null);
      }
    } catch (caught) {
      if (!controller.signal.aborted && scopeRef.current === scope) setError({ scope, message: journeyError(caught) });
    } finally { setMoreScope(previous => previous === scope ? null : previous); }
  };

  return <aside aria-label="Jornada comercial" data-testid="commercial-journey" className="absolute right-0 top-0 z-30 flex h-[65%] w-full flex-col border-l border-slate-200 bg-white shadow-xl lg:relative lg:h-full lg:w-[360px] lg:max-w-[40vw] lg:shrink-0">
    <header className="shrink-0 border-b border-slate-200 p-4">
      <div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Activity size={17} />Jornada comercial</h2><button type="button" aria-label="Fechar jornada comercial" onClick={onClose} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><X size={18} /></button></div>
      <p className="mt-1 text-xs text-slate-500">Fatos persistidos · mais recentes primeiro · somente leitura</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div><label htmlFor="journey-category" className="text-xs font-semibold text-slate-600">Categoria</label><select id="journey-category" value={category} onChange={event => setCategory(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-xs"><option value="">Todas</option>{Object.entries(JOURNEY_CATEGORIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
        <div><label htmlFor="journey-zone" className="text-xs font-semibold text-slate-600">Fuso horário</label><select id="journey-zone" value={zone} onChange={event => setZone(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-xs"><option value="UTC">UTC</option><option value="local">Local</option></select></div>
      </div>
    </header>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-4">
      <p className="text-xs text-slate-500">Horários em {zoneLabel}{data ? ` · consulta ${journeyTime(data.checked_at, zone)}` : ""}</p>
      {!validTenant ? <InfoBanner tone="warning">Tenant alterado. Selecione um contato no tenant atual para consultar sua jornada.</InfoBanner> : <>
        {failure && <div role="alert"><InfoBanner tone="warning">{failure}</InfoBanner><ActionButton variant="secondary" className="mt-2" onClick={() => setRefresh(value => value + 1)}>Atualizar jornada</ActionButton></div>}
        {!data && !failure && <LoadingState label="Consultando fatos da jornada..." />}
        {data && <>
          <details className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-semibold text-slate-800">Estado atual e cobertura</summary>
            <div className="mt-3 space-y-3 text-xs text-slate-600">
              <p>CRM atual: {data.summary?.crm?.stage?.name || "Não observado"}. Tags atuais: {(data.summary?.crm?.tags || []).map(tag => tag.name).join(", ") || "Nenhuma registrada"}.</p>
              <p>FunnelSpec: {data.summary?.funnel?.version || "Não disponível"}{data.summary?.commercial_state?.stage_key ? ` · estado comercial atual ${data.summary.commercial_state.stage_key}` : ""}. Etapas definidas: {(data.summary?.funnel?.stages || []).map(stage => stage.display_name || stage.key).join(" → ") || "Não disponíveis"}.</p>
              <p>Snapshots atuais não comprovam transições históricas nem completude da jornada.</p>
              <p>Sinais atuais persistidos: {(data.summary?.commercial_state?.signals || []).join(", ") || "Não observados"}. Representações relacionadas não são novas ações do contato.</p>
              {(data.summary?.bookings || []).length > 0 && <div><p className="font-semibold">Agendamentos atuais</p><p>Horário planejado, sem data de registro comprovada; não é um evento cronológico inventado.</p>{data.summary.bookings.map(booking => <p key={booking.id} className="mt-1">{journeyState(booking.status).label} · planejado em {journeyTime(booking.scheduled_at, zone)} · {zoneLabel}{booking.ended_at ? ` · fim planejado em ${journeyTime(booking.ended_at, zone)}` : ""}</p>)}</div>}
              <p>Última atividade de mensagens: {journeyTime(data.summary?.activity?.last_event_at, zone)}. Inatividade observada: {data.summary?.activity?.inactive_seconds == null ? "Desconhecida" : `${Math.floor(data.summary.activity.inactive_seconds / 60)} min`}. Não há inferência de abandono ou causa.</p>
              <p>Primeira mensagem: {journeyTime(data.summary?.activity?.first_message_at, zone)}. Mensagens recebidas/enviadas: {data.summary?.activity?.inbound_messages ?? "Desconhecido"}/{data.summary?.activity?.outbound_messages ?? "Desconhecido"}. Intervalos de resposta registrados: {data.summary?.activity?.recorded_response_intervals ?? "Desconhecido"}; média {data.summary?.activity?.mean_recorded_response_seconds == null ? "desconhecida" : `${data.summary.activity.mean_recorded_response_seconds} s`}. Intervalos não comprovam entrega.</p>
              <p>Atribuição por resolução persistida: {Object.entries(data.summary?.attribution?.status_counts || {}).map(([key, value]) => `${key}: ${value}`).join(" · ") || "Não observada"}. Identidade Ads verificada: {data.summary?.attribution?.ads_identity_verified ? "sim" : "não"}.</p>
              {(data.coverage?.sources || []).map(source => <div key={`${source.source}:${source.category}`} className="border-t border-slate-100 pt-2"><p className="font-semibold">{source.source} · {JOURNEY_CATEGORIES[source.category] || source.category}</p><p>{source.records} registros · {source.timeline_records} fatos na timeline · {source.missing_timestamp} sem timestamp</p><p>{journeyState(source.state).label}</p><p>{source.limitation}</p></div>)}
              {(data.coverage?.limitations || []).map(limit => <p key={limit}>{limit}</p>)}
            </div>
          </details>
          {!data.items?.length ? <EmptyState title="Nenhum fato observado neste filtro" description="Não observado nas fontes consultadas. Isso não comprova que o evento não ocorreu." /> : <ol className="space-y-3" data-testid="journey-events">{data.items.map(item => {
            const state = journeyState(item.status);
            return <li key={item.id} className="rounded-xl border border-slate-200 p-3" data-event-id={item.id}>
              <p className="text-xs text-slate-500">{journeyTime(item.occurred_at, zone)} · {zoneLabel}</p>
              <h3 className="mt-1 text-sm font-semibold text-slate-900">{item.title}</h3>
              <p className="mt-1 text-xs text-slate-500">{JOURNEY_CATEGORIES[item.category] || "Categoria desconhecida"} · {item.evidence?.source}</p>
              <div className="mt-2"><StatusBadge tone={state.tone}>{state.label}</StatusBadge></div>
              <details className="mt-3 text-xs text-slate-600"><summary className="cursor-pointer font-semibold text-indigo-700">Ver evidência</summary><dl className="mt-2 space-y-2">
                <div><dt className="font-semibold">Fonte / registro</dt><dd className="break-all">{item.evidence?.source} / {item.evidence?.record_id}</dd></div>
                <div><dt className="font-semibold">Relação persistida</dt><dd>{item.evidence?.relationship || "Desconhecida"}</dd></div>
                {item.conversation_id && <div><dt className="font-semibold">Conversa comprovada</dt><dd className="break-all">{item.conversation_id}</dd></div>}
                {safeJourneyDetails(item.details).map(row => <div key={row.key}><dt className="font-semibold">{row.label}</dt><dd className="break-all">{row.value}</dd></div>)}
              </dl></details>
            </li>;
          })}</ol>}
          {data.has_more && <ActionButton variant="secondary" disabled={moreScope === scope} onClick={loadMore}>{moreScope === scope ? "Carregando fatos..." : "Carregar mais fatos"}</ActionButton>}
        </>}
      </>}
    </div>
  </aside>;
}
