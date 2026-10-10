export const FB_SDK_ORIGIN = "https://connect.facebook.net";
export const FB_SDK_TIMEOUT_MS = 12000;
export const META_SESSION_EXPIRY_BUFFER_MS = 30000;
export const META_LOGIN_EMPTY_RESPONSE_MS = 1500;
export const META_SESSION_EVENT_WAIT_MS = 2500;
export const META_ONBOARDING_MODES = {
  BUSINESS_APP_ONBOARDING: "business_app_onboarding",
  STANDARD_EMBEDDED_SIGNUP: "standard_embedded_signup",
};
export const META_BUSINESS_APP_FEATURE_TYPE = "whatsapp_business_app_onboarding";

export const META_SIGNUP_ERROR_MESSAGES = {
  META_SDK_NOT_LOADED: "Não foi possível carregar o SDK oficial da Meta. Atualize a página e tente novamente.",
  META_SDK_INIT_FAILED: "Não foi possível inicializar o SDK oficial da Meta. Atualize a página e tente novamente.",
  META_POPUP_BLOCKED: "Seu navegador bloqueou a janela da Meta. Permita pop-ups para este domínio e tente novamente.",
  META_LOGIN_CANCELLED: "Embedded Signup cancelado ou popup fechado antes da autorização.",
  META_LOGIN_EMPTY_RESPONSE: "A Meta não retornou autorização. Tente novamente.",
  META_SESSION_EXPIRED: "A sessão de segurança da Meta expirou. Aguarde a preparação e tente novamente.",
  META_INVALID_CONFIG: "Configuração global Meta ausente. Informe App ID e Config ID no ambiente.",
  META_LOGIN_INVALID_OPTIONS: "A configuração do Embedded Signup da Meta é inválida. Atualize a página e tente novamente.",
  META_APP_DOMAIN_NOT_ALLOWED: "Este domínio não está autorizado na configuração da Meta.",
  META_SDK_NOT_INITIALIZED: "O SDK oficial da Meta ainda não foi inicializado. Atualize a página e tente novamente.",
  META_BROWSER_ERROR: "Não foi possível abrir o Embedded Signup neste navegador. Atualize a página e tente novamente.",
};

let sdkPromise = null;
let sdkKey = "";
let sdkInitialized = false;
const preparedSessions = new Map();

