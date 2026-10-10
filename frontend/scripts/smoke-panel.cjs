const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

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
  const result = { origin, synthetic, browser: browser.version(), errors: [], failedAssets: [], routes: [], roots: [], interceptedApi: [], blockedExternal: [] };
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
      localStorage.setItem('user', JSON.stringify({ id: '00000000-0000-0000-0000-000000000003', empresa_id: '00000000-0000-0000-0000-000000000002', role: 'admin_empresa', nome: 'Teste sintético' }));
      localStorage.setItem('token', 'synthetic-issue16-browser-only');
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
    const paths = synthetic ? ['/painel', '/painel/inbox', '/painel/conexoes', '/painel/instagram', '/painel/trafego/meta', '/painel/crm', '/painel/tags', '/painel/agenda', '/painel/followups', '/painel/campanhas', '/painel/campanhas/resultados/custos', '/painel'] : ['/painel'];
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
