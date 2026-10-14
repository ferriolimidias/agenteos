const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const tenant = '00000000-0000-0000-0000-000000000002';
const lead = '00000000-0000-0000-0000-000000000101';
const conversation = '00000000-0000-0000-0000-000000000201';
const execution = '00000000-0000-0000-0000-000000000301';
const metric = (numerator, denominator, state = 'observed', coverage = {}) => ({
  numerator, denominator, value: denominator ? numerator / denominator : null, state, coverage,
  definition: 'Evidência persistida sintética; associação temporal não comprova causalidade.',
});
const dashboard = {
  window: { start: '2026-10-01T00:00:00+00:00', end: '2026-10-15T00:00:00+00:00', observed_at: '2026-10-14T12:00:00+00:00', response_window_days: 7, reactivation_days: 7 },
  summary: {
    scheduled_executions: metric(10, 10), currently_scheduled: metric(2, 10), eligible_contacts: metric(7, 8, 'partial'),
    executions_started: metric(8, 10), messages_sent: metric(6, 8, 'partial'), deliveries_confirmed: metric(4, 6, 'partial'),
    responses_observed: metric(2, 4, 'partial', { mature_sent: 4, pending_window: 2 }),
    reactivations_observed: metric(1, 3, 'partial'), stage_advances_observed: metric(1, 2, 'partial'),
    confirmed_conversions: metric(1, 6, 'partial'), cancelled_or_stopped: metric(1, 10), failed: metric(1, 10),
  },
  by_mode: {
    conversation_resume: { scheduled: 6, executed: 5, sent: 4, delivered: 3, responded: 2, response_rate: metric(2, 3, 'partial') },
    scheduled_message: { scheduled: 4, executed: 3, sent: 2, delivered: 1, responded: 0, response_rate: metric(0, 2, 'partial') },
  },
  stop_reasons: [{ reason: 'human_takeover', count: 1 }],
  coverage: {
    state: 'partial', execution_rows_read: 10, execution_limit: 20000, execution_rows_truncated: false,
    stage_events_truncated: false, eligibility_decision_coverage: { recorded: 8, unknown: 2, denominator: 10 },
    legacy_logs: { reason: 'Logs legados preservados separadamente.' },
    limitations: ['A elegibilidade cobre somente decisões persistidas.', 'Resposta posterior é uma associação temporal na mesma conversa.'],
  },
  legacy_send_logs: { send_log_rows: 2, distinct_contacts: 2 },
  options: { channels: ['whatsapp', 'instagram'], statuses: ['scheduled', 'sent', 'delivered', 'cancelled'], stop_reasons: ['human_takeover', 'opt_out'] },
};
const evidence = {
  items: [{ execution_id: execution, lead_id: lead, conversation_id: conversation, followup_id: '00000000-0000-0000-0000-000000000401', mode: 'conversation_resume', channel: 'whatsapp', status: 'sent', stop_reason: null, scheduled_at: '2026-10-05T10:00:00', created_at: '2026-10-01T10:00:00', processing_started_at: '2026-10-05T10:00:00', sent_at: '2026-10-05T10:00:00', response_at: '2026-10-06T10:00:00', delivered: true, eligibility: true }],
  total: 1, limit: 25, offset: 0, kind: 'all', coverage: { state: 'observed' },
};

async function main() {
  const origin = new URL(process.env.A09_ORIGIN || 'http://127.0.0.1:18088').origin;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  const context = await browser.newContext({ viewport: { width: 1327, height: 709 }, serviceWorkers: 'block' });
  const requests = [];
  const errors = [];
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ tenantId }) => {
    localStorage.setItem('user', JSON.stringify({ id: '00000000-0000-0000-0000-000000000003', empresa_id: tenantId, role: 'admin_empresa', nome: 'Operador sintético' }));
    localStorage.setItem('token', 'synthetic-a09-only');
    window.__a09Roots = [];
    const seen = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject: () => 1, onCommitFiberRoot(_id, root) { if (!seen.has(root)) { seen.add(root); window.__a09Roots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { tenantId: tenant });
  await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    assert.equal(request.method(), 'GET', 'A09 browser attempted an API write');
    requests.push({ path: url.pathname, search: url.search });
    if (url.pathname.endsWith('/public/configuracoes-branding')) return route.fulfill({ json: { nome_sistema: 'AgentOS teste sintético' } });
    if (url.pathname.endsWith('/followup-analytics/evidence')) return route.fulfill({ json: evidence });
    if (url.pathname.endsWith('/followup-analytics')) return route.fulfill({ json: dashboard });
    return route.fulfill({ json: {} });
  });
  try {
    await page.goto(`${origin}/painel/followups/impacto`, { waitUntil: 'networkidle' });
    const main = page.getByTestId('followup-analytics-dashboard');
    await main.waitFor();
    await main.getByText('Desempenho dos follow-ups').waitFor();
    await main.getByText('Entregas confirmadas').waitFor();
    await main.getByLabel('Tipo').selectOption('scheduled_message');
    await main.getByRole('button', { name: 'Aplicar filtros' }).click();
    await page.waitForTimeout(100);
    assert.ok(requests.some(item => item.path.endsWith('/followup-analytics') && item.search.includes('mode=scheduled_message')));
    await main.getByTestId('followup-metric-scheduled_executions').getByRole('button', { name: 'Ver registros' }).click();
    await main.getByRole('region', { name: 'Registros de follow-up' }).waitFor();
    await main.getByText('Follow-up 00000000').waitFor();
    assert.ok(requests.some(item => item.path.endsWith('/followup-analytics/evidence') && item.search.includes('kind=all')));
    const content = await main.innerText();
    for (const forbidden of ['Pessoa Sintética Privada', '+5511999999999', 'conteúdo cliente secreto']) assert.ok(!content.includes(forbidden));
    assert.equal(await page.evaluate(() => document.querySelectorAll('#root').length), 1);
    assert.deepEqual(await page.evaluate(() => window.__a09Roots), ['root']);

    const results = [];
    for (const viewport of [{ name: 'desktop', width: 1327, height: 709 }, { name: 'notebook', width: 1280, height: 800 }, { name: 'mobile', width: 390, height: 844 }]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.evaluate(() => {
        window.scrollTo(0, 0);
        for (const element of document.querySelectorAll('*')) if (element.scrollTop) element.scrollTop = 0;
      });
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `${viewport.name} horizontal overflow: ${JSON.stringify(dimensions)}`);
      results.push({ viewport: viewport.name, ...dimensions });
      const output = process.env.A09_SCREENSHOT_DIR;
      if (output) {
        fs.mkdirSync(output, { recursive: true });
        await page.screenshot({ path: path.join(output, `a09-followups-${viewport.name}.png`), fullPage: true });
      }
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'PASS', viewports: results, apiMethods: [...new Set(requests.map(() => 'GET'))], requestCount: requests.length, evidenceRows: 1, reactRoots: 1, screenshots: process.env.A09_SCREENSHOT_DIR ? ['a09-followups-desktop.png', 'a09-followups-notebook.png', 'a09-followups-mobile.png'] : [] }, null, 2));
  } catch (error) {
    console.error(JSON.stringify({ status: 'FAIL', errors, requests, reason: error.message }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
