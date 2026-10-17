import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  META_BUSINESS_APP_FEATURE_TYPE,
  META_ONBOARDING_MODES,
  buildMetaLoginOptions,
  buildMetaMarketingLoginOptions,
  buildSignupSession,
  classifyMetaLoginResponse,
  classifyMetaBrowserException,
  consumeMetaSignupSession,
  createMetaSignupCompletionAggregator,
  createMetaSignupSession,
  META_SIGNUP_ERROR_MESSAGES,
  getMetaSignupErrorMessage,
  isMetaConfigValid,
  isSignupSessionUsable,
  loadFacebookSdk,
  logMetaSignupDiagnostic,
  parseMetaEmbeddedSignupEvent,
  prepareMetaSignupSession,
  resetMetaSignupSdkForTests,
  sanitizeMetaBrowserError,
  startMetaEmbeddedSignup,
  startMetaMarketingAuthorization,
} from "./metaEmbeddedSignup.js";

const validConfig = {
  configured: true,
  app_id: "app-123",
  config_id: "cfg-123",
  graph_api_version: "v25.0",
  sdk_url: "https://connect.facebook.net/pt_BR/sdk.js",
};

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.dataset = {};
    this.listeners = {};
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  dispatch(name) {
    this.listeners[name]?.();
  }
}

function installDom() {
  const elements = new Map();
  const appended = [];
  globalThis.window = {
    location: { origin: "https://painel.example.test", href: "https://painel.example.test/conexoes" },
    setTimeout,
    clearTimeout,
  };
  globalThis.document = {
    getElementById: (id) => elements.get(id) || null,
    createElement: (tagName) => new FakeElement(tagName),
    body: {
      appendChild: (element) => {
        elements.set(element.id, element);
        appended.push(element);
      },
    },
  };
  return { elements, appended };
}

function cleanupDom() {
  delete globalThis.window;
  delete globalThis.document;
  resetMetaSignupSdkForTests();
}

test("SDK carregado inicializa uma unica vez e usa origem oficial", async () => {
  const { appended } = installDom();
  try {
    let initCount = 0;
    const promise = loadFacebookSdk(validConfig.app_id, validConfig.graph_api_version, validConfig.sdk_url);
    assert.equal(appended.length, 1);
    assert.equal(appended[0].src, validConfig.sdk_url);
    window.FB = {
      init: (options) => {
        initCount += 1;
        assert.equal(options.appId, validConfig.app_id);
      },
      login: () => {},
    };
    window.fbAsyncInit();
    const FB = await promise;
    await loadFacebookSdk(validConfig.app_id, validConfig.graph_api_version, validConfig.sdk_url);
    assert.equal(FB, window.FB);
    assert.equal(initCount, 1);
  } finally {
    cleanupDom();
  }
});

test("SDK ausente rejeita com codigo sanitizado", async () => {
  const { appended } = installDom();
  try {
    const promise = loadFacebookSdk(validConfig.app_id, validConfig.graph_api_version, validConfig.sdk_url);
    appended[0].dispatch("error");
    await assert.rejects(promise, { code: "META_SDK_NOT_LOADED" });
  } finally {
    cleanupDom();
  }
});

test("SDK com erro de init rejeita META_SDK_INIT_FAILED", async () => {
  installDom();
  try {
    const promise = loadFacebookSdk(validConfig.app_id, validConfig.graph_api_version, validConfig.sdk_url);
    window.FB = {};
    window.fbAsyncInit();
    await assert.rejects(promise, { code: "META_SDK_INIT_FAILED" });
  } finally {
    cleanupDom();
  }
});

test("config invalida bloqueia inicio do fluxo", () => {
  assert.equal(isMetaConfigValid(validConfig), true);
  assert.equal(isMetaConfigValid({ ...validConfig, config_id: "" }), false);
  assert.equal(getMetaSignupErrorMessage("META_INVALID_CONFIG"), "Configuração global Meta ausente. Informe App ID e Config ID no ambiente.");
});

test("sessao pre-carregada valida permanece utilizavel ate o buffer de expiracao", () => {
  const session = buildSignupSession({ state: "signed.state", expires_in: 600, config: validConfig }, null, 1000);
  assert.equal(isSignupSessionUsable(session, 2000), true);
  assert.equal(isSignupSessionUsable(session, 571001), false);
});