function redactSensitiveText(value) {
  return String(value || "")
    .replace(/([?&](?:state|code|token|access_token|client_secret|nonce|session(?:_id)?)=)[^&#\s]+/gi, "$1[redacted]")
    .replace(/("(?:state|code|token|access_token|client_secret|nonce|session(?:_id)?)"\s*:\s*")[^"]*(")/gi, "$1[redacted]$2")
    .replace(/\b(?:state|code|token|access_token|client_secret|nonce|session(?:_id)?)\s*[:=]\s*[^,\s&]+/gi, (match) => match.replace(/([:=]\s*)[^,\s&]+$/, "$1[redacted]"));
}

export function sanitizeMetaBrowserError(error) {
  const rawCode = error?.code;
  const code = typeof rawCode === "number" || (typeof rawCode === "string" && /^[A-Z0-9_-]{1,80}$/i.test(rawCode))
    ? String(rawCode).slice(0, 80)
    : undefined;
  return {
    name: redactSensitiveText(error?.name || "Error").slice(0, 80),
    message: redactSensitiveText(error?.message || "Erro sem mensagem retornado pelo SDK da Meta.").slice(0, 240),
    ...(code ? { code } : {}),
  };
}

export function getMetaLoginOptionMetadata(options) {
  return Object.fromEntries(
    Object.keys(options || {}).sort().map((key) => [key, Array.isArray(options[key]) ? "array" : typeof options[key]]),
  );
}

function safeOrigin() {
  return typeof window !== "undefined" ? window.location.origin : "";
}

function sdkLoadState() {
  if (typeof document === "undefined") return "no_document";
  const script = document.getElementById("facebook-jssdk");
  if (!script) return "missing";
  return script.dataset?.loaded === "true" ? "loaded" : "loading";
}

export function logMetaSignupDiagnostic(code, stage, extra = {}) {
  const sanitizedExtra = {};
  if (extra.error) sanitizedExtra.error = sanitizeMetaBrowserError(extra.error);
  if (extra.sdk_state) sanitizedExtra.sdk_state = String(extra.sdk_state).slice(0, 80);
  if (extra.login_options) {
    sanitizedExtra.login_option_keys = Object.keys(extra.login_options).sort();
    sanitizedExtra.login_option_types = getMetaLoginOptionMetadata(extra.login_options);
    sanitizedExtra.feature_type_present = Boolean(extra.login_options.extras?.featureType);
  }
  if (extra.onboarding_mode) sanitizedExtra.onboarding_mode = normalizeMetaOnboardingMode(extra.onboarding_mode);
  const details = {
    stage,
    code,
    sdk_load_state: sdkLoadState(),
    has_window_fb: Boolean(typeof window !== "undefined" && window.FB),
    origin: safeOrigin(),
    ...sanitizedExtra,
  };
  console.warn("[meta-embedded-signup]", details);
}

export function isMetaConfigValid(config) {
  return Boolean(
    config?.configured
      && typeof config.app_id === "string"
      && config.app_id.trim()
      && typeof config.config_id === "string"
      && config.config_id.trim()
      && typeof config.graph_api_version === "string"
      && /^v\d+\.\d+$/.test(config.graph_api_version),
  );
}

export function isSignupSessionUsable(session, now = Date.now()) {
  return Boolean(
    session?.state
      && !session.consumed
      && Number(session.expiresAt || 0) - META_SESSION_EXPIRY_BUFFER_MS > now
      && isValidMetaOnboardingMode(session.onboardingMode)
      && isMetaConfigValid(session.config),
  );
}

export function normalizeMetaOnboardingMode(mode) {
  return mode === META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING
    ? META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING
    : META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP;
}

export function isValidMetaOnboardingMode(mode) {
  return Object.values(META_ONBOARDING_MODES).includes(mode);
}

export function buildSignupSession(data, fallbackConfig, now = Date.now()) {
  const config = data?.config || fallbackConfig;
  const expiresIn = Number(data?.expires_in || 0);
  return {
    state: String(data?.state || ""),
    onboardingMode: normalizeMetaOnboardingMode(data?.onboarding_mode),
    expiresAt: now + Math.max(0, expiresIn) * 1000,
    config,
    consumed: false,
  };
}

export async function createMetaSignupSession(api, empresaId, fallbackConfig, now = Date.now(), onboardingMode = META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP) {
  const mode = normalizeMetaOnboardingMode(onboardingMode);
  const sessionRes = await api.post(`/empresas/${empresaId}/conexoes/meta/session`, { onboarding_mode: mode });
  const session = buildSignupSession(sessionRes.data || {}, fallbackConfig, now);
  if (session.onboardingMode !== mode) {
    throw Object.assign(new Error("invalid Meta Embedded Signup mode"), { code: "META_SESSION_EXPIRED" });
  }
  if (!isSignupSessionUsable(session, now)) {
    throw Object.assign(new Error("invalid Meta Embedded Signup session"), { code: "META_SESSION_EXPIRED" });
  }
  return session;
}

function sessionKey(empresaId, usuarioId, config, onboardingMode) {
  return `${empresaId}:${usuarioId || "anonymous"}:${config?.app_id || ""}:${config?.config_id || ""}:${normalizeMetaOnboardingMode(onboardingMode)}`;
}

export function consumeMetaSignupSession(empresaId, usuarioId, session) {
  if (!session) return;
  session.consumed = true;
  const key = sessionKey(empresaId, usuarioId, session.config, session.onboardingMode);
  if (preparedSessions.get(key)?.session === session) preparedSessions.delete(key);
}

export async function prepareMetaSignupSession(api, empresaId, usuarioId, config, now = Date.now(), onboardingMode = META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP) {
  const mode = normalizeMetaOnboardingMode(onboardingMode);
  const key = sessionKey(empresaId, usuarioId, config, mode);
  const cached = preparedSessions.get(key);
  if (cached?.session && isSignupSessionUsable(cached.session, now)) {
    return { session: cached.session, reused: true };
  }
  if (cached?.promise) return cached.promise;

  const promise = createMetaSignupSession(api, empresaId, config, now, mode)
    .then((session) => {
      preparedSessions.set(key, { session });
      return { session, reused: false };
    })
    .catch((error) => {
      if (preparedSessions.get(key)?.promise === promise) preparedSessions.delete(key);
      throw error;
    });
  preparedSessions.set(key, { promise });
  return promise;
}

function assertOfficialSdkUrl(url) {
  const resolved = new URL(url || `${FB_SDK_ORIGIN}/pt_BR/sdk.js`, window.location.href);
  if (resolved.origin !== FB_SDK_ORIGIN) {
    throw Object.assign(new Error("invalid Meta SDK origin"), { code: "META_INVALID_CONFIG" });
  }
  return resolved.href;
}

function initFacebookSdk(appId, version) {
  if (!window.FB?.init) {
    throw Object.assign(new Error("FB.init unavailable"), { code: "META_SDK_INIT_FAILED" });
  }
  if (!sdkInitialized) {
    window.FB.init({ appId, autoLogAppEvents: false, xfbml: false, version });
    sdkInitialized = true;
  }
  return window.FB;
}

export function loadFacebookSdk(appId, version, sdkUrl) {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return Promise.reject(Object.assign(new Error("browser unavailable"), { code: "META_BROWSER_ERROR" }));
  }
  if (!appId || !version) {
    return Promise.reject(Object.assign(new Error("invalid SDK config"), { code: "META_INVALID_CONFIG" }));
  }

  const nextKey = `${appId}:${version}`;
  if (window.FB?.init && sdkInitialized && sdkKey === nextKey) {
    return Promise.resolve(window.FB);
  }
  if (sdkPromise && sdkKey === nextKey) return sdkPromise;

  sdkKey = nextKey;
  sdkPromise = new Promise((resolve, reject) => {
    let settled = false;
    const timeout = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(Object.assign(new Error("Meta SDK load timeout"), { code: "META_SDK_NOT_LOADED" }));
    }, FB_SDK_TIMEOUT_MS);

    const finish = () => {
      if (settled) return;
      try {
        const FB = initFacebookSdk(appId, version);
        settled = true;
        window.clearTimeout(timeout);
        const script = document.getElementById("facebook-jssdk");
        if (script?.dataset) script.dataset.loaded = "true";
        resolve(FB);
      } catch (err) {
        settled = true;
        window.clearTimeout(timeout);
        reject(Object.assign(err, { code: err?.code || "META_SDK_INIT_FAILED" }));
      }
    };
    const fail = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      reject(Object.assign(new Error("Meta SDK load error"), { code: "META_SDK_NOT_LOADED" }));
    };

    if (window.FB?.init) {
      finish();
      return;
    }

    const previousAsyncInit = window.fbAsyncInit;
    window.fbAsyncInit = function fbAsyncInit() {
      if (typeof previousAsyncInit === "function") previousAsyncInit();
      finish();
    };

    const existing = document.getElementById("facebook-jssdk");
    if (existing) {
      existing.addEventListener("load", finish, { once: true });
      existing.addEventListener("error", fail, { once: true });
      return;
    }

    let src;
    try {
      src = assertOfficialSdkUrl(sdkUrl);
    } catch (err) {
      if (!settled) {
        settled = true;
        window.clearTimeout(timeout);
        reject(Object.assign(err, { code: "META_INVALID_CONFIG" }));
      }
      return;
    }
    const script = document.createElement("script");
    script.id = "facebook-jssdk";
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    script.src = src;
    script.addEventListener("load", finish, { once: true });
    script.addEventListener("error", fail, { once: true });
    document.body.appendChild(script);
  });

  sdkPromise = sdkPromise.catch((err) => {
    sdkPromise = null;
    sdkInitialized = false;
    throw err;
  });
  return sdkPromise;
}

