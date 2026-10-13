// All API/socket operations are synthetic; phone/UUID shapes are invented.
const assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path"),
  { createHash } = require("node:crypto");
const { chromium } = require(
  process.env.PLAYWRIGHT_MODULE_PATH || "playwright",
);
const origin = new URL(process.env.UX_ORIGIN || "http://127.0.0.1:18891")
    .origin,
  output = process.env.UX_EVIDENCE_DIR || "/tmp/inbox29-send",
  baseline = process.env.INBOX29_SEND_BASELINE === "1";
const TENANT = "a17e0000-1111-4111-8111-000000000001",
  ID = "b28e0000-2222-4222-8222-000000000001",
  CONVO = "c39e0000-3333-4333-8333-000000000001",
  PHONE = "5511999900001";
const contact = {
  id: ID,
  conversation_id: null,
  nome_contato: "Contato sintético de investigação",
  foto_url: null,
  telefone_contato: PHONE,
  ultima_mensagem: "Histórico sintético",
  ultima_mensagem_data: "2026-10-13T04:00:00Z",
  nao_lidas: 0,
  status_atendimento: "aberto",
  bot_pausado: false,
  bot_pausado_ate: null,
  ia_ativa: true,
  etapa_crm: "Qualificação",
  tags: [],
  historico_resumo: "",
  dados_adicionais: {},
};
const initial = Array.from({ length: 36 }, (_, i) => ({
  id: `d40e0000-4444-4444-8444-${String(i + 1).padStart(12, "0")}`,
  texto: `Histórico sintético preservado ${i + 1}`,
  from_me: i % 2 === 0,
  tipo_mensagem: "text",
  origem_mensagem: i % 2 === 0 ? "humano" : "whatsapp",
  media_url: null,
  criado_em: new Date(Date.UTC(2026, 9, 13, 4, i)).toISOString(),
}));
const variants = [
  "canonical",
  "formatted-phone",
  "ninth-digit-phone",
  "partial-null",
  "partial-omitted",
  "uppercase-id",
  "ws-before-http",
  "http-before-ws",
  "empty-http-after-send",
  "history404-after-key",
  "empty-history-after-key",
  "ws-phone-key-empty",
  "ws-phone-key-404",
  "post202",
  "post500",
  "post-offline",
  "post-timeout",
  "duplicate-confirmation",
  "same-cycle-confirmation",
  "partial-lead-after-send",
  "pending-first-history-alias",
  "genuine-empty-history",
  "fresh-ai-without-state-ws",
  "pending-reconnect-history-alias",
];
async function run(browser, variant, viewport) {
  let page;
  const r = {
    variant,
    viewport,
    mode: "ALL_APIS_SOCKETS_SYNTHETIC",
    counts: { list: 0, history: 0, ai: 0, send: 0, sockets: 0 },
    errors: [],
    unsafe: [],
    steps: [],
    metrics: {},
  };
  let sent = false;
  const sockets = [],
    releases = [];
  const ctx = await browser.newContext({
    viewport,
    serviceWorkers: "block",
    recordVideo: baseline ? { dir: output, size: viewport } : undefined,
  });
  await ctx.routeWebSocket("**/*", (s) => {
    if (!/\/api\/empresas\/[^/]+\/ws$/.test(new URL(s.url()).pathname)) {
      s.close();
      return;
    }
    sockets.push(s);
    r.counts.sockets++;
    s.onMessage((raw) => {
      if (JSON.parse(String(raw)).type === "ping")
        s.send(JSON.stringify({ type: "pong" }));
    });
  });
  const record = async (name, detail = {}) => {
    if (!page) return;
    const state = await page.evaluate(() => ({
      rows: document.querySelectorAll("[data-message-id]").length,
      optimistic: document.querySelectorAll('[data-message-id^="temp-"]')
        .length,
      focusedComposer:
        document.activeElement ===
        document.querySelector(".inbox-composer textarea"),
      lastCommit: window.__send29Commits?.at(-1),
    }));
    r.steps.push({ name, ...detail, ...state });
  };
  const canonical = {
    id: "e51e0000-5555-4555-8555-000000000001",
    texto: "Envio sintético controlado",
    from_me: true,
    tipo_mensagem: "text",
    origem_mensagem: "humano",
    media_url: null,
    criado_em: "2026-10-13T08:00:00Z",
  };
  const emit = () => {
    if (variant === "partial-lead-after-send")
      sockets
        .at(-1)
        .send(
          JSON.stringify({
            tipo_evento: "atualizacao_lead",
            lead: { id: ID, telefone: "551199900001" },
          }),
        );
    sockets
      .at(-1)
      .send(
        JSON.stringify({
          type: "inbox.message.created",
          tipo_evento: "nova_mensagem_outbound",
          empresa_id: TENANT,
          lead_id: ID,
          conversation_id: CONVO,
          telefone: [
            "ninth-digit-phone",
            "history404-after-key",
            "empty-history-after-key",
            "ws-phone-key-empty",
            "ws-phone-key-404",
            "pending-reconnect-history-alias",
          ].includes(variant)
            ? "551199900001"
            : variant === "formatted-phone"
              ? "+55 (11) 99990-0001"
              : PHONE,
          direction: "outbound",
          created_at: canonical.criado_em,
          last_message_preview: canonical.texto,
          last_message_at: canonical.criado_em,
          mensagem: canonical,
        }),
      );
    if (variant !== "fresh-ai-without-state-ws")
      sockets
        .at(-1)
        .send(
          JSON.stringify({
            type: "STATUS_IA_CHANGED",
            payload: { lead_id: ID, ia_ativa: false },
          }),
        );
  };
  await ctx.route("**/*", async (route) => {
    const q = route.request(),
      u = new URL(q.url());
    if (u.origin !== origin) {
      r.unsafe.push("external");
      return route.abort();
    }
    if (!u.pathname.startsWith("/api/")) return route.continue();
    let data;
    if (q.method() !== "GET") {
      if (
        q.method() === "POST" &&
        u.pathname.endsWith("/send") &&
        q.postDataJSON()?.texto === canonical.texto
      ) {
        r.counts.send++;
        sent = true;
        await record("post-request-start");
        if (variant === "post500")
          return route.fulfill({
            status: 500,
            json: { detail: "Falha sintética de envio" },
          });
        if (variant === "post-offline" || variant === "post-timeout")
          return route.abort(
            variant === "post-offline" ? "internetdisconnected" : "timedout",
          );
        if (
          variant === "ws-before-http" ||
          variant === "same-cycle-confirmation"
        )
          emit();
        await new Promise((resolve) => setTimeout(resolve, 120));
        const status =
          process.env.POST_STATUS === "202" || variant === "post202"
            ? 202
            : 200;
        await route.fulfill({ status, json: { status: "success" } });
        await record("post-response-success", { status });
        return;
      }
      r.unsafe.push(q.method() + " " + u.pathname);
      return route.abort();
    }
    if (u.pathname.endsWith("/configuracoes-branding"))
      data = { nome_sistema: "Ferrioli sintético" };
    else if (u.pathname.endsWith("/inbox")) {
      r.counts.list++;
      let c = { ...contact };
      if (sent) {
        c = {
          ...c,
          conversation_id: CONVO,
          ia_ativa: false,
          bot_pausado: true,
        };
        if (variant === "formatted-phone")
          c.telefone_contato = "+55 (11) 99990-0001";
        if (
          [
            "ninth-digit-phone",
            "history404-after-key",
            "empty-history-after-key",
          ].includes(variant)
        )
          c.telefone_contato = "551199900001";
        if (variant === "partial-null") {
          c.telefone_contato = null;
          c.nome_contato = null;
        }
        if (variant === "partial-omitted") {
          delete c.telefone_contato;
          delete c.nome_contato;
        }
        if (variant === "uppercase-id") c.id = ID.toUpperCase();
        await new Promise((resolve) => setTimeout(resolve, 100));
        await record("lead-snapshot-before-response");
      }
      data = [c];
    } else if (u.pathname.endsWith("/foto")) data = { foto_url: null };
    else if (u.pathname.endsWith("/ai-state")) {
      r.counts.ai++;
      const observedSent = sent;
      if (variant === "fresh-ai-without-state-ws" && r.counts.ai === 1)
        await new Promise((resolve) => releases.push(resolve));
      data = {
        manual_enabled: !observedSent,
        processing_state: observedSent ? "manual_pause" : "allowed",
      };
    } else if (/\/(destinos|grupos|oficiais)$/.test(u.pathname)) data = [];
    else if (u.pathname.includes("/inbox/")) {
      r.counts.history++;
      if (
        variant === "pending-reconnect-history-alias" &&
        r.counts.history === 2
      )
        await new Promise((resolve) => releases.push(resolve));
      if (
        variant === "pending-first-history-alias" &&
        u.pathname.endsWith("/" + PHONE) &&
        r.counts.history === 1
      )
        await new Promise((resolve) => releases.push(resolve));
      if (
        sent &&
        ["history404-after-key", "ws-phone-key-404"].includes(variant) &&
        u.pathname.endsWith("/551199900001")
      )
        return route.fulfill({
          status: 404,
          json: { detail: "synthetic-no-alias" },
        });
      data =
        variant === "genuine-empty-history"
          ? []
          : sent &&
              (variant === "empty-http-after-send" ||
                (["empty-history-after-key", "ws-phone-key-empty"].includes(
                  variant,
                ) &&
                  u.pathname.endsWith("/551199900001")))
            ? []
            : sent
              ? [
                  ...initial,
                  ...(variant === "pending-reconnect-history-alias" &&
                  u.pathname.endsWith("/551199900001")
                    ? [
                        {
                          ...initial[0],
                          id: "f73e0000-7777-4777-8777-000000000001",
                          texto: "Gap sintético recuperado",
                        },
                      ]
                    : []),
                  canonical,
                ]
              : initial;
    } else {
      r.unsafe.push("unknown " + u.pathname);
      return route.fulfill({ status: 404, json: {} });
    }
    try {
      return await route.fulfill({ json: data });
    } catch {
      return;
    }
  });
  await ctx.addInitScript(
    ({ tenant }) => {
      localStorage.setItem("token", "synthetic-send-incident-only");
      localStorage.setItem(
        "user",
        JSON.stringify({
          id: "synthetic-user",
          nome: "Operador sintético",
          role: "admin_empresa",
          empresa_id: tenant,
        }),
      );
      window.__send29Commits = [];
      window.__send29Roots = new WeakSet();
      window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
        supportsFiber: true,
        inject() {
          return 1;
        },
        onCommitFiberRoot(_id, root) {
          function walk(f) {
            if (!f) return;
            let h = f.memoizedState,
              a = [];
            while (h && a.length < 200) {
              a.push(h.memoizedState);
              h = h.next;
            }
            const chosen = a.find((v) => v && v.id && v.telefone_contato);
            let messageIndex = a.findIndex(
              (v) => Array.isArray(v) && v.some((m) => m?.tipo_mensagem),
            );
            if (messageIndex >= 0) window.__send29MessageIndex = messageIndex;
            else messageIndex = window.__send29MessageIndex;
            const lists = messageIndex >= 0 ? [a[messageIndex]] : [];
            if (chosen && lists.length) {
              const active = a.find(
                (v, i) =>
                  v?.current &&
                  typeof v.current === "string" &&
                  v.current.startsWith("[") &&
                  a[i + 1]?.current instanceof Set,
              );
              const caches = a
                .filter((v) => v?.current instanceof Map)
                .map((v) => ({
                  size: v.current.size,
                  entries: [...v.current]
                    .filter(
                      ([k, val]) =>
                        typeof k === "string" &&
                        k.startsWith("[") &&
                        Array.isArray(val),
                    )
                    .map(([k, val]) => ({ key: k, length: val.length })),
                }));
              window.__send29Commits.push({
                time: performance.now(),
                selectedId: chosen.id,
                selectedPhone: chosen.telefone_contato,
                key: active?.current,
                length: lists[0].length,
                oldIdsMissing:
                  window.__send29ExpectedIds?.some(
                    (id) => !lists[0].some((m) => m.id === id),
                  ) ?? false,
                caches,
              });
            }
            walk(f.child);
            walk(f.sibling);
          }
          walk(root.current);
        },
        onCommitFiberUnmount() {},
        onPostCommitFiberRoot() {},
      };
    },
    { tenant: TENANT },
  );
  page = await ctx.newPage();
  page.setDefaultTimeout(5000);
  const sendFailure = ["post500", "post-offline", "post-timeout"].includes(
    variant,
  );
  page.on("pageerror", (e) => r.errors.push(e.message));
  try {
    await page.goto(origin + "/painel/chat", { waitUntil: "networkidle" });
    await page.getByText(contact.nome_contato, { exact: true }).click();
    if (variant === "genuine-empty-history") {
      await page
        .getByText("Nenhuma mensagem neste histórico", { exact: true })
        .waitFor();
      assert.equal(await page.locator("[data-message-id]").count(), 0);
      await record("genuine-empty-get-remains-empty");
      r.status = "PASS";
      return r;
    }
    if (variant === "pending-first-history-alias") {
      await page.waitForTimeout(100);
      sockets
        .at(-1)
        .send(
          JSON.stringify({
            type: "inbox.message.created",
            lead_id: ID,
            telefone: "551199900001",
            conversation_id: CONVO,
            direction: "inbound",
            mensagem: {
              ...canonical,
              id: "f62e0000-6666-4666-8666-000000000001",
              texto: "Evento sintético durante primeiro GET",
              from_me: false,
            },
          }),
        );
      await page.locator(`[data-message-id="${initial.at(-1).id}"]`).waitFor();
      releases.splice(0).forEach((resolve) => resolve());
      await page.waitForTimeout(120);
      assert.ok(
        r.counts.history >= 2,
        "address change while first GET pending did not load rerouted history",
      );
      await record("pending-first-history-aborted-rerouted-alias");
    }
    await page.locator(`[data-message-id="${initial.at(-1).id}"]`).waitFor();
    await page.waitForTimeout(200);
    await record("initial-history-loaded");
    await page.evaluate(
      ({ expectedIds }) => {
        window.__send29ExpectedIds = expectedIds;
        window.__send29 = {
          armed: true,
          frames: 0,
          mutations: 0,
          clears: 0,
          headerBlank: 0,
          nodeChanges: 0,
          oldMissingFrames: 0,
          composer: document.querySelector(".inbox-composer textarea"),
          history: document.querySelector(
            '[data-testid="inbox-message-scroll"]',
          ),
          last: document.querySelectorAll("[data-message-id]").length,
        };
        const sample = (kind) => {
          const s = window.__send29,
            n = document.querySelectorAll("[data-message-id]").length;
          if (!s.armed) return;
          const currentIds = new Set(
            [...document.querySelectorAll("[data-message-id]")].map(
              (n) => n.dataset.messageId,
            ),
          );
          if (
            kind === "frame" &&
            window.__send29ExpectedIds.some((id) => !currentIds.has(id))
          )
            s.oldMissingFrames++;
          if (
            !document
              .querySelector(".inbox-contact-identity h3")
              ?.textContent?.trim()
          )
            s.headerBlank++;
          if (
            s.composer !== document.querySelector(".inbox-composer textarea") ||
            s.history !==
              document.querySelector('[data-testid="inbox-message-scroll"]')
          )
            s.nodeChanges++;
          if (!n) {
            if (kind === "frame") s.frames++;
            else s.mutations++;
          }
          if (s.last > 0 && !n) s.clears++;
          s.last = n;
        };
        new MutationObserver(() => sample("mutation")).observe(
          document.querySelector('[data-testid="inbox-message-scroll"]'),
          { subtree: true, childList: true },
        );
        function raf() {
          sample("frame");
          if (window.__send29.armed) requestAnimationFrame(raf);
        }
        requestAnimationFrame(raf);
      },
      { expectedIds: initial.map((m) => m.id) },
    );
    await page.screenshot({
      path: path.join(output, `${variant}-${viewport.width}-before.png`),
    });
    if (variant === "pending-reconnect-history-alias") {
      sockets.at(-1).close({ code: 1012, reason: "synthetic-gap-reconnect" });
      for (let i = 0; i < 40 && r.counts.history < 2; i++)
        await page.waitForTimeout(100);
      assert.equal(r.counts.history, 2);
      await record("reconnect-history-held");
    }
    await page.getByPlaceholder("Digite uma mensagem").fill(canonical.texto);
    await page.getByPlaceholder("Digite uma mensagem").press("Enter");
    await record("optimistic-after-enter");
    await page.waitForTimeout(450);
    await page
      .getByPlaceholder("Digite uma mensagem")
      .fill("Rascunho seguinte sintético");
    await record("post-and-lead-update-settled");
    if (
      !sendFailure &&
      variant !== "ws-before-http" &&
      variant !== "same-cycle-confirmation"
    ) {
      await record("before-ws-confirmation");
      emit();
    }
    if (variant === "duplicate-confirmation") {
      emit();
      emit();
    }
    await page.waitForTimeout(850);
    releases.splice(0).forEach((resolve) => resolve());
    await page.waitForTimeout(150);
    await record("confirmed-or-failed-final");
    const dom = await page.evaluate(() => ({
      frames: window.__send29.frames,
      mutations: window.__send29.mutations,
      clears: window.__send29.clears,
      headerBlank: window.__send29.headerBlank,
      nodeChanges: window.__send29.nodeChanges,
      oldMissingFrames: window.__send29.oldMissingFrames,
      rows: document.querySelectorAll("[data-message-id]").length,
      commits: window.__send29Commits,
    }));
    r.debugSyntheticCommits = dom.commits;
    r.metrics = {
      POST_SEND_HISTORY_CLEAR: dom.clears,
      HISTORY_BLANK_FRAMES: dom.frames,
      HEADER_FLICKER: dom.headerBlank,
      COMPOSER_HISTORY_REMOUNTS: dom.nodeChanges,
      OLD_HISTORY_MISSING_FRAMES: dom.oldMissingFrames,
      OLD_HISTORY_MISSING_COMMITS: dom.commits.filter((c) => c.oldIdsMissing)
        .length,
      CONVERSATION_KEY_UNEXPECTED_CHANGE:
        new Set(
          dom.commits
            .filter((c) => c.length > 0 || sent)
            .map((c) => c.key)
            .filter(Boolean),
        ).size - 1,
      DOUBLE_SEND: Math.max(0, r.counts.send - 1),
      RELOAD_REQUIRED: dom.rows === 0 ? "YES" : "NO",
      rows: dom.rows,
    };
    await page.screenshot({
      path: path.join(output, `${variant}-${viewport.width}-after.png`),
    });
    assert.deepEqual(r.unsafe, []);
    assert.deepEqual(r.errors, []);
    if (baseline) {
      r.status = dom.rows === 0 || dom.clears ? "REPRODUCED" : "NOT_REPRODUCED";
      if (dom.rows === 0) {
        await page.reload({ waitUntil: "networkidle" });
        await page.getByText(contact.nome_contato, { exact: true }).click();
        await page
          .locator(`[data-message-id="${initial.at(-1).id}"]`)
          .waitFor();
        r.metrics.REFRESH_RESTORES_HISTORY = await page
          .locator("[data-message-id]")
          .count();
        await page.screenshot({
          path: path.join(
            output,
            `${variant}-${viewport.width}-refresh-restored.png`,
          ),
        });
      }
    } else {
      assert.equal(dom.frames, 0, "blank frame after send");
      assert.equal(
        dom.oldMissingFrames,
        0,
        "old history vanished in rendered frame",
      );
      assert.equal(
        r.metrics.OLD_HISTORY_MISSING_COMMITS,
        0,
        "old history vanished in React commit",
      );
      assert.equal(
        await page.getByPlaceholder("Digite uma mensagem").inputValue(),
        "Rascunho seguinte sintético",
      );
      assert.equal(
        await page
          .getByPlaceholder("Digite uma mensagem")
          .evaluate((n) => n === document.activeElement),
        true,
        "composer focus lost during confirmation",
      );
      assert.equal(dom.headerBlank, 0);
      assert.equal(dom.nodeChanges, 0);
      assert.equal(
        r.counts.sockets,
        variant === "pending-reconnect-history-alias" ? 2 : 1,
        "unexpected socket reconnect after send",
      );
      if (variant === "fresh-ai-without-state-ws") {
        assert.ok(
          r.counts.ai >= 2,
          "successful send did not request fresh AI state",
        );
        assert.match(
          await page.locator(".chat-header-ai").innerText(),
          /desligada|pausada|desativada/i,
        );
      }
      if (variant === "pending-reconnect-history-alias") {
        assert.ok(
          r.counts.history >= 3,
          "cancelled in-flight history did not reroute",
        );
        assert.equal(
          await page
            .locator('[data-message-id="f73e0000-7777-4777-8777-000000000001"]')
            .count(),
          1,
          "missed HTTP gap was not recovered",
        );
      }
      assert.equal(dom.clears, 0, "history cleared after send");
      assert.equal(r.counts.send, 1);
      for (const m of initial)
        assert.equal(
          await page.locator(`[data-message-id="${m.id}"]`).count(),
          1,
          "old history lost",
        );
      if (sendFailure) {
        assert.equal(
          await page.locator('[data-message-id^="temp-"]').count(),
          1,
        );
        assert.match(
          await page.locator('[data-message-id^="temp-"]').innerText(),
          /Falha|erro|reenviar/i,
        );
      } else {
        assert.equal(
          await page.locator(`[data-message-id="${canonical.id}"]`).count(),
          1,
        );
        assert.equal(
          await page.locator('[data-message-id^="temp-"]').count(),
          0,
        );
      }
      assert.equal(
        r.metrics.CONVERSATION_KEY_UNEXPECTED_CHANGE,
        0,
        "same contact changed active key",
      );
      r.status = "PASS";
    }
  } catch (e) {
    r.status = "FAIL";
    r.failure = e.message;
  } finally {
    try {
      await page.evaluate(() => {
        if (window.__send29) window.__send29.armed = false;
      });
    } catch {}
    releases.splice(0).forEach((resolve) => resolve());
    await ctx.close();
    fs.writeFileSync(
      path.join(output, `${variant}-${viewport.width}.json`),
      JSON.stringify(r, null, 2),
    );
  }
  return r;
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const b = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const results = [];
  try {
    for (const viewport of baseline
      ? [{ width: 1327, height: 709 }]
      : [
          { width: 1327, height: 709 },
          { width: 1440, height: 900 },
          { width: 1280, height: 800 },
          { width: 768, height: 1024 },
          { width: 390, height: 844 },
        ].filter(
          (v) =>
            !process.env.SEND_WIDTHS ||
            process.env.SEND_WIDTHS.split(",").includes(String(v.width)),
        ))
      for (const v of (process.env.SEND_VARIANTS || variants.join(",")).split(
        ",",
      )) {
        const r = await run(b, v, viewport);
        results.push(r);
        console.log(
          JSON.stringify({
            variant: v,
            width: viewport.width,
            status: r.status,
            metrics: r.metrics,
            failure: r.failure,
          }),
        );
      }
  } finally {
    await b.close();
  }
  const status = baseline
    ? results.some((r) => r.status === "REPRODUCED")
      ? "REPRODUCED"
      : "NOT_REPRODUCED"
    : results.every((r) => r.status === "PASS")
      ? "PASS"
      : "FAIL";
  fs.writeFileSync(
    path.join(output, "summary.json"),
    JSON.stringify(
      {
        status,
        baseline,
        origin,
        sourceSha: process.env.UX_SOURCE_SHA,
        scriptSha256: createHash("sha256")
          .update(fs.readFileSync(__filename))
          .digest("hex"),
        results,
      },
      null,
      2,
    ),
  );
  if (status === "FAIL") process.exitCode = 1;
})();
