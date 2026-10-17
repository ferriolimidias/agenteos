// Synthetic Super Admin navigation smoke. All API requests are intercepted;
// non-GET requests and cross-origin requests are blocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const COMPANY_ID = '00000000-0000-0000-0000-000000000002';
const ORIGIN = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:18848').origin;
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'notebook', width: 1280, height: 720 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'mobile-large', width: 430, height: 932 },
  { name: 'mobile', width: 390, height: 844 },
];
const selectedViewports = process.env.UX_VIEWPORTS ? VIEWPORTS.filter(viewport => process.env.UX_VIEWPORTS.split(',').includes(viewport.name)) : VIEWPORTS;
const ROUTES = [
  { name: 'companies', path: '/admin', heading: 'Carteira de empresas' },
  { name: 'operation', path: '/admin/operacao', heading: 'Operação e saúde' },
  { name: 'integrations', path: '/admin/integracoes', heading: 'Integrações' },
  { name: 'security', path: '/admin/seguranca', heading: 'Segurança e auditoria' },
  { name: 'platform-settings', path: '/admin/configuracoes', heading: 'Configurações da plataforma' },
  { name: 'company-overview', path: `/admin/empresas/${COMPANY_ID}`, heading: 'Visão geral' },
  { name: 'company-ai', path: `/admin/empresas/${COMPANY_ID}?area=atendimento-ia`, heading: 'Atendimento e IA' },
  { name: 'company-ai-prompts', path: `/admin/empresas/${COMPANY_ID}/atendimento-ia/prompts`, heading: 'Atendimento e IA' },
  { name: 'company-ai-diagnostics', path: `/admin/empresas/${COMPANY_ID}/atendimento-ia/jev`, heading: 'Inteligência conversacional' },
  { name: 'company-ai-simulator', path: `/admin/empresas/${COMPANY_ID}/atendimento-ia/simulador`, heading: 'Simulador do Agente AI' },
  { name: 'company-crm', path: `/admin/empresas/${COMPANY_ID}?area=crm-regras`, heading: 'CRM e regras comerciais' },
  { name: 'company-channels', path: `/admin/empresas/${COMPANY_ID}?area=canais`, heading: 'Canais e credenciais' },
  { name: 'company-campaigns', path: `/admin/empresas/${COMPANY_ID}/meta`, heading: 'Campanhas e Meta' },
  { name: 'company-payments', path: `/admin/empresas/${COMPANY_ID}?area=pagamentos`, heading: 'Pagamentos' },
  { name: 'company-users', path: `/admin/empresas/${COMPANY_ID}?area=usuarios`, heading: 'Usuários e permissões' },
  { name: 'company-diagnostics', path: `/admin/empresas/${COMPANY_ID}?area=diagnosticos`, heading: 'Diagnósticos e auditoria' },
];
const selectedRoutes = process.env.UX_ROUTES ? ROUTES.filter(route => process.env.UX_ROUTES.split(',').includes(route.name)) : ROUTES;

const syntheticUser = { id: 'synthetic-super-admin', role: 'super_admin', empresa_id: null, nome: 'Operador sintético' };
const branding = { id: 1, nome_sistema: 'Ferrioli · sintético', cor_primaria: '#235568', logo_base64: '', favicon_base64: '', openai_key_global_configurada: true };
const company = { id: COMPANY_ID, nome_empresa: 'Empresa sintética UX-3', area_atuacao: 'Validação', ativo: true };
const operations = { database: { healthy: true }, redis: { healthy: true }, status_lock: 'not_running', workers: { followup: { running: false, status: 'STOPPED', enabled: false, instance_count: 0, pending: 0, retry: 0 }, campaign: { running: false, status: 'STOPPED', enabled: false, instance_count: 0, pending: 0, retry: 0 } } };

