import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Image as ImageIcon, Loader2, RefreshCw, Search, Video } from "lucide-react";
import api from "../../services/api";
import { ActionButton, InfoBanner } from "../../components/ui";
import { describeMetaReadError } from "../../utils/metaReadErrors";

function formatDate(value) {
  if (!value) return "Data não informada";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Data não informada" : date.toLocaleString("pt-BR");
}

export default function ExistingPostPicker({
  empresaId, pages = [], adAccountId = "", initialPageId = "", campaignDraft = {}, onPageChange,
  enabled = false, pagesLoading = false, pagesError = null, onRetryPages,
}) {
  const [platform, setPlatform] = useState("instagram");
  const [pageId, setPageId] = useState("");
  const [posts, setPosts] = useState([]);
  const [cursor, setCursor] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const requestSequence = useRef(0);
  const previewSequence = useRef(0);
  const previewInFlight = useRef(false);
  const selectedPageId = selected?.id_mapping?.page_id || "";
  const campaignReadyForPreview = Boolean(
    enabled
    && selected?.selection_token
    && campaignDraft.daily_budget_brl
    && campaignDraft.page_id
    && campaignDraft.page_id === selectedPageId
    && campaignDraft.whatsapp_connection_id,
  );

  const availablePages = useMemo(() => pages.filter((page) => platform !== "instagram" || page.instagram_account_id), [pages, platform]);
  const visiblePosts = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("pt-BR");
    if (!query) return posts;
    return posts.filter((post) => `${post.caption || ""} ${post.source_id || ""}`.toLocaleLowerCase("pt-BR").includes(query));
  }, [posts, search]);

  const previewContextKey = JSON.stringify([
    empresaId, selected?.selection_token, pageId, platform, campaignDraft.daily_budget_brl,
    campaignDraft.page_id, campaignDraft.whatsapp_connection_id, campaignDraft.location_type,
    campaignDraft.latitude, campaignDraft.longitude, campaignDraft.radius_km, campaignDraft.gender_mode,
  ]);

  useEffect(() => {
    if (!pageId && initialPageId && availablePages.some((page) => page.id === initialPageId)) setPageId(initialPageId);
  }, [availablePages, initialPageId, pageId]);

  const loadPosts = useCallback(async ({ append = false, nextCursor = "" } = {}) => {
    if (!enabled || !empresaId || !pageId || (append && !nextCursor)) return;
    const requestId = ++requestSequence.current;
    setLoading(true);
    setListError(null);
    setPreview(null);
    setPreviewError(null);
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/existing-posts`, {
        params: { platform, page_id: pageId, limit: 30, ...(nextCursor ? { after: nextCursor } : {}) },
        timeout: 30000,
      });
      if (requestId !== requestSequence.current) return;
      const nextPosts = response.data?.items || [];
      setPosts((current) => {
        const merged = append ? [...current, ...nextPosts] : nextPosts;
        const seen = new Set();
        return merged.filter((post) => {
          const key = `${post.source_platform}:${post.source_id}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      });
      if (!append) {
        setSelected((current) => current
          ? nextPosts.find((post) => post.source_platform === current.source_platform && post.source_id === current.source_id) || null
          : null);
      }
      setCursor(response.data?.next_cursor || "");
    } catch (requestError) {
      if (requestId !== requestSequence.current) return;
      setListError(describeMetaReadError(requestError));
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [enabled, empresaId, pageId, platform]);

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setListError(null);
    setPreviewError(null);
    setPosts([]);
    setCursor("");
    setSearch("");
    if (enabled && pageId) loadPosts();
    return () => { requestSequence.current += 1; };
  }, [enabled, loadPosts, pageId]);

  useEffect(() => {
    previewSequence.current += 1;
    previewInFlight.current = false;
    setPreviewLoading(false);
    setPreview(null);
    setPreviewError(null);
  }, [previewContextKey]);

  const simulateCampaign = async () => {
    if (!enabled || !selected?.selection_token || !campaignReadyForPreview || previewInFlight.current) return;
    const requestId = ++previewSequence.current;
    previewInFlight.current = true;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const response = await api.post(`/empresas/${empresaId}/traffic/meta/existing-posts/campaign-preview`, {
        selection_token: selected.selection_token,
        creative_name: `Criativo · ${selected.source_platform === "instagram" ? "Instagram" : "Facebook"} · ${selected.source_id}`,
        page_id: campaignDraft.page_id,
        whatsapp_connection_id: campaignDraft.whatsapp_connection_id,
        daily_budget_brl: campaignDraft.daily_budget_brl,
        location_type: campaignDraft.location_type || "brazil",
        latitude: campaignDraft.location_type === "radius" ? Number(campaignDraft.latitude) : null,
        longitude: campaignDraft.location_type === "radius" ? Number(campaignDraft.longitude) : null,
        radius_km: campaignDraft.location_type === "radius" ? Number(campaignDraft.radius_km) : null,
        gender_mode: campaignDraft.gender_mode || "all",
      }, { timeout: 30000 });
      if (requestId !== previewSequence.current) return;
      if (response.data?.creation_mode !== "local_mock" || response.data?.meta_write_performed !== false
        || response.data?.database_write_performed !== false || response.data?.external_side_effects !== false) {
        setPreviewError({ message: "A resposta não confirmou uma simulação local sem efeitos. A prévia foi bloqueada.", retryable: false });
        return;
      }
      setPreview(response.data);
    } catch (requestError) {
      if (requestId === previewSequence.current) setPreviewError(describeMetaReadError(requestError));
    } finally {
      if (requestId === previewSequence.current) {
        previewInFlight.current = false;
        setPreviewLoading(false);
      }
    }
  };

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="Selecionar publicação existente" aria-busy={loading || previewLoading}>
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-slate-900">Publicação existente</h3>
        <p className="text-sm text-slate-600">Consulte publicações da Página ou do Instagram vinculado. Nesta candidata, a seleção gera apenas uma prévia local do criativo.</p>
      </div>
      {!enabled ? <div role="status"><InfoBanner tone="warning">A leitura de publicações existentes está desativada por segurança até o operador liberar a integração. O fluxo de novo criativo continua disponível.</InfoBanner></div> : <InfoBanner tone="warning">Somente leitura e prévia local: nenhuma publicação, criativo, anúncio, campanha ou orçamento será enviado à Meta.</InfoBanner>}

      {enabled ? <>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 text-sm font-medium text-slate-700">
          Conta de anúncios configurada
          <div className="fm-field w-full truncate rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-normal" data-testid="existing-post-ad-account">{adAccountId || "Não configurada"}</div>
        </label>
        <label className="space-y-1 text-sm font-medium text-slate-700">
          Origem
          <select className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" value={platform} onChange={(event) => { setPlatform(event.target.value); setPageId(""); setSelected(null); setPreview(null); onPageChange?.(""); }}>
            <option value="instagram">Instagram</option>
            <option value="facebook">Facebook</option>
          </select>
        </label>
        <label className="space-y-1 text-sm font-medium text-slate-700">
          Página / conta vinculada
          <select className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" value={pageId} onChange={(event) => { setPageId(event.target.value); onPageChange?.(event.target.value); }}>
            <option value="">Selecione uma conta</option>
            {availablePages.map((page) => <option key={page.id} value={page.id}>{page.name || "Página Meta"}{platform === "instagram" && page.instagram_username ? ` · @${page.instagram_username}` : ""}</option>)}
          </select>
        </label>
      </div>

      {pagesLoading && !availablePages.length ? <div role="status" aria-live="polite" className="text-sm text-slate-600">Carregando Páginas vinculadas…</div> : null}
      {!pagesLoading && !availablePages.length && !pagesError ? <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">Nenhuma Página compatível foi retornada pela credencial da empresa.</p> : null}
      {pagesError ? <div role="alert" className="space-y-2"><InfoBanner tone="warning">{pagesError.message}</InfoBanner>{pagesError.retryable && onRetryPages ? <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={onRetryPages} disabled={pagesLoading}>{pagesLoading ? "Atualizando Páginas…" : "Tentar novamente"}</ActionButton> : null}</div> : null}
      {listError ? <div role="alert" className="space-y-2"><InfoBanner tone="warning">{listError.message}{posts.length ? " A lista anterior continua visível." : ""}</InfoBanner>{listError.retryable && pageId ? <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={() => loadPosts()} disabled={loading}>{loading ? "Consultando…" : "Tentar novamente"}</ActionButton> : null}</div> : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="relative min-w-0 flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input className="fm-field w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm" placeholder="Buscar nas publicações carregadas" value={search} onChange={(event) => setSearch(event.target.value)} disabled={!pageId} />
        </label>
        <ActionButton type="button" variant="secondary" icon={loading ? Loader2 : RefreshCw} onClick={() => loadPosts()} disabled={!pageId || loading}>{loading ? "Carregando..." : "Atualizar lista"}</ActionButton>
      </div>

      {loading && !posts.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" role="status" aria-label="Carregando publicações">{[0, 1, 2].map((item) => <div key={item} className="h-52 animate-pulse rounded-lg bg-slate-100" />)}</div> : null}
      {!loading && pageId && !posts.length && !listError ? <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-600">Nenhuma publicação retornada para esta conta.</p> : null}

      {visiblePosts.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {visiblePosts.map((post) => {
          const supported = Boolean(post.selection_token);
          const active = selected?.source_id === post.source_id;
          return <article key={`${post.source_platform}:${post.source_id}`} className={`min-w-0 overflow-hidden rounded-xl border transition ${active ? "border-indigo-500 ring-2 ring-indigo-100" : "border-slate-200"}`}>
            {post.media_type === "VIDEO" && post.media_url ? <video className="h-44 w-full bg-slate-100 object-cover" src={post.media_url} poster={post.thumbnail_url || undefined} controls preload="none" referrerPolicy="no-referrer" aria-label="Prévia de vídeo" /> : post.thumbnail_url ? <img className="h-44 w-full bg-slate-100 object-cover" src={post.thumbnail_url} alt="Prévia da publicação" loading="lazy" referrerPolicy="no-referrer" /> : <div className="flex h-44 items-center justify-center bg-slate-100 text-slate-400"><ImageIcon size={32} /></div>}
            <button key={`${post.source_platform}:${post.source_id}:select`} type="button" onClick={() => { setSelected(supported ? post : null); setPreview(null); }} disabled={!supported} aria-pressed={active} aria-label={`Selecionar ${post.caption || post.source_id}`} className={`w-full space-y-2 p-3 text-left transition ${!supported ? "cursor-not-allowed opacity-60" : "hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"}`}>
              <div className="flex items-center justify-between gap-2 text-xs text-slate-500"><span className="inline-flex items-center gap-1">{post.media_type === "VIDEO" ? <Video size={14} /> : <ImageIcon size={14} />}{post.media_type}{post.media_product_type ? ` · ${post.media_product_type}` : ""}</span>{active ? <CheckCircle2 size={16} className="text-indigo-600" /> : null}</div>
              <p className="line-clamp-3 min-h-14 break-words text-sm text-slate-700">{post.caption || "Publicação sem legenda"}</p>
              <p className="text-xs text-slate-500">{formatDate(post.created_at)}</p>
              <p className={`text-xs font-medium ${supported ? "text-emerald-700" : "text-amber-800"}`}>{post.media_type === "VIDEO" && !post.media_url ? "Vídeo compatível · exibindo miniatura; reprodução indisponível" : supported ? "Formato elegível para prévia" : "Formato sem compatibilidade confirmada"}</p>
            </button>
          </article>;
        })}
      </div> : null}

      {search && !visiblePosts.length && posts.length ? <p className="text-sm text-slate-500">Nenhuma publicação carregada corresponde à busca. Use “Carregar mais” para pesquisar outras páginas.</p> : null}
      {cursor ? <div className="flex justify-center"><ActionButton type="button" variant="secondary" onClick={() => loadPosts({ append: true, nextCursor: cursor })} disabled={loading}>{loading ? "Carregando..." : "Carregar mais publicações"}</ActionButton></div> : null}

      {selected ? <div className="space-y-3 rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div><p className="text-sm font-semibold text-slate-900">Publicação selecionada</p><p className="break-all text-xs text-slate-600">{selected.source_platform === "facebook" ? "Post ID" : "Media ID"}: {selected.source_id}</p></div>
          <ActionButton type="button" onClick={simulateCampaign} disabled={previewLoading || !campaignReadyForPreview}>{previewLoading ? "Validando e simulando..." : "Simular campanha e anúncio"}</ActionButton>
        </div>
        {!campaignReadyForPreview ? <p className="text-xs text-amber-800">Preencha orçamento e WhatsApp da campanha e mantenha a Página do anúncio igual à publicação selecionada para simular no builder.</p> : null}
        <p className="text-xs text-slate-600">A compatibilidade exibida é preliminar. A confirmação final de formato, objetivo e permissões depende de validação da Marketing API em uma etapa real autorizada.</p>
        {previewError ? <div role="alert" className="space-y-2"><InfoBanner tone="warning">{previewError.message}{preview ? " A última simulação válida continua visível." : " Nenhuma criação foi confirmada."}</InfoBanner>{previewError.retryable ? <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={simulateCampaign} disabled={previewLoading || !campaignReadyForPreview}>{previewLoading ? "Validando novamente…" : "Tentar validar novamente"}</ActionButton> : null}</div> : null}
        {preview ? <div className="space-y-2 rounded-lg border border-emerald-200 bg-white p-3" role="status" data-testid="existing-post-preview-result">
          <p className="text-sm font-semibold text-emerald-900">Prévia local do fluxo · nada foi salvo ou criado na Meta</p>
          <p className="text-xs text-slate-600">Post ID, Media ID, Comment ID e Creative ID são identificadores diferentes. A simulação deixa os que não existem como null.</p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs text-slate-700">{JSON.stringify({ state: preview.state, meta_write_performed: preview.meta_write_performed, database_write_performed: preview.database_write_performed, id_mapping: preview.id_mapping, compatibility: preview.compatibility, builder_preview: preview.builder_preview }, null, 2)}</pre>
        </div> : null}
      </div> : null}
      </> : null}
    </section>
  );
}
