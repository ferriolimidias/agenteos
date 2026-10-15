import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Image as ImageIcon, Loader2, RefreshCw, Search, Video } from "lucide-react";
import api from "../../services/api";
import { ActionButton, InfoBanner } from "../../components/ui";

function errorText(error) {
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === "object") return detail.message || "A consulta à Meta não foi concluída.";
  return typeof detail === "string" ? detail : "Não foi possível carregar as publicações. Verifique as permissões da credencial Meta da empresa.";
}

function formatDate(value) {
  if (!value) return "Data não informada";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Data não informada" : date.toLocaleString("pt-BR");
}

export default function ExistingPostPicker({ empresaId, pages = [], adAccountId = "", initialPageId = "", campaignDraft = {}, onPageChange }) {
  const [platform, setPlatform] = useState("instagram");
  const [pageId, setPageId] = useState("");
  const [posts, setPosts] = useState([]);
  const [cursor, setCursor] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const requestSequence = useRef(0);
  const selectedPageId = selected?.id_mapping?.page_id || "";
  const campaignReadyForPreview = Boolean(
    selected?.selection_token
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

  useEffect(() => {
    if (!pageId && initialPageId && availablePages.some((page) => page.id === initialPageId)) setPageId(initialPageId);
  }, [availablePages, initialPageId, pageId]);

  const loadPosts = useCallback(async ({ append = false, nextCursor = "" } = {}) => {
    if (!empresaId || !pageId) return;
    const requestId = ++requestSequence.current;
    setLoading(true);
    setError("");
    setPreview(null);
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/existing-posts`, {
        params: { platform, page_id: pageId, limit: 30, ...(nextCursor ? { after: nextCursor } : {}) },
        timeout: 30000,
      });
      if (requestId !== requestSequence.current) return;
      const nextPosts = response.data?.items || [];
      setPosts((current) => append ? [...current, ...nextPosts] : nextPosts);
      setCursor(response.data?.next_cursor || "");
    } catch (requestError) {
      if (requestId !== requestSequence.current) return;
      setError(errorText(requestError));
      if (!append) setPosts([]);
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [empresaId, pageId, platform]);

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setPosts([]);
    setCursor("");
    if (pageId) loadPosts();
    return () => { requestSequence.current += 1; };
  }, [loadPosts, pageId]);

  const simulateCampaign = async () => {
    if (!selected?.selection_token) return;
    setPreviewLoading(true);
    setError("");
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
      setPreview(response.data || null);
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="Selecionar publicação existente">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-slate-900">Publicação existente</h3>
        <p className="text-sm text-slate-600">Consulte publicações da Página ou do Instagram vinculado. Nesta candidata, a seleção gera apenas uma prévia local do criativo.</p>
      </div>
      <InfoBanner tone="warning">Somente leitura: nenhuma publicação, criativo, anúncio, campanha ou orçamento será enviado à Meta.</InfoBanner>

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

      {!availablePages.length ? <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">Nenhuma Página compatível foi retornada pela credencial autorizada.</p> : null}
      {error ? <InfoBanner tone="warning">{error}</InfoBanner> : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="relative min-w-0 flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input className="fm-field w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm" placeholder="Buscar nas publicações carregadas" value={search} onChange={(event) => setSearch(event.target.value)} disabled={!pageId} />
        </label>
        <ActionButton type="button" variant="secondary" icon={loading ? Loader2 : RefreshCw} onClick={() => loadPosts()} disabled={!pageId || loading}>{loading ? "Carregando..." : "Atualizar lista"}</ActionButton>
      </div>

      {loading && !posts.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((item) => <div key={item} className="h-52 animate-pulse rounded-lg bg-slate-100" />)}</div> : null}
      {!loading && pageId && !posts.length && !error ? <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-600">Nenhuma publicação retornada para esta conta.</p> : null}

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
        {preview ? <div className="space-y-2 rounded-lg border border-emerald-200 bg-white p-3" role="status" data-testid="existing-post-preview-result">
          <p className="text-sm font-semibold text-emerald-900">Fluxo completo simulado · nenhuma estrutura foi salva ou criada na Meta</p>
          <p className="text-xs text-slate-600">Post ID, Media ID, Comment ID e Creative ID são identificadores diferentes. A simulação deixa os que não existem como null.</p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs text-slate-700">{JSON.stringify({ state: preview.state, meta_write_performed: preview.meta_write_performed, database_write_performed: preview.database_write_performed, id_mapping: preview.id_mapping, compatibility: preview.compatibility, builder_preview: preview.builder_preview }, null, 2)}</pre>
        </div> : null}
      </div> : null}
    </section>
  );
}