function responseFor(pathname) {
  if (pathname === '/api/admin/configuracoes') return branding;
  if (pathname === '/api/admin/integrations/global-status') return { meta: { public_configuration_ready: true, server_configuration_ready: true, graph_api_version: 'v25.0', marketing_graph_api_version: 'v26.0', marketing_write_enabled: false, live_actions_enabled: false, campaign_create_enabled: false, ad_create_enabled: false, existing_post_ads_enabled: true }, openai: { global_credential_configured: true }, marketing_credentials: { scope: 'per_company' }, jev: { scope: 'per_company' } };
  if (pathname === '/api/empresas' || pathname === '/api/empresas/') return [company];
  if (pathname === `/api/empresas/${COMPANY_ID}`) return company;
  if (pathname === '/api/admin/workers/status') return operations;
  if (pathname === '/api/admin/security/overview') return {
    companies: [{ id: COMPANY_ID, name: company.nome_empresa, user_count: 1 }],
    users: [{ id: 'synthetic-user', company_id: COMPANY_ID, company_name: company.nome_empresa, name: 'Pessoa sintética', email: 'synthetic@example.invalid', role: 'admin_empresa', active: true }],
    prompt_audit: { scope: 'A11 sintético', items: [], returned: 0, limit: 100 },
    general_admin_audit_available: false,
  };
  if (pathname.endsWith('/dashboard/stats')) return { total_mensagens: 0 };
  if (pathname.endsWith('/whatsapp/status')) return { status: 'unknown', online: false, provider: null };
  if (pathname.endsWith('/ia-config')) return { ia_personalidade: '', ia_regras_negocio: '', modelo_ia: '', modelo_ia_reasoning_effort: null, tags_ia_habilitadas: [] };
  if (pathname.endsWith('/crm/tags/oficiais')) return [];
  if (pathname.endsWith('/monitor/funnel')) return { status: 'not_configured', configured: false };
  if (pathname.endsWith('/crm')) return { funil_nome: 'Funil sintético', etapas: [] };
  if (pathname.endsWith('/usuarios')) return [];
  if (pathname.endsWith('/openai-models')) return { items: [], credential_status: 'not_configured', stale: false, load_error: false };
  if (pathname.endsWith('/payments/mercado-pago')) return { configured: false, enabled: false, environment: 'test', access_token_configured: false, webhook_secret_configured: false, webhook_url: '' };
  if (pathname.endsWith('/conexoes/')) return [];
  if (pathname.endsWith('/rag')) return [];
  if (pathname.endsWith('/instagram/connections')) return { connections: [] };
  if (pathname.endsWith('/traffic/meta/config')) return { configured: true, config: { ad_account_id: 'act_synthetic', page_id: 'page_synthetic', dataset_id: 'dataset_synthetic' }, active_official_whatsapps: [], meta_write_enabled: false };
  if (pathname.endsWith('/traffic/meta/credentials/status')) return { marketing_readiness_status: 'READY_TENANT_OAUTH', token_present: true, ads_read: true, permissions: { ads_read: true }, configured: true };
  if (pathname.endsWith('/traffic/meta/conversions')) return { conversion_engine: { source_options: [], default_allowed_sources: [] }, status: 'unknown' };
  if (pathname.endsWith('/traffic/meta/conversions/optimization-eligibility')) return { status: 'unknown', ready: false, blockers: [] };
  if (pathname.endsWith('/traffic/meta/conversions/history')) return { items: [] };
  if (pathname.endsWith('/traffic/meta/assets')) return { pages: [], whatsapps: [], dataset: null };
  if (pathname.endsWith('/api/admin/configuracoes-branding')) return branding;
  if (pathname.includes('/admin/empresas/') && pathname.endsWith('/usuarios')) return [];
  if (pathname.includes('/instagram/')) return [];
  if (pathname.includes('/prompt-optimizer/') || pathname.includes('/prompt-optimizer')) return {};
  return {};
}