test("sessao expirada e renovada usa novo state sem reutilizar o consumido", async () => {
  const states = ["state-1", "state-2"];
  const api = {
    post: async () => ({ data: { state: states.shift(), expires_in: 600, config: validConfig } }),
  };
  const first = await createMetaSignupSession(api, "empresa-a", validConfig, 1000);
  first.consumed = true;
  assert.equal(isSignupSessionUsable(first, 2000), false);
  const second = await createMetaSignupSession(api, "empresa-a", validConfig, 2000);
  assert.equal(second.state, "state-2");
  assert.notEqual(first.state, second.state);
});

test("FB.login recebe exatamente os parametros oficiais do Embedded Signup v4", () => {
  const calls = [];
  const FB = {
    login: (callback, options) => {
      calls.push({ callback, options });
      callback({ authResponse: { code: "code-123" } });
    },
  };
  let responseCode = "";
  startMetaEmbeddedSignup(FB, validConfig, META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP, (response) => {
    responseCode = response.authResponse.code;
  });
  assert.equal(calls.length, 1);
  assert.equal(typeof calls[0].callback, "function");
  assert.notEqual(calls[0].callback.constructor.name, "AsyncFunction");
  assert.deepEqual(calls[0].options, {
    config_id: "cfg-123",
    response_type: "code",
    override_default_response_type: true,
    extras: { version: "v4", sessionInfoVersion: "3" },
  });
  assert.deepEqual(Object.keys(calls[0].options).sort(), ["config_id", "extras", "override_default_response_type", "response_type"]);
  assert.equal(typeof calls[0].options.config_id, "string");
  assert.equal(calls[0].options.response_type, "code");
  assert.equal(calls[0].options.override_default_response_type, true);
  assert.deepEqual(Object.keys(calls[0].options.extras).sort(), ["sessionInfoVersion", "version"]);
  assert.equal(calls[0].options.extras.sessionInfoVersion, "3");
  assert.equal(calls[0].options.extras.version, "v4");
  assert.equal("featureType" in calls[0].options.extras, false);
  assert.deepEqual(calls[0].options, buildMetaLoginOptions({ configId: validConfig.config_id }));
  assert.equal(responseCode, "code-123");
});

test("FB.login em business app onboarding recebe featureType oficial sem alterar tipos", () => {
  const calls = [];
  const FB = { login: (callback, options) => calls.push({ callback, options }) };
  startMetaEmbeddedSignup(FB, validConfig, META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING, () => {});
  assert.equal(typeof calls[0].callback, "function");
  assert.notEqual(calls[0].callback.constructor.name, "AsyncFunction");
  assert.deepEqual(calls[0].options, {
    config_id: "cfg-123",
    response_type: "code",
    override_default_response_type: true,
    extras: {
      version: "v4",
      sessionInfoVersion: "3",
      featureType: META_BUSINESS_APP_FEATURE_TYPE,
    },
  });
});

test("fluxo padrao preserva v4 e sessionInfoVersion sem featureType de coexistencia", () => {
  const standard = buildMetaLoginOptions({
    configId: validConfig.config_id,
    onboardingMode: META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP,
  });
  const businessApp = buildMetaLoginOptions({
    configId: validConfig.config_id,
    onboardingMode: META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING,
  });
  assert.equal(standard.extras.version, "v4");
  assert.equal(standard.extras.sessionInfoVersion, "3");
  assert.equal("featureType" in standard.extras, false);
  assert.equal(businessApp.extras.featureType, META_BUSINESS_APP_FEATURE_TYPE);
  assert.notEqual(standard.extras, businessApp.extras);
});

test("Marketing Login solicita escopos explícitos de leitura e gestão sem alterar o Embedded Signup", () => {
  const calls = [];
  let receivedCode = "";
  const FB = { login: (callback, options) => calls.push({ callback, options }) };
  startMetaMarketingAuthorization(FB, (response) => { receivedCode = response.authResponse.code; });
  assert.deepEqual(calls[0].options, {
    scope: "ads_read,ads_management,business_management,pages_show_list,pages_read_engagement,instagram_basic",
    auth_type: "rerequest",
    response_type: "code",
    override_default_response_type: true,
  });
  assert.equal("config_id" in calls[0].options, false);
  calls[0].callback({ authResponse: { code: "marketing-code" } });
  assert.equal(receivedCode, "marketing-code");
  assert.deepEqual(buildMetaMarketingLoginOptions(), calls[0].options);
  assert.equal(buildMetaLoginOptions({ configId: validConfig.config_id }).config_id, "cfg-123");
});

