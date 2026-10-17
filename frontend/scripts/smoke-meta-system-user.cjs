// Synthetic browser smoke for the central Meta System User administration.
// Every API request is intercepted and fulfilled in memory; no Graph call or
// production credential is available to the browser context.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const ORIGIN = new URL(process.env.META_ADMIN_ORIGIN || 'http://127.0.0.1:18849').origin;
const COMPANY_ID = '00000000-0000-0000-0000-000000000036';
const TEST_TOKEN = 'synthetic-only-token-never-sent-to-meta';
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'notebook', width: 1280, height: 720 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'mobile', width: 390, height: 844 },
];
const ASSETS = [
  { id: 'asset-business', type: 'business', external_id: 'business-synthetic', name: 'Ferrioli BM Sintética', business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', business_access: [], access_state: 'verified', access_path: 'assigned_business', metadata: {}, linked_empresa_ids: [] },
  { id: 'asset-account', type: 'ad_account', external_id: '123456', name: 'Conta Ads Sintética', business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', business_access: [{ business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', access_path: 'business_owned' }], access_state: 'verified', access_path: 'business_owned', metadata: { account_status: 1, currency: 'BRL' }, linked_empresa_ids: [] },
  { id: 'asset-page', type: 'page', external_id: 'page-synthetic', name: 'Página Sintética', business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', business_access: [{ business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', access_path: 'business_owned' }], access_state: 'verified', access_path: 'business_owned', metadata: { tasks: ['ADVERTISE'], instagram_account_id: 'ig-synthetic' }, linked_empresa_ids: [] },
  { id: 'asset-instagram', type: 'instagram_account', external_id: 'ig-synthetic', name: 'perfil_sintetico', business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', business_access: [{ business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', access_path: 'business_owned' }], access_state: 'verified', access_path: 'business_owned', metadata: { username: 'perfil_sintetico' }, linked_empresa_ids: [] },
  { id: 'asset-dataset', type: 'dataset', external_id: 'dataset-synthetic', name: 'CRM Sintético', business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', business_access: [{ business_id: 'business-synthetic', business_name: 'Ferrioli BM Sintética', access_path: 'business_dataset' }], access_state: 'verified', access_path: 'business_dataset', metadata: { is_crm: true }, linked_empresa_ids: [] },
];

const company = { id: COMPANY_ID, nome_empresa: 'Empresa Sintética #36', ativo: true };
const user = { id: 'synthetic-super-admin', role: 'super_admin', empresa_id: null, nome: 'Admin sintético' };

function makeApiState() {
  return { saved: false, discovered: false, linked: [], calls: [], apiWrites: [], datasetReadValidations: 0, unexpected: [], external: [], errors: [] };
}

function json(status, body) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) };
}