async function main() {
  const output = process.env.UX_EVIDENCE_DIR || path.resolve('ux3-super-admin-evidence');
  fs.mkdirSync(output, { recursive: true });
  const result = { status: 'RUNNING', origin: ORIGIN, mode: 'SYNTHETIC_ALL_API_INTERCEPTED', viewports: selectedViewports, routes: [], apiRequests: [], blockedWrites: [], externalRequests: [], pageErrors: [], failedAssets: [] };
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  result.browser = browser.version();
  try {
    for (const viewport of selectedViewports) {
      const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: viewport.width, height: viewport.height }, locale: 'pt-BR', reducedMotion: 'reduce' });
      await context.routeWebSocket('**/*', socket => socket.close());
      await context.addInitScript(({ user }) => {
        localStorage.setItem('user', JSON.stringify(user));
        localStorage.setItem('token', 'synthetic-ux3-session');
        window.__uxRoots = [];
        const roots = new WeakSet();
        window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, renderers: new Map(), inject(renderer) { this.renderers.set(1, renderer); return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__uxRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
      }, { user: syntheticUser });
      await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== ORIGIN) { result.externalRequests.push(url.pathname); return route.abort(); }
        if (!url.pathname.startsWith('/api/')) return route.continue();
        result.apiRequests.push({ viewport: viewport.name, method: request.method(), path: url.pathname });
        if (request.method() !== 'GET') { result.blockedWrites.push({ viewport: viewport.name, method: request.method(), path: url.pathname }); return route.abort(); }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responseFor(url.pathname)) });
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => result.pageErrors.push({ viewport: viewport.name, message: error.message, stack: error.stack }));
      page.on('response', response => { if (/\.(js|css)(\?|$)/.test(response.url()) && response.status() >= 400) result.failedAssets.push({ viewport: viewport.name, status: response.status(), path: new URL(response.url()).pathname }); });
      for (const route of selectedRoutes) {
        console.log(`CHECK ${viewport.name}:${route.name}`);
        await page.goto(`${ORIGIN}${route.path}`, { waitUntil: 'domcontentloaded' });
        await page.locator('#admin-content').getByText(route.heading, { exact: false }).first().waitFor();
        if (route.name === 'integrations') {
          await page.getByText('Aplicativo Meta', { exact: true }).waitFor();
          await page.getByText('Credencial configurada', { exact: true }).waitFor();
          await page.getByRole('button', { name: 'Ver integrações' }).click();
          await page.getByRole('button', { name: 'Consultar status' }).click();
          await page.getByText('Leitura validada', { exact: true }).waitFor();
          await page.getByText('ads_read concedido', { exact: true }).waitFor();
          const metaReadPaths = result.apiRequests
            .filter(item => item.viewport === viewport.name && item.method === 'GET')
            .map(item => item.path);
          assert.equal(metaReadPaths.some(item => item.endsWith(`/empresas/${COMPANY_ID}/traffic/meta/config`)), true);
          assert.equal(metaReadPaths.some(item => item.endsWith(`/empresas/${COMPANY_ID}/traffic/meta/credentials/status`)), true);
        }
        if (route.path.includes('/empresas/')) {
          await page.waitForFunction(() => document.querySelectorAll('#admin-navigation a[aria-current="page"]').length === 1);
          const sectionLinks = page.locator('#admin-navigation a[aria-current="page"]');
          assert.equal(await sectionLinks.count(), 1, `${route.name}: exactly one company area should be active (found ${await sectionLinks.count()})`);
        }
        const metrics = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth, roots: window.__uxRoots }));
        assert.deepEqual(metrics.roots, ['root'], `${route.name}/${viewport.name}: React root must remain unique`);
        assert.ok(metrics.documentWidth <= metrics.width + 2, `${route.name}/${viewport.name}: horizontal overflow ${metrics.documentWidth}/${metrics.width}`);
        const screenshot = `${route.name}-${viewport.name}.png`;
        await page.screenshot({ path: path.join(output, screenshot), fullPage: true });
        result.routes.push({ name: route.name, viewport: viewport.name, path: route.path, ...metrics, screenshot });
      }
      if (viewport.width < 1024) {
        await page.goto(`${ORIGIN}/admin`, { waitUntil: 'domcontentloaded' });
        const trigger = page.getByRole('button', { name: 'Abrir menu' });
        await trigger.click();
        await page.getByRole('dialog', { name: 'Navegação Super Admin' }).waitFor();
        assert.equal(await page.locator('.fm-shell-main').evaluate(node => node.inert), true);
        await page.keyboard.press('Escape');
        await page.getByRole('dialog', { name: 'Navegação Super Admin' }).waitFor({ state: 'hidden' });
        await page.waitForFunction(() => document.activeElement === document.querySelector('.fm-menu-toggle'));
        assert.equal(await page.locator('.fm-shell-main').evaluate(node => node.inert), false);
      }
      await context.close();
    }
    assert.deepEqual(result.pageErrors, []);
    assert.deepEqual(result.failedAssets, []);
    assert.deepEqual(result.externalRequests, []);
    assert.deepEqual(result.blockedWrites, []);
    result.status = 'PASS';
  } catch (error) {
    result.status = 'FAIL';
    result.failure = error.stack;
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ status: result.status, browser: result.browser, routeCount: result.routes.length, apiRequestCount: result.apiRequests.length, blockedWrites: result.blockedWrites.length, pageErrors: result.pageErrors, externalRequests: result.externalRequests, failure: result.failure, output }, null, 2));
    await browser.close();
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