export function buildMetaLoginOptions({ configId, onboardingMode } = {}) {
  const mode = normalizeMetaOnboardingMode(onboardingMode);
  const extras = {
    version: "v4",
    sessionInfoVersion: "3",
  };
  if (mode === META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING) {
    extras.featureType = META_BUSINESS_APP_FEATURE_TYPE;
  }
  return {
    config_id: String(configId || ""),
    response_type: "code",
    override_default_response_type: true,
    extras,
  };
}

export function buildMetaMarketingLoginOptions() {
  // This is a standard Facebook Login permission request, separate from the
  // WhatsApp Business Login configuration (`config_id`).
  return {
    scope: "ads_read",
    auth_type: "rerequest",
    response_type: "code",
    override_default_response_type: true,
  };
}

export function startMetaMarketingAuthorization(FB, onResponse, onResponseError) {
  if (!FB?.login) {
    throw Object.assign(new Error("FB.login unavailable"), { code: "META_SDK_NOT_LOADED" });
  }
  return FB.login((response) => {
    try {
      const result = onResponse(response);
      if (result && typeof result.then === "function") {
        result.catch((error) => {
          if (typeof onResponseError === "function") onResponseError(error);
        });
      }
    } catch (error) {
      if (typeof onResponseError === "function") onResponseError(error);
    }
    return undefined;
  }, buildMetaMarketingLoginOptions());
}