test("callback entregue ao FB.login e normal e aciona processador async com rejeicao capturada", async () => {
  const calls = [];
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args);
  try {
    let resolveProcessed;
    const processed = new Promise((resolve) => {
      resolveProcessed = resolve;
    });
    let displayedError = "";
    const FB = {
      login: (callback, options) => {
        calls.push({ callback, options });
      },
    };

    startMetaEmbeddedSignup(
      FB,
      validConfig,
      META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP,
      async (response) => {
        assert.equal(response.authResponse.code, "code-ok");
        resolveProcessed();
        throw new Error("complete failed state=secret-state code=secret-code token=EAAT-secret-token");
      },
      (error) => {
        logMetaSignupDiagnostic("META_BROWSER_ERROR", "complete_failed", { error });
        displayedError = getMetaSignupErrorMessage("META_BROWSER_ERROR");
      },
    );

    assert.equal(calls.length, 1);
    assert.equal(typeof calls[0].callback, "function");
    assert.notEqual(calls[0].callback.constructor.name, "AsyncFunction");
    assert.deepEqual(calls[0].options, {
      config_id: "cfg-123",
      response_type: "code",
      override_default_response_type: true,
      extras: { version: "v4", sessionInfoVersion: "3" },
    });
    assert.equal(calls[0].callback({ authResponse: { code: "code-ok" } }), undefined);
    await processed;
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(displayedError, META_SIGNUP_ERROR_MESSAGES.META_BROWSER_ERROR);
    const logged = JSON.stringify(warnings);
    assert.match(logged, /complete_failed/);
    assert.doesNotMatch(logged, /secret-state|secret-code|EAAT-secret-token|cfg-123/);
  } finally {
    console.warn = originalWarn;
  }
});

test("callback do FB.login continua sincrono mesmo quando processador e async", () => {
  let sdkCallback;
  startMetaEmbeddedSignup(
    {
      login: (callback) => {
        sdkCallback = callback;
      },
    },
    validConfig,
    META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP,
    async () => ({ ok: true }),
  );

  assert.equal(typeof sdkCallback, "function");
  assert.notEqual(sdkCallback.constructor.name, "AsyncFunction");
  assert.equal(sdkCallback({ authResponse: { code: "code-ok" } }), undefined);
});

test("popup bloqueado, cancelamento e resposta vazia recebem codigos distintos", () => {
  assert.equal(classifyMetaLoginResponse({ status: "unknown" }, 10), "META_POPUP_BLOCKED");
  assert.equal(classifyMetaLoginResponse({ status: "unknown" }, 3000), "META_LOGIN_CANCELLED");
  assert.equal(classifyMetaLoginResponse(undefined, 3000), "META_LOGIN_EMPTY_RESPONSE");
  assert.equal(classifyMetaLoginResponse({ authResponse: { code: "code-123" } }, 3000), null);
});

test("sucesso mockado preserva apenas code no callback controlado", () => {
  let completed = false;
  startMetaEmbeddedSignup({ login: (callback) => callback({ authResponse: { code: "code-ok" } }) }, validConfig, META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP, (response) => {
    completed = response.authResponse.code === "code-ok";
  });
  assert.equal(completed, true);
});

test("clique duplo fica bloqueado quando a sessao foi marcada como consumida", () => {
  const session = buildSignupSession({ state: "state-1", expires_in: 600, config: validConfig }, null, 1000);
  assert.equal(isSignupSessionUsable(session, 2000), true);
  session.consumed = true;
  assert.equal(isSignupSessionUsable(session, 2000), false);
});

test("preparacoes concorrentes e remontagem StrictMode reutilizam uma unica sessao", async () => {
  let calls = 0;
  const api = {
    post: async (url, body) => {
      calls += 1;
      return { data: { state: "state-1", expires_in: 600, config: validConfig, onboarding_mode: body.onboarding_mode } };
    },
  };
  const [first, second] = await Promise.all([
    prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 1000),
    prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 1000),
  ]);
  const remount = await prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 2000);
  assert.equal(calls, 1);
  assert.equal(first.session, second.session);
  assert.equal(remount.session, first.session);
  assert.equal(remount.reused, true);
  resetMetaSignupSdkForTests();
});

