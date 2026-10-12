import "../../styles/ux05-settings.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  LogOut,
  MessageCircle,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  X,
} from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId, getAuthenticatedUser } from "../../utils/auth";
import {
  FB_SDK_ORIGIN,
  META_ONBOARDING_MODES,
  buildMetaLoginOptions,
  classifyMetaLoginResponse,
  classifyMetaBrowserException,
  consumeMetaSignupSession,
  createMetaSignupCompletionAggregator,
  getMetaSignupErrorMessage,
  isMetaConfigValid,
  isSignupSessionUsable,
  loadFacebookSdk,
  logMetaSignupDiagnostic,
  parseMetaEmbeddedSignupEvent,
  prepareMetaSignupSession,
  startMetaEmbeddedSignup,
} from "../../utils/metaEmbeddedSignup";

const POLL_INTERVAL_MS = 5000;
const META_MESSAGE_ORIGINS = new Set([
  FB_SDK_ORIGIN,
  "https://www.facebook.com",
  "https://web.facebook.com",
]);

function StatusBadge({ status, loading }) {
  if (loading) {
    return <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600"><Loader2 size={12} className="animate-spin" />Verificando</span>;
  }
  if (["connected", "ativo"].includes(String(status || "").toLowerCase())) {
    return <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700"><span className="h-2 w-2 rounded-full bg-emerald-500" />Conectado</span>;
  }
  if (["connecting", "pending", "pending_session", "exchanging_code", "token_obtained", "discovering_assets", "retryable_error", "qrcode", "qr", "pairing"].includes(String(status || "").toLowerCase())) {
    return <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700"><span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />Pendente</span>;
  }
  if (String(status || "").toLowerCase() === "attention") {
    return <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700"><span className="h-2 w-2 rounded-full bg-amber-500" />Atenção</span>;
  }
  return <span className="inline-flex items-center gap-2 rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-semibold text-red-700"><span className="h-2 w-2 rounded-full bg-red-500" />Desconectado</span>;
}

function normalizarStatus(data) {
  const raw = String(data?.status || "").toLowerCase();
  if (data?.conectado || data?.online || ["open", "connected", "online", "ativo"].includes(raw)) return "connected";
  if (["connecting", "qrcode", "qr", "pairing"].includes(raw)) return "connecting";
  return "disconnected";
}

