const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const TENANT = '00000000-0000-0000-0000-000000000002';
const OTHER = '00000000-0000-0000-0000-000000000004';
const EXAMPLE = { id: 'price-conditions', title: 'Preço, condições e confiança', messages: [{ id: 'm1', role: 'user', text: 'Qual o preço, parcela em três vezes e tem garantia?' }], expected_intents: ['price', 'conditions', 'trust'] };
const RUN = { id: 'synthetic-run', created_at: '2026-10-12T12:00:00Z', source: 'synthetic', status: 'demo', ledger_persisted: true, classification: { primary_stage: { key: 'consideration', confidence: 0.8, abstained: false }, intents: ['price', 'conditions', 'trust'].map(key => ({ key, confidence: 0.9, evidence_ids: ['m1'] })), objections: [{ key: 'trust_objection', confidence: 0.7, evidence_ids: ['m1'] }], active_topics: ['price', 'conditions', 'trust'], signals: { interest: 0.8, readiness: 0.4, human_request: 0, risk: 0.2 }, abstention: { abstained: false }, evidence: [{ message_id: 'm1', excerpt: EXAMPLE.messages[0].text }], tag_suggestions: [{ tag_id: 'synthetic-tag', tag_name: 'Tag sintética existente', reason: 'Condições mencionadas', hypothetical: true }] }, metrics: { latency_ms: null, cost_usd: null, input_tokens: null } };
const STATUS = { schema_version: 'a06.v1', mode: 'shadow', enabled: true, provider: { configured: false, enabled: false, authorized_real: false, availability: 'not_configured' }, ledger: { available: true, retention_days: 7 }, budget: { daily_usd: 0, reserved_usd: 0, measured_usd: null }, coverage: { requested: 1, evaluated: 0, abstained: 0, errors: 0, denominator: 'retained_runs' }, limitations: ['Exemplos não validam o provider real.'] };

