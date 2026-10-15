import "../../styles/ux05-analytics.css";
import { useEffect, useMemo, useState } from "react";
import { BarChart3, CheckCircle2, Eye, Loader2, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { loadFacebookSdk, startMetaMarketingAuthorization, sanitizeMetaBrowserError } from "../../utils/metaEmbeddedSignup";
import { ActionButton, EmptyState, InfoBanner, LoadingState, PageHeader, SectionCard, StatusBadge } from "../../components/ui";
import ExistingPostPicker from "./ExistingPostPicker";

const emptyConfig = {
  ad_account_id: "",
};

const emptyStatus = {
  marketing_graph_version: "v26.0",
  config: {
    configured: false,
    status: "missing",
    business_id: null,
    ad_account_id: null,
    page_id: null,
    instagram_account_id: null,
    whatsapp_connection_id: null,
    waba_id: null,
    phone_number_id: null,
    dataset_id: null,
    dataset_name: null,
    dataset_name_is_generic: false,
  },
  official_whatsapp: { detected: false },
  official_whatsapps: [],
  active_official_whatsapps: [],
  meta_write_enabled: false,
  campaign_create_enabled: false,
  adset_create_enabled: false,
  creative_create_enabled: false,
  ad_create_enabled: false,
};

const MARKETING_RECONNECT_STATUSES = new Set(["REAUTH_MARKETING_REQUIRED", "REAUTH_REQUIRED", "TOKEN_INVALID"]);

const makeEmptyAd = () => ({
  media_type: "image",
  media_data_url: "",
  media_name: "",
  item_key: "",
  thumbnail_url: "",
  analysis_status: "idle",
  analysis_error: "",
  primary_text: "",
  headline: "",
  description: "",
});

function safeError(error, fallback) {
  const detail = error?.response?.data?.detail;
  const creation = detail?.creation;
  const graphError = creation?.error || detail?.error;
  if (detail?.partial_failure) return "A criação ficou parcial. Os objetos confirmados foram preservados e podem ser retomados.";
  if (detail?.success === false && creation?.status === "error") {
    return graphError?.message ? `Não foi possível criar a estrutura na Meta. A campanha não foi criada. Meta: ${graphError.message}` : "Não foi possível criar a estrutura na Meta. A campanha não foi criada.";
  }
  if (detail?.friendly_message) return detail.friendly_message;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  if (detail?.code) return detail.code;
  return fallback;
}

function normalizeAdAccountId(value) {
  const raw = String(value || "").trim();
  return raw.startsWith("act_") ? raw.slice(4) : raw;
}

function maskTail(value) {
  const text = String(value || "").replace(/^act_/, "");
  if (!text) return "não configurado";
  return `••••${text.slice(-4)}`;
}

function accountStatusText(value) {
  const text = String(value ?? "").trim();
  if (text === "1" || text.toLowerCase() === "active") return "Ativa";
  if (!text) return "Não retornado";
  return text;
}

function formatBrlCents(value) {
  const cents = Number(value || 0);
  return (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function formatBudget(value) {
  return value ? `${formatBrlCents(value)}/dia` : "Sem orçamento no objeto";
}

function statusText(value) {
  return String(value || "Não retornado").replaceAll("_", " ");
}

function creativeItemStatus(ad) {
  if (ad.analysis_status === "pending") return "Na fila para análise";
  if (ad.analysis_status === "analyzing") return "Analisando a imagem...";
  if (ad.analysis_status === "generating") return "Gerando texto, título e descrição...";
  if (ad.analysis_status === "error") return "Erro na análise";
  if (ad.meta_status === "ad_created" || ad.ad_id) return "Anúncio criado";
  if (ad.meta_status === "creative_created" || ad.creative_id) return "Criativo criado";
  if (ad.meta_status === "media_uploaded" || ad.media_reference) return "Imagem enviada";
  if (ad.analysis_status === "ready" && ad.primary_text && ad.headline) return "Copy pronta";
  return "Aguardando análise";
}

function analysisBusy(ad) {
  return ["pending", "analyzing", "generating"].includes(ad.analysis_status);
}

function analysisReady(ad) {
  return ad.analysis_status === "ready" && Boolean(ad.primary_text && ad.headline);
}

function creativeSummary(creative) {
  const story = creative?.object_story_spec || {};
  const linkData = story.link_data || {};
  const videoData = story.video_data || {};
  return {
    headline: linkData.name || videoData.title || creative?.name || "Creative",
    text: linkData.message || videoData.message || "",
    description: linkData.description || videoData.description || "",
    thumbnail: creative?.thumbnail_url || "",
  };
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

const draftStoreName = "agentos-traffic-meta-drafts";

function openDraftStore() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("indexeddb_unavailable"));
      return;
    }
    const request = window.indexedDB.open(draftStoreName, 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("indexeddb_open_failed"));
  });
}

async function saveCampaignDraft(empresaId, value) {
  try {
    const db = await openDraftStore();
    await new Promise((resolve, reject) => {
      const request = db.transaction("drafts", "readwrite").objectStore("drafts").put({ id: empresaId, ...value });
      request.onsuccess = resolve;
      request.onerror = () => reject(request.error);
    });
    db.close();
  } catch {
    try { window.localStorage.setItem(`agentos-traffic-meta-draft:${empresaId}`, JSON.stringify(value)); } catch { /* quota/private mode */ }
  }
}

async function loadCampaignDraft(empresaId) {
  try {
    const db = await openDraftStore();
    const value = await new Promise((resolve, reject) => {
      const request = db.transaction("drafts", "readonly").objectStore("drafts").get(empresaId);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
    db.close();
    if (value) return value;
  } catch { /* fallback below */ }
  try { return JSON.parse(window.localStorage.getItem(`agentos-traffic-meta-draft:${empresaId}`) || "null"); } catch { return null; }
}

async function fingerprintFile(file) {
  try {
    const digest = await window.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
  } catch {
    return `${file.name}:${file.size}:${file.lastModified}`;
  }
}

async function readValidatedImage(file) {
  if (!file || !["image/jpeg", "image/png"].includes(file.type) || file.size <= 0) throw new Error("invalid_image");
  const dataUrl = await fileToDataUrl(file);
  await new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => image.width >= 10 && image.height >= 10 ? resolve() : reject(new Error("invalid_dimensions"));
    image.onerror = () => reject(new Error("invalid_image"));
    image.src = dataUrl;
  });
  return dataUrl;
}

function credentialSourceLabel(credentials) {
  const source = credentials?.credential_source;
  if (source === "tenant_marketing_oauth") return "Token tenant de leitura de anúncios";
  if (source === "embedded_signup_tenant") return "Token da empresa";
  if (source === "admin_managed_marketing_token" || source === "admin_global_fallback" || source === "admin_marketing_fallback" || source === "admin_test_fallback") return "Credencial administrativa de Marketing (somente leitura)";
  if (credentials?.credential_error) return "Não configurada";
  return "Indisponível";
}

function whatsappLabel(item) {
  const label = item?.verified_name || item?.label || item?.name || "WhatsApp Oficial";
  return item?.display_number ? `${label} · ${item.display_number}` : label;
}

function AssetLine({ title, detected, value, detail }) {
  return (
    <div className="ux05-asset-card flex items-start justify-between gap-4 rounded-lg border border-slate-100 bg-slate-50 px-4 py-3">
      <div>
        <p className="text-sm font-semibold text-slate-900">{title}</p>
        <p className="mt-1 text-sm text-slate-500">{value || "Não detectado"}</p>
        {detail ? <p className="mt-1 text-xs text-slate-400">{detail}</p> : null}
      </div>
      <StatusBadge tone={detected ? "active" : "default"}>{detected ? "Detectado" : "Não detectado"}</StatusBadge>
    </div>
  );
}

function datasetDisplay(config, officialWhatsapp) {
  const datasetId = config.dataset_id || officialWhatsapp?.dataset_id || "";
  const datasetName = config.dataset_name || officialWhatsapp?.dataset_name || "";
  if (!datasetId) {
    return { detected: false, value: "Não detectado", detail: "" };
  }
  if (datasetName) {
    return {
      detected: true,
      value: datasetName,
      detail: `${officialWhatsapp?.dataset_name_is_generic || config.dataset_name_is_generic ? "Nome genérico da Meta · " : ""}ID: ${datasetId}`,
    };
  }
  return {
    detected: true,
    value: "unknown Event Data",
    detail: `Nome genérico da Meta · ID: ${datasetId}`,
  };
}

function conversionStatusText(conversions) {
  if (!conversions?.whatsapp_connected) return "WhatsApp Oficial não conectado";
  if (!conversions?.conversion_token_configured) return "Credencial Meta sem permissão de conversões";
  if (!conversions?.conversion_capability_ready) return "Credencial sem permissão de conversões";
  if (!conversions?.dataset_configured) return "Dataset ainda não provisionado";
  if (conversions?.messaging_source?.manual_action_required) return "Ação manual pendente na Meta";
  if (!conversions?.messaging_source?.configured) return "Integração de mensagens pendente";
  if (!conversions?.conversion_ready) return "Configuração pendente";
  return "Conversões configuradas";
}

const fallbackSourceOptions = [
  { value: "ai", label: "Inteligência Artificial" },
  { value: "manual", label: "Alteração manual" },
  { value: "automation", label: "Automação" },
  { value: "integration", label: "Integração" },
  { value: "system", label: "Sistema" },
];

function JsonPreview({ value }) {
  return (
    <pre className="ux05-json-preview max-h-[520px] overflow-auto rounded-lg border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">
      {JSON.stringify(value || {}, null, 2)}
    </pre>
  );
}

function formatConversionTime(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "-";
  }
}

function conversionHistoryTone(status) {
  if (status === "sent") return "active";
  if (status === "failed" || status === "blocked") return "danger";
  if (status === "not_applicable" || status === "skipped") return "warning";
  return "default";
}

