const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const SYNTHETIC_TENANT = '00000000-0000-0000-0000-000000000002';
const EMPTY_TENANT = '00000000-0000-0000-0000-000000000004';
const TRANSPORT_PATHS = {
  business_messaging: 'ads_integration_service.enviar_payload_meta_business_messaging',
  capi: 'ads_integration_service.enviar_payload_meta_capi',
};
const attemptFixture = index => ({
  id: `00000000-0000-0000-0001-${String(index + 1).padStart(12, '0')}`,
  event_name: 'Purchase', outcome: index === 0 ? 'DELIVERY_UNKNOWN' : 'PROVIDER_ACCEPTED',
  delivery_status: index === 0 ? 'unknown' : 'accepted', transport: 'capi', transport_path: TRANSPORT_PATHS.capi,
  started_at: '2026-10-10T12:00:00Z', finished_at: '2026-10-10T12:00:01Z', http_status: index === 0 ? null : 200,
  identity: { external_event_id_hash: 'a'.repeat(64), dataset_id_hash: 'b'.repeat(64) },
  identity_classification: 'UNLINKED', related: { commercial_event_id: null, persisted_link: null, outbox: [], attribution: null },
  provider_response: index === 0 ? {} : { events_received: 1 }, shadow_diagnostics: { same_identity_attempts: 1, retry_authorized: false },
});
function operationsFixture(url, empty) {
  const all = empty ? [] : Array.from({ length: 21 }, (_, index) => attemptFixture(index));
  const summary = {
    observation_status: empty ? 'NO_EVENT_OBSERVED' : 'EVENT_OBSERVED', delivery_validation: 'NÃO_VALIDADO',
    total_attempts: all.length, last_attempt_at: empty ? null : all[0].started_at,
    outcome_counts: { accepted: empty ? 0 : 20, unknown: empty ? 0 : 1 },
    canonical_live_enabled: false, ownership_enforced: false, ownership_mode: 'SHADOW_VALIDATION_ONLY',
    configuration: { status: 'CONFIGURED_NOT_VALIDATED' },
    coverage: { paths_discovered: 2, paths_instrumented: 2, paths: ['capi', 'business_messaging'].map(transport => ({
      transport, transport_path: TRANSPORT_PATHS[transport], total_attempts: empty ? 0 : (transport === 'capi' ? 21 : 0),
      last_attempt_at: empty || transport !== 'capi' ? null : all[0].started_at,
      observation_status: empty || transport !== 'capi' ? 'NO_EVENT_OBSERVED' : 'EVENT_OBSERVED',
    })) },
    historical: { protected_commercial_events: 126, historical_outbox_blocked: 126 },
    persisted_links: { LINKED_EXACT: 0, LINKED_PROBABLE: 88, AMBIGUOUS: 0, UNLINKED: 686 },
    outbox: { total: 126 }, attribution: { touches: 0, resolutions: 0 }, shadow_diagnostics: { reservations: 0 },
    correspondence: { legacy_events: 774, legacy_without_persisted_link: 686, commercial_events: 126, projections: 126 },
  };
  const filtered = all.filter(item => (!url.searchParams.get('outcome') || item.delivery_status === url.searchParams.get('outcome'))
    && (!url.searchParams.get('event_name') || item.event_name === url.searchParams.get('event_name')));
  const limit = Number(url.searchParams.get('limit') || 20), offset = Number(url.searchParams.get('offset') || 0);
  return { checked_at: '2026-10-10T13:00:00Z', summary, items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset };
}