export function startMetaEmbeddedSignup(FB, config, onboardingMode, onResponse, onResponseError) {
  if (!FB?.login) {
    throw Object.assign(new Error("FB.login unavailable"), { code: "META_SDK_NOT_LOADED" });
  }
  return FB.login(
    function metaEmbeddedSignupCallback(response) {
      try {
        const result = onResponse(response);
        if (result && typeof result.then === "function") {
          result.catch((error) => {
            if (typeof onResponseError === "function") onResponseError(error);
          });
        }
      } catch (error) {
        if (typeof onResponseError === "function") onResponseError(error);
      }
      return undefined;
    },
    buildMetaLoginOptions({ configId: config?.config_id, onboardingMode }),
  );
}

export function parseMetaEmbeddedSignupEvent(rawData, onboardingMode) {
  let data = rawData;
  if (typeof rawData === "string") {
    try {
      data = JSON.parse(rawData);
    } catch {
      return null;
    }
  }
  if (!data || data.type !== "WA_EMBEDDED_SIGNUP") return null;
  const mode = normalizeMetaOnboardingMode(onboardingMode);
  const eventName = String(data.event || "");
  return {
    type: data.type,
    event: eventName,
    onboarding_mode: mode,
    is_finish: eventName === "FINISH" || eventName === "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
    is_error: eventName === "ERROR",
    data: data.data && typeof data.data === "object" ? data.data : {},
  };
}

export function createMetaSignupCompletionAggregator({ waitMs = META_SESSION_EVENT_WAIT_MS, setTimeoutFn, clearTimeoutFn } = {}) {
  const schedule = setTimeoutFn || ((handler, ms) => setTimeout(handler, ms));
  const cancel = clearTimeoutFn || ((timer) => clearTimeout(timer));
  let codeResponse = null;
  let sessionEvent = null;
  let completed = false;
  let timer = null;
  let resolver = null;

  function finish(reason) {
    if (completed) return null;
    if (!codeResponse) return null;
    if (!sessionEvent && reason !== "timeout") return null;
    completed = true;
    if (timer) {
      cancel(timer);
      timer = null;
    }
    const result = {
      response: codeResponse,
      signupPayload: sessionEvent || {},
      reason,
    };
    if (resolver) resolver(result);
    return result;
  }

  function waitForCompletion() {
    if (completed && codeResponse) {
      return Promise.resolve({ response: codeResponse, signupPayload: sessionEvent || {}, reason: sessionEvent ? "combined" : "timeout" });
    }
    return new Promise((resolve) => {
      resolver = resolve;
      const immediate = finish("combined");
      if (immediate) resolve(immediate);
    });
  }

  return {
    addSessionEvent(event) {
      if (completed || !event) return null;
      sessionEvent = event;
      return finish("combined");
    },
    addLoginResponse(response) {
      if (completed) return null;
      codeResponse = response;
      const immediate = finish("combined");
      if (immediate) return immediate;
      timer = schedule(() => finish("timeout"), waitMs);
      return null;
    },
    waitForCompletion,
    isCompleted() {
      return completed;
    },
  };
}

export function classifyMetaBrowserException(error) {
  const message = sanitizeMetaBrowserError(error).message.toLowerCase();
  if (/domain|origin|redirect uri|not allowed|not authorized/.test(message)) return "META_APP_DOMAIN_NOT_ALLOWED";
  if (/init|not initialized/.test(message)) return "META_SDK_NOT_INITIALIZED";
  if (/config_id|response_type|override_default_response_type|sessioninfoversion|extras|invalid option|invalid parameter/.test(message)) return "META_LOGIN_INVALID_OPTIONS";
  if (/popup|blocked/.test(message)) return "META_POPUP_BLOCKED";
  return "META_BROWSER_ERROR";
}

export function classifyMetaLoginResponse(response, elapsedMs) {
  if (response?.authResponse?.code) return null;
  if (!response) return "META_LOGIN_EMPTY_RESPONSE";
  if (response.status === "unknown" && elapsedMs < META_LOGIN_EMPTY_RESPONSE_MS) return "META_POPUP_BLOCKED";
  if (response.status === "not_authorized" || response.status === "unknown") return "META_LOGIN_CANCELLED";
  return "META_LOGIN_EMPTY_RESPONSE";
}

export function getMetaSignupErrorMessage(code) {
  return META_SIGNUP_ERROR_MESSAGES[code] || META_SIGNUP_ERROR_MESSAGES.META_BROWSER_ERROR;
}

export function resetMetaSignupSdkForTests() {
  sdkPromise = null;
  sdkKey = "";
  sdkInitialized = false;
  preparedSessions.clear();
}