export default function Conexoes() {
  const empresaId = getActiveEmpresaId();
  const usuarioId = getAuthenticatedUser()?.id || "";
  const isReviewer = getAuthenticatedUser()?.role === "meta_app_reviewer";
  const pollRef = useRef(null);
  const signupSessionRef = useRef(null);
  const signupDataRef = useRef({});
  const signupCompletionRef = useRef(null);
  const selectedOnboardingModeRef = useRef(META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP);
  const signupPreparationRef = useRef(null);
  const metaLoginAttemptRef = useRef(false);
  const [statusData, setStatusData] = useState(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [qrLoading, setQrLoading] = useState(false);
  const [disconnectLoading, setDisconnectLoading] = useState(false);
  const [qrBase64, setQrBase64] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [toast, setToast] = useState(null);
  const [metaConfig, setMetaConfig] = useState(null);
  const [metaStatus, setMetaStatus] = useState(null);
  const [metaAssets, setMetaAssets] = useState(null);
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaPreparing, setMetaPreparing] = useState(false);
  const [activeMetaOnboardingMode, setActiveMetaOnboardingMode] = useState(null);
  const [selectedWaba, setSelectedWaba] = useState("");
  const [selectedPhone, setSelectedPhone] = useState("");
  const [reviewerOverview, setReviewerOverview] = useState(null);

  const statusAtual = useMemo(() => normalizarStatus(statusData), [statusData]);
  const conectado = statusAtual === "connected";
  const wabas = useMemo(() => {
    const businesses = metaAssets?.businesses || [];
    return businesses.flatMap((business) => (business.wabas || []).map((waba) => ({ ...waba, business_id: business.id, business_name: business.name })));
  }, [metaAssets]);
  const phones = useMemo(() => (wabas.find((waba) => String(waba.id) === String(selectedWaba))?.phone_numbers || []), [wabas, selectedWaba]);
  const metaSignupBlocked = metaLoading || metaPreparing || !metaConfig?.configured;

  const showToast = useCallback((type, message) => setToast({ type, message }), []);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const pararPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const carregarMeta = useCallback(async () => {
    if (!empresaId) return;
    try {
      const [configRes, statusRes] = await Promise.all([
        api.get(`/empresas/${empresaId}/conexoes/meta/config`),
        api.get(`/empresas/${empresaId}/conexoes/meta/status`),
      ]);
      setMetaConfig(configRes.data || {});
      setMetaStatus(statusRes.data || {});
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível carregar a conexão Meta Official.");
    }
  }, [empresaId]);

  const prepararMetaSignup = useCallback(async ({ silent = true, onboardingMode = META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP } = {}) => {
    if (!empresaId || !isMetaConfigValid(metaConfig)) return null;
    if (isSignupSessionUsable(signupSessionRef.current) && signupSessionRef.current.onboardingMode === onboardingMode) {
      logMetaSignupDiagnostic("META_SESSION_READY", "session_prepare_reused", { onboarding_mode: onboardingMode });
      return signupSessionRef.current;
    }
    if (signupPreparationRef.current?.mode === onboardingMode) return signupPreparationRef.current.promise;
    if (signupPreparationRef.current) return null;

    setMetaPreparing(true);
    logMetaSignupDiagnostic("META_SESSION_PREPARING", "session_prepare_started", { onboarding_mode: onboardingMode });
    const preparation = Promise.all([
      prepareMetaSignupSession(api, empresaId, usuarioId, metaConfig, Date.now(), onboardingMode),
      loadFacebookSdk(metaConfig.app_id, metaConfig.graph_api_version, metaConfig.sdk_url),
    ])
      .then(([prepared]) => {
        const session = prepared.session;
        signupSessionRef.current = session;
        logMetaSignupDiagnostic("META_SDK_READY", prepared.reused ? "session_prepare_reused" : "session_prepare_completed", { onboarding_mode: session.onboardingMode });
        return session;
      })
      .catch((err) => {
        const code = err?.code || "META_BROWSER_ERROR";
        logMetaSignupDiagnostic(code, "prepare_failed");
        if (!silent) setErrorMessage(getMetaSignupErrorMessage(code));
        return null;
      })
      .finally(() => {
        if (signupPreparationRef.current?.promise === preparation) signupPreparationRef.current = null;
        setMetaPreparing(false);
      });

    signupPreparationRef.current = { mode: onboardingMode, promise: preparation };
    return preparation;
  }, [empresaId, metaConfig, usuarioId]);

  const carregarStatus = useCallback(async ({ silent = false } = {}) => {
    if (!empresaId) {
      setStatusLoading(false);
      setErrorMessage("Não foi possível identificar sua empresa. Faça login novamente.");
      return null;
    }
    if (!silent) setStatusLoading(true);
    try {
      const res = await api.get(`/empresas/${empresaId}/whatsapp/status`);
      const data = res.data || {};
      setStatusData(data);
      setErrorMessage("");
      if (normalizarStatus(data) === "connected") {
        setQrBase64("");
        pararPolling();
      }
      return data;
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível consultar o status do WhatsApp.");
      return null;
    } finally {
      if (!silent) setStatusLoading(false);
    }
  }, [empresaId, pararPolling]);

  const iniciarPolling = useCallback(() => {
    pararPolling();
    pollRef.current = window.setInterval(async () => {
      const data = await carregarStatus({ silent: true });
      if (normalizarStatus(data) === "connected") showToast("success", "WhatsApp conectado com sucesso.");
    }, POLL_INTERVAL_MS);
  }, [carregarStatus, pararPolling, showToast]);

  useEffect(() => {
    carregarStatus();
    carregarMeta();
    if (isReviewer && empresaId) {
      api.get(`/reviewer/${empresaId}/overview`).then((response) => setReviewerOverview(response.data || {})).catch(() => setReviewerOverview(null));
    }
    const onMessage = (event) => {
      if (!META_MESSAGE_ORIGINS.has(event.origin) || typeof event.data !== "string") return;
      const data = parseMetaEmbeddedSignupEvent(event.data, selectedOnboardingModeRef.current);
      if (data) {
        signupDataRef.current = data;
        signupCompletionRef.current?.addSessionEvent(data);
      }
    };
    window.addEventListener("message", onMessage);
    return () => {
      pararPolling();
      window.removeEventListener("message", onMessage);
    };
  }, [carregarStatus, carregarMeta, pararPolling, isReviewer, empresaId]);

  const conectarWhatsApp = async () => {
    if (!empresaId) return;
    setActionLoading(true);
    setQrLoading(true);
    setQrBase64("");
    setErrorMessage("");
    try {
      const res = await api.post(`/empresas/${empresaId}/whatsapp/conectar`);
      const data = res.data || {};
      setStatusData(data);
      if (data.base64) {
        setQrBase64(data.base64);
        iniciarPolling();
      } else setErrorMessage(data.detail || "QR Code ainda não disponível. Atualize em alguns segundos.");
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível iniciar a conexão WhatsApp.");
    } finally {
      setActionLoading(false);
      setQrLoading(false);
    }
  };

  const atualizarQrCode = async () => {
    if (!empresaId) return;
    setQrLoading(true);
    setErrorMessage("");
    try {
      const res = await api.get(`/empresas/${empresaId}/whatsapp/qrcode`);
      const data = res.data || {};
      setStatusData(data);
      setQrBase64(data.base64 || "");
      if (data.base64) iniciarPolling();
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível atualizar o QR Code.");
    } finally {
      setQrLoading(false);
    }
  };

  const atualizarStatus = async () => {
    if (!empresaId) return;
    setStatusLoading(true);
    try {
      const res = await api.post(`/empresas/${empresaId}/whatsapp/atualizar-status`);
      setStatusData(res.data || {});
      setErrorMessage("");
      await carregarMeta();
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível atualizar o status.");
    } finally {
      setStatusLoading(false);
    }
  };

  const desconectarWhatsApp = async () => {
    if (!empresaId) return;
    const confirmar = window.confirm("Desconectar o WhatsApp Evolution pausa o atendimento automático até uma nova leitura de QR Code. Continuar?");
    if (!confirmar) return;
    setDisconnectLoading(true);
    pararPolling();
    try {
      const res = await api.post(`/empresas/${empresaId}/whatsapp/desconectar`);
      setStatusData(res.data || {});
      setQrBase64("");
      showToast("success", "WhatsApp Evolution desconectado.");
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível desconectar o WhatsApp.");
    } finally {
      setDisconnectLoading(false);
    }
  };

  const finalizarMetaSignupCancelado = async (session, errorCode) => {
    try {
      await api.post(`/empresas/${empresaId}/conexoes/meta/complete`, {
        state: session.state,
        onboarding_mode: session.onboardingMode,
        cancelled: true,
        signup_payload: signupDataRef.current,
      });
    } catch (err) {
      logMetaSignupDiagnostic(err?.response?.status === 400 ? "META_SESSION_EXPIRED" : "META_BROWSER_ERROR", "complete_cancel_failed");
    } finally {
      setErrorMessage(getMetaSignupErrorMessage(errorCode));
    }
  };

  const iniciarMetaSignup = async (onboardingMode) => {
    if (!empresaId) return;
    if (metaLoginAttemptRef.current || metaLoading) return;
    metaLoginAttemptRef.current = true;
    setMetaLoading(true);
    setActiveMetaOnboardingMode(onboardingMode);
    setErrorMessage("");
    selectedOnboardingModeRef.current = onboardingMode;
    signupDataRef.current = {};
    signupCompletionRef.current = createMetaSignupCompletionAggregator();

    const session = isSignupSessionUsable(signupSessionRef.current) && signupSessionRef.current.onboardingMode === onboardingMode
      ? signupSessionRef.current
      : await prepararMetaSignup({ silent: false, onboardingMode });
    if (!isSignupSessionUsable(session)) {
      logMetaSignupDiagnostic("META_SESSION_EXPIRED", "click_without_valid_session", { onboarding_mode: onboardingMode });
      setErrorMessage(getMetaSignupErrorMessage("META_SESSION_EXPIRED"));
      setMetaLoading(false);
      setActiveMetaOnboardingMode(null);
      metaLoginAttemptRef.current = false;
      return;
    }

    const FB = window.FB;
    if (!FB?.login) {
      logMetaSignupDiagnostic("META_SDK_NOT_LOADED", "click_without_sdk", { onboarding_mode: session.onboardingMode });
      setErrorMessage(getMetaSignupErrorMessage("META_SDK_NOT_LOADED"));
      setMetaLoading(false);
      setActiveMetaOnboardingMode(null);
      metaLoginAttemptRef.current = false;
      return;
    }

    const startedAt = Date.now();
    consumeMetaSignupSession(empresaId, usuarioId, session);
    signupSessionRef.current = session;

    const loginOptions = buildMetaLoginOptions({ configId: session.config?.config_id, onboardingMode: session.onboardingMode });
    const processMetaLoginResponse = async (response) => {
      const errorCode = classifyMetaLoginResponse(response, Date.now() - startedAt);
      try {
        if (errorCode) {
          logMetaSignupDiagnostic(errorCode, "login_callback");
          await finalizarMetaSignupCancelado(session, errorCode);
          return;
        }
        signupCompletionRef.current?.addLoginResponse(response);
        const completedSignup = await signupCompletionRef.current.waitForCompletion();
        const completeRes = await api.post(`/empresas/${empresaId}/conexoes/meta/complete`, {
          state: session.state,
          onboarding_mode: session.onboardingMode,
          code: response.authResponse.code,
          signup_payload: completedSignup.signupPayload || signupDataRef.current,
        });
        setMetaStatus(completeRes.data || {});
        logMetaSignupDiagnostic("META_LOGIN_SUCCESS", "complete_success", { onboarding_mode: session.onboardingMode });
        showToast("success", "Autorização Meta recebida.");
      } catch (err) {
        logMetaSignupDiagnostic(err?.response?.status === 400 ? "META_SESSION_EXPIRED" : "META_BROWSER_ERROR", "complete_failed");
        setErrorMessage(err?.response?.data?.detail?.message || err?.response?.data?.detail || "Não foi possível concluir o Embedded Signup.");
      } finally {
        signupSessionRef.current = null;
        signupCompletionRef.current = null;
        setMetaLoading(false);
        setActiveMetaOnboardingMode(null);
        metaLoginAttemptRef.current = false;
      }
    };
    try {
      logMetaSignupDiagnostic("META_LOGIN_OPENING", "login_started", { login_options: loginOptions, sdk_state: "initialized", onboarding_mode: session.onboardingMode });
      startMetaEmbeddedSignup(
        FB,
        session.config,
        session.onboardingMode,
        processMetaLoginResponse,
        (err) => {
          logMetaSignupDiagnostic(err?.response?.status === 400 ? "META_SESSION_EXPIRED" : "META_BROWSER_ERROR", "complete_failed");
          setErrorMessage(err?.response?.data?.detail?.message || err?.response?.data?.detail || "Não foi possível concluir o Embedded Signup.");
        },
      );
    } catch (err) {
      const code = classifyMetaBrowserException(err);
      logMetaSignupDiagnostic(code, "login_throw", { error: err, login_options: loginOptions, sdk_state: "initialized", onboarding_mode: session.onboardingMode });
      setErrorMessage(getMetaSignupErrorMessage(code));
      // FB.login threw before returning control to the SDK, so the prepared state was not sent.
      // Keep the same unexpired session available for a deliberate retry, without issuing a new one.
      session.consumed = false;
      signupSessionRef.current = session;
      setMetaLoading(false);
      setActiveMetaOnboardingMode(null);
      metaLoginAttemptRef.current = false;
    }
  };

  const carregarAssetsMeta = async () => {
    if (!empresaId || !metaStatus?.conexao_id) return;
    setMetaLoading(true);
    setErrorMessage("");
    try {
      const res = await api.get(`/empresas/${empresaId}/conexoes/meta/assets`, { params: { conexao_id: metaStatus.conexao_id } });
      setMetaAssets(res.data || {});
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Os ativos da Meta ainda não estão disponíveis. Tente novamente mais tarde.");
    } finally {
      setMetaLoading(false);
    }
  };

  const selecionarNumeroMeta = async () => {
    if (!empresaId || !metaStatus?.conexao_id || !selectedWaba || !selectedPhone) return;
    const waba = wabas.find((item) => String(item.id) === String(selectedWaba));
    setMetaLoading(true);
    try {
      const res = await api.post(`/empresas/${empresaId}/conexoes/meta/select-number`, {
        conexao_id: metaStatus.conexao_id,
        business_id: waba?.business_id,
        waba_id: selectedWaba,
        phone_number_id: selectedPhone,
      });
      setMetaStatus(res.data || {});
      showToast("success", "Número Meta selecionado.");
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || "Não foi possível selecionar o número Meta.");
    } finally {
      setMetaLoading(false);
    }
  };

  const acaoMeta = async (action) => {
    if (!empresaId || !metaStatus?.conexao_id) return;
    setMetaLoading(true);
    setErrorMessage("");
    try {
      const res = await api.post(`/empresas/${empresaId}/conexoes/${metaStatus.conexao_id}/${action}`, {});
      setMetaStatus(res.data || {});
      showToast("success", "Status Meta atualizado.");
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail?.message || err?.response?.data?.detail || "Não foi possível executar a ação Meta.");
    } finally {
      setMetaLoading(false);
    }
  };

  if (isReviewer) {
    const connection = reviewerOverview?.conexao_whatsapp || metaStatus || {};
    return (
      <div className="fm-settings fm-connections space-y-6 text-slate-950">
        <div className="fm-settings-reviewer-heading rounded-xl border border-blue-200 bg-blue-50 p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-700">Conta de análise da Meta</p>
          <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold"><ShieldCheck className="text-blue-600" size={25} />Conexões WhatsApp</h1>
          <p className="mt-2 text-sm text-slate-600">Acesso limitado à demonstração da integração de conversões do WhatsApp.</p>
        </div>
        <div className="fm-settings-panel rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold">API Oficial da Meta</h2>
          <p className="mt-2 text-sm text-slate-600">Status: <strong>{connection.status || "indisponível"}</strong></p>
          <p className="text-sm text-slate-600">WABA configurada: {connection.waba_configured ? "sim" : "não"}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="fm-settings fm-connections space-y-6 text-slate-950">
      {toast ? <div role="status" aria-live="polite" className="fm-settings-toast fixed right-6 top-6 z-50 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800 shadow-lg">{toast.message}</div> : null}

      <div className="fm-settings-panel rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-700">Painel cliente</p>
            <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold text-slate-950"><Smartphone className="text-emerald-600" size={25} />Conexões WhatsApp</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-500">Evolution e API Oficial da Meta podem coexistir. Cada provider mantém status, credenciais e ações separados.</p>
          </div>
          <button type="button" onClick={atualizarStatus} disabled={statusLoading || metaLoading} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-60"><RefreshCw size={14} className={statusLoading || metaLoading ? "animate-spin" : ""} />Atualizar status</button>
        </div>
      </div>

      {errorMessage ? (
        <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="flex items-start gap-2"><AlertCircle size={17} className="mt-0.5 shrink-0" /><div><p className="font-semibold">Não foi possível concluir a operação.</p><p className="mt-1">{typeof errorMessage === "string" ? errorMessage : JSON.stringify(errorMessage)}</p></div><button type="button" onClick={() => setErrorMessage("")} aria-label="Fechar aviso da conexão" className="ml-auto rounded-md p-1 hover:bg-amber-100"><X size={14} /></button></div>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="fm-settings-panel rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div><h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950"><MessageCircle size={17} className="text-emerald-600" />Evolution por QR Code</h2><p className="mt-2 text-sm text-slate-500">Provider legado preservado para conexões atuais.</p></div>
            <StatusBadge status={statusAtual} loading={statusLoading} />
          </div>
          <div className="mt-5 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Instância</p><p className="mt-1 break-all text-sm font-medium text-slate-900">{statusData?.instance_name || "Ainda não iniciada"}</p></div>
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Última atualização</p><p className="mt-1 text-sm text-slate-700">{statusData?.ultima_atualizacao || "Não registrado"}</p></div>
          </div>
          <div className="fm-connection-qr mt-5 flex min-h-[300px] items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5">
            {qrLoading ? <div className="text-center text-sm text-slate-500"><Loader2 size={28} className="mx-auto mb-3 animate-spin" />Gerando QR Code...</div> : qrBase64 ? <img src={`data:image/png;base64,${qrBase64}`} alt="QR Code para conectar WhatsApp Evolution" className="mx-auto h-64 w-64 rounded-lg border border-slate-200 bg-white p-3 shadow-sm" /> : conectado ? <div className="text-center text-sm text-emerald-700"><CheckCircle2 size={32} className="mx-auto mb-3" />WhatsApp Evolution conectado.</div> : <div className="text-center text-sm text-slate-500"><Smartphone size={32} className="mx-auto mb-3 text-slate-400" />O QR Code Evolution aparecerá aqui.</div>}
          </div>
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            {conectado ? <button type="button" onClick={desconectarWhatsApp} disabled={disconnectLoading} className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-100 disabled:opacity-60">{disconnectLoading ? <Loader2 size={14} className="animate-spin" /> : <LogOut size={14} />}Desconectar Evolution</button> : <button type="button" onClick={conectarWhatsApp} disabled={actionLoading} className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-60">{actionLoading ? <Loader2 size={14} className="animate-spin" /> : <MessageCircle size={14} />}Conectar WhatsApp</button>}
            <button type="button" onClick={atualizarQrCode} disabled={qrLoading || conectado} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"><RefreshCw size={14} className={qrLoading ? "animate-spin" : ""} />Atualizar QR Code</button>
          </div>
        </div>

        <div className="fm-settings-panel rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div><h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950"><ShieldCheck size={17} className="text-blue-600" />API Oficial da Meta</h2><p className="mt-2 text-sm text-slate-500">Conexão por Embedded Signup oficial, sem QR Code Evolution.</p></div>
            <StatusBadge status={metaStatus?.status} loading={metaLoading} />
          </div>
          <div className="mt-5 grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2">
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Número</p><p className="mt-1 text-sm font-medium text-slate-900">{metaStatus?.display_number || "Não selecionado"}</p></div>
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Nome verificado</p><p className="mt-1 text-sm text-slate-700">{metaStatus?.verified_name || "Não informado"}</p></div>
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Qualidade</p><p className="mt-1 text-sm text-slate-700">{metaStatus?.quality_rating || "Não informado"}</p></div>
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Uso no celular</p><p className="mt-1 text-sm text-slate-700">{metaStatus?.coexistence?.state || "não verificado"}</p></div>
            <div><p className="text-xs uppercase tracking-wide text-slate-500">Última atualização</p><p className="mt-1 text-sm text-slate-700">{metaStatus?.last_synced_at || "Não registrada"}</p></div>
          </div>
          {!metaConfig?.configured ? <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">A conexão com a Meta ainda não está disponível. Entre em contato com o suporte.</p> : null}
          {metaStatus?.current_error ? <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{metaStatus.current_error.user_message || "Seu cadastro foi concluído na Meta, mas o AgentOS ainda não conseguiu consultar os dados do número."}</p> : null}
          <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4">
            <div className="flex items-start gap-2">
              <ShieldCheck size={16} className="mt-0.5 shrink-0 text-blue-700" />
              <div>
                <h3 className="text-base font-semibold text-blue-950">Conecte seu WhatsApp Business ao AgentOS</h3>
                <p className="mt-2 text-sm leading-6 text-blue-800">Continue usando o WhatsApp Business no celular e conecte o mesmo número ao AgentOS para atendimento, automações, campanhas e follow-ups.</p>
              </div>
            </div>
            <p className="mt-4 rounded-lg border border-blue-100 bg-white/70 p-3 text-xs text-blue-900">A disponibilidade dessa modalidade depende da confirmação de elegibilidade pela Meta.</p>
            <button
              type="button"
              onClick={() => iniciarMetaSignup(META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING)}
              disabled={metaSignupBlocked}
              aria-label="Conectar meu WhatsApp Business ao AgentOS"
              className="mt-4 inline-flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-blue-700 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-blue-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
            >
              {activeMetaOnboardingMode === META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING && (metaLoading || metaPreparing) ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
              {metaStatus?.status === "connected" ? "Gerenciar conexão Meta" : "Conectar meu WhatsApp Business"}
            </button>
          </div>
          {activeMetaOnboardingMode && (metaLoading || metaPreparing) ? (
            <p className="mt-3 inline-flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-900">
              <Loader2 size={13} className="animate-spin" />
              Abrindo conexão segura com a Meta…
            </p>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={carregarAssetsMeta} disabled={metaLoading || !metaStatus?.conexao_id} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"><RefreshCw size={14} />Listar ativos</button>
            <button type="button" onClick={() => acaoMeta("validate")} disabled={metaLoading || !metaStatus?.conexao_id || !metaStatus?.configured} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60">Validar</button>
            <button type="button" onClick={() => acaoMeta("sync")} disabled={metaLoading || !metaStatus?.conexao_id || !metaStatus?.configured} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60">Sincronizar dados</button>
            <button type="button" onClick={() => acaoMeta("disconnect")} disabled={metaLoading || !metaStatus?.conexao_id || !metaStatus?.configured} className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-100 disabled:opacity-60">Desconectar</button>
          </div>
          {wabas.length ? (
            <div className="mt-5 grid gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-[1fr_1fr_auto]">
              <label className="fm-settings-field"><span>Conta WhatsApp autorizada</span><select value={selectedWaba} onChange={(event) => { setSelectedWaba(event.target.value); setSelectedPhone(""); }} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <option value="">Selecione a conta autorizada</option>
                {wabas.map((waba) => <option key={waba.id} value={waba.id}>{waba.name || waba.id}</option>)}
              </select></label>
              <label className="fm-settings-field"><span>Número de telefone</span><select value={selectedPhone} onChange={(event) => setSelectedPhone(event.target.value)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <option value="">Selecione o número</option>
                {phones.map((phone) => <option key={phone.id} value={phone.id}>{phone.display_phone_number || phone.verified_name || phone.id}</option>)}
              </select></label>
              <button type="button" onClick={selecionarNumeroMeta} disabled={!selectedWaba || !selectedPhone || metaLoading} className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-60">Usar número</button>
            </div>
          ) : null}
          <p className="mt-4 text-xs text-slate-600">Conecte sua conta do WhatsApp Business pela Meta. A disponibilidade do uso conjunto com o aplicativo depende da elegibilidade do número.</p>
        </div>
      </div>
    </div>
  );
}