// Synthetic browser-only session: every API call is fulfilled locally. No login,
// token creation, backend mutation, or external Meta/WhatsApp request is performed.
async function main() {
  const origin = new URL(process.env.PANEL_ORIGIN || 'http://127.0.0.1:18086').origin;
  const synthetic = process.env.PANEL_SMOKE_MODE !== 'public-readonly';
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}),
    args: ['--no-sandbox'],
  });
  const context = await browser.newContext({ serviceWorkers: 'block' });
  await context.routeWebSocket('**/*', socket => socket.close());
  const page = await context.newPage();
  const result = { origin, synthetic, browser: browser.version(), errors: [], failedAssets: [], routes: [], roots: [], interceptedApi: [], blockedExternal: [], operationsChecks: [] };
  let operationsDisabled = false;
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('dialog', async dialog => { result.errors.push(dialog.message()); await dialog.dismiss(); });
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith('/assets/') && response.status() !== 200) result.failedAssets.push({ path, status: response.status() });
  });
  await context.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== origin) { result.blockedExternal.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    result.interceptedApi.push({ path: url.pathname, method: request.method() });
    if (!synthetic) {
      // The public smoke uses no account/session and permits only optional branding.
      if (request.method() === 'GET' && url.pathname === '/api/public/configuracoes-branding') return route.continue();
      return route.abort();
    }
    assert.equal(request.method(), 'GET', 'Navigation attempted a write; refusing live effects');
    const path = url.pathname.replace(/\/$/, ''); let data = {};
    if (path.includes('/analytics/meta-sender-operations')) {
      if (operationsDisabled) return route.fulfill({ status: 404, json: { detail: 'feature disabled' } });
      const endpoint = path.split('/analytics/meta-sender-operations')[1];
      if (endpoint) {
        const item = Array.from({ length: 21 }, (_, index) => attemptFixture(index)).find(item => endpoint === `/${item.id}`);
        return route.fulfill({ status: item ? 200 : 404, json: item || { detail: 'not found' } });
      }
      if (url.searchParams.get('event_name') === 'SlowEvent') {
        await new Promise(resolve => setTimeout(resolve, 700));
        // The page must abort this obsolete filter request; its delayed empty
        // result must never replace the newer query's observed attempt.
        try { return await route.fulfill({ json: operationsFixture(url, path.includes(EMPTY_TENANT)) }); }
        catch { return; }
      }
      return route.fulfill({ json: operationsFixture(url, path.includes(EMPTY_TENANT)) });
    }
    if (path.endsWith('/configuracoes-branding')) data = { nome_sistema: 'Issue 16 — teste sintético' };
    if (path.endsWith('/crm')) data = { etapas: [] };
    if (path.endsWith('/agenda')) data = { config: null, agendamentos: [] };
    if (/\/(inbox|conexoes|oficiais|grupos|destinos|agentes|unidades|transferencias|followups)$/.test(path)) data = [];
    if (path.endsWith('/meta/templates')) data = { templates: [] };
    if (path.endsWith('/campaign-central/campaigns')) data = { campaigns: [] };
    if (/\/campaign-central\/audiences\//.test(path)) data = { items: [], tags: [], imports: [], funnels: [], audiences: [], snapshots: [] };
    return route.fulfill({ json: data });
  });
  await page.addInitScript(({ synthetic }) => {
    if (synthetic) {
      // Seed a fresh context once; preserve role and tenant changes on reload
      // so reviewer restrictions are exercised with the intended session.
      if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify({ id: '00000000-0000-0000-0000-000000000003', empresa_id: '00000000-0000-0000-0000-000000000002', role: 'admin_empresa', nome: 'Teste sintético' }));
      if (!localStorage.getItem('token')) localStorage.setItem('token', 'synthetic-issue16-browser-only');
    }
    window.__panelSmokeRoots = [];
    const roots = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true, inject() { return 1; },
      onCommitFiberRoot(_renderer, root) {
        if (!roots.has(root)) { roots.add(root); window.__panelSmokeRoots.push({ container: root.containerInfo?.id || null }); }
      }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
    };
  }, { synthetic });
  try {
    await page.goto(`${origin}/painel`, { waitUntil: 'networkidle' });
    await page.locator('main').waitFor();
    await page.waitForTimeout(300);
    const paths = synthetic ? ['/painel', '/painel/inbox', '/painel/conexoes', '/painel/instagram', '/painel/trafego/meta', '/painel/trafego/meta/conversoes', '/painel/crm', '/painel/tags', '/painel/agenda', '/painel/followups', '/painel/campanhas', '/painel/campanhas/resultados/custos', '/painel'] : ['/painel'];
    for (const path of paths) {
      if (new URL(page.url()).pathname !== path) {
        // SPA transition, including routes without a sidebar link (commercial costs).
        await page.evaluate(path => { history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }, path);
        await page.waitForTimeout(500);
      }
      await page.locator('main').waitFor({ timeout: 10000 });
      const text = await page.locator('main').innerText();
      assert.ok(text.trim().length > 30, `Empty main content for ${path}`);
      assert.equal(new URL(page.url()).pathname, path);
      result.routes.push({ path, rendered: true });
    }
    if (synthetic) {
      // Exercise the actual router link and a browser history transition as well.
      await page.locator('nav a[href="/painel/crm"]').click();
      await page.waitForURL('**/painel/crm'); await page.locator('main').waitFor();
      await page.goBack(); await page.waitForURL('**/painel');
      await page.locator('nav a[href="/painel/trafego/meta/conversoes"]').click();
      const operations = page.getByTestId('sender-operations');
      await operations.getByText('21 resultados · página 1', { exact: true }).waitFor();
      await operations.getByRole('button', { name: 'Próxima', exact: true }).click();
      await operations.getByText('21 resultados · página 2', { exact: true }).waitFor();
      await operations.getByRole('button', { name: 'Anterior', exact: true }).click();
      await operations.getByText('21 resultados · página 1', { exact: true }).waitFor();
      await operations.getByLabel('Estado', { exact: true }).selectOption('unknown');
      await operations.getByRole('button', { name: 'Aplicar filtros' }).click();
      await operations.getByText('1 resultados · página 1', { exact: true }).waitFor();
      assert.equal(await operations.locator('tbody tr').count(), 1);
      await operations.getByRole('button', { name: /^Ver detalhes da tentativa/ }).click();
      await page.getByTestId('sender-attempt-detail').waitFor();
      assert.match(await page.getByTestId('sender-attempt-detail').innerText(), /UNKNOWN_DELIVERY.*não há confirmação de entrega/s);
      assert.match(await operations.innerText(), /NÃO VALIDADO/);
      result.operationsChecks.push('pagination', 'unknown-filter', 'safe-detail', 'unknown-not-retryable', 'accepted-not-attribution');
      await operations.getByLabel('Nome do evento', { exact: true }).fill('SlowEvent');
      const slowRequest = page.waitForRequest(request => request.url().includes('event_name=SlowEvent'));
      await operations.getByRole('button', { name: 'Aplicar filtros' }).click();
      await slowRequest;
      await operations.getByLabel('Nome do evento', { exact: true }).fill('Purchase');
      await operations.getByRole('button', { name: 'Aplicar filtros' }).click();
      await operations.getByText('1 resultados · página 1', { exact: true }).waitFor();
      await page.waitForTimeout(800);
      assert.equal(await operations.locator('tbody tr').count(), 1);
      assert.equal(await operations.getByText('Nenhuma tentativa corresponde aos filtros', { exact: true }).count(), 0);
      result.operationsChecks.push('obsolete-response-cannot-overwrite-new-filter');
      await operations.getByLabel('Nome do evento', { exact: true }).fill('MissingEvent');
      await operations.getByRole('button', { name: 'Aplicar filtros' }).click();
      await operations.getByText('Nenhuma tentativa corresponde aos filtros', { exact: true }).waitFor();
      assert.equal(await page.getByTestId('sender-attempt-detail').count(), 0);
      result.operationsChecks.push('filtered-empty-distinct-from-no-event', 'detail-reset-on-filter');
      await page.evaluate(empresa => {
        const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = empresa;
        localStorage.setItem('user', JSON.stringify(user)); window.dispatchEvent(new Event('storage'));
      }, EMPTY_TENANT);
      await operations.getByText('Nenhum evento natural observado', { exact: true }).waitFor();
      assert.match(await operations.innerText(), /NO_EVENT_OBSERVED/);
      assert.match(await operations.innerText(), /0 resultados/);
      result.operationsChecks.push('tenant-switch-clears-previous-results', 'no-natural-event-explicit');
      operationsDisabled = true;
      await operations.getByRole('button', { name: 'Atualizar evidências' }).click();
      await operations.getByRole('alert').filter({ hasText: 'não indica ausência de eventos' }).waitFor();
      assert.equal(await operations.getByText('Nenhum evento natural observado', { exact: true }).count(), 0);
      result.operationsChecks.push('disabled-api-distinct-from-no-event');
      operationsDisabled = false;
      await page.evaluate(empresa => {
        const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = empresa; user.role = 'meta_app_reviewer';
        localStorage.setItem('user', JSON.stringify(user));
      }, SYNTHETIC_TENANT);
      const beforeReviewer = result.interceptedApi.filter(item => item.path.includes('meta-sender-operations')).length;
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForURL('**/painel/conexoes');
      assert.equal(await page.locator('nav a[href="/painel/trafego/meta/conversoes"]').count(), 0);
      assert.equal(result.interceptedApi.filter(item => item.path.includes('meta-sender-operations')).length, beforeReviewer);
      result.operationsChecks.push('reviewer-navigation-and-data-restricted');
    }
    result.roots = await page.evaluate(() => window.__panelSmokeRoots);
    assert.equal(result.roots.length, 1, 'Multiple React roots mounted for one panel');
    assert.deepEqual(result.errors, []); assert.deepEqual(result.failedAssets, []);
    if (process.env.PANEL_SCREENSHOT) await page.screenshot({ path: process.env.PANEL_SCREENSHOT, fullPage: true });
    result.status = 'PASS';
  } catch (error) { result.status = 'FAIL'; result.failure = error.message; process.exitCode = 1; }
  finally {
    if (process.env.PANEL_EVIDENCE) fs.writeFileSync(process.env.PANEL_EVIDENCE, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2)); await browser.close();
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
