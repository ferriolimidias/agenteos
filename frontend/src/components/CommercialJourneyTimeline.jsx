import { useEffect, useRef, useState } from "react";
import { Activity, X } from "lucide-react";
import api from "../services/api";
import { getActiveEmpresaId } from "../utils/auth";
import { JOURNEY_CATEGORIES, journeyError, journeyState, journeyTime, mergeJourney, safeJourneyDetails } from "../utils/commercialJourney";
import { ActionButton, EmptyState, InfoBanner, LoadingState, StatusBadge } from "./ui";
import "../styles/journey-ux04.css";

function JourneyTimestamp({ value }) {
  const original = typeof value === "string" && !Number.isNaN(new Date(value).getTime()) ? value : undefined;
  return <time dateTime={original}>{journeyTime(value)}</time>;
}

export default function CommercialJourneyTimeline({ empresaId, leadId, contactName, onClose }) {
  const [activeTenant, setActiveTenant] = useState(getActiveEmpresaId);
  const [category, setCategory] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [moreScope, setMoreScope] = useState(null);
  const moreController = useRef(null);
  const historyRef = useRef(null);
  const summaryRef = useRef(null);
  const coverageRef = useRef(null);
  const scope = JSON.stringify([activeTenant, empresaId, leadId, category, refresh]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const validTenant = activeTenant === empresaId;
  const data = result?.scope === scope && validTenant ? result.data : null;
  const failure = error?.scope === scope ? error.message : null;
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

  const visitSection = ref => {
    const section = ref.current;
    if (!section) return;
    if (section.tagName === "DETAILS") section.open = true;
    section.scrollIntoView({ block: "start" });
    (section.querySelector("summary") || section).focus({ preventScroll: true });
  };

  return <aside id="inbox-journey-panel" aria-label="Jornada comercial" data-testid="commercial-journey" className="inbox-journey-panel flex min-h-0 w-full flex-col">
    <header className="inbox-journey-heading shrink-0">
      <div className="inbox-journey-title-row">
        <div className="min-w-0">
          <h2 id="journey-dialog-title"><Activity size={20} aria-hidden="true" />Jornada comercial</h2>
          <p className="inbox-journey-contact"><span>{contactName || "Contato selecionado"}</span>{data && <StatusBadge>{data.summary?.crm?.stage?.name || "Etapa não observada"}</StatusBadge>}</p>
        </div>
        <ActionButton type="button" variant="ghost" icon={X} aria-label="Fechar jornada comercial" onClick={onClose}>Voltar à conversa</ActionButton>
      </div>
      <div className="inbox-journey-toolbar">
        <div className="inbox-journey-filter"><label htmlFor="journey-category">Categoria</label><select id="journey-category" value={category} onChange={event => setCategory(event.target.value)} className="fm-field"><option value="">Todas</option>{Object.entries(JOURNEY_CATEGORIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
        <div className="inbox-journey-time-policy"><p>Horário de Brasília</p><p>Fatos persistidos · mais recentes primeiro · somente leitura</p>{data && <p>Consulta: <JourneyTimestamp value={data.checked_at} /></p>}</div>
      </div>
      {data && <nav className="inbox-journey-section-nav" aria-label="Seções da jornada">
        <ActionButton type="button" variant="ghost" onClick={() => visitSection(historyRef)}>Linha do tempo</ActionButton>
        <ActionButton type="button" variant="ghost" onClick={() => visitSection(summaryRef)}>Resumo do contato</ActionButton>
        <ActionButton type="button" variant="ghost" onClick={() => visitSection(coverageRef)}>Cobertura das fontes</ActionButton>
      </nav>}
    </header>
    <div className="inbox-journey-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain">
      {!validTenant ? <InfoBanner tone="warning">Tenant alterado. Selecione um contato no tenant atual para consultar sua jornada.</InfoBanner> : <>
        {failure && <div role="alert" className="inbox-journey-error"><InfoBanner tone="warning">{failure}</InfoBanner><ActionButton type="button" variant="secondary" className="mt-2" onClick={() => setRefresh(value => value + 1)}>Atualizar jornada</ActionButton></div>}
        {!data && !failure && <LoadingState label="Consultando fatos da jornada..." />}
        {data && <div className="inbox-journey-layout">
          <section ref={historyRef} tabIndex={-1} className="inbox-journey-history" aria-labelledby="journey-history-title">
            <div className="inbox-journey-section-title"><h3 id="journey-history-title">Linha do tempo</h3><p>Acontecimentos, estados e evidências registrados.</p></div>
            {!data.items?.length ? <EmptyState title="Nenhum fato observado neste filtro" description="Não observado nas fontes consultadas. Isso não comprova que o evento não ocorreu." /> : <ol className="inbox-journey-events" data-testid="journey-events">{data.items.map(item => {
              const state = journeyState(item.status);
              return <li key={item.id} className="inbox-journey-event" data-event-id={item.id}>
                <p className="inbox-journey-event-time"><JourneyTimestamp value={item.occurred_at} /></p>
                <h4>{item.title}</h4>
                <div className="inbox-journey-event-meta"><p>{JOURNEY_CATEGORIES[item.category] || "Categoria desconhecida"} · {item.evidence?.source}</p><StatusBadge tone={state.tone}>{state.label}</StatusBadge></div>
                <details className="inbox-journey-evidence"><summary>Ver evidência</summary><dl>
                  <div><dt>Fonte / registro</dt><dd>{item.evidence?.source} / {item.evidence?.record_id}</dd></div>
                  <div><dt>Relação persistida</dt><dd>{item.evidence?.relationship || "Desconhecida"}</dd></div>
                  <div><dt>Horário técnico (UTC)</dt><dd>{journeyTime(item.occurred_at, "UTC")} · UTC</dd></div>
                  {item.conversation_id && <div><dt>Conversa comprovada</dt><dd>{item.conversation_id}</dd></div>}
                  {safeJourneyDetails(item.details).map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.original ? <time dateTime={row.original}>{row.value}</time> : row.value}</dd></div>)}
                </dl></details>
              </li>;
            })}</ol>}
            {data.has_more && <ActionButton type="button" variant="secondary" className="inbox-journey-more" disabled={moreScope === scope} onClick={loadMore}>{moreScope === scope ? "Carregando fatos..." : "Carregar mais fatos"}</ActionButton>}
          </section>
          <div className="inbox-journey-context">
            <details ref={summaryRef} className="inbox-journey-summary"><summary>Resumo do contato</summary>
              <div className="inbox-journey-context-body">
                <dl className="inbox-journey-contact-facts"><div><dt>Etapa atual do CRM</dt><dd>{data.summary?.crm?.stage?.name || "Não observado"}</dd></div><div><dt>Tags atuais</dt><dd>{(data.summary?.crm?.tags || []).map(tag => tag.name).join(", ") || "Nenhuma registrada"}</dd></div><div><dt>Estado comercial atual</dt><dd>{data.summary?.commercial_state?.stage_key || "Não observado"}</dd></div></dl>
                <p>Snapshots atuais não comprovam transições históricas nem completude da jornada.</p>
                <details className="inbox-journey-summary-group"><summary>Funil e sinais atuais</summary><div>
                  <p>FunnelSpec: {data.summary?.funnel?.version || "Não disponível"}. Etapas definidas: {(data.summary?.funnel?.stages || []).map(stage => stage.display_name || stage.key).join(" → ") || "Não disponíveis"}.</p>
                  <p>Sinais atuais persistidos: {(data.summary?.commercial_state?.signals || []).join(", ") || "Não observados"}. Representações relacionadas não são novas ações do contato.</p>
                </div></details>
                {(data.summary?.bookings || []).length > 0 && <details className="inbox-journey-summary-group"><summary>Agendamentos atuais</summary><div><p>Horário planejado, sem data de registro comprovada; não é um evento cronológico inventado.</p>{data.summary.bookings.map(booking => <p key={booking.id}>{journeyState(booking.status).label} · planejado em <JourneyTimestamp value={booking.scheduled_at} />{booking.ended_at && <> · fim planejado em <JourneyTimestamp value={booking.ended_at} /></>}</p>)}</div></details>}
                <details className="inbox-journey-summary-group"><summary>Atividade de mensagens</summary><div>
                  <p>Última atividade de mensagens: <JourneyTimestamp value={data.summary?.activity?.last_event_at} />. Inatividade observada: {data.summary?.activity?.inactive_seconds == null ? "Desconhecida" : `${Math.floor(data.summary.activity.inactive_seconds / 60)} min`}. Não há inferência de abandono ou causa.</p>
                  <p>Primeira mensagem: <JourneyTimestamp value={data.summary?.activity?.first_message_at} />. Mensagens recebidas/enviadas: {data.summary?.activity?.inbound_messages ?? "Desconhecido"}/{data.summary?.activity?.outbound_messages ?? "Desconhecido"}.</p>
                  <p>Intervalos de resposta registrados: {data.summary?.activity?.recorded_response_intervals ?? "Desconhecido"}; média {data.summary?.activity?.mean_recorded_response_seconds == null ? "desconhecida" : `${data.summary.activity.mean_recorded_response_seconds} s`}. Intervalos não comprovam entrega.</p>
                </div></details>
                <details className="inbox-journey-summary-group"><summary>Atribuição persistida</summary><div><p>Atribuição por resolução persistida: {Object.entries(data.summary?.attribution?.status_counts || {}).map(([key, value]) => `${key}: ${value}`).join(" · ") || "Não observada"}. Identidade Ads verificada: {data.summary?.attribution?.ads_identity_verified ? "sim" : "não"}.</p></div></details>
              </div>
            </details>
            <details ref={coverageRef} className="inbox-journey-coverage"><summary>Cobertura das fontes</summary>
              <div className="inbox-journey-context-body">
                <p>A ausência de registro não comprova que o acontecimento não ocorreu.</p>
                {(data.coverage?.sources || []).map(source => <div key={`${source.source}:${source.category}`} className="inbox-journey-source"><h4>{source.source}</h4><p>{JOURNEY_CATEGORIES[source.category] || source.category}</p><p>{source.records} registros · {source.timeline_records} fatos na timeline · {source.missing_timestamp} sem timestamp</p><StatusBadge tone={journeyState(source.state).tone}>{journeyState(source.state).label}</StatusBadge><p>{source.limitation}</p></div>)}
                {(data.coverage?.limitations || []).map(limit => <p key={limit}>{limit}</p>)}
              </div>
            </details>
          </div>
        </div>}
      </>}
    </div>
  </aside>;
}