test("modos diferentes preparam sessoes separadas e bloqueiam clique duplo por modo", async () => {
  const requests = [];
  const api = {
    post: async (url, body) => {
      requests.push(body.onboarding_mode);
      return { data: { state: `state-${requests.length}`, expires_in: 600, config: validConfig, onboarding_mode: body.onboarding_mode } };
    },
  };
  const appSession = await prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 1000, META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING);
  const standardSession = await prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 1000, META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP);
  assert.deepEqual(requests, [META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING, META_ONBOARDING_MODES.STANDARD_EMBEDDED_SIGNUP]);
  assert.notEqual(appSession.session.state, standardSession.session.state);
  consumeMetaSignupSession("empresa-a", "user-a", appSession.session);
  assert.equal(isSignupSessionUsable(appSession.session, 2000), false);
  assert.equal(isSignupSessionUsable(standardSession.session, 2000), true);
  resetMetaSignupSdkForTests();
});

test("sessao consumida nao e reutilizada", async () => {
  const states = ["state-1", "state-2"];
  const api = { post: async () => ({ data: { state: states.shift(), expires_in: 600, config: validConfig } }) };
  const first = await prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 1000);
  consumeMetaSignupSession("empresa-a", "user-a", first.session);
  const second = await prepareMetaSignupSession(api, "empresa-a", "user-a", validConfig, 2000);
  assert.notEqual(second.session.state, first.session.state);
  resetMetaSignupSdkForTests();
});

test("tenant divergente continua bloqueado no contrato do backend", () => {
  const session = buildSignupSession({ state: "state-tenant-a", expires_in: 600, config: validConfig }, null, 1000);
  assert.equal(isSignupSessionUsable(session, 2000), true);
  assert.equal(session.state, "state-tenant-a");
});

test("parser reconhece evento especifico do onboarding do WhatsApp Business app", () => {
  const parsed = parseMetaEmbeddedSignupEvent(
    JSON.stringify({
      type: "WA_EMBEDDED_SIGNUP",
      event: "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
      data: { waba_id: "waba-1", phone_number_id: "phone-1" },
    }),
    META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING,
  );
  assert.equal(parsed.is_finish, true);
  assert.equal(parsed.is_error, false);
  assert.equal(parsed.onboarding_mode, META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING);
  assert.equal(parsed.data.waba_id, "waba-1");
  assert.equal(parseMetaEmbeddedSignupEvent("not-json", META_ONBOARDING_MODES.BUSINESS_APP_ONBOARDING), null);
});

test("agregador combina code e session event em ordem invertida", async () => {
  const timers = [];
  const aggregator = createMetaSignupCompletionAggregator({
    setTimeoutFn: (handler) => {
      timers.push(handler);
      return handler;
    },
    clearTimeoutFn: () => {},
  });
  const sessionEvent = {
    type: "WA_EMBEDDED_SIGNUP",
    event: "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
    data: { waba_id: "waba-1", phone_number_id: "phone-1" },
  };
  assert.equal(aggregator.addSessionEvent(sessionEvent), null);
  const combined = aggregator.addLoginResponse({ authResponse: { code: "code-ok" } });
  assert.equal(combined.reason, "combined");
  assert.equal(combined.signupPayload.data.waba_id, "waba-1");
  assert.equal(aggregator.isCompleted(), true);
});

test("agregador espera session event apos code e conclui por timeout sem duplicar", async () => {
  const timers = [];
  const aggregator = createMetaSignupCompletionAggregator({
    setTimeoutFn: (handler) => {
      timers.push(handler);
      return handler;
    },
    clearTimeoutFn: () => {},
  });
  const wait = aggregator.waitForCompletion();
  assert.equal(aggregator.addLoginResponse({ authResponse: { code: "code-ok" } }), null);
  timers[0]();
  const completed = await wait;
  assert.equal(completed.reason, "timeout");
  assert.deepEqual(completed.signupPayload, {});
  assert.equal(aggregator.addSessionEvent({ data: { waba_id: "late" } }), null);
});

test("diagnostico nao registra tokens, code ou state completos", () => {
  installDom();
  try {
    const calls = [];
    const originalWarn = console.warn;
    console.warn = (...args) => calls.push(args);
    logMetaSignupDiagnostic("META_BROWSER_ERROR", "test", {
      access_token: "EAAT-secret-token",
      code: "secret-code",
      state: "secret-state",
      http_status: 400,
    });
    console.warn = originalWarn;
    const logged = JSON.stringify(calls);
    assert.match(logged, /META_BROWSER_ERROR/);
    assert.doesNotMatch(logged, /EAAT-secret-token|secret-code|secret-state/);
  } finally {
    cleanupDom();
  }
});

