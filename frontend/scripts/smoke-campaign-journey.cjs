const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const baseUrl = process.env.A10_FRONTEND_BASE_URL || "http://127.0.0.1:5174";
const tenantId = "00000000-0000-4000-8000-000000000010";
const campaignId = "00000000-0000-4000-8000-000000000011";
const leadId = "00000000-0000-4000-8000-000000000012";
const recipientId = "00000000-0000-4000-8000-000000000013";
const calls = [];

const metric = (numerator, denominator, state = "observed") => ({
  numerator,
  denominator,
  value: denominator ? numerator / denominator : null,
  state,
});

const dashboard = {
  campaigns: [{
    campaign_id: campaignId,
    name: "Campanha sintética A10",
    coverage: { state: "observed", capped: false },
    eligible_snapshot: { value: 3, state: "observed" },
    attempted: { value: 3, denominator: 3, state: "observed" },
    delivery: { audience_total: 3, meta_accepted: 3, delivered: 2, read: 1, failed: 0, pending: 0, replies: 1 },
    delivery_rates: { accepted: metric(3, 3), delivered: metric(2, 3), read: metric(1, 2) },
    response_after_send: metric(1, 2, "partial"),
    reactivation_after_send: metric(1, 1),
    stage_advance_after_send: metric(0, 1),
    verified_purchase_after_send: metric(0, 1),
    opt_out: { snapshot_exclusions: 1, current_recipients: 0 },
    campaign_assistance: { sent_touches: 3, reply_touches: 1 },
    failed_cancelled: { failed: 0, cancelled: 0, stopped: 0, blocked: 0 },
    funnel: [
      { key: "sent", label: "Aceitos pelo provider", value: 3, denominator: 3, state: "observed" },
      { key: "delivered", label: "Entregues", value: 2, denominator: 3, state: "partial" },
      { key: "reply", label: "Respostas posteriores", value: 1, denominator: 2, state: "partial" },
    ],
  }],
  segments: [{
    campaign_id: campaignId,
    origin: "ctwa_referral",
    recipient_denominator: 3,
    state: "observed",
    delivery: { meta_accepted: 3, delivered: 2, read: 1 },
    delivery_rates: { accepted: metric(3, 3), delivered: metric(2, 3), read: metric(1, 2) },
    reply_after_send: metric(1, 2, "partial"),
    reactivation_after_send: metric(1, 1),
    stage_advance_after_send: metric(0, 1),
    verified_purchase_after_send: metric(0, 1),
  }],
  coverage: { state: "observed", sample_limit_per_campaign: 10000 },
};

const recipients = {
  campaign_id: campaignId,
  items: [{
    recipient_id: recipientId,
    lead_id: leadId,
    conversation_id: "00000000-0000-4000-8000-000000000014",
    status: "delivered",
    sent_at: "2026-10-01T12:00:00Z",
    origin_segment: "ctwa_referral",
    replied: true,
    reactivated: false,
    stage_advanced: false,
    verified_purchase: false,
    campaign_sent_touch: true,
    campaign_reply_touch: true,
  }],
  pagination: { limit: 25, offset: 0, returned: 1, has_more: false },
};

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1365, height: 900, name: "desktop" }, { width: 390, height: 844, name: "mobile" }]) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      await context.addInitScript(({ tenant, campaign }) => {
        localStorage.setItem("token", "synthetic-only");
        localStorage.setItem("user", JSON.stringify({ empresa_id: tenant, role: "tenant_user", nome: "Operador sintético" }));
        localStorage.setItem("campaignJourneyCampaignId", campaign);
      }, { tenant: tenantId, campaign: campaignId });
      const page = await context.newPage();
      const pageErrors = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        calls.push({ method: request.method(), path });
        let body = {};
        if (path.endsWith("/campaign-central/journey/recipients")) body = recipients;
        else if (path.endsWith("/campaign-central/journey")) body = dashboard;
        else if (path.endsWith("/campaign-central/campaigns")) body = [{ id: campaignId, name: "Campanha sintética A10" }];
        else if (path.endsWith("/campaign-central/overview") || path.endsWith("/campaign-central/results")) body = {};
        else body = [];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
      });
      await page.goto(`${baseUrl}/painel/campanhas/resultados/jornada`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: /Jornada · Campanha sintética A10/ }).waitFor();
      await page.getByText("Respostas posteriores").first().waitFor();
      await page.getByRole("button", { name: "Nova campanha" }).waitFor({ state: "detached" });
      await page.getByRole("button", { name: "Criar template" }).waitFor({ state: "detached" });
      await page.getByRole("button", { name: "Ver destinatários" }).click();
      await page.getByRole("link", { name: "Abrir evidência" }).waitFor();
      assert.equal(await page.getByText("compra verificada após envio", { exact: false }).count(), 0);
      const bodyWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(bodyWidth <= viewport.width + 2, `${viewport.name} page has horizontal overflow: ${bodyWidth}px`);
      assert.deepEqual(pageErrors, [], `${viewport.name} rendered page errors`);
      await page.screenshot({ path: `/tmp/a10-campaign-journey-${viewport.name}.png`, fullPage: true });
      await context.close();
    }
    assert.ok(calls.some((call) => call.path.endsWith("/campaign-central/journey") && call.method === "GET"));
    assert.ok(calls.some((call) => call.path.endsWith("/campaign-central/journey/recipients") && call.method === "GET"));
    assert.ok(calls.every((call) => call.method === "GET"), "browser smoke must remain read-only");
    process.stdout.write(`A10 browser smoke passed: desktop + mobile, ${calls.length} intercepted GET requests, synthetic API only.\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