async function main() {
  const origin = new URL(process.env.JEV_ORIGIN || process.env.PANEL_ORIGIN || 'http://127.0.0.1:18086').origin;
  const actual = process.env.JEV_SMOKE_MODE === 'api-synthetic';
  // In API mode, credential arrives only over stdin, never CLI/env/report/file.
  const session = actual ? JSON.parse(fs.readFileSync(0, 'utf8')) : { token: 'synthetic-browser-only', user: { id: '00000000-0000-0000-0000-000000000003', empresa_id: TENANT, role: 'admin_empresa' } };
  assert.ok(session.token && session.user?.empresa_id, 'Authenticated synthetic session required');
  const result = { mode: actual ? 'actual-api-server-owned-synthetic-only' : 'intercepted-synthetic', checks: [], errors: [], failedAssets: [], unexpectedWrites: [], externalRequests: [] };
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  const context = await browser.newContext({ serviceWorkers: 'block' });
  await context.routeWebSocket('**/*', socket => socket.close());
  const page = await context.newPage();
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('response', response => { if (response.status() >= 400 && /\.(js|css)(\?|$)/.test(response.url())) result.failedAssets.push(new URL(response.url()).pathname); });
  let slowAnalyze = false, outage = false, providerConfigured = false, savedKey = false, adminCalls = 0, testCalls = 0;
  let adminConfig = { provider: 'typesafe', model: 'jev-1.13.0', daily_budget_usd: '0', enabled: false, mode: 'shadow', timeout_seconds: 8, has_key: false, configured: false, status: 'not_configured', tested_at: null, credential_storage_available: true };
  let lastBody = null;
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { result.externalRequests.push('blocked-external'); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const method = request.method(), jev = /\/empresas\/[^/]+\/jev\//.test(url.pathname);
    const isAnalyze = jev && url.pathname.endsWith('/analyze') && method === 'POST';
    if (actual) {
      if (isAnalyze) {
        const body = request.postDataJSON();
        assert.ok(body.example_id && !body.lead_id && !body.use_provider, 'Only explicit server-owned local synthetic demo allowed');
        return route.continue();
      }
      if (method === 'GET' && jev) return route.continue();
      if (method !== 'GET') { result.unexpectedWrites.push('blocked-non-synthetic-write'); return route.abort(); }
      // Render branding from a safe local fixture; other APIs are not part of proof.
      return route.fulfill({ json: { nome_sistema: 'A06 · teste sintético' } });
    }
    if (url.pathname.includes('/integrations/jev')) {
      adminCalls++;
      if (method === 'PUT') {
        const body = request.postDataJSON();
        assert.equal(body.mode, 'shadow'); assert.equal(body.real_conversation_egress_enabled, undefined);
        savedKey = Boolean(body.api_key) || (savedKey && !body.remove_api_key);
        adminConfig = { ...adminConfig, daily_budget_usd: body.daily_budget_usd, enabled: body.enabled, timeout_seconds: body.timeout_seconds, has_key: savedKey, configured: savedKey, status: savedKey ? 'not_evaluated' : 'not_configured' };
        return route.fulfill({ json: adminConfig });
      }
      if (method === 'POST' && url.pathname.endsWith('/test')) { testCalls++; adminConfig.status = 'connected'; adminConfig.tested_at = '2026-10-12T12:00:00Z'; return route.fulfill({ json: { status: 'connected' } }); }
      if (method === 'GET') return route.fulfill({ json: adminConfig });
    }
    if (jev) {
      if (outage && url.pathname.endsWith('/status')) return route.fulfill({ status: 503, json: { detail: 'private-do-not-echo' } });
      if (url.pathname.endsWith('/status')) return route.fulfill({ json: { ...STATUS, provider: { ...STATUS.provider, configured: providerConfigured, enabled: providerConfigured } } });
      if (url.pathname.endsWith('/examples')) return route.fulfill({ json: { items: [EXAMPLE] } });
      if (isAnalyze) {
        lastBody = request.postDataJSON();
        if (slowAnalyze) { await new Promise(resolve => setTimeout(resolve, 650)); try { return await route.fulfill({ json: { ...RUN, id: 'obsolete-other-tenant' } }); } catch { return; } }
        if (lastBody.lead_id) return route.fulfill({ json: { ...RUN, source: 'real', status: 'not_configured', classification: null, lead_id: lastBody.lead_id } });
        return route.fulfill({ json: lastBody.use_provider ? { ...RUN, status: 'completed', engine: 'typesafe', metrics: { cost_usd: 0.000042, latency_ms: 50, input_tokens: 1 } } : RUN });
      }
      if (url.pathname.endsWith('/runs')) {
        if (url.searchParams.get('intent') === 'price') { await new Promise(resolve => setTimeout(resolve, 500)); try { return await route.fulfill({ json: { items: [RUN], total: 1, limit: 50 } }); } catch { return; } }
        return route.fulfill({ json: { items: url.pathname.includes(OTHER) || url.searchParams.get('intent') ? [] : [RUN], total: url.pathname.includes(OTHER) || url.searchParams.get('intent') ? 0 : 1, limit: 50 } });
      }
      return route.fulfill({ json: RUN });
    }
    if (method !== 'GET') { result.unexpectedWrites.push('unexpected-api-write'); return route.abort(); }
    if (url.pathname === '/api/empresas/') return route.fulfill({ json: [{ id: TENANT, nome_empresa: 'Empresa sintética A' }, { id: OTHER, nome_empresa: 'Empresa sintética B' }] });
    return route.fulfill({ json: { nome_sistema: 'A06 · teste sintético' } });
  });
  await page.addInitScript(({ token, user }) => {
    if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify({ ...user, nome: 'Teste sintético A06' }));
    if (!localStorage.getItem('token')) localStorage.setItem('token', token);
    const roots = new WeakSet(); window.__jevRoots = [];
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__jevRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, session);
  const panel = page.getByTestId('jev-diagnostics');
  const changeSession = async (tenant, role = 'admin_empresa') => page.evaluate(({ tenant, role }) => { const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = tenant; user.role = role; localStorage.setItem('user', JSON.stringify(user)); window.dispatchEvent(new Event('storage')); }, { tenant, role });
  try {
    await page.goto(`${origin}/painel/inteligencia${actual ? '' : `?lead_id=${TENANT}`}`, { waitUntil: 'networkidle' });
    await panel.getByRole('button', { name: 'Analisar exemplo sintético' }).waitFor();
    await panel.getByRole('button', { name: 'Analisar exemplo sintético' }).click();
    await page.getByTestId('jev-detail').getByText('Resultado SHADOW', { exact: true }).waitFor();
    assert.match(await page.getByTestId('jev-detail').innerText(), /Demonstração local/);
    assert.match(await page.getByTestId('jev-detail').innerText(), /não valida o provider real/);
    assert.match(await page.getByTestId('jev-detail').innerText(), /Não medido no provider/);
    result.checks.push('explicit-synthetic-demo', 'fixture-not-provider-validation', 'no-invented-provider-metrics', 'separate-stage-and-topics', 'evidence-and-hypothetical-tags');
    // Tags returned by an authenticated tenant may contain private labels. Keep
    // assertions on the real UI, then replace labels only for screenshot capture.
    if (actual && process.env.JEV_SCREENSHOT_DIR) await page.evaluate(() => { const detail = document.querySelector('[data-testid="jev-detail"]'); const heading = [...detail.querySelectorAll('h3')].find(node => node.textContent.startsWith('Sugestões de tags')); const list = heading?.nextElementSibling?.nextElementSibling; if (list?.tagName === 'UL') { window.__jevScreenshotTagContent = [...list.children].map(node => node.textContent); [...list.children].forEach((node, index) => { node.textContent = `Tag existente ${index + 1} · sugestão hipotética (label sanitizado)`; }); } });
    if (process.env.JEV_SCREENSHOT_DIR) { fs.mkdirSync(process.env.JEV_SCREENSHOT_DIR, { recursive: true }); await page.screenshot({ path: path.join(process.env.JEV_SCREENSHOT_DIR, 'jev-synthetic-desktop.png'), fullPage: true }); }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, 'Mobile document overflows');
    if (process.env.JEV_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.JEV_SCREENSHOT_DIR, 'jev-synthetic-mobile.png'), fullPage: true });
    result.checks.push('mobile-no-document-overflow');
    await page.setViewportSize({ width: 1440, height: 1000 });
    if (!actual) {
      await panel.getByLabel('Intenção', { exact: true }).selectOption('price');
      await page.waitForTimeout(80);
      await panel.getByLabel('Intenção', { exact: true }).selectOption('human');
      await panel.getByText('Nenhuma execução retida neste filtro. Isso não comprova ausência de intenções.', { exact: true }).waitFor();
      await page.waitForTimeout(700); assert.equal(await panel.locator('tbody tr').count(), 0);
      result.checks.push('obsolete-filter-response-hidden');
      await panel.getByLabel('Intenção', { exact: true }).selectOption('');
      await panel.getByLabel('ID do contato (UUID)').fill(TENANT);
      await panel.getByRole('button', { name: 'Solicitar diagnóstico SHADOW' }).click();
      await page.getByTestId('jev-detail').getByText('Provider não configurado', { exact: true }).waitFor();
      assert.equal(await page.getByTestId('jev-detail').getByText('Intenções simultâneas', { exact: true }).count(), 0);
      result.checks.push('real-not-configured-not-fabricated');
      providerConfigured = true;
      await panel.getByRole('button', { name: 'Atualizar diagnóstico' }).click();
      await panel.getByLabel('Usar provider externo somente neste exemplo sintético (consome orçamento)').check();
      await panel.getByRole('button', { name: 'Analisar exemplo sintético' }).click();
      await page.getByTestId('jev-detail').getByText('Provider: análise concluída', { exact: true }).waitFor();
      assert.equal(lastBody.use_provider, true); assert.match(await page.getByTestId('jev-detail').innerText(), /0,000042 USD/);
      result.checks.push('synthetic-provider-explicit-and-metrics-distinct');
      slowAnalyze = true;
      await panel.getByRole('button', { name: 'Analisar exemplo sintético' }).click();
      await changeSession(OTHER); await page.waitForTimeout(850);
      assert.equal(await page.getByTestId('jev-detail').count(), 0);
      assert.equal(await panel.getByLabel('ID do contato (UUID)').inputValue(), '');
      result.checks.push('tenant-switch-aborts-analysis-clears-private-state');
      slowAnalyze = false; outage = true;
      await panel.getByRole('button', { name: 'Atualizar diagnóstico' }).click();
      await panel.getByRole('alert').filter({ hasText: 'não indica ausência' }).waitFor();
      assert.doesNotMatch(await panel.innerText(), /private-do-not-echo/);
      result.checks.push('outage-sanitized-no-classification-fallback');
      outage = false;
      await changeSession(TENANT, 'super_admin');
      await page.evaluate(() => { history.pushState({}, '', '/admin/configuracoes/integracoes/jev'); window.dispatchEvent(new PopStateEvent('popstate')); });
      const admin = page.getByTestId('jev-admin'); await admin.getByLabel('Empresa', { exact: true }).selectOption(TENANT);
      await admin.getByLabel('Chave da API (nova ou substituição)').fill('synthetic-discard-on-tenant-switch');
      await admin.getByLabel('Empresa', { exact: true }).selectOption(OTHER);
      assert.equal(await admin.getByLabel('Chave da API (nova ou substituição)').inputValue(), '');
      await admin.getByLabel('Empresa', { exact: true }).selectOption(TENANT);
      await admin.getByLabel('Chave da API (nova ou substituição)').fill('synthetic-browser-only-write-key');
      await admin.getByLabel('Orçamento diário (USD)').fill('0.1');
      await admin.getByLabel('Habilitar provider em SHADOW para exemplos sintéticos explícitos').check();
      await admin.getByRole('button', { name: 'Salvar configuração JEV' }).click();
      await admin.getByText(/Configuração salva. Disponível imediatamente/).waitFor();
      assert.equal(await admin.getByLabel('Chave da API (nova ou substituição)').inputValue(), '');
      assert.doesNotMatch(await page.evaluate(() => JSON.stringify(localStorage)), /synthetic-browser-only-write-key/);
      await admin.getByRole('button', { name: 'Testar conexão salva' }).click();
      await admin.getByText('Conexão validada', { exact: true }).first().waitFor(); assert.equal(testCalls, 1);
      const before = adminCalls;
      await changeSession(TENANT, 'admin_empresa');
      await page.getByText('Configuração JEV exclusiva do Super Admin.', { exact: true }).waitFor(); assert.equal(adminCalls, before);
      result.checks.push('admin-tenant-hot-config', 'admin-tenant-switch-clears-unsaved-key', 'write-only-key-cleared-not-in-storage', 'synthetic-connection-test', 'non-admin-settings-restricted');
      await changeSession(TENANT, 'meta_app_reviewer');
      await page.evaluate(() => { history.pushState({}, '', '/painel/inteligencia'); window.dispatchEvent(new PopStateEvent('popstate')); });
      await page.waitForURL('**/painel/conexoes');
      assert.equal(await page.locator('nav a[href="/painel/inteligencia"]').count(), 0);
      result.checks.push('reviewer-diagnostics-restricted');
    }
    result.reactRoots = await page.evaluate(() => window.__jevRoots);
    assert.equal(result.reactRoots.length, 1); assert.deepEqual(result.errors, []); assert.deepEqual(result.failedAssets, []); assert.deepEqual(result.unexpectedWrites, []); assert.deepEqual(result.externalRequests, []);
    result.status = 'PASS';
  } catch (error) { result.status = 'FAIL'; result.failure = error.message; process.exitCode = 1; }
  finally { if (process.env.JEV_EVIDENCE) fs.writeFileSync(process.env.JEV_EVIDENCE, JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2)); await browser.close(); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