export default function TrafficMeta() {
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(null);
  const [credentials, setCredentials] = useState(null);
  const [marketingAuthorization, setMarketingAuthorization] = useState(null);
  const [marketingAuthorizationLoading, setMarketingAuthorizationLoading] = useState(false);
  const [marketingAuthorizationBusy, setMarketingAuthorizationBusy] = useState(false);
  const [conversions, setConversions] = useState(null);
  const [optimizationEligibility, setOptimizationEligibility] = useState(null);
  const [conversionHistory, setConversionHistory] = useState([]);
  const [savingMappingSources, setSavingMappingSources] = useState("");
  const [pages, setPages] = useState([]);
  const [assets, setAssets] = useState(null);
  const [assetForm, setAssetForm] = useState({ page_id: "", whatsapp_connection_id: "", dataset_id: "" });
  const [savingAssets, setSavingAssets] = useState(false);
  const [campaigns, setCampaigns] = useState([]);
  const [adsets, setAdsets] = useState([]);
  const [ads, setAds] = useState([]);
  const [form, setForm] = useState(emptyConfig);
  const [editingAdAccount, setEditingAdAccount] = useState(true);
  const [savedAdAccount, setSavedAdAccount] = useState(null);
  const [selectedCampaignId, setSelectedCampaignId] = useState("");
  const [inspection, setInspection] = useState(null);
  const [inspecting, setInspecting] = useState(false);
  const [validatingAdAccount, setValidatingAdAccount] = useState(false);
  const [validatedAdAccount, setValidatedAdAccount] = useState(null);
  const [showNewCampaign, setShowNewCampaign] = useState(false);
  const [confirmingCampaign, setConfirmingCampaign] = useState(false);
  const [creatingCampaign, setCreatingCampaign] = useState(false);
  const [createdStructure, setCreatedStructure] = useState(null);
  const [campaignReadiness, setCampaignReadiness] = useState(null);
  const [loadingReadiness, setLoadingReadiness] = useState(false);
  const [draftReady, setDraftReady] = useState(false);
  const [draftCreationId, setDraftCreationId] = useState("");
  const [resolvingPin, setResolvingPin] = useState(false);
  const [draftSessionId, setDraftSessionId] = useState("");
  const [creativeSource, setCreativeSource] = useState("new");
  const [campaignForm, setCampaignForm] = useState({
    daily_budget_brl: "",
    location_type: "brazil",
    location_input: "",
    location_display_name: "",
    latitude: "",
    longitude: "",
    radius_km: "",
    gender_mode: "all",
    page_id: "",
    whatsapp_connection_id: "",
    conversion_goal_key: "",
    ads: [],
  });

  const config = status?.config || {};
  const officialWhatsapp = status?.official_whatsapp;
  const activeOfficialWhatsapps = status?.active_official_whatsapps || [];
  const selectedWhatsapp = activeOfficialWhatsapps.find((item) => item.connection_id === campaignForm.whatsapp_connection_id) || null;
  const assetPages = assets?.pages?.length ? assets.pages : pages;
  const compatibleWhatsapps = (assets?.whatsapps || activeOfficialWhatsapps).filter((item) => !assets?.whatsapps || item.page_compatible);
  const selectedPage = assetPages.find((item) => item.id === (assetForm.page_id || config.page_id)) || null;
  const selectedInstagram = assets?.instagram || {
    account_id: selectedPage?.instagram_account_id || config.instagram_account_id || "",
    username: selectedPage?.instagram_username || "",
  };
  const configuredAdAccountId = normalizeAdAccountId(config.ad_account_id);
  const writesEnabled = Boolean(status?.meta_write_enabled && status?.campaign_create_enabled && status?.adset_create_enabled && status?.creative_create_enabled && status?.ad_create_enabled);
  const datasetAsset = datasetDisplay(config, officialWhatsapp);
  const conversionSourceOptions = conversions?.conversion_engine?.source_options || fallbackSourceOptions;
  const conversionGoalOptions = campaignReadiness?.supported_goals || [];

  const capabilityBadges = useMemo(() => {
    const data = credentials || {};
    const permissions = data.permissions || data;
    return [
      ["ads_read", "Leitura de anúncios"],
      ["ads_management", "Gerenciamento de anúncios"],
      ["business_management", "Gerenciamento do negócio"],
    ].map(([name, label]) => ({
      name,
      label,
      granted: Boolean(permissions[name]),
    }));
  }, [credentials]);

  const prepareMarketingAuthorization = async (readiness) => {
    const oauth = readiness?.marketing_authorization;
    if (!empresaId || !MARKETING_RECONNECT_STATUSES.has(readiness?.marketing_readiness_status) || !oauth?.enabled) {
      setMarketingAuthorization(null);
      return null;
    }
    setMarketingAuthorizationLoading(true);
    try {
      const sessionResponse = await api.post(`/empresas/${empresaId}/conexoes/meta/marketing/session`, {}, { timeout: 15000 });
      const session = sessionResponse.data || {};
      const FB = await loadFacebookSdk(session.app_id || oauth.app_id, session.graph_api_version || oauth.graph_api_version, session.sdk_url || oauth.sdk_url);
      const prepared = {
        state: String(session.state || ""),
        expiresAt: Date.now() + Number(session.expires_in || 0) * 1000,
        FB,
      };
      setMarketingAuthorization(prepared);
      return prepared;
    } catch {
      setMarketingAuthorization(null);
      return null;
    } finally {
      setMarketingAuthorizationLoading(false);
    }
  };

  const loadStatus = async () => {
    if (!empresaId) {
      setStatus(emptyStatus);
      setCredentials(null);
      setError("Empresa não identificada no contexto atual.");
      setLoading(false);
      return;
    }
    setError("");
    setLoading(true);
    try {
      const configResponse = await api.get(`/empresas/${empresaId}/traffic/meta/config`, { timeout: 15000 });
      setStatus(configResponse.data);
      const config = configResponse.data?.config || {};
      const activeWhatsapps = configResponse.data?.active_official_whatsapps || [];
      if (activeWhatsapps.length === 1) {
        setCampaignForm((current) => ({ ...current, whatsapp_connection_id: activeWhatsapps[0].connection_id || "" }));
      }
      setAssetForm({
        page_id: config.page_id || "",
        whatsapp_connection_id: config.whatsapp_connection_id || (activeWhatsapps.length === 1 ? activeWhatsapps[0].connection_id || "" : ""),
        dataset_id: config.dataset_id || "",
      });
      setForm({
        ad_account_id: config.ad_account_id || "",
      });
      setValidatedAdAccount(null);
      const normalizedSavedId = normalizeAdAccountId(config.ad_account_id);
      setEditingAdAccount(!normalizedSavedId);
      setSavedAdAccount(normalizedSavedId ? { id: normalizedSavedId } : null);
      if (normalizedSavedId) {
        try {
          const response = await api.get(`/empresas/${empresaId}/traffic/meta/adaccounts/${normalizedSavedId}`, { timeout: 15000 });
          const account = response.data?.adaccount || {};
          setSavedAdAccount({
            id: normalizeAdAccountId(account.account_id || account.id || normalizedSavedId),
            name: account.name || "",
            account_status: account.account_status,
            currency: account.currency || "",
            timezone_name: account.timezone_name || "",
          });
        } catch {
          setSavedAdAccount({ id: normalizedSavedId });
        }
      }
      if (normalizedSavedId) {
        try {
          const assetResponse = await api.get(`/empresas/${empresaId}/traffic/meta/assets`, { timeout: 20000 });
          setAssets(assetResponse.data);
          const graphPages = assetResponse.data?.pages || [];
          const graphWhatsapps = assetResponse.data?.whatsapps || [];
          const compatible = graphWhatsapps.filter((item) => item.page_compatible);
          const defaultWhatsappId = config.whatsapp_connection_id || (compatible.length === 1 ? compatible[0]?.connection_id || "" : "");
          setPages(graphPages);
          setAssetForm({
            page_id: assetResponse.data?.selected_page?.id || config.page_id || "",
            whatsapp_connection_id: defaultWhatsappId,
            dataset_id: config.dataset_id || assetResponse.data?.dataset?.id || "",
          });
          setCampaignForm((current) => ({
            ...current,
            page_id: assetResponse.data?.selected_page?.id || config.page_id || current.page_id,
            whatsapp_connection_id: defaultWhatsappId || current.whatsapp_connection_id,
          }));
        } catch {
          setAssets(null);
        }
      }
    } catch (err) {
      setStatus(emptyStatus);
      setAssets(null);
      setError(safeError(err, "Não foi possível carregar Tráfego Meta."));
    } finally {
      setLoading(false);
    }
    try {
      const credentialResponse = await api.get(`/empresas/${empresaId}/traffic/meta/credentials/status`, { timeout: 15000 });
      setCredentials(credentialResponse.data);
      await prepareMarketingAuthorization(credentialResponse.data);
    } catch (err) {
      setCredentials(null);
      setError((current) => current || safeError(err, "Não foi possível carregar o status das credenciais Meta."));
    }
    try {
      const conversionResponse = await api.get(`/empresas/${empresaId}/traffic/meta/conversions`, { timeout: 15000 });
      setConversions(conversionResponse.data);
    } catch (err) {
      setConversions(null);
      setError((current) => current || safeError(err, "Não foi possível carregar o status de conversões Meta."));
    }
    try {
      const eligibilityResponse = await api.get(`/empresas/${empresaId}/traffic/meta/conversions/optimization-eligibility`, { timeout: 15000 });
      setOptimizationEligibility(eligibilityResponse.data || null);
    } catch (err) {
      setOptimizationEligibility(null);
      setError((current) => current || safeError(err, "Não foi possível carregar a elegibilidade de otimização Meta."));
    }
    try {
      const historyResponse = await api.get(`/empresas/${empresaId}/traffic/meta/conversions/history`, { timeout: 15000 });
      setConversionHistory(historyResponse.data?.items || []);
    } catch (err) {
      setConversionHistory([]);
      setError((current) => current || safeError(err, "Não foi possível carregar o histórico de conversões Meta."));
    }
  };

  const authorizeMarketingRead = async () => {
    let prepared = marketingAuthorization;
    if (!prepared || prepared.expiresAt - 30000 <= Date.now()) {
      prepared = await prepareMarketingAuthorization(credentials);
      if (!prepared) {
        setError("Não foi possível preparar a autorização Meta. Atualize o status e tente novamente.");
        return;
      }
    }
    setMarketingAuthorizationBusy(true);
    setError("");
    try {
      startMetaMarketingAuthorization(
        prepared.FB,
        async (response) => {
          const code = response?.authResponse?.code;
          if (!code) {
            setError("A autorização foi cancelada ou a Meta não retornou o código OAuth.");
            setMarketingAuthorizationBusy(false);
            return;
          }
          try {
            await api.post(`/empresas/${empresaId}/conexoes/meta/marketing/complete`, {
              state: prepared.state,
              code,
            }, { timeout: 30000 });
            setMarketingAuthorization(null);
            await loadStatus();
          } catch (err) {
            setError(safeError(err, "A Meta não concluiu a autorização de anúncios."));
          } finally {
            setMarketingAuthorizationBusy(false);
          }
        },
        (err) => {
          setError(sanitizeMetaBrowserError(err).message || "Não foi possível abrir a autorização Meta.");
          setMarketingAuthorizationBusy(false);
        },
      );
    } catch (err) {
      setError(sanitizeMetaBrowserError(err).message || "Não foi possível abrir a autorização Meta.");
      setMarketingAuthorizationBusy(false);
    }
  };

  const loadCampaignReadiness = async (nextForm = campaignForm) => {
    if (!empresaId || !showNewCampaign) {
      setCampaignReadiness(null);
      return;
    }
    const pageId = nextForm.page_id || config.page_id || assetForm.page_id || "";
    const whatsappConnectionId = nextForm.whatsapp_connection_id || assetForm.whatsapp_connection_id || config.whatsapp_connection_id || "";
    setLoadingReadiness(true);
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/campaigns/conversion-readiness`, {
        params: {
          conversion_goal_key: nextForm.conversion_goal_key || undefined,
          page_id: pageId || undefined,
          whatsapp_connection_id: whatsappConnectionId || undefined,
        },
        timeout: 15000,
      });
      setCampaignReadiness(response.data || null);
    } catch {
      setCampaignReadiness(null);
    } finally {
      setLoadingReadiness(false);
    }
  };

  const discoverPages = async () => {
    setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/pages`);
      const availablePages = response.data?.pages || [];
      setPages(availablePages);
      if (availablePages.length === 1) setCampaignForm((current) => ({ ...current, page_id: availablePages[0].id }));
      return availablePages;
    } catch (err) {
      setError(safeError(err, "Não foi possível listar Pages."));
      return [];
    }
  };

  const saveMetaAssets = async () => {
    if (!assetForm.page_id) {
      setError("Selecione a Página Facebook correta antes de salvar os ativos Meta.");
      return;
    }
    if (compatibleWhatsapps.length > 1 && !assetForm.whatsapp_connection_id) {
      setError("Selecione o WhatsApp compatível com a Página.");
      return;
    }
    setSavingAssets(true);
    setError("");
    try {
      const response = await api.put(`/empresas/${empresaId}/traffic/meta/config`, {
        page_id: assetForm.page_id,
        whatsapp_connection_id: assetForm.whatsapp_connection_id || compatibleWhatsapps[0]?.connection_id || "",
        dataset_id: assetForm.dataset_id || null,
        status: "active",
      });
      setStatus((current) => ({ ...(current || {}), ...response.data }));
      const assetResponse = await api.get(`/empresas/${empresaId}/traffic/meta/assets`, { timeout: 20000 });
      setAssets(assetResponse.data);
      setPages(assetResponse.data?.pages || []);
      setCampaignForm((current) => ({
        ...current,
        page_id: assetForm.page_id,
        whatsapp_connection_id: assetForm.whatsapp_connection_id || compatibleWhatsapps[0]?.connection_id || "",
      }));
    } catch (err) {
      setError(safeError(err, "Não foi possível salvar os ativos Meta."));
    } finally {
      setSavingAssets(false);
    }
  };

  const openNewCampaign = async () => {
    setShowNewCampaign(true);
    setConfirmingCampaign(false);
    setCampaignReadiness(null);
    const sessionId = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    setDraftSessionId(sessionId);
    setDraftCreationId("");
    setCreatedStructure(null);
    setCreativeSource("new");
    setCampaignForm((current) => ({ ...current, daily_budget_brl: "", location_type: "brazil", location_input: "", location_display_name: "", latitude: "", longitude: "", radius_km: "", gender_mode: "all", conversion_goal_key: "", ads: [] }));
    if (compatibleWhatsapps.length === 1) {
      setCampaignForm((current) => ({ ...current, whatsapp_connection_id: compatibleWhatsapps[0].connection_id || "" }));
    }
    if (config.page_id) setCampaignForm((current) => ({ ...current, page_id: config.page_id }));
    if (!pages.length) await discoverPages();
  };

  const updateAd = (index, values) => setCampaignForm((current) => ({ ...current, ads: current.ads.map((ad, itemIndex) => itemIndex === index ? { ...ad, ...values } : ad) }));
  const updateAdByKey = (itemKey, values) => setCampaignForm((current) => ({ ...current, ads: current.ads.map((ad) => ad.item_key === itemKey ? { ...ad, ...values } : ad) }));

  const removeAd = (index) => setCampaignForm((current) => ({ ...current, ads: current.ads.filter((_, itemIndex) => itemIndex !== index) }));

  const analyzeImage = async (itemKey, ad) => {
    updateAdByKey(itemKey, { analysis_status: "analyzing", analysis_error: "" });
    try {
      const response = await api.post(`/empresas/${empresaId}/traffic/meta/ad-copy/analyze-image`, {
        image_data_url: ad.media_data_url,
        filename: ad.media_name,
        objective: "OUTCOME_LEADS",
        destination: "whatsapp",
      }, { timeout: 90000 });
      const copy = response.data?.copy || {};
      updateAdByKey(itemKey, { analysis_status: "generating", analysis_error: "" });
      updateAdByKey(itemKey, {
        analysis_status: "ready",
        analysis_error: "",
        primary_text: copy.primary_text || "",
        headline: copy.headline || "",
        description: copy.description || "",
      });
      if (draftCreationId && copy.primary_text && copy.headline) {
        try {
          await api.patch(`/empresas/${empresaId}/traffic/meta/campaigns/${draftCreationId}/items/${encodeURIComponent(itemKey)}/copy`, { primary_text: copy.primary_text, headline: copy.headline, description: copy.description || "", copy_source: "AI" });
        } catch { setError("A copy foi gerada, mas ainda não foi persistida no servidor; tente editar novamente."); }
      }
    } catch {
      updateAdByKey(itemKey, {
        analysis_status: "error",
        analysis_error: "Não foi possível analisar esta imagem. Você pode tentar novamente ou preencher a copy manualmente.",
      });
    }
  };

  const resolvePin = async () => {
    const query = String(campaignForm.location_input || "").trim();
    if (!query) { setError("Informe um CEP, endereço ou local para localizar o pino."); return; }
    setResolvingPin(true); setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/location/resolve`, { params: { q: query }, timeout: 20000 });
      setCampaignForm((current) => ({ ...current, location_display_name: response.data.display_name || query, latitude: String(response.data.latitude), longitude: String(response.data.longitude) }));
    } catch (err) { setError(safeError(err, "Não foi possível localizar o pino.")); }
    finally { setResolvingPin(false); }
  };

  const persistDraft = async (ads) => {
    try {
      const response = await api.post(`/empresas/${empresaId}/traffic/meta/campaigns/draft`, { ...campaignForm, draft_session_id: draftSessionId, ads, latitude: campaignForm.location_type === "radius" && campaignForm.latitude ? Number(campaignForm.latitude) : null, longitude: campaignForm.location_type === "radius" && campaignForm.longitude ? Number(campaignForm.longitude) : null, radius_km: campaignForm.location_type === "radius" && campaignForm.radius_km ? Number(campaignForm.radius_km) : null });
      const id = response.data?.creation?.id || "";
      setDraftCreationId(id);
      setCreatedStructure({ ...(response.data?.creation || {}), pipeline: response.data?.pipeline, draft: true });
      return true;
    } catch (err) { setError(safeError(err, "Não foi possível preparar a campanha. Nenhum anúncio foi enviado à Meta. Tente novamente.")); return false; }
  };

  const selectImages = async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    if (campaignForm.ads.length + files.length > 5) {
      setError("Uma criação pode ter no máximo 5 imagens.");
      return;
    }
    try {
      const existingKeys = new Set(campaignForm.ads.map((ad) => ad.item_key));
      const accepted = [];
      let invalidCount = 0;
      let duplicateCount = 0;
      for (const file of files) {
        try {
          const itemKey = await fingerprintFile(file);
          if (existingKeys.has(itemKey)) {
            duplicateCount += 1;
            continue;
          }
          const dataUrl = await readValidatedImage(file);
          existingKeys.add(itemKey);
          accepted.push({ ...makeEmptyAd(), item_key: itemKey, media_name: file.name, media_data_url: dataUrl, thumbnail_url: URL.createObjectURL(file), analysis_status: "pending" });
        } catch {
          invalidCount += 1;
        }
      }
      const availableSlots = Math.max(0, 5 - campaignForm.ads.length);
      const ads = accepted.slice(0, availableSlots);
      if (accepted.length > availableSlots) setError("Uma criação pode ter no máximo 5 imagens.");
      else if (invalidCount || duplicateCount) setError(`${invalidCount ? `${invalidCount} imagem(ns) inválida(s)` : ""}${invalidCount && duplicateCount ? " e " : ""}${duplicateCount ? `${duplicateCount} duplicata(s) ignorada(s)` : ""}.`);
      else setError("");
      if (!ads.length) return;
      const nextAds = [...campaignForm.ads, ...ads];
      setCampaignForm((current) => ({ ...current, ads: nextAds }));
      const persisted = await persistDraft(nextAds);
      if (!persisted) {
        ads.forEach((ad) => updateAdByKey(ad.item_key, { analysis_status: "error", analysis_error: "Não foi possível persistir esta imagem no servidor." }));
        return;
      }
      ads.forEach((ad) => analyzeImage(ad.item_key, ad));
    } catch {
      setError("Não foi possível ler as imagens selecionadas.");
    }
  };

  const retryImageAnalysis = (index) => {
    const ad = campaignForm.ads[index];
    if (ad?.media_data_url) analyzeImage(ad.item_key, ad);
  };

  const validateAdAccount = async () => {
    const normalized = normalizeAdAccountId(form.ad_account_id);
    if (!normalized) {
      setError("Informe o ID da conta de anúncios Meta.");
      return;
    }
    setError("");
    setValidatingAdAccount(true);
    setValidatedAdAccount(null);
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/adaccounts/${normalized}`);
      const account = response.data?.adaccount || {};
      setForm((current) => ({ ...current, ad_account_id: normalizeAdAccountId(account.account_id || account.id || normalized) }));
      setValidatedAdAccount({
        id: normalizeAdAccountId(account.account_id || account.id || normalized),
        name: account.name || "",
        account_status: account.account_status,
        currency: account.currency || "",
        timezone_name: account.timezone_name || "",
      });
    } catch (err) {
      setError(safeError(err, "Não foi possível validar a conta de anúncios."));
    } finally {
      setValidatingAdAccount(false);
    }
  };

  const saveAdAccount = async () => {
    const normalizedAdAccountId = normalizeAdAccountId(form.ad_account_id);
    if (normalizedAdAccountId && validatedAdAccount?.id !== normalizedAdAccountId) {
      setError("Valide a conta de anúncios Meta antes de salvar.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const response = await api.put(`/empresas/${empresaId}/traffic/meta/config`, {
        ad_account_id: normalizedAdAccountId,
        ad_account_validation_id: validatedAdAccount?.id || "",
      });
      setStatus((current) => ({ ...(current || {}), ...response.data }));
      setSavedAdAccount(validatedAdAccount);
      setEditingAdAccount(false);
    } catch (err) {
      setError(safeError(err, "Não foi possível salvar a configuração."));
    } finally {
      setSaving(false);
    }
  };

  const loadCampaigns = async () => {
    setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/campaigns`);
      setCampaigns(response.data?.campaigns || []);
    } catch (err) {
      setError(safeError(err, "Não foi possível listar campanhas."));
    }
  };

  const loadAdsets = async () => {
    setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/adsets`, {
        params: selectedCampaignId ? { campaign_id: selectedCampaignId } : {},
      });
      setAdsets(response.data?.adsets || []);
    } catch (err) {
      setError(safeError(err, "Não foi possível listar AdSets."));
    }
  };

  const loadAds = async () => {
    setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/ads`, {
        params: selectedCampaignId ? { campaign_id: selectedCampaignId } : {},
      });
      setAds(response.data?.ads || []);
    } catch (err) {
      setError(safeError(err, "Não foi possível listar Ads."));
    }
  };

  const inspectCampaign = async (campaignId = selectedCampaignId) => {
    if (!campaignId) return;
    setInspecting(true);
    setError("");
    try {
      const response = await api.get(`/empresas/${empresaId}/traffic/meta/campaigns/${campaignId}/inspect`);
      setInspection(response.data);
      setAdsets(response.data?.adsets || []);
      setAds(response.data?.ads || []);
    } catch (err) {
      setError(safeError(err, "Não foi possível inspecionar a campanha."));
    } finally {
      setInspecting(false);
    }
  };

  const selectExistingCampaign = (campaignId) => {
    setSelectedCampaignId(campaignId);
    setInspection(null);
    setAdsets([]);
    setAds([]);
    if (campaignId) inspectCampaign(campaignId);
  };

  const toggleMappingSource = async (item, sourceValue) => {
    if (!item?.mapping_id || savingMappingSources) return;
    const current = new Set(item.allowed_sources || conversions?.conversion_engine?.default_allowed_sources || []);
    if (current.has(sourceValue)) current.delete(sourceValue);
    else current.add(sourceValue);
    setSavingMappingSources(item.mapping_id);
    setError("");
    try {
      const response = await api.patch(`/empresas/${empresaId}/traffic/meta/conversions/mappings/${item.mapping_id}/sources`, {
        allowed_sources: Array.from(current),
      });
      setConversions(response.data?.status || null);
    } catch (err) {
      setError(safeError(err, "Não foi possível salvar a política de origem do mapping."));
    } finally {
      setSavingMappingSources("");
    }
  };

  const submitNewCampaign = async () => {
    if (compatibleWhatsapps.length > 1 && !campaignForm.whatsapp_connection_id) {
      setError("Selecione qual WhatsApp Oficial será usado nesta campanha.");
      return;
    }
    setCreatingCampaign(true);
    setError("");
    try {
      const resumeId = draftCreationId || (createdStructure?.partial ? createdStructure.id : null);
      const response = await api.post(`/empresas/${empresaId}/traffic/meta/campaigns/full`, {
        ...campaignForm,
        conversion_goal_key: campaignForm.conversion_goal_key || campaignReadiness?.selected_goal || "",
        resume_creation_id: resumeId,
        latitude: campaignForm.location_type === "radius" ? Number(campaignForm.latitude) : null,
        longitude: campaignForm.location_type === "radius" ? Number(campaignForm.longitude) : null,
        radius_km: campaignForm.location_type === "radius" ? Number(campaignForm.radius_km) : null,
      });
      const pipelineAds = response.data?.pipeline?.ads || [];
      if (pipelineAds.length) {
        setCampaignForm((current) => ({
          ...current,
          ads: current.ads.map((ad, index) => {
            const state = pipelineAds.find((item) => item.item_key === ad.item_key || item.sequence === index + 1);
            if (!state) return ad;
            return { ...ad, meta_status: state.status || state.ad_status || ad.meta_status, media_reference: state.media_reference || ad.media_reference, creative_id: state.creative_id || ad.creative_id, ad_id: state.ad_id || ad.ad_id };
          }),
        }));
      }
      setConfirmingCampaign(false);
      await loadCampaigns();
      if (response.data?.partial_failure) {
        setCreatedStructure({ ...(response.data.creation || {}), pipeline: response.data.pipeline, partial: true });
        setError("A criação ficou parcial. Os objetos já confirmados foram preservados; corrija a pendência e retome.");
      } else if (response.data?.success && response.data?.creation?.status === "created") {
        setCreatedStructure({ ...response.data.creation, pipeline: response.data.pipeline });
      } else {
        setCreatedStructure(null);
        setError("A Meta não confirmou a criação completa pausada.");
      }
    } catch (err) {
      setCreatedStructure(null);
      setError(safeError(err, "Não foi possível criar a campanha pausada."));
    } finally {
      setCreatingCampaign(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, [empresaId]);

  useEffect(() => {
    let active = true;
    if (!empresaId) return undefined;
    loadCampaignDraft(empresaId).then((draft) => {
      if (!active || !draft) return;
      const stale = draft.createdStructure?.partial || ["partial_error", "error"].includes(draft.createdStructure?.status);
      if (draft.campaignForm && !stale) setCampaignForm((current) => ({ ...current, ...draft.campaignForm, ads: (draft.campaignForm.ads || []).map((ad) => ({ ...ad, thumbnail_url: ad.media_data_url || ad.thumbnail_url })) }));
      if (draft.createdStructure) {
        if (!stale) {
          setCreatedStructure(draft.createdStructure);
          setDraftCreationId(draft.createdStructure.id || "");
          setDraftSessionId(draft.createdStructure.draft_session_id || "");
          setShowNewCampaign(true);
        }
      }
    }).finally(() => { if (active) setDraftReady(true); });
    return () => { active = false; };
  }, [empresaId]);

  useEffect(() => {
    if (!draftReady || !empresaId) return undefined;
    const timer = window.setTimeout(() => saveCampaignDraft(empresaId, { campaignForm, createdStructure }), 250);
    return () => window.clearTimeout(timer);
  }, [campaignForm, createdStructure, draftReady, empresaId]);

  useEffect(() => {
    if (!showNewCampaign || !empresaId) return undefined;
    const timer = window.setTimeout(() => loadCampaignReadiness(campaignForm), 250);
    return () => window.clearTimeout(timer);
  }, [showNewCampaign, empresaId, campaignForm.page_id, campaignForm.whatsapp_connection_id, campaignForm.conversion_goal_key, assetForm.page_id, assetForm.whatsapp_connection_id, config.page_id, config.whatsapp_connection_id]);

  const visibleCreationItems = createdStructure?.pipeline?.ads?.length
    ? createdStructure.pipeline.ads.map((item, index) => ({ ...item, sequence: item.sequence || index + 1 }))
    : (campaignForm.ads || []).map((item, index) => ({
        ...item,
        sequence: index + 1,
        status: item.meta_status || "pending",
        error: createdStructure?.error,
      }));

  if (loading) return <LoadingState label="Carregando Tráfego Meta..." />;

  const marketingReady = ["READY", "READY_TENANT_OAUTH", "READY_ADMIN_MANAGED_READ"].includes(credentials?.marketing_readiness_status);

  return (
    <div className="ux05-analytics ux05-meta space-y-6">
      <PageHeader
        title="Tráfego / Meta Ads"
        description="Configure a conta de anúncios utilizada por esta empresa e acompanhe os ativos Meta vinculados."
        icon={BarChart3}
        actions={<ActionButton variant="secondary" icon={RefreshCw} onClick={loadStatus}>Atualizar</ActionButton>}
      />

      {error ? <InfoBanner tone="warning">{error}</InfoBanner> : null}

      <SectionCard title="Status da integração" icon={ShieldCheck} className="ux05-integration-overview">
        <div className="ux05-meta-status grid gap-4 lg:grid-cols-4">
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium text-slate-700">Marketing API</span>
              <StatusBadge tone={marketingReady ? "active" : "warning"}>
                {marketingReady ? "Pronta" : "Ação necessária"}
              </StatusBadge>
            </div>
            <p className="text-slate-600">Versão: <strong>{status?.marketing_graph_version || "v26.0"}</strong></p>
            <p className="text-slate-600">Credencial: <strong>{credentialSourceLabel(credentials)}</strong></p>
            <p className="text-slate-600">Último sync: <strong>{credentials?.last_sync_at ? new Date(credentials.last_sync_at).toLocaleString("pt-BR") : "Ainda não executado"}</strong></p>
          </div>
          <div className="lg:col-span-2">
            <p className="mb-3 text-sm font-medium text-slate-700">Permissões</p>
            <div className="flex flex-wrap gap-2">
            {capabilityBadges.map((item) => (
              <StatusBadge key={item.name} tone={item.granted ? "active" : "default"}>{item.label}</StatusBadge>
            ))}
            </div>
          </div>
        </div>
        <div className="mt-4 rounded-lg border border-slate-100 bg-white p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-semibold text-slate-900">Acesso a anúncios e Insights</p>
              <p className="mt-1 text-slate-600">
                Conta: <strong>{credentials?.asset_access?.ad_account ? "validada" : "pendente"}</strong>
                {" · "}Campanhas: <strong>{credentials?.asset_access?.campaigns ? "lidas" : "pendentes"}</strong>
                {" · "}Conjuntos: <strong>{credentials?.asset_access?.adsets ? "lidos" : "pendentes"}</strong>
                {" · "}Anúncios: <strong>{credentials?.asset_access?.ads ? "lidos" : "pendentes"}</strong>
                {" · "}Insights: <strong>{credentials?.insights?.preflight === "pass" ? "validados" : "pendentes"}</strong>
              </p>
              {credentials?.action_required ? <p className="mt-2 text-amber-800">{credentials.action_required}</p> : null}
              {credentials?.app_advanced_access_status === "UNKNOWN_REQUIRES_META_DASHBOARD" ? (
                <p className="mt-1 text-xs text-slate-500">O nível Advanced Access do aplicativo depende da configuração no Meta App Dashboard.</p>
              ) : null}
            </div>
            {MARKETING_RECONNECT_STATUSES.has(credentials?.marketing_readiness_status) ? (
              <ActionButton
                type="button"
                variant="secondary"
                icon={marketingAuthorizationBusy || marketingAuthorizationLoading ? Loader2 : ShieldCheck}
                onClick={authorizeMarketingRead}
                disabled={marketingAuthorizationBusy || marketingAuthorizationLoading || !marketingAuthorization || !credentials?.marketing_authorization?.enabled}
              >
                {marketingAuthorizationLoading ? "Preparando..." : marketingAuthorizationBusy ? "Aguardando Meta..." : "Autorizar leitura de anúncios"}
              </ActionButton>
            ) : null}
          </div>
        </div>
        <div className="mt-4 rounded-lg border border-slate-100 bg-white p-4">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold text-slate-900">Histórico</p>
              <p className="mt-1 text-sm text-slate-500">Últimas conversões registradas para esta empresa.</p>
            </div>
            <StatusBadge tone={conversionHistory.length ? "active" : "default"}>{conversionHistory.length ? `${conversionHistory.length} evento(s)` : "Sem registros"}</StatusBadge>
          </div>
          {conversionHistory.length ? (
            <div className="fm-table-scroll mt-4" tabIndex={0} role="region" aria-label="Histórico de conversões Meta">
              <table className="fm-table min-w-full divide-y divide-slate-100 text-left text-sm">
                <thead>
                  <tr className="text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 font-semibold">Evento</th>
                    <th className="px-3 py-2 font-semibold">Lead</th>
                    <th className="px-3 py-2 font-semibold">Trigger</th>
                    <th className="px-3 py-2 font-semibold">Horário</th>
                    <th className="px-3 py-2 font-semibold">Modo</th>
                    <th className="px-3 py-2 font-semibold">Status</th>
                    <th className="px-3 py-2 font-semibold">Meta</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {conversionHistory.map((item) => (
                    <tr key={item.id} className="text-slate-700">
                      <td className="whitespace-nowrap px-3 py-3 font-medium text-slate-900">{item.event_name || "-"}</td>
                      <td className="whitespace-nowrap px-3 py-3">{item.lead?.name || "Lead"}</td>
                      <td className="whitespace-nowrap px-3 py-3">{item.trigger?.label || "-"}</td>
                      <td className="whitespace-nowrap px-3 py-3">{formatConversionTime(item.created_at)}</td>
                      <td className="whitespace-nowrap px-3 py-3">{item.mode || "-"}</td>
                      <td className="whitespace-nowrap px-3 py-3">
                        <StatusBadge tone={conversionHistoryTone(item.status)}>{item.status_label || item.status || "-"}</StatusBadge>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-500">
                        {item.meta?.status_code ? `HTTP ${item.meta.status_code}` : item.meta?.delivery || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="Sem conversões registradas" description="Os eventos aparecerão aqui quando forem processados." />
          )}
        </div>
      </SectionCard>

      <SectionCard
        title="Conta de anúncios Meta"
        description="Informe a conta de anúncios utilizada por esta empresa."
      >
        {configuredAdAccountId && !editingAdAccount ? (
          <div className="space-y-4">
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <p className="flex items-center gap-2 font-semibold"><CheckCircle2 size={16} />{savedAdAccount?.name || "Conta de anúncios vinculada"}</p>
                  <p className="mt-1 text-emerald-800">
                    {accountStatusText(savedAdAccount?.account_status)}
                    {savedAdAccount?.currency ? ` · ${savedAdAccount.currency}` : ""}
                    {savedAdAccount?.timezone_name ? ` · ${savedAdAccount.timezone_name}` : ""}
                  </p>
                  <p className="mt-2 text-xs font-medium text-emerald-700">ID: {maskTail(savedAdAccount?.id || configuredAdAccountId)}</p>
                </div>
                <ActionButton type="button" variant="secondary" onClick={() => {
                  setForm({ ad_account_id: configuredAdAccountId });
                  setValidatedAdAccount(null);
                  setEditingAdAccount(true);
                }}>Alterar conta</ActionButton>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {!configuredAdAccountId ? <EmptyState title="Ainda não configurada" description="Valide a conta antes de salvar o vínculo da empresa." /> : null}
            <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
              <label className="space-y-1 text-sm font-medium text-slate-700">
                ID da conta de anúncios
                <input
                  className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2"
                  placeholder="act_123456789 ou 123456789"
                  value={form.ad_account_id}
                  onChange={(e) => {
                    setForm({ ad_account_id: e.target.value });
                    setValidatedAdAccount(null);
                  }}
                />
              </label>
              <ActionButton type="button" variant="secondary" icon={validatingAdAccount ? Loader2 : ShieldCheck} onClick={validateAdAccount} disabled={validatingAdAccount || !normalizeAdAccountId(form.ad_account_id)}>
                {validatingAdAccount ? "Validando..." : "Validar conta"}
              </ActionButton>
            </div>
            {validatedAdAccount ? (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
                <p className="flex items-center gap-2 font-semibold"><CheckCircle2 size={16} />Conta validada</p>
                <div className="mt-3 grid gap-3 md:grid-cols-4">
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Nome</span>{validatedAdAccount.name || "não retornado"}</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Status</span>{accountStatusText(validatedAdAccount.account_status)}</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Moeda</span>{validatedAdAccount.currency || "não retornada"}</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Fuso horário</span>{validatedAdAccount.timezone_name || "não retornado"}</div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500">A conta de anúncios precisa ser validada por GET read-only antes de salvar.</p>
            )}
            <div className="flex flex-wrap gap-2">
              <ActionButton icon={Save} onClick={saveAdAccount} disabled={saving || !normalizeAdAccountId(form.ad_account_id) || validatedAdAccount?.id !== normalizeAdAccountId(form.ad_account_id)}>
                {saving ? "Salvando..." : "Salvar conta de anúncios"}
              </ActionButton>
              {configuredAdAccountId ? <ActionButton type="button" variant="secondary" onClick={() => setEditingAdAccount(false)}>Cancelar</ActionButton> : null}
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Ativos Meta da empresa"
        description="Estes ativos formam o grafo usado para criar Campaign, AdSet, Creative e Ad."
        icon={ShieldCheck}
        actions={<ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={loadStatus}>Atualizar</ActionButton>}
      >
        <div className="grid gap-3 md:grid-cols-2">
          <AssetLine title="Business" detected={Boolean(assets?.ad_account?.business_id || config.business_id)} value={assets?.ad_account?.business_name || config.business_id} detail={assets?.ad_account?.business_id_masked || config.business_id} />
          <AssetLine title="Conta de anúncios" detected={Boolean(config.ad_account_id)} value={savedAdAccount?.name || assets?.ad_account?.name || maskTail(config.ad_account_id)} detail={maskTail(config.ad_account_id)} />
        </div>
        <div className="ux05-meta-asset-fields mt-4 grid gap-4 lg:grid-cols-4">
          <label className="space-y-1 text-sm font-medium text-slate-700">
            Página Facebook
            <select
              className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2"
              value={assetForm.page_id}
              onChange={(e) => {
                setAssetForm((current) => ({ ...current, page_id: e.target.value, whatsapp_connection_id: "" }));
                setCampaignForm((current) => ({ ...current, page_id: e.target.value, whatsapp_connection_id: "" }));
              }}
            >
              <option value="">Selecione a Page correta</option>
              {assetPages.map((page) => <option key={page.id} value={page.id}>{page.name || "Página sem nome"} {page.id_masked ? `· ${page.id_masked}` : ""}</option>)}
            </select>
          </label>
          <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3 text-sm">
            <p className="font-semibold text-slate-900">Instagram</p>
            <p className="mt-1 text-slate-600">{selectedInstagram?.username ? `@${selectedInstagram.username}` : "Nenhum Instagram vinculado à Page selecionada"}</p>
            {selectedInstagram?.account_id_masked ? <p className="mt-1 text-xs text-slate-400">{selectedInstagram.account_id_masked}</p> : null}
          </div>
          <label className="space-y-1 text-sm font-medium text-slate-700">
            WhatsApp
            {compatibleWhatsapps.length === 1 ? (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-emerald-900">{whatsappLabel(compatibleWhatsapps[0])} ✓</p>
            ) : (
              <select
                className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2"
                value={assetForm.whatsapp_connection_id}
                onChange={(e) => setAssetForm((current) => ({ ...current, whatsapp_connection_id: e.target.value }))}
              >
                <option value="">{compatibleWhatsapps.length ? "Selecione o WhatsApp compatível" : "Nenhum WhatsApp compatível validado"}</option>
                {compatibleWhatsapps.map((item) => <option key={item.connection_id} value={item.connection_id}>{whatsappLabel(item)}</option>)}
              </select>
            )}
          </label>
          <label className="space-y-1 text-sm font-medium text-slate-700">
            Dataset / Event Source
            <input
              className="fm-field w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
              value={datasetAsset.value}
              readOnly
            />
            <span className="block text-xs font-normal text-slate-500">Mensuração separada do delivery. O envio depende de contrato Meta e elegibilidade de conversão comprovados.</span>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <ActionButton type="button" icon={Save} onClick={saveMetaAssets} disabled={savingAssets || !assetForm.page_id || (!assetForm.whatsapp_connection_id && compatibleWhatsapps.length !== 1)}>
            {savingAssets ? "Salvando..." : "Salvar ativos Meta"}
          </ActionButton>
          <StatusBadge tone={assets?.asset_graph_valid ? "active" : "warning"}>{assets?.asset_graph_valid ? "Grafo validado" : "Validação pendente"}</StatusBadge>
        </div>
      </SectionCard>

      <SectionCard
        title="Conversões Meta / WhatsApp"
        description="Status de mensuração, readiness e mappings. A credencial vem da conexão Meta oficial da empresa."
        icon={ShieldCheck}
        actions={<ActionButton variant="secondary" icon={RefreshCw} onClick={loadStatus}>Atualizar</ActionButton>}
      >
        <div className="ux05-meta-readiness grid gap-4 lg:grid-cols-3">
          <div className="space-y-3 rounded-lg border border-slate-100 bg-slate-50 p-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-slate-900">Status</span>
              <StatusBadge tone={conversions?.conversion_ready ? "active" : "warning"}>{conversionStatusText(conversions)}</StatusBadge>
            </div>
            <p className="text-slate-600">WhatsApp Oficial: <strong>{conversions?.whatsapp_connected ? "conectado" : "não conectado"}</strong></p>
            <p className="text-slate-600">WABA: <strong>{conversions?.waba_detected ? "detectada" : "não detectada"}</strong></p>
            <p className="text-slate-600">Permissão para conversões: <strong>{conversions?.conversion_capability_ready ? "validada" : "pendente"}</strong></p>
            <p className="text-slate-600">Mapeamento de eventos: <strong>{conversions?.mapping_count ? `${conversions.mapping_count} configurado(s)` : "Ainda não configurado"}</strong></p>
          </div>

          <div className="space-y-3 rounded-lg border border-slate-100 bg-white p-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-slate-900">Credencial de conversões</span>
              <StatusBadge tone={conversions?.conversion_token_valid ? "active" : "default"}>{conversions?.conversion_token_valid ? "Validado" : "Não configurado"}</StatusBadge>
            </div>
            <p className="text-slate-600">Origem: <strong>{conversions?.conversion_token?.origin || conversions?.conversion_token?.source || "Conexão Meta"}</strong></p>
            {conversions?.conversion_token?.fingerprint ? <p className="text-slate-600">Fingerprint: <strong>{conversions.conversion_token.fingerprint}</strong></p> : null}
            {conversions?.conversion_token?.last_validated_at ? <p className="text-slate-600">Validado em: <strong>{conversions.conversion_token.last_validated_at}</strong></p> : null}
            <p className="text-xs text-slate-500">O AgentOS não solicita token manual redundante quando a conexão Meta oficial já possui as permissões necessárias.</p>
          </div>

          <div className="space-y-3 rounded-lg border border-slate-100 bg-white p-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-slate-900">Dataset / Event Source</span>
              <StatusBadge tone={conversions?.dataset_configured ? "active" : "default"}>{conversions?.dataset_configured ? "Configurado" : "Não provisionado"}</StatusBadge>
            </div>
            <p className="text-slate-600">Nome no AgentOS: <strong>{conversions?.dataset_agentos_display_name || "Não disponível"}</strong></p>
            {conversions?.dataset_meta_id_masked ? <p className="text-slate-600">Meta ID: <strong>{conversions.dataset_meta_id_masked}</strong></p> : null}
            {conversions?.dataset_meta_name ? <p className="text-slate-600">Nome na Meta: <strong>{conversions.dataset_meta_name}</strong>{conversions.dataset_meta_name_is_generic ? " · nome genérico da Meta" : ""}</p> : null}
            <p className="text-xs text-slate-500">Dataset pronto não cria mappings, não envia eventos e não é recriado automaticamente por campanha.</p>
          </div>

          <div className="space-y-3 rounded-lg border border-slate-100 bg-white p-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-slate-900">Elegibilidade de otimização</span>
              <StatusBadge tone={optimizationEligibility?.purchase_optimization_ready ? "active" : "warning"}>
                {optimizationEligibility?.purchase_optimization_ready ? "Compra disponível" : "Aguardando elegibilidade"}
              </StatusBadge>
            </div>
            <p className="text-slate-600">Sinais aceitos: <strong>{optimizationEligibility?.accepted_signal_counts?.Purchase ?? 0} Purchase</strong></p>
            <p className="text-slate-600">Lead qualificado: <strong>{optimizationEligibility?.accepted_signal_counts?.QualifiedLead ?? 0}</strong></p>
            <p className="text-slate-600">Estado: <strong>{String(optimizationEligibility?.state || "Não consultado").replaceAll("_", " ")}</strong></p>
            {optimizationEligibility?.bootstrap?.required ? (
              <InfoBanner tone="warning">Coletando sinais de conversão. Nenhum evento artificial é enviado.</InfoBanner>
            ) : null}
          </div>

          <div className="space-y-3 rounded-lg border border-slate-100 bg-white p-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-slate-900">Integração de mensagens</span>
              <StatusBadge tone={conversions?.messaging_source?.configured ? "active" : "default"}>
                {conversions?.messaging_source?.configured ? "Configurada" : "Pendente"}
              </StatusBadge>
            </div>
            <p className="text-slate-600">Página Facebook: <strong>{conversions?.messaging_source?.facebook_page_binding_required ? "necessária" : "não exigida pela API pública"}</strong></p>
            <p className="text-slate-600">Automação pública: <strong>{conversions?.messaging_source?.automatic ? "concluída" : "não disponível"}</strong></p>
            {conversions?.messaging_source?.manual_action_required ? (
              <InfoBanner tone="warning">{conversions.messaging_source.manual_action_description || "Conclua a autorização indicada pela Meta e verifique novamente."}</InfoBanner>
            ) : (
              <p className="text-xs text-slate-500">O AgentOS não usa endpoints privados do Events Manager.</p>
            )}
          </div>
        </div>
        <div className="mt-4 rounded-lg border border-slate-100 bg-white p-4">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold text-slate-900">Mapeamento de conversões</p>
              <p className="mt-1 text-sm text-slate-500">Configure depois quais acontecimentos comerciais enviam cada evento Meta.</p>
            </div>
            <StatusBadge tone={conversions?.conversion_engine?.mapping_count ? "active" : "default"}>
              {conversions?.conversion_engine?.mapping_count ? `${conversions.conversion_engine.mapping_count} mapping(s)` : "Ainda não configurado"}
            </StatusBadge>
          </div>
          <div className="ux05-meta-events mt-4 grid gap-3 lg:grid-cols-5">
            {(conversions?.conversion_engine?.five_event_funnel || [
              { event_name: "LeadSubmitted" },
              { event_name: "QualifiedLead" },
              { event_name: "InitiateCheckout" },
              { event_name: "OrderCreated" },
              { event_name: "Purchase" },
            ]).map((item) => (
              <div key={item.event_name} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-3 text-sm">
                <p className="font-semibold text-slate-800">{item.event_name}</p>
                <p className="mt-1 text-xs text-slate-500">{item.configured ? "Configurado" : "Pendente"}</p>
                <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Pode ser disparado por</p>
                <div className="mt-2 space-y-1">
                  {conversionSourceOptions.map((option) => {
                    const checked = (item.allowed_sources || conversions?.conversion_engine?.default_allowed_sources || []).includes(option.value);
                    const editable = Boolean(item.mapping_id);
                    return (
                      <label key={`${item.event_name}-${option.value}`} className="flex items-center gap-2 text-xs text-slate-600">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 rounded border-slate-300 text-emerald-600"
                          checked={checked}
                          disabled={!editable || savingMappingSources === item.mapping_id}
                          onChange={() => toggleMappingSource(item, option.value)}
                        />
                        <span>{option.label}</span>
                      </label>
                    );
                  })}
                </div>
                {!item.mapping_id ? <p className="mt-2 text-xs text-slate-400">Crie o mapping para editar as origens.</p> : null}
              </div>
            ))}
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Campanha WhatsApp de Conversão"
        description="Campaign, AdSet, Creatives e Ads são preparados pausados apenas quando a conta estiver elegível para conversão."
        actions={<ActionButton icon={Plus} onClick={openNewCampaign}>Nova campanha</ActionButton>}
      >
        {!writesEnabled ? (
          <InfoBanner tone="warning">A criação real está desabilitada por configuração. O formulário pode ser preparado, mas o backend só cria quando as flags de Marketing API estiverem ativas.</InfoBanner>
        ) : null}
        {!showNewCampaign ? (
          <EmptyState title="Nenhuma criação em andamento" description="Abra Nova campanha para preparar uma estrutura WhatsApp otimizada por conversão." />
        ) : (
          <div className="mt-4 space-y-4">
            <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <p className="font-semibold text-slate-900">Readiness da campanha</p>
                <StatusBadge tone={campaignReadiness?.ready ? "active" : "warning"}>
                  {loadingReadiness ? "Validando..." : campaignReadiness?.ready ? "Pronta" : "Bloqueada"}
                </StatusBadge>
              </div>
              <p className="mt-2 text-slate-600">
                {campaignReadiness?.ready
                  ? `Objetivo pronto: ${campaignReadiness?.selected_goal_label || "conversão"} · Meta: ${campaignReadiness?.best_available_conversion_optimization || "-"}`
                  : campaignReadiness?.reason
                    ? `A conta ainda não aceita o objetivo de conversão solicitado: ${campaignReadiness.reason}`
                    : "A campanha será bloqueada até a conta, o Dataset e a elegibilidade da Meta estarem consistentes."}
              </p>
            </div>
            {!confirmingCampaign ? (
              <>
                {conversionGoalOptions.length ? (
                  <div className="grid gap-4 md:grid-cols-2">
                    <label className="space-y-1 text-sm font-medium text-slate-700">
                      Objetivo comercial
                      <select
                        className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2"
                        value={campaignForm.conversion_goal_key}
                        onChange={(e) => setCampaignForm({ ...campaignForm, conversion_goal_key: e.target.value })}
                      >
                        <option value="">Auto</option>
                        {conversionGoalOptions.map((goal) => (
                          <option key={goal.key} value={goal.key}>{goal.label}</option>
                        ))}
                      </select>
                    </label>
                    <div className="rounded-lg border border-slate-100 bg-white px-4 py-3 text-sm text-slate-600">
                      <p className="font-semibold text-slate-900">Meta elegível</p>
                      <p className="mt-1">{campaignReadiness?.best_available_conversion_optimization || "Nenhuma ainda"}</p>
                    </div>
                  </div>
                ) : null}
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="space-y-1 text-sm font-medium text-slate-700">
                    Orçamento diário
                    <input className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" placeholder="R$ 30,00" value={campaignForm.daily_budget_brl} onChange={(e) => setCampaignForm({ ...campaignForm, daily_budget_brl: e.target.value })} />
                  </label>
                  <div className="ux05-generated-name rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-950">
                    <p className="font-semibold">Nome gerado automaticamente</p>
                    <p className="mt-1 text-xs text-indigo-800">O AgentOS define o nome padronizado no backend.</p>
                  </div>
                </div>
                <div className="rounded-lg border border-slate-200 p-4">
                  <p className="mb-3 text-sm font-semibold text-slate-900">Localização</p>
                  <div className="flex flex-wrap gap-4 text-sm text-slate-700">
                    <label className="flex items-center gap-2"><input type="radio" checked={campaignForm.location_type === "brazil"} onChange={() => setCampaignForm({ ...campaignForm, location_type: "brazil" })} /> Brasil inteiro</label>
                    <label className="flex items-center gap-2"><input type="radio" checked={campaignForm.location_type === "radius"} onChange={() => setCampaignForm({ ...campaignForm, location_type: "radius" })} /> Região por raio</label>
                  </div>
                  {campaignForm.location_type === "radius" ? (
                    <div className="mt-4 space-y-3">
                      <div className="ux05-pin-search flex gap-2"><input className="fm-field min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="Digite CEP, endereço ou local" value={campaignForm.location_input} onChange={(e) => setCampaignForm({ ...campaignForm, location_input: e.target.value })} /><ActionButton type="button" variant="secondary" onClick={resolvePin} disabled={resolvingPin}>{resolvingPin ? "Localizando..." : "Localizar pino"}</ActionButton></div>
                      {campaignForm.location_display_name ? <p className="text-sm text-emerald-800">📍 {campaignForm.location_display_name}</p> : null}
                      <label className="block max-w-xs text-sm font-medium text-slate-700">Raio em km<input className="fm-field mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="Ex.: 20" value={campaignForm.radius_km} onChange={(e) => setCampaignForm({ ...campaignForm, radius_km: e.target.value })} /></label>
                    </div>
                  ) : null}
                </div>
                <div className="rounded-lg border border-slate-200 p-4"><p className="mb-3 text-sm font-semibold text-slate-900">Gênero</p><div className="flex flex-wrap gap-4 text-sm text-slate-700"><label className="flex items-center gap-2"><input type="radio" checked={campaignForm.gender_mode === "all"} onChange={() => setCampaignForm({ ...campaignForm, gender_mode: "all" })} /> Todos</label><label className="flex items-center gap-2"><input type="radio" checked={campaignForm.gender_mode === "women"} onChange={() => setCampaignForm({ ...campaignForm, gender_mode: "women" })} /> Mulheres</label><label className="flex items-center gap-2"><input type="radio" checked={campaignForm.gender_mode === "men"} onChange={() => setCampaignForm({ ...campaignForm, gender_mode: "men" })} /> Homens</label></div></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="space-y-1 text-sm font-medium text-slate-700">
                    Página Meta
                    {assetPages.length === 1 ? <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-emerald-900">{assetPages[0].name || "Página Meta"} ✓</p> : (
                      <select className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" value={campaignForm.page_id} onChange={(e) => setCampaignForm({ ...campaignForm, page_id: e.target.value, whatsapp_connection_id: "" })}>
                        <option value="">Selecione pelo nome</option>
                        {assetPages.map((page) => <option key={page.id} value={page.id}>{page.name || "Página sem nome"}</option>)}
                      </select>
                    )}
                  </label>
                  <label className="space-y-1 text-sm font-medium text-slate-700">
                    WhatsApp da campanha
                    {compatibleWhatsapps.length === 0 ? (
                      <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">WhatsApp Oficial ativo não conectado</p>
                    ) : compatibleWhatsapps.length === 1 ? (
                      <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-emerald-900">{whatsappLabel(compatibleWhatsapps[0])} ✓</p>
                    ) : (
                      <select
                        className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2"
                        value={campaignForm.whatsapp_connection_id}
                        onChange={(e) => setCampaignForm({ ...campaignForm, whatsapp_connection_id: e.target.value })}
                      >
                        <option value="">Selecione o WhatsApp</option>
                        {compatibleWhatsapps.map((item) => <option key={item.connection_id} value={item.connection_id}>{whatsappLabel(item)}</option>)}
                      </select>
                    )}
                  </label>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                  Público: <strong>Advantage+ / automático ativado</strong>
                </div>
                <fieldset className="space-y-3">
                  <legend className="text-sm font-semibold text-slate-900">Origem do criativo</legend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className={`cursor-pointer rounded-lg border p-3 text-sm ${creativeSource === "new" ? "border-indigo-500 bg-indigo-50" : "border-slate-200"}`}>
                      <input type="radio" name="traffic-meta-creative-source" value="new" checked={creativeSource === "new"} onChange={() => setCreativeSource("new")} className="mr-2" />
                      <span className="font-semibold">Novo criativo</span><span className="mt-1 block pl-6 text-xs text-slate-600">Manter o fluxo atual com imagem e copy próprias.</span>
                    </label>
                    <label className={`cursor-pointer rounded-lg border p-3 text-sm ${creativeSource === "existing" ? "border-indigo-500 bg-indigo-50" : "border-slate-200"}`}>
                      <input type="radio" name="traffic-meta-creative-source" value="existing" checked={creativeSource === "existing"} onChange={() => { setCreativeSource("existing"); setConfirmingCampaign(false); }} className="mr-2" />
                      <span className="font-semibold">Publicação existente</span><span className="mt-1 block pl-6 text-xs text-slate-600">Consultar e gerar uma prévia simulada de Facebook ou Instagram.</span>
                    </label>
                  </div>
                </fieldset>
                {creativeSource === "new" ? (
                  <>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-slate-900">Imagens do anúncio</h3><span className="text-xs text-slate-500">{campaignForm.ads.filter(analysisReady).length} de {campaignForm.ads.length} concluídas · análise automática</span></div>
                      <label className="block rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm font-medium text-slate-700">
                        {campaignForm.ads.length ? "Adicionar imagens" : "Selecionar imagens"}
                        <input type="file" accept="image/jpeg,image/png" multiple onChange={selectImages} disabled={campaignForm.ads.length >= 5} className="fm-field mt-2 block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50" />
                        <span className="mt-1 block text-xs font-normal text-slate-500">JPEG ou PNG. A IA analisa cada imagem; duplicatas e arquivos inválidos são ignorados.</span>
                      </label>
                      {campaignForm.ads.map((ad, index) => (
                        <div key={ad.item_key || `ad-${index}`} className="space-y-3 rounded-lg border border-slate-200 p-4">
                          <div className="flex items-center justify-between"><p className="text-sm font-semibold text-slate-900">Imagem {index + 1}</p><button type="button" title="Remover imagem" onClick={() => removeAd(index)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><Trash2 size={16} /></button></div>
                          <div className="flex items-center gap-3">
                            {ad.thumbnail_url ? <img src={ad.thumbnail_url} alt={ad.media_name || `Imagem ${index + 1}`} className="h-20 w-20 rounded-lg object-cover" /> : null}
                            <div className="text-sm text-slate-600"><p>{ad.media_name || "Imagem selecionada"}</p><p className="mt-1 font-medium">{creativeItemStatus(ad)}</p></div>
                          </div>
                          {ad.analysis_error ? <div className="flex items-center justify-between gap-3"><InfoBanner tone="warning">{ad.analysis_error}</InfoBanner><ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={() => retryImageAnalysis(index)}>Tentar novamente</ActionButton></div> : null}
                          <div className="grid gap-3 md:grid-cols-2">
                            <label className="space-y-1 text-sm font-medium text-slate-700">Texto principal{analysisBusy(ad) ? <div className="h-[104px] animate-pulse rounded-lg bg-slate-100" aria-label="Gerando texto principal" /> : <textarea rows={4} value={ad.primary_text} onChange={(event) => updateAd(index, { primary_text: event.target.value })} className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" />}</label>
                            <div className="space-y-3">
                              <label className="block space-y-1 text-sm font-medium text-slate-700">Título{analysisBusy(ad) ? <div className="h-10 animate-pulse rounded-lg bg-slate-100" aria-label="Gerando título" /> : <input value={ad.headline} onChange={(event) => updateAd(index, { headline: event.target.value })} className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" />}</label>
                              <label className="block space-y-1 text-sm font-medium text-slate-700">Descrição{analysisBusy(ad) ? <div className="h-10 animate-pulse rounded-lg bg-slate-100" aria-label="Gerando descrição" /> : <input value={ad.description} onChange={(event) => updateAd(index, { description: event.target.value })} className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2" />}</label>
                            </div>
                          </div>
                          <p className="text-xs text-slate-500">CTA: WhatsApp / enviar mensagem · a copy continua editável.</p>
                        </div>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <ActionButton variant="secondary" onClick={() => setShowNewCampaign(false)}>Cancelar</ActionButton>
                      <ActionButton onClick={() => setConfirmingCampaign(true)} disabled={!writesEnabled || !campaignReadiness?.ready || !campaignForm.daily_budget_brl || !campaignForm.page_id || !compatibleWhatsapps.length || (compatibleWhatsapps.length > 1 && !campaignForm.whatsapp_connection_id) || !campaignForm.ads.length || (campaignForm.location_type === "radius" && (!campaignForm.latitude || !campaignForm.longitude || !campaignForm.location_display_name || !campaignForm.radius_km)) || campaignForm.ads.some((ad) => !ad.media_data_url || !analysisReady(ad) || analysisBusy(ad))}>Criar campanha e anúncios</ActionButton>
                    </div>
                  </>
                ) : <ExistingPostPicker
                  empresaId={empresaId}
                  pages={assetPages}
                  adAccountId={configuredAdAccountId}
                  initialPageId={campaignForm.page_id || config.page_id || ""}
                  campaignDraft={{
                    daily_budget_brl: campaignForm.daily_budget_brl,
                    location_type: campaignForm.location_type,
                    latitude: campaignForm.latitude,
                    longitude: campaignForm.longitude,
                    radius_km: campaignForm.radius_km,
                    gender_mode: campaignForm.gender_mode,
                    page_id: campaignForm.page_id,
                    whatsapp_connection_id: campaignForm.whatsapp_connection_id,
                  }}
                  onPageChange={(pageId) => setCampaignForm((current) => ({ ...current, page_id: pageId }))}
                />}
              </>
            ) : (
              <div className="space-y-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <p className="font-semibold">Confirme a criação pausada</p>
                <div className="grid gap-2 md:grid-cols-2">
                  <p>Orçamento: <strong>{campaignForm.daily_budget_brl}/dia</strong></p>
                  <p>Local: <strong>{campaignForm.location_type === "brazil" ? "Brasil" : `${campaignForm.location_display_name || "Pino localizado"} · ${campaignForm.radius_km} km`}</strong></p>
                  <p>Gênero: <strong>{campaignForm.gender_mode === "all" ? "Todos" : campaignForm.gender_mode === "women" ? "Mulheres" : "Homens"}</strong></p>
                  <p>Página: <strong>{assetPages.find((page) => page.id === campaignForm.page_id)?.name || "Selecionada"}</strong></p>
                  <p>WhatsApp: <strong>{selectedWhatsapp ? whatsappLabel(selectedWhatsapp) : "Oficial da empresa"}</strong></p>
                  <p>Status inicial: <strong>Pausada</strong></p>
                </div>
                <p>Campanha e anúncios serão criados pausados para revisão. Cada imagem gera um anúncio independente no mesmo conjunto.</p>
                <div className="flex flex-wrap gap-2">
                  <ActionButton variant="secondary" onClick={() => setConfirmingCampaign(false)}>Voltar</ActionButton>
                  <ActionButton onClick={submitNewCampaign} disabled={!writesEnabled || !campaignReadiness?.ready || creatingCampaign}>{creatingCampaign ? "Criando..." : "Criar campanha e anúncios"}</ActionButton>
                </div>
              </div>
            )}
            {createdStructure ? (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
                <p className="flex items-center gap-2 font-semibold"><CheckCircle2 size={16} />{createdStructure.partial ? "Criação parcial preservada" : "Campanha completa criada na Meta"}</p>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Campanha</span>{createdStructure.name}<br />{createdStructure.campaign_status || "PAUSED"}</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Conjunto de anúncios</span>{createdStructure.adset_name}<br />{createdStructure.adset_status || "PAUSED"}</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Orçamento</span>{formatBrlCents(createdStructure.daily_budget_cents)}/dia</div>
                  <div><span className="block text-xs font-semibold uppercase text-emerald-700">Anúncios</span>{createdStructure.ad_count || createdStructure.pipeline?.ads?.length || 0} validado(s) e pausado(s)</div>
                </div>
                {visibleCreationItems.length ? <div className="mt-4 space-y-2"><p className="font-semibold">Progresso por imagem</p>{visibleCreationItems.map((item, index) => {
                  const failed = ["partial_error", "failed", "error", "ad_created_unverified"].includes(item.status) || Boolean(item.error);
                  const label = item.ad_status === "PAUSED" || item.status === "ad_created" ? "Anúncio criado" : item.status === "creative_created" ? "Criativo criado" : item.status === "media_uploaded" ? "Imagem enviada" : item.status === "analyzing" ? "Analisando imagem" : failed ? "Erro na criação" : "Pendente";
                  return <div key={item.item_key || item.sequence || index} className={`rounded-lg border px-3 py-2 ${failed ? "border-red-200 bg-red-50 text-red-950" : "border-emerald-200 bg-white"}`}><div className="flex items-center justify-between gap-3"><span>Imagem {item.sequence || index + 1}</span><span className="font-medium">{label}</span></div>{failed && item.error?.message ? <p className="mt-1 text-xs">{item.error.message}</p> : null}</div>;
                })}</div> : null}
                {createdStructure.partial && createdStructure.error?.message ? <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-950">Pendência: {createdStructure.error.message}</p> : null}
                {createdStructure.partial ? <ActionButton variant="secondary" onClick={submitNewCampaign} disabled={creatingCampaign}>{creatingCampaign ? "Retomando..." : "Retomar criação"}</ActionButton> : null}
              </div>
            ) : null}
          </div>
        )}
      </SectionCard>

      <SectionCard className="ux05-existing-campaigns" title="Campanhas existentes" actions={<ActionButton variant="secondary" icon={RefreshCw} onClick={loadCampaigns}>Atualizar</ActionButton>}>
          <div className="space-y-3">
            <select className="fm-field w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" value={selectedCampaignId} onChange={(e) => selectExistingCampaign(e.target.value)}>
              <option value="">Selecione uma campanha</option>
              {campaigns.map((campaign) => (
                <option key={campaign.id} value={campaign.id}>{campaign.name || campaign.id}</option>
              ))}
            </select>
            <div className="flex flex-wrap gap-2">
              <ActionButton variant="secondary" icon={RefreshCw} onClick={loadAdsets}>AdSets</ActionButton>
              <ActionButton variant="secondary" icon={RefreshCw} onClick={loadAds}>Ads</ActionButton>
              <ActionButton icon={inspecting ? Loader2 : Eye} onClick={() => inspectCampaign()} disabled={!selectedCampaignId || inspecting}>
                {inspecting ? "Carregando..." : "Carregar dados reais"}
              </ActionButton>
            </div>
          </div>
      </SectionCard>

      {inspection ? (
        <SectionCard title="Dados reais da campanha">
          <div className="space-y-4 text-sm text-slate-700">
            <div className="rounded-lg border border-slate-200 p-4">
              <p className="font-semibold text-slate-950">{inspection.campaign?.name || "Campanha Meta"}</p>
              <div className="mt-2 grid gap-2 md:grid-cols-4">
                <p>Status: <strong>{statusText(inspection.campaign?.status)}</strong></p>
                <p>Efetivo: <strong>{statusText(inspection.campaign?.effective_status)}</strong></p>
                <p>Objetivo: <strong>{inspection.campaign?.objective || "Não retornado"}</strong></p>
                <p>Compra: <strong>{inspection.campaign?.buying_type || "Não retornado"}</strong></p>
              </div>
            </div>

            <div>
              <p className="mb-2 font-semibold text-slate-950">Conjuntos de anúncios</p>
              {adsets.length ? (
                <div className="space-y-2">
                  {adsets.map((adset) => (
                    <div key={adset.id} className="rounded-lg border border-slate-200 p-4">
                      <p className="font-medium text-slate-900">{adset.name || adset.id}</p>
                      <div className="mt-2 grid gap-2 md:grid-cols-4">
                        <p>Status: <strong>{statusText(adset.status)}</strong></p>
                        <p>Efetivo: <strong>{statusText(adset.effective_status)}</strong></p>
                        <p>Orçamento: <strong>{formatBudget(adset.daily_budget || adset.lifetime_budget)}</strong></p>
                        <p>Destino: <strong>{adset.destination_type || "Não retornado"}</strong></p>
                        <p>Otimização: <strong>{adset.optimization_goal || "Não retornado"}</strong></p>
                        <p>Cobrança: <strong>{adset.billing_event || "Não retornado"}</strong></p>
                        <p>Lance: <strong>{adset.bid_strategy || "Não retornado"}</strong></p>
                        <p>WhatsApp: <strong>{adset.promoted_object?.whatsapp_phone_number || maskTail(adset.promoted_object?.whats_app_business_phone_number_id)}</strong></p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : <EmptyState title="Nenhum conjunto retornado" description="A Meta não retornou AdSets para esta campanha." />}
            </div>

            <div>
              <p className="mb-2 font-semibold text-slate-950">Anúncios</p>
              {ads.length ? (
                <div className="space-y-2">
                  {ads.map((ad) => (
                    <div key={ad.id} className="rounded-lg border border-slate-200 p-4">
                      <p className="font-medium text-slate-900">{ad.name || ad.id}</p>
                      <div className="mt-2 grid gap-2 md:grid-cols-3">
                        <p>Status: <strong>{statusText(ad.status)}</strong></p>
                        <p>Efetivo: <strong>{statusText(ad.effective_status)}</strong></p>
                        <p>Creative: <strong>{ad.creative?.name || ad.creative?.id || "Não retornado"}</strong></p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : <EmptyState title="Nenhum anúncio retornado" description="A Meta não retornou Ads para esta campanha." />}
            </div>

            {inspection.creatives?.length ? (
              <div>
                <p className="mb-2 font-semibold text-slate-950">Criativos</p>
                <div className="grid gap-3 md:grid-cols-2">
                  {inspection.creatives.map((creative) => {
                    const summary = creativeSummary(creative);
                    return (
                      <div key={creative.id} className="rounded-lg border border-slate-200 p-4">
                        {summary.thumbnail ? <img src={summary.thumbnail} alt="" className="mb-3 h-24 w-24 rounded-md object-cover" /> : null}
                        <p className="font-medium text-slate-900">{summary.headline}</p>
                        {summary.text ? <p className="mt-1 text-slate-600">{summary.text}</p> : null}
                        {summary.description ? <p className="mt-1 text-slate-500">{summary.description}</p> : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>
        </SectionCard>
      ) : null}

      {inspection ? (
        <SectionCard title="Diagnóstico avançado">
          <JsonPreview value={inspection} />
        </SectionCard>
      ) : null}
    </div>
  );
}
