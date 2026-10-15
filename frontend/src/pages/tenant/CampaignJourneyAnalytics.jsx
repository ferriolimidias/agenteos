import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, BarChart3, CheckCircle2, Eye, GitBranch, RefreshCw, Send, Users } from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { a10Rate, a10StateLabel } from "../../utils/campaignJourneyAnalytics";
import { ActionButton, EmptyState, InfoBanner, LoadingState, MetricCard, StatusBadge } from "../../components/ui";

const DAY = 86_400_000;
const ORIGIN_LABELS = {
  ctwa_referral: "Origem CTWA", tracked_link_click: "Link rastreado",
  manual_touch: "Origem manual", unknown_source: "Origem não identificada", unknown: "Origem desconhecida",
};
const FUNNEL_LABELS = {
  eligible_snapshot: "Elegíveis no snapshot", attempted: "Tentativa persistida",
  sent: "Aceitos pelo provider", delivered: "Entregues", read: "Lidos",
  reply: "Respostas", reactivation: "Reativações observadas",
  stage_advance: "Avanços comerciais observados", purchase: "Compras verificadas após envio",
};

function utcInput(value) {
  return new Date(value).toISOString().slice(0, 16);
}

function toUtc(value) {
  return value ? `${value}:00Z` : "";
}

function number(value) {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString("pt-BR");
}

function utcTime(value) {
  if (!value) return "";
  return `${new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(value))} UTC`;
}

function stateTone(value) {
  return value === "observed" ? "active" : value === "partial" ? "warning" : "neutral";
}