test("throw sincrono preserva somente name, message e code sanitizados", () => {
  const error = Object.assign(new Error("invalid extras: state=secret-state&access_token=EAAT-secret-token"), { code: 100 });
  const sanitized = sanitizeMetaBrowserError(error);
  assert.deepEqual(sanitized, {
    name: "Error",
    message: "invalid extras: state=[redacted]&access_token=[redacted]",
    code: "100",
  });
  assert.equal(classifyMetaBrowserException(error), "META_LOGIN_INVALID_OPTIONS");
  assert.doesNotMatch(JSON.stringify(sanitized), /secret-state|EAAT-secret-token/);
});

test("sanitizacao remove segredos de mensagens JSON", () => {
  const sanitized = sanitizeMetaBrowserError(new Error('{"state":"secret-state","code":"secret-code","access_token":"EAAT-secret-token"}'));
  assert.doesNotMatch(JSON.stringify(sanitized), /secret-state|secret-code|EAAT-secret-token/);
});

test("diagnostico de throw nao registra state, code ou token", () => {
  installDom();
  try {
    const calls = [];
    const originalWarn = console.warn;
    console.warn = (...args) => calls.push(args);
    logMetaSignupDiagnostic("META_LOGIN_INVALID_OPTIONS", "login_throw", {
      error: Object.assign(new Error("state=secret-state token=EAAT-secret-token code=secret-code"), { code: "META_INVALID" }),
      login_options: buildMetaLoginOptions({ configId: validConfig.config_id }),
      sdk_state: "initialized",
    });
    console.warn = originalWarn;
    const logged = JSON.stringify(calls);
    assert.match(logged, /login_option_keys/);
    assert.doesNotMatch(logged, /secret-state|EAAT-secret-token|secret-code|cfg-123/);
  } finally {
    cleanupDom();
  }
});

test("a interface nao expoe flag interna de acoes Meta", async () => {
  const source = await readFile(new URL("../pages/tenant/Conexoes.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /META_LIVE_ACTIONS_ENABLED/);
  assert.doesNotMatch(source, /META_APP_ID|META_EMBEDDED_SIGNUP_CONFIG_ID/);
  assert.match(source, /Conecte sua conta do WhatsApp Business pela Meta/);
  assert.match(source, /Conecte seu WhatsApp Business ao AgentOS/);
  assert.match(source, /Conectar meu WhatsApp Business/);
  assert.match(source, /BUSINESS_APP_ONBOARDING/);
  assert.doesNotMatch(source, /Conectar um novo número à API Oficial/);
  assert.doesNotMatch(source, /Conectar novo número/);
  assert.doesNotMatch(source, /featureType|sessionInfoVersion/);
});

test("interface oferece somente botao explicito de coexistencia ao cliente", async () => {
  const source = await readFile(new URL("../pages/tenant/Conexoes.jsx", import.meta.url), "utf8");
  assert.match(source, /Conectar meu WhatsApp Business/);
  assert.match(source, /aria-label="Conectar meu WhatsApp Business ao AgentOS"/);
  assert.match(source, /onClick=\{\(\) => iniciarMetaSignup\(META_ONBOARDING_MODES\.BUSINESS_APP_ONBOARDING\)\}/);
  assert.doesNotMatch(source, /onClick=\{\(\) => iniciarMetaSignup\(META_ONBOARDING_MODES\.STANDARD_EMBEDDED_SIGNUP\)\}/);
  assert.match(source, /Abrindo conexão segura com a Meta…/);
  assert.match(source, /focus-visible:outline/);
  assert.match(source, /cursor-pointer/);
  assert.doesNotMatch(source, /metaSignupReady/);
  assert.equal((source.match(/Conectar meu WhatsApp Business/g) || []).length, 2);
});

test("acoes Meta pos-conexao continuam bloqueadas quando nao ha conexao persistida", async () => {
  const source = await readFile(new URL("../pages/tenant/Conexoes.jsx", import.meta.url), "utf8");
  assert.match(source, /Listar ativos/);
  assert.match(source, /Validar/);
  assert.match(source, /Sincronizar/);
  assert.match(source, /Desconectar/);
  assert.match(source, /disabled=\{metaLoading \|\| !metaStatus\?\.conexao_id\}/);
});