async function main() {
  const output = process.env.META_ADMIN_EVIDENCE_DIR || path.resolve('../reports/issue36-meta-system-user-smoke');
  fs.mkdirSync(output, { recursive: true });
  const result = { status: 'RUNNING', mode: 'SYNTHETIC_INTERCEPTED_API', origin: ORIGIN, viewports: [], apiWrites: [], externalRequests: [], pageErrors: [], screenshots: [] };
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  result.browser = browser.version();
  try {
    for (const viewport of VIEWPORTS) {
      const state = makeApiState();
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, locale: 'pt-BR', reducedMotion: 'reduce', serviceWorkers: 'block' });
      await context.routeWebSocket('**/*', socket => socket.close());
      await context.addInitScript(({ syntheticUser }) => {
        localStorage.setItem('user', JSON.stringify(syntheticUser));
        localStorage.setItem('token', 'synthetic-session-only');
        window.__uxRoots = [];
        const seen = new WeakSet();
        window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, renderers: new Map(), inject(renderer) { this.renderers.set(1, renderer); return 1; }, onCommitFiberRoot(_id, root) { if (!seen.has(root)) { seen.add(root); window.__uxRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
      }, { syntheticUser: user });
      await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== ORIGIN) { state.external.push(url.pathname); return route.abort(); }
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const method = request.method();
        const pathname = url.pathname;
        let body = {};
        try { body = request.postDataJSON() || {}; } catch {}
        state.calls.push({ method, path: pathname });

        if (pathname === '/api/empresas/' && method === 'GET') return route.fulfill(json(200, [company]));
        if (pathname === '/api/public/configuracoes-branding' && method === 'GET') return route.fulfill(json(200, { nome_sistema: 'AgentOS · sintético', cor_primaria: '#235568' }));
        if (pathname === '/api/admin/configuracoes' && method === 'GET') return route.fulfill(json(200, { nome_sistema: 'AgentOS · sintético', cor_primaria: '#235568' }));
        if (pathname === '/api/admin/integrations/global-status' && method === 'GET') return route.fulfill(json(200, { meta: { public_configuration_ready: true, server_configuration_ready: true, graph_api_version: 'v25.0', marketing_graph_api_version: 'v26.0', marketing_write_enabled: false, live_actions_enabled: false, campaign_create_enabled: false, ad_create_enabled: false }, openai: {}, marketing_credentials: { scope: 'central_synthetic' }, jev: {} }));
        if (pathname === '/api/admin/meta/system-user/credential' && method === 'GET') return route.fulfill(json(200, state.saved ? { configured: true, validated: true, token_masked: '***test', system_user_id: 'system-user-synthetic', system_user_name: 'Ferrioli System User (sintético)', app_id: 'app-synthetic', app_name: 'App sintético', permissions: ['ads_read'], permissions_status: 'verified', permission_capabilities: { ads_read: 'granted', ads_management: 'missing', business_management: 'missing', pages_show_list: 'missing', pages_read_engagement: 'missing', instagram_basic: 'missing' }, expiry_status: 'unknown', encryption_available: true } : { configured: false, validated: false, permissions: [], permissions_status: 'unknown', encryption_available: true }));
        if (pathname === '/api/admin/meta/system-user/assets' && method === 'GET') return route.fulfill(json(200, { assets: state.discovered ? ASSETS.map(asset => ({ ...asset, linked_empresa_id: state.linked.includes(asset.id) && asset.type !== 'business' ? COMPANY_ID : null, linked_empresa_ids: state.linked.includes(asset.id) ? [COMPANY_ID] : [] })) : [] }));
        if (pathname === `/api/admin/meta/system-user/companies/${COMPANY_ID}/assets` && method === 'GET') return route.fulfill(json(200, { empresa_id: COMPANY_ID, assets: state.linked.map(id => { const asset = ASSETS.find(row => row.id === id); return { id: asset.id, type: asset.type, external_id: asset.external_id, name: asset.name, access_state: 'verified', read_enabled: true, write_enabled: false, verified_at: new Date().toISOString(), business_access: asset.business_access }; }) }));
        if (pathname === `/api/admin/meta/system-user/companies/${COMPANY_ID}/datasets/crm/validate` && method === 'POST') {
          state.apiWrites.push({ method, path: pathname }); state.datasetReadValidations += 1;
          return route.fulfill(json(200, { empresa_id: COMPANY_ID, asset_read_state: 'observed', crm_dataset_state: 'observed', dataset: { id: 'dataset-synthetic', name: 'CRM Sintético' }, is_crm: true, is_unavailable: false, system_user_permissions_state: 'verified', system_user_permissions: ['business_management'], capi_delivery_state: 'unknown', live_sender_enabled: false, external_write_performed: false }));
        }
        if (pathname === '/api/admin/meta/system-user/credential/validate' && method === 'POST') {
          state.apiWrites.push({ method, path: pathname, token_value_seen_by_mock_only: body.token === TEST_TOKEN });
          if (body.token !== TEST_TOKEN) { state.unexpected.push('unexpected-token-validation-payload'); return route.fulfill(json(400, { detail: 'synthetic token mismatch' })); }
          return route.fulfill(json(200, { valid: true, system_user_id: 'system-user-synthetic', system_user_name: 'Ferrioli System User (sintético)', app_id: 'app-synthetic', app_name: 'App sintético', permissions: ['ads_read'], permissions_status: 'verified', expiry_status: 'unknown', expires_at: null }));
        }
        if (pathname === '/api/admin/meta/system-user/credential' && method === 'PUT') {
          state.apiWrites.push({ method, path: pathname, token_value_seen_by_mock_only: body.token === TEST_TOKEN });
          if (body.token !== TEST_TOKEN) { state.unexpected.push('unexpected-token-save-payload'); return route.fulfill(json(400, { detail: 'synthetic token mismatch' })); }
          state.saved = true;
          return route.fulfill(json(200, { configured: true, validated: true, token_masked: '***test', system_user_id: 'system-user-synthetic', permissions: ['ads_read'], permissions_status: 'verified', permission_capabilities: { ads_read: 'granted', ads_management: 'missing' }, encryption_available: true, expiry_status: 'unknown', existing_asset_links_invalidated: false }));
        }
        if (pathname === '/api/admin/meta/system-user/assets/discover' && method === 'POST') {
          state.apiWrites.push({ method, path: pathname }); state.discovered = true;
          return route.fulfill(json(200, { assets: ASSETS.length, businesses: 1, complete: true, errors: [] }));
        }
        if (pathname === '/api/admin/meta/system-user/companies/' + COMPANY_ID + '/assets' && method === 'PUT') {
          state.apiWrites.push({ method, path: pathname, ids: (body.assets || []).map(item => item.asset_id), anyWriteEnabled: (body.assets || []).some(item => item.write_enabled) });
          if ((body.assets || []).some(item => item.write_enabled)) state.unexpected.push('write-enabled-in-synthetic-test');
          state.linked = (body.assets || []).map(item => item.asset_id);
          return route.fulfill(json(200, { empresa_id: COMPANY_ID, assets: [] }));
        }
        if (pathname.startsWith(`/api/empresas/${COMPANY_ID}/`)) {
          if (method !== 'GET') state.unexpected.push(`unexpected-other-write:${pathname}`);
          return route.fulfill(json(200, {}));
        }
        state.unexpected.push(`unhandled:${method}:${pathname}`);
        return route.fulfill(json(200, {}));
      });
      const page = await context.newPage();
      page.setDefaultTimeout(12000);
      page.on('pageerror', error => state.errors.push(error.message));
      await page.goto(`${ORIGIN}/admin/integracoes`, { waitUntil: 'domcontentloaded' });
      await page.getByText('Meta · Usuário do Sistema', { exact: true }).waitFor();
      await page.getByText('Sem token central cadastrado', { exact: true }).waitFor();
      await page.getByLabel('Token de usuário do sistema').fill(TEST_TOKEN);
      await page.getByRole('button', { name: 'Validar token' }).click();
      await page.getByText('Validação sem gravação', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Salvar credencial' }).click();
      await page.getByText('Credencial validada e salva de forma criptografada.', { exact: false }).waitFor();
      assert.equal(await page.getByLabel('Token de usuário do sistema').inputValue(), '');
      assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => value.includes('synthetic-only-token-never-sent-to-meta'))), false);
      await page.getByRole('button', { name: 'Atualizar ativos Meta' }).click();
      await page.getByText('Atualização concluída: 5 ativos verificados.', { exact: true }).waitFor();
      const assetCheckbox = name => page.locator('label').filter({ hasText: name }).locator('input[type="checkbox"]').first();
      for (const name of ['Ferrioli BM Sintética', 'Conta Ads Sintética', 'Página Sintética', 'perfil_sintetico', 'CRM Sintético']) {
        await assetCheckbox(name).check();
      }
      const writeLabel = page.locator('label').filter({ hasText: 'Permitir criação explícita de objetos de anúncios pausados' });
      assert.equal(await writeLabel.locator('input[type="checkbox"]').isDisabled(), true, 'ads_management missing must keep account write control disabled');
      await page.getByRole('button', { name: 'Salvar vínculos da empresa' }).click();
      await page.getByText('Ativos vinculados após nova validação de acesso na Meta.', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Validar leitura do Dataset CRM' }).click();
      await page.getByText('Leitura Graph:', { exact: false }).waitFor();
      await page.getByText('Entrega CAPI / otimização:', { exact: false }).waitFor();
      assert.equal(state.datasetReadValidations, 1);
      const metrics = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth, roots: window.__uxRoots }));
      assert.deepEqual(metrics.roots, ['root']);
      assert.ok(metrics.documentWidth <= metrics.width + 2, `${viewport.name} overflow ${metrics.documentWidth}/${metrics.width}`);
      assert.deepEqual(state.external, []);
      assert.deepEqual(state.errors, []);
      assert.deepEqual(state.unexpected, []);
      assert.equal(state.apiWrites.some(call => call.path.includes('credential') && call.token_value_seen_by_mock_only), true);
      assert.equal(state.apiWrites.some(call => call.path.endsWith('/assets') && call.anyWriteEnabled), false);
      const screenshot = `meta-system-user-${viewport.name}.png`;
      await page.screenshot({ path: path.join(output, screenshot), fullPage: true });
      result.screenshots.push({ viewport: viewport.name, path: path.join(output, screenshot) });
      result.viewports.push({ ...viewport, ...metrics, credentialValidation: 'PASS_SYNTHETIC', credentialSave: 'PASS_SYNTHETIC', assets: ASSETS.length, tenantLinking: 'PASS_SYNTHETIC', writesDisabledWithoutAdsManagement: true, apiWrites: state.apiWrites.length });
      result.apiWrites.push(...state.apiWrites.map(({ token_value_seen_by_mock_only, ...call }) => call));
      result.externalRequests.push(...state.external);
      await context.close();
    }
    assert.deepEqual(result.externalRequests, []);
    result.status = 'PASS';
  } catch (error) {
    result.status = 'FAIL'; result.failure = error.stack; process.exitCode = 1;
  } finally {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ status: result.status, browser: result.browser, viewports: result.viewports.length, screenshots: result.screenshots, apiWrites: result.apiWrites.length, externalRequests: result.externalRequests, failure: result.failure, output }, null, 2));
    await browser.close();
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