export default function CampaignJourneyAnalytics({ campaigns = [] }) {
  const tenantId = getActiveEmpresaId();
  const now = useMemo(() => new Date(), []);
  const [period, setPeriod] = useState({ start: utcInput(now.getTime() - 30 * DAY), end: utcInput(now) });
  const [campaignIds, setCampaignIds] = useState([]);
  const [responseWindow, setResponseWindow] = useState(7);
  const [reactivationWindow, setReactivationWindow] = useState(7);
  const [stageWindow, setStageWindow] = useState(30);
  const [purchaseWindow, setPurchaseWindow] = useState(30);
  const [data, setData] = useState(null);
  const [selectedCampaignId, setSelectedCampaignId] = useState("");
  const [selectedOrigin, setSelectedOrigin] = useState("");
  const [recipients, setRecipients] = useState(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [error, setError] = useState("");
  const [evidenceError, setEvidenceError] = useState("");

  const loadDashboard = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    setError("");
    try {
      const params = {
        start: toUtc(period.start), end: toUtc(period.end), response_window_days: responseWindow,
        reactivation_days: reactivationWindow, stage_window_days: stageWindow, purchase_window_days: purchaseWindow,
      };
      if (campaignIds.length) params.campaign_ids = campaignIds.join(",");
      const response = await api.get(`/empresas/${tenantId}/campaign-central/journey`, { params });
      const next = response?.data || null;
      setData(next);
      setSelectedCampaignId((current) => next?.campaigns?.some((item) => item.campaign_id === current)
        ? current : next?.campaigns?.[0]?.campaign_id || "");
      setSelectedOrigin("");
      setOffset(0);
      setRecipients(null);
      setEvidenceError("");
    } catch (requestError) {
      setData(null);
      setError(requestError?.response?.data?.detail || "Não foi possível carregar a jornada das campanhas.");
    } finally {
      setLoading(false);
    }
  }, [tenantId, period, responseWindow, reactivationWindow, stageWindow, purchaseWindow, campaignIds]);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const currentCampaign = data?.campaigns?.find((item) => item.campaign_id === selectedCampaignId) || data?.campaigns?.[0] || null;
  const currentSegments = (data?.segments || []).filter((item) => item.campaign_id === currentCampaign?.campaign_id);
  const origins = [...new Set(currentSegments.map((item) => item.origin))];

  const loadRecipients = useCallback(async (nextOffset = 0) => {
    if (!tenantId || !currentCampaign?.campaign_id) return;
    setEvidenceLoading(true);
    setEvidenceError("");
    try {
      const params = {
        campaign_id: currentCampaign.campaign_id, start: toUtc(period.start), end: toUtc(period.end),
        response_window_days: responseWindow, reactivation_days: reactivationWindow,
        stage_window_days: stageWindow, purchase_window_days: purchaseWindow, limit: 25, offset: nextOffset,
      };
      if (selectedOrigin) params.segment = selectedOrigin;
      const response = await api.get(`/empresas/${tenantId}/campaign-central/journey/recipients`, { params });
      setRecipients(response?.data || null);
      setOffset(nextOffset);
    } catch (requestError) {
      setRecipients(null);
      setEvidenceError(requestError?.response?.data?.detail || "Não foi possível carregar os destinatários desta campanha.");
    } finally {
      setEvidenceLoading(false);
    }
  }, [tenantId, currentCampaign?.campaign_id, period, selectedOrigin, responseWindow, reactivationWindow, stageWindow, purchaseWindow]);

  const selectedCampaignOptions = useMemo(() => campaigns.map((campaign) => ({
    id: String(campaign.id), name: campaign.name || campaign.nome || `Campanha ${String(campaign.id).slice(0, 8)}`,
  })), [campaigns]);

  if (loading && !data) return <LoadingState label="Carregando jornada das campanhas..." />;

  return (
    <section className="space-y-5" data-testid="campaign-journey-analytics">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-sm font-semibold text-slate-700">
            Comparar campanhas (até 5)
            <select multiple value={campaignIds} onChange={(event) => setCampaignIds(Array.from(event.target.selectedOptions, (item) => item.value).slice(0, 5))}
              aria-label="Comparar campanhas, até cinco" className="mt-1 min-h-28 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {selectedCampaignOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold text-slate-700">Início<input type="datetime-local" value={period.start} onChange={(event) => setPeriod((current) => ({ ...current, start: event.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
          <label className="text-sm font-semibold text-slate-700">Fim<input type="datetime-local" value={period.end} onChange={(event) => setPeriod((current) => ({ ...current, end: event.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
          <label className="text-sm font-semibold text-slate-700">
            Janela para respostas
            <select value={responseWindow} onChange={(event) => setResponseWindow(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {[1, 3, 7, 14, 30].map((days) => <option key={days} value={days}>{days} dias</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold text-slate-700">
            Janela de reativação
            <select value={reactivationWindow} onChange={(event) => setReactivationWindow(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {[1, 3, 7, 14, 30, 60, 90].map((days) => <option key={days} value={days}>{days} dias</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold text-slate-700">
            Janela de avanço comercial
            <select value={stageWindow} onChange={(event) => setStageWindow(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {[7, 14, 30, 60, 90].map((days) => <option key={days} value={days}>{days} dias</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold text-slate-700">
            Janela de compra verificada
            <select value={purchaseWindow} onChange={(event) => setPurchaseWindow(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {[7, 14, 30, 60, 90].map((days) => <option key={days} value={days}>{days} dias</option>)}
            </select>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-slate-500">Datas e horários em UTC. Sem seleção, compara as cinco campanhas mais recentes com destinatários no período.</p>
          <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={loadDashboard} disabled={loading}>{loading ? "Atualizando..." : "Atualizar análise"}</ActionButton>
        </div>
      </div>

      {error ? <InfoBanner tone="warning">{error}</InfoBanner> : null}
      {loading && data ? <p role="status" className="text-sm text-slate-500">Atualizando indicadores…</p> : null}

      {!error && !data?.campaigns?.length ? <EmptyState title="Sem dados de campanha neste período" description="A jornada usa destinatários, status e eventos já persistidos. Nenhuma campanha ou envio será criado aqui." icon={BarChart3} /> : null}

      {data?.campaigns?.length ? (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.campaigns.map((campaign) => (
              <button key={campaign.campaign_id} type="button" onClick={() => { setSelectedCampaignId(campaign.campaign_id); setRecipients(null); }}
                aria-pressed={currentCampaign?.campaign_id === campaign.campaign_id}
                className={`rounded-xl border bg-white p-4 text-left transition ${currentCampaign?.campaign_id === campaign.campaign_id ? "border-slate-900 ring-2 ring-slate-200" : "border-slate-200 hover:border-slate-400"}`}>
                <div className="flex items-start justify-between gap-3"><span className="font-semibold text-slate-900">{campaign.name}</span><StatusBadge tone={stateTone(campaign.coverage?.state)}>{a10StateLabel(campaign.coverage?.state)}</StatusBadge></div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-sm text-slate-600">
                  <span>Elegíveis <b className="text-slate-950">{number(campaign.eligible_snapshot?.value)}</b></span>
                  <span>Aceitos <b className="text-slate-950">{number(campaign.delivery?.meta_accepted)}</b></span>
                  <span>Entregues <b className="text-slate-950">{number(campaign.delivery?.delivered)}</b></span>
                  <span>Respostas posteriores <b className="text-slate-950">{a10Rate(campaign.response_after_send)}</b></span>
                </div>
              </button>
            ))}
          </div>

          {currentCampaign ? (
            <>
              <div className="rounded-xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><h2 className="text-lg font-semibold text-slate-950">Jornada · {currentCampaign.name}</h2><p className="mt-1 text-sm text-slate-500">Aceite, entrega e leitura vêm do Campaign Central. Resultados posteriores são correlação temporal, não causalidade.</p></div>
                  <StatusBadge tone={stateTone(currentCampaign.coverage?.state)}>{a10StateLabel(currentCampaign.coverage?.state)}</StatusBadge>
                </div>
                <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <MetricCard label="Elegíveis no snapshot" value={number(currentCampaign.eligible_snapshot?.value)} helper={`${a10StateLabel(currentCampaign.eligible_snapshot?.state)} · total congelado, não limitado ao período`} icon={Users} />
                  <MetricCard label="Envios tentados" value={number(currentCampaign.attempted?.value)} helper={`de ${number(currentCampaign.attempted?.denominator)} amostrados`} icon={Send} />
                  <MetricCard label="Entregas confirmadas" value={number(currentCampaign.delivery?.delivered)} helper={a10Rate(currentCampaign.delivery_rates?.delivered)} icon={CheckCircle2} tone="active" />
                  <MetricCard label="Respostas posteriores" value={a10Rate(currentCampaign.response_after_send)} helper={`assistência com toque explícito: ${number(currentCampaign.campaign_assistance?.reply_touches)}`} icon={GitBranch} />
                </div>
                <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <MetricCard label="Reativações" value={a10Rate(currentCampaign.reactivation_after_send)} helper="Resposta após intervalo sem inbound" icon={RefreshCw} />
                  <MetricCard label="Avanços de etapa" value={a10Rate(currentCampaign.stage_advance_after_send)} helper={currentCampaign.stage_advance_after_send?.state === "unknown" ? "Etapas configuradas ausentes" : "Evento posterior na mesma conversa"} icon={BarChart3} />
                  <MetricCard label="Compras verificadas" value={a10Rate(currentCampaign.verified_purchase_after_send)} helper="Correlação temporal; não causalidade" icon={CheckCircle2} />
                  <MetricCard label="Opt-outs" value={number(currentCampaign.opt_out?.snapshot_exclusions)} helper={`atuais entre destinatários: ${number(currentCampaign.opt_out?.current_recipients)}`} icon={AlertTriangle} tone="warning" />
                </div>

                <h3 className="mt-6 text-sm font-semibold text-slate-900">Funil e estados</h3>
                <ol className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {(currentCampaign.funnel || []).map((step) => (
                    <li key={step.key} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 p-3 text-sm">
                      <span className="text-slate-700">{FUNNEL_LABELS[step.key] || step.label}</span>
                      <span className="flex items-center gap-2 font-semibold text-slate-950">{number(step.value)}{step.denominator !== null && step.denominator !== undefined ? ` / ${number(step.denominator)}` : ""} <span className="text-xs font-normal text-slate-500">{a10StateLabel(step.state)}</span></span>
                    </li>
                  ))}
                </ol>
                <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
                  <p><b>Origem inicial</b> é agrupada pelo primeiro touch de aquisição persistido. <b>Assistência</b> é mostrada separadamente pelos toques `campaign_sent` e `campaign_reply` ligados ao destinatário.</p>
                  <p className="mt-1">{number(currentCampaign.failed_cancelled?.failed)} falhas · {number(currentCampaign.failed_cancelled?.cancelled)} cancelados · {number(currentCampaign.failed_cancelled?.stopped)} parados · {number(currentCampaign.failed_cancelled?.blocked)} bloqueados</p>
                  <p className="mt-1">Bloqueios registrados: consentimento {number(currentCampaign.failed_cancelled?.reasons?.consent_block)} · lista permitida {number(currentCampaign.failed_cancelled?.reasons?.allowlist_block)} · variável de template ausente {number(currentCampaign.failed_cancelled?.reasons?.template_missing)} · demais falhas codificadas {number(currentCampaign.failed_cancelled?.reasons?.other_recorded_failure)}.</p>
                  <p className="mt-1">Tentativa não significa aceite; aceite não significa entrega. Ausência de evento de provider mantém cobertura parcial.</p>
                </div>
              </div>

              <div className="rounded-xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div><h3 className="text-base font-semibold text-slate-950">Segmentos por origem inicial</h3><p className="mt-1 text-sm text-slate-500">O segmento desconhecido permanece visível quando falta touch de origem.</p></div>
                  <label className="min-w-56 text-sm font-semibold text-slate-700">Origem para detalhar
                    <select value={selectedOrigin} onChange={(event) => { setSelectedOrigin(event.target.value); setRecipients(null); }} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                      <option value="">Todas as origens</option>
                      {origins.map((origin) => <option key={origin} value={origin}>{ORIGIN_LABELS[origin] || origin}</option>)}
                    </select>
                  </label>
                </div>
                <div className="mt-4 overflow-x-auto" role="region" aria-label="Métricas por origem inicial" tabIndex={0}>
                  <table className="min-w-full divide-y divide-slate-200 text-sm">
                    <thead><tr className="text-left text-xs uppercase text-slate-500"><th className="px-3 py-2">Origem</th><th className="px-3 py-2">Destinatários</th><th className="px-3 py-2">Aceitos</th><th className="px-3 py-2">Entregues</th><th className="px-3 py-2">Lidos</th><th className="px-3 py-2">Respostas</th><th className="px-3 py-2">Reativações</th><th className="px-3 py-2">Etapas</th><th className="px-3 py-2">Compras</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {currentSegments.map((segment) => <tr key={segment.origin}>
                        <td className="px-3 py-3 font-medium">{ORIGIN_LABELS[segment.origin] || segment.origin}</td>
                        <td className="px-3 py-3">{number(segment.recipient_denominator)} · {a10StateLabel(segment.state)}</td>
                        <td className="px-3 py-3">{a10Rate(segment.delivery_rates?.accepted)}</td><td className="px-3 py-3">{a10Rate(segment.delivery_rates?.delivered)}</td>
                        <td className="px-3 py-3">{a10Rate(segment.delivery_rates?.read)}</td><td className="px-3 py-3">{a10Rate(segment.reply_after_send)}</td>
                        <td className="px-3 py-3">{a10Rate(segment.reactivation_after_send)}</td><td className="px-3 py-3">{a10Rate(segment.stage_advance_after_send)}</td>
                        <td className="px-3 py-3">{a10Rate(segment.verified_purchase_after_send)}</td>
                      </tr>)}
                      {!currentSegments.length ? <tr><td colSpan="9" className="px-3 py-5 text-center text-slate-500">Nenhum segmento com eventos de destinatário neste período.</td></tr> : null}
                    </tbody>
                  </table>
                </div>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-slate-500">Drill-down exibe apenas identificadores operacionais e marcos; sem telefone, nome ou conteúdo de mensagem.</p>
                  <ActionButton type="button" variant="secondary" icon={Eye} onClick={() => loadRecipients(0)} disabled={evidenceLoading}>{evidenceLoading ? "Carregando..." : "Ver destinatários"}</ActionButton>
                </div>
                {evidenceError ? <div className="mt-3"><InfoBanner tone="warning">{evidenceError}</InfoBanner></div> : null}
                {evidenceLoading && !recipients ? <p role="status" className="mt-3 text-sm text-slate-500">Carregando evidências…</p> : null}
                {recipients ? (
                  <div className="mt-4 overflow-x-auto" role="region" aria-label="Destinatários e evidências" tabIndex={0}>
                    <table className="min-w-full divide-y divide-slate-200 text-sm">
                      <thead><tr className="text-left text-xs uppercase text-slate-500"><th className="px-3 py-2">Destinatário</th><th className="px-3 py-2">Origem</th><th className="px-3 py-2">Envio</th><th className="px-3 py-2">Evidências posteriores</th><th className="px-3 py-2">Conversa</th></tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {(recipients.items || []).map((item) => <tr key={item.recipient_id}>
                          <td className="px-3 py-3">Contato · {String(item.lead_id || item.recipient_id).slice(-6)}</td>
                          <td className="px-3 py-3">{ORIGIN_LABELS[item.origin_segment] || item.origin_segment}</td>
                          <td className="px-3 py-3">{item.status || "unknown"} {item.sent_at ? `· ${utcTime(item.sent_at)}` : item.acceptance_time_unknown ? "· aceite sem horário observado" : ""}</td>
                          <td className="px-3 py-3">{[
                            item.replied && "resposta",
                            item.reactivated && "reativação",
                            item.stage_advanced && "avanço de etapa",
                            item.verified_purchase && "compra verificada após envio",
                            item.campaign_sent_touch && "assistência registrada",
                            item.campaign_reply_touch && "resposta vinculada à campanha",
                          ].filter(Boolean).join(" · ") || "Nenhum evento observado"}</td>
                          <td className="px-3 py-3">{item.lead_id ? <Link className="font-semibold text-slate-900 underline" to={`/painel/inbox?journey=1&lead=${item.lead_id}`}>Abrir evidência</Link> : <span className="text-slate-500">Lead não vinculado</span>}</td>
                        </tr>)}
                        {!recipients.items?.length ? <tr><td colSpan="5" className="px-3 py-5 text-center text-slate-500">Nenhum destinatário nesta página.</td></tr> : null}
                      </tbody>
                    </table>
                    <div className="mt-3 flex justify-end gap-2">
                      <ActionButton type="button" variant="secondary" onClick={() => loadRecipients(Math.max(0, offset - 25))} disabled={evidenceLoading || offset === 0}>Anterior</ActionButton>
                      <ActionButton type="button" variant="secondary" onClick={() => loadRecipients(offset + 25)} disabled={evidenceLoading || !recipients.pagination?.has_more}>Próxima</ActionButton>
                    </div>
                  </div>
                ) : null}
              </div>
              <InfoBanner>
                Cobertura da análise: {a10StateLabel(data.coverage?.state)} · máximo {number(data.coverage?.sample_limit_per_campaign)} destinatários por campanha. Janelas de resposta, etapa e compra podem continuar abertas; amostras limitadas aparecem como parciais.
              </InfoBanner>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
