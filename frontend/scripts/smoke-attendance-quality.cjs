const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const tenant = '00000000-0000-0000-0000-000000000002';
const lead = '00000000-0000-0000-0000-000000000101';
const conversation = '00000000-0000-0000-0000-000000000201';
const dashboard = {
  window: { start: '2026-10-01T00:00:00+00:00', end: '2026-10-15T00:00:00+00:00', observed_at: '2026-10-14T12:00:00+00:00', response_window_hours: 24, continuity_window_days: 7, timezone: 'UTC' },
  metrics: {
    question_without_observed_response: { numerator: 2, denominator: 10, value: 0.2, state: 'partial', definition: 'Sinal observado, sem inferir intenção.' },
    price_offer_without_observed_continuity: { numerator: 1, denominator: 5, value: 0.2, state: 'observed', definition: 'Sinal temporal, não prova abandono.' },
    response_coverage: { numerator: 8, denominator: 10, value: 0.8, state: 'observed', definition: 'Respostas persistidas.' },
    median_response_seconds: { value: 120, state: 'observed', denominator: 8, mature_inbound_denominator: 10 },
  },
  objections: { state: 'observed', definition: 'Tags explicitamente aplicadas.', items: [{ tag_id: '00000000-0000-0000-0000-000000000301', label: 'Preço', distinct_leads: 2, distinct_conversations: 2, events: 2 }] },
  composition_traces: { state: 'observed', exact_prompt_version_state: 'unknown', definition: 'A07 registra composição, sem versão exata de prompt.', items: [{ version: 'a07.compose.v1', mode: 'LEGACY', source: 'real', metric_scope: 'final_generation_only', measurement_source: 'provider_observed', runs: 3, completed: 3, completion_rate: { value: 1 }, input_tokens_observed: 320, output_tokens_observed: 80, median_latency_ms: 410, observed_cost_usd: null, cost_state: 'unknown' }] },
  filter_scope: { channel: 'Canal persistido; valores nulos são excluídos e contados como desconhecidos.', stage: 'Etapa atual CRM.', ad: 'Coorte pelo contato.', composition_version: 'Somente traces A07.' },
  coverage: { state: 'partial', distinct_leads_by_crm_id: 10, messages_in_period: 76, messages_loaded: 80, questions_total: 12, price_offer_signals_total: 7, missing_conversation_messages: 1, unknown_channel_messages: 1, questions_pending_window: 2, price_offer_pending_window: 0, messages_truncated: false, limitations: ['Sinal determinístico; não mede satisfação.'] },
  opportunities: [{ key: 'review_question_response', kind: 'hypothesis', title: 'Revisar perguntas sem resposta observada', basis: 'Não prova abandono.' }],
  examples: [{ kind: 'question_signal', label: "Inbound com marcador '?'", count: 12, privacy: 'sem texto' }],
  options: { channels: ['whatsapp'], stages: [{ id: '00000000-0000-0000-0000-000000000401', label: 'Novo', order: 1 }], ads: ['123456'], composition_versions: ['a07.compose.v1'], offer_signals: [{ id: 'price', label: 'Preço / valor' }, { id: 'offer', label: 'Oferta / condição' }] },
};
const evidence = { items: [{ evidence_type: 'question_without_observed_response', message_id: '00000000-0000-0000-0000-000000000501', lead_id: lead, conversation_id: conversation, observed_at: '2026-10-10T10:00:00+00:00', state: 'observed' }], total: 1, truncated: false, state: 'observed', privacy: 'Somente identificadores e timestamps.' };

async function main() {
  const origin = new URL(process.env.QUALITY_ORIGIN || 'http://127.0.0.1:18087').origin;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1327, height: 709 }, serviceWorkers: 'block' });
  const requests = [];
  const errors = [];
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ tenantId }) => {
    localStorage.setItem('user', JSON.stringify({ id: '00000000-0000-0000-0000-000000000003', empresa_id: tenantId, role: 'admin_empresa', nome: 'Operador sintético' }));
    localStorage.setItem('token', 'synthetic-browser-only');
    window.__a08Roots = [];
    const seen = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject: () => 1, onCommitFiberRoot(_id, root) { if (!seen.has(root)) { seen.add(root); window.__a08Roots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { tenantId: tenant });
  await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    assert.equal(request.method(), 'GET', 'A08 browser attempted an API write');
    requests.push({ path: url.pathname, search: url.search });
    if (url.pathname.endsWith('/public/configuracoes-branding')) return route.fulfill({ json: { nome_sistema: 'AgentOS teste sintético' } });
    if (url.pathname.endsWith('/attendance-quality/evidence')) return route.fulfill({ json: evidence });
    if (url.pathname.endsWith('/attendance-quality')) return route.fulfill({ json: dashboard });
    return route.fulfill({ json: {} });
  });
  try {
    await page.goto(`${origin}/painel/qualidade`, { waitUntil: 'networkidle' });
    const main = page.getByTestId('attendance-quality-dashboard');
    await main.waitFor();
    await main.getByText('Qualidade do atendimento').waitFor();
    await main.getByText('Perguntas sem resposta observada').first().waitFor();
    await main.getByLabel('Etapa atual do CRM').selectOption('00000000-0000-0000-0000-000000000401');
    await main.getByRole('button', { name: 'Aplicar filtros' }).click();
    await page.waitForTimeout(100);
    assert.ok(requests.some(item => item.path.endsWith('/attendance-quality') && item.search.includes('stage=')));
    await main.getByRole('button', { name: /Ver evidências/ }).first().click();
    await main.getByRole('region', { name: 'Evidências do atendimento' }).waitFor();
    await main.getByText(lead).waitFor();
    assert.ok(requests.some(item => item.path.endsWith('/attendance-quality/evidence')));
    const text = await main.innerText();
    for (const forbidden of ['Pessoa Sintética Privada', '+5511999999999', 'conteúdo cliente secreto']) assert.ok(!text.includes(forbidden));
    assert.equal(await page.evaluate(() => document.querySelectorAll('#root').length), 1);
    assert.deepEqual(await page.evaluate(() => window.__a08Roots), ["root"]);
    await page.evaluate(() => {
      document.activeElement?.blur();
      document.querySelector('.fm-shell-main')?.scrollTo(0, 0);
      window.scrollTo(0, 0);
    });
    const desktop = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    assert.ok(desktop.scrollWidth <= desktop.width + 1, JSON.stringify(desktop));
    const screenshotDir = process.env.QUALITY_SCREENSHOT_DIR;
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'a08-quality-desktop.png'), fullPage: true });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => {
      document.activeElement?.blur();
      document.querySelector('.fm-shell-main')?.scrollTo(0, 0);
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(100);
    const mobile = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    assert.ok(mobile.scrollWidth <= mobile.width + 1, JSON.stringify(mobile));
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'a08-quality-mobile.png'), fullPage: true });
    await main.getByRole('link', { name: /Abrir Inbox/ }).waitFor();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'PASS', desktop, mobile, apiMethods: [...new Set(requests.map(() => 'GET'))], requestCount: requests.length, evidenceRows: 1, reactRoots: 1, screenshots: screenshotDir ? ['a08-quality-desktop.png', 'a08-quality-mobile.png'] : [] }, null, 2));
  } catch (error) {
    console.error(JSON.stringify({ status: 'FAIL', errors, requests, reason: error.message }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
