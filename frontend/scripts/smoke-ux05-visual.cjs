// UX05 visual route gate. Synthetic API fixtures, no live auth or business actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const F = require('./fixtures-ux05-pages.cjs');
const { createHash } = require('node:crypto');
const fixtureMode = process.env.UX_DATA_MODE || 'normal';
const tenant = '00000000-0000-0000-0000-000000000002';
const fixedHorizon = '2026-10-11T12:00:00Z';
const leadId = '10000000-0000-0000-0000-000000000001';
const syntheticId = index => `10000000-0000-0000-0000-${String(index + 1).padStart(12, '0')}`;
function rate(numerator, denominator) { return { numerator, denominator, value: denominator ? numerator / denominator : null, state: denominator ? 'observed' : 'unknown' }; }
function syntheticDashboard(url) {
  const stage = url.searchParams.get('stage');
  const zero = url.searchParams.get('origin') === 'no_event';
  const total = zero ? 0 : stage === 'qualified' ? 45 : 60;
  return {
    window: { start: url.searchParams.get('start'), end: url.searchParams.get('end'), observed_at: url.searchParams.get('observed_at') || fixedHorizon, inactivity_hours: Number(url.searchParams.get('inactivity_hours') || 168), return_window_days: 7, entry_definition: 'Primeira conversa/inbound elegível', unit: 'CRMLead.id' },
    summary: { total_leads: total, conversations: total + 3, inbound_leads: zero ? 0 : 20, active: zero ? 0 : 5, inactive: zero ? 0 : total - 15, converted: 0, lost: 0, indeterminate: zero ? 0 : 10, closed_without_conversion: zero ? 0 : 3, revenue: { status: 'unknown', by_currency: [] } },
    stages: [{ key: 'entry', label: 'Entrada', order: 1, source: 'tag', reached_leads: total, state: 'observed', rate: rate(total, total) }, { key: 'qualified', label: 'Qualificado', order: 2, source: 'tag', reached_leads: zero ? 0 : 45, state: 'observed', rate: rate(zero ? 0 : 45, total) }, { key: 'purchase', label: 'Compra confirmada', order: 3, source: 'payment', reached_leads: null, state: 'unknown', rate: rate(null, 0) }],
    transitions: [{ from_stage: 'qualified', to_stage: 'entry', lead_count: 2, occurrences: 3, kind: 'return', rate: rate(2, 45) }, { from_stage: 'entry', to_stage: 'purchase', lead_count: 0, occurrences: 0, kind: 'skip', rate: rate(0, total) }],
    bottlenecks: zero ? [] : [{ key: 'inactive:qualified', stage_key: 'qualified', title: 'Inativos após qualificação', count: 30, denominator: 45, rate: rate(30, 45), duration_hours: { min: 168, median: 336, max: 672 }, coverage: { state: 'observed', denominator: 45 } }],
    series: [{ period: '2026-08', leads: total, active: 5, inactive: total - 15, converted: 0, indeterminate: 10 }], crm_snapshot: [{ key: 'crm-1', label: 'Em atendimento', current_leads: 12 }],
    followup: { sent_leads: 3, returned_leads: 1, numerator: 1, denominator: 3, rate: 1 / 3, window_days: 7, relation: 'temporal_not_causal' },
    attribution: { exact_leads: zero ? 0 : 10, partial_leads: zero ? 0 : 15, unknown_leads: zero ? 0 : total - 25, denominator: total, coverage: { value: total ? 10 / total : null, numerator: 10, denominator: total } },
    ads_comparison: { state: 'unknown', level: 'ad', rows: [], limitations: ['Cliques não são contatos locais.'] },
    coverage: { entry: { state: 'observed', denominator: total }, stage_history: { state: 'partial', history_complete: false }, financial: { state: 'unknown', unconfirmed_purchase_signals: 7 }, loss: { state: 'unknown' }, prompt_exposure: { state: 'unknown' }, limitations: ['Histórico parcial; sem motivos de abandono inferidos.'] },
    options: { stages: [{ key: 'entry', label: 'Entrada' }, { key: 'qualified', label: 'Qualificado' }], origins: [{ key: 'ctwa', label: 'CTWA comprovado' }, { key: 'no_event', label: 'Sem evidência sintética' }], campaigns: [{ id: '1234', label: 'Campanha sintética' }], ads: [{ id: '5678', label: 'Anúncio sintético' }] },
  };
}
function syntheticDrill(url) {
  const total = url.searchParams.get('bottleneck') ? 30 : url.searchParams.get('stage') === 'qualified' ? 45 : 60;
  const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 25);
  return { items: Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, index) => ({ lead_id: syntheticId(index + offset), display_name: `Contato sintético ${index + offset + 1}`, entry_at: '2026-08-01T12:00:00Z', last_activity_at: '2026-08-10T12:00:00Z', status: 'inactive', current_stage_key: 'qualified', current_stage_label: 'Qualificado', attribution: 'partial', reached_stage_keys: ['entry', 'qualified'] })), total, offset, limit, window: { observed_at: url.searchParams.get('observed_at') } };
}
function syntheticJourney(id) { return { lead_id: id, checked_at: fixedHorizon, items: [{ id: 'conversation:synthetic', category: 'conversation', type: 'conversation_started', title: 'Conversa registrada', status: 'observed', occurred_at: '2026-08-01T12:00:00Z', evidence: { source: 'channel_conversations', record_id: 'synthetic', relationship: 'explicit_lead_id' }, details: {} }], next_cursor: null, has_more: false, limit: 30, summary: { crm: { tags: [] }, funnel: { stages: [] }, activity: { inactive_seconds: 3600 }, attribution: {} }, coverage: { sources: [], categories: [], limitations: [] } }; }
const EXAMPLE = { id: 'price-conditions', title: 'Preço, condições e confiança', messages: [{ id: 'm1', role: 'user', text: 'Qual o preço, parcela em três vezes e tem garantia?' }], expected_intents: ['price', 'conditions', 'trust'] };
const RUN = { id: 'synthetic-run', created_at: '2026-10-12T12:00:00Z', source: 'synthetic', status: 'demo', ledger_persisted: true, classification: { primary_stage: { key: 'consideration', confidence: 0.8, abstained: false }, intents: ['price', 'conditions', 'trust'].map(key => ({ key, confidence: 0.9, evidence_ids: ['m1'] })), objections: [{ key: 'trust_objection', confidence: 0.7, evidence_ids: ['m1'] }], active_topics: ['price', 'conditions', 'trust'], signals: { interest: 0.8, readiness: 0.4, human_request: 0, risk: 0.2 }, abstention: { abstained: false }, evidence: [{ message_id: 'm1', excerpt: EXAMPLE.messages[0].text }], tag_suggestions: [{ tag_id: 'synthetic-tag', tag_name: 'Tag sintética existente', reason: 'Condições mencionadas', hypothetical: true }] }, metrics: { latency_ms: null, cost_usd: null, input_tokens: null } };
const STATUS = { schema_version: 'a06.v1', mode: 'shadow', enabled: true, provider: { configured: false, enabled: false, authorized_real: false, availability: 'not_configured' }, ledger: { available: true, retention_days: 7 }, budget: { daily_usd: 0, reserved_usd: 0, measured_usd: null }, coverage: { requested: 1, evaluated: 0, abstained: 0, errors: 0, denominator: 'retained_runs' }, limitations: ['Exemplos não validam o provider real.'] };

const brand = 'Clínica Horizonte · demonstração';
const logo = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="48"><rect width="180" height="48" rx="6" fill="#ffffff"/><text x="12" y="30" fill="#235568" font-family="sans-serif" font-size="17">Horizonte</text></svg>').toString('base64');
const companies = [{ id: tenant, nome_empresa: 'Clínica Horizonte', area_atuacao: 'Saúde e bem-estar' }, { id: '00000000-0000-0000-0000-000000000004', nome_empresa: 'Estúdio Aurora', area_atuacao: 'Arquitetura' }, { id: '00000000-0000-0000-0000-000000000006', nome_empresa: 'Loja Cedro', area_atuacao: 'Varejo' }];
const leads = [{ id: leadId, nome_contato: 'Contato sintético A', telefone_contato: 'SYNTHETIC_A', status_atendimento: 'aberto', ia_ativa: true, tags: [{ id: 'tag-1', nome: 'Interesse' }], ultima_mensagem: 'Qual o valor e quais são as condições?', criado_em: fixedHorizon }, { id: '10000000-0000-0000-0000-000000000002', nome_contato: 'Contato sintético B', telefone_contato: 'SYNTHETIC_B', status_atendimento: 'aberto', ia_ativa: true, tags: [], ultima_mensagem: 'Gostaria de conhecer o serviço.', criado_em: fixedHorizon }];
const messages = [{ id: 'msg-1', texto: 'Olá! Gostaria de saber o valor e as condições de pagamento.', from_me: false, tipo_mensagem: 'text', criado_em: '2026-10-11T11:00:00Z' }, { id: 'msg-2', texto: 'Olá! Vou ajudar com as informações do serviço.', from_me: true, tipo_mensagem: 'text', criado_em: '2026-10-11T11:01:00Z' }];
const adminConfig = { provider: 'typesafe', model: 'jev-1.13.0', daily_budget_usd: '0', enabled: false, mode: 'shadow', timeout_seconds: 8, has_key: false, configured: false, status: 'not_configured', tested_at: null, credential_storage_available: true };
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

// Concrete examples cover every declared Tenant route, including aliases,
// redirect and parameterized campaign URLs. No action/save/send is clicked.
const routePaths = [
  '/painel', '/painel/funil', '/painel/inteligencia', '/painel/agentes',
  '/painel/followups', '/painel/crm', '/painel/rag', '/painel/agenda',
  '/painel/simulador', '/painel/inbox', '/painel/chat', '/painel/conversas',
  '/painel/conexoes', '/painel/instagram', '/painel/trafego/meta',
  '/painel/trafego/meta/conversoes', '/painel/integracoes', '/painel/transferencias',
  '/painel/campanhas', '/painel/campanhas/templates', '/painel/campanhas/templates/novo',
  '/painel/campanhas/templates/synthetic-template', '/painel/campanhas/nova',
  '/painel/campanhas/synthetic-campaign', '/painel/campanhas/publicos',
  '/painel/campanhas/resultados', '/painel/campanhas/resultados/custos',
  '/painel/campanhas/resultados/conversoes', '/painel/templates-whatsapp',
  '/painel/tags', '/painel/unidades', '/painel/conversoes',
];
const viewports = [
  { name: 'desktop', width: 1440, height: 1000 },
  { name: 'notebook', width: 1280, height: 800 },
  { name: 'tablet', width: 820, height: 1180 },
  { name: 'mobile', width: 390, height: 844 },
];
const selectedViewports = process.env.UX_VIEWPORTS ? viewports.filter(viewport => process.env.UX_VIEWPORTS.split(',').includes(viewport.name)) : viewports;
assert.ok(selectedViewports.length, 'No valid viewport selected');
const screenshotRoutes = new Set(routePaths);
const userFixture = { id: '00000000-0000-0000-0000-000000000005', empresa_id: tenant, role: 'admin_empresa', nome: 'Operador sintético' };
async function main() {
  const phase = process.env.UX_PHASE || 'after', before = phase === 'before';
  const origin = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:18838').origin;
  const output = process.env.UX_EVIDENCE_DIR || path.resolve('ux05-evidence');
  fs.mkdirSync(output, { recursive: true });
  const result = { phase, origin, mode: 'ALL_API_INTERCEPTED_SYNTHETIC', dataset: 'ux05.v1', fixtureMode, scriptSha256:createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),fixtureSha256:createHash('sha256').update(fs.readFileSync(path.join(__dirname,'fixtures-ux05-pages.cjs'))).digest('hex'),sourceSha:process.env.UX_SOURCE_SHA || '73084772ed5b0c03b4b2659f2208dfad617447ec', routes: [], checks: [], errors: [], failedAssets: [], externalRequests: [], unexpectedWrites: [], apiRequests: [], status: 'RUNNING' };
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  result.browser = browser.version();
  async function createSession(viewport, role = 'admin_empresa', impersonation = false) {
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: viewport.width, height: viewport.height }, locale: 'pt-BR', timezoneId: 'UTC', reducedMotion: 'reduce' });
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname.replace(/\/$/, '');
      if (url.origin !== origin) { result.externalRequests.push({ path: pathname, kind: 'blocked-external' }); return route.abort(); }
      if (!url.pathname.startsWith('/api/')) return route.continue();
      result.apiRequests.push({ path: pathname, method: request.method(), role });
      if (request.method() !== 'GET') { result.unexpectedWrites.push({ path: pathname, method: request.method() }); return route.abort(); }
      let data = {};
      if (pathname.endsWith('/configuracoes-branding') || pathname === '/api/admin/configuracoes') data = { nome_sistema: brand };
      else if (pathname === '/api/empresas') data = companies;
      else if (pathname === '/api/admin/modelos-ia') data = [];
      else if (pathname.endsWith('/jev/status')) data = STATUS;
      else if (pathname.endsWith('/jev/examples')) data = { items: [EXAMPLE] };
      else if (pathname.endsWith('/jev/runs')) data = { items: [RUN], total: 1, limit: 50 };
      else if (pathname.includes('/jev/')) data = RUN;
      else if (pathname.includes('/analytics/meta-sender-operations')) data = operationsFixture(url, fixtureMode === "empty");
      else if (pathname.includes('/commercial-funnel')) data = pathname.endsWith('/leads') ? syntheticDrill(url) : syntheticDashboard(fixtureMode==='empty'?new URL(url.href+(url.search?'&':'?')+'origin=no_event'):url);
      else if (pathname.endsWith('/journey')) data = syntheticJourney(leadId);
      else if (pathname.endsWith('/dashboard/stats')) data = { total_mensagens: 147 };
      else if (pathname.endsWith('/whatsapp/status')) data = { status: 'connected', conectado: true, provider: 'meta_whatsapp', display_phone_number: 'Número sintético' };
      else if (pathname.endsWith('/ia-config')) data = { status_openai: 'ok', openai_configurada: false, telefone_notificacao: '' };
      else if (pathname.endsWith('/crm')) data = { etapas: [{ id: 'stage-1', nome: 'Qualificação', leads }] };
      else if (pathname.endsWith('/inbox')) data = leads;
      else if (/\/inbox\/SYNTHETIC_[AB]$/.test(pathname)) data = messages;
      else if (pathname.endsWith('/ai-state')) data = { manual_enabled: true, processing_state: 'allowed' };
      else if (pathname.endsWith('/foto')) data = { foto_url: null };
      else if (pathname.endsWith('/agenda')) data = { config: null, agendamentos: [] };
      else if (pathname.includes('/instagram/')) data = { items: [], connections: [] };
      else if (pathname.endsWith('/meta/templates')) data = [];
      else if (pathname.endsWith('/campaign-central/campaigns')) data = [];
      else if (pathname.includes('/campaign-central/audiences/')) data = [];
      else if (/\/(destinos|grupos|oficiais|conexoes|agentes|unidades|transferencias|followups|historico|rag)$/.test(pathname)) data = [];
      const override = F.response(url, fixtureMode);
      if (fixtureMode === 'error' && !pathname.includes('configuracoes-branding') && !pathname.includes('/configuracoes')) return route.fulfill({status:503,json:{detail:'Serviço sintético temporariamente indisponível'}});
      if (fixtureMode === 'loading') await new Promise(resolve=>setTimeout(resolve,800));
      return route.fulfill({ json: override === undefined ? data : override });
    });
    await context.addInitScript(({ user, impersonation }) => {
      if (!localStorage.getItem('user')) {
        localStorage.setItem('user', JSON.stringify(user)); localStorage.setItem('token', 'synthetic-ux02-browser-only');
        if (impersonation) {
          localStorage.setItem('impersonating', 'true'); localStorage.setItem('impersonating_empresa', 'Clínica Horizonte sintética');
          localStorage.setItem('impersonated_empresa_id', user.empresa_id);
          localStorage.setItem('original_user', JSON.stringify({ id: 'synthetic-admin', nome: 'Admin sintético', role: 'super_admin', empresa_id: null }));
          localStorage.setItem('original_token', 'synthetic-original-admin-only');
        }
      }
      const roots = new WeakSet(); window.__uxRoots = [];
      window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__uxRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
    }, { user: { ...userFixture, role }, impersonation });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    page.on('pageerror', error => result.errors.push({ role, viewport: viewport.name, route: new URL(page.url()).pathname, error: error.message }));
    page.on('dialog', async dialog => { if(fixtureMode==='error'){result.expectedErrorDialogs ||= [];result.expectedErrorDialogs.push({role,message:dialog.message()});}else result.errors.push({role,error:dialog.message()});await dialog.dismiss(); });
    page.on('response', response => { if (/\.(js|css)(\?|$)/.test(response.url()) && response.status() >= 400) result.failedAssets.push({ role, status: response.status() }); });
    return { context, page };
  }
  async function openMenu(page, viewport) {
    if (viewport.width < 1024) {
      await page.getByRole('button', { name: 'Abrir menu', exact: true }).click(); await page.getByRole('dialog').waitFor();
    }
  }
  async function closeMenu(page, viewport) {
    if (viewport.width < 1024) {
      await page.keyboard.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement === document.querySelector('.fm-menu-toggle'), null, { timeout: 12000 });
    }
  }
  async function activeModuleReady(page, href) {
    // History commits before the concurrent/lazy route. Wait for the actual
    // destination context instead of reading a prior page after waitForURL.
    await page.waitForFunction(href => document.querySelector('#tenant-navigation nav[aria-label="Menu principal"] a[aria-current="page"]')?.getAttribute('href') === href, href, { timeout: 12000 });
  }
  async function contentReady(page, routePath) {
    await page.locator('#tenant-content').waitFor();
    if (routePath === '/painel/funil' && fixtureMode !== 'error') await page.getByTestId('funnel-total').waitFor();
    if (routePath === '/painel/inteligencia') await page.getByTestId('jev-diagnostics').waitFor();
    await page.waitForTimeout(100);
    assert.ok((await page.locator('#tenant-content').innerText()).trim().length > 20, `Missing route content: ${routePath}`);
  }
  try {
    for (const viewport of selectedViewports) {
      const { context, page } = await createSession(viewport);
      for (const routePath of routePaths.filter(p=>!process.env.UX05_PART || F.routes[process.env.UX05_PART]?.includes(p))) {
        try {
        console.log(JSON.stringify({progress:viewport.name,route:routePath,mode:fixtureMode}));
        if(fixtureMode==='loading'){await page.goto(origin+routePath,{waitUntil:'domcontentloaded'});await page.waitForTimeout(160);await page.screenshot({path:path.join(output,`loading-${routePath.replaceAll('/','-')}-${viewport.name}.png`)});await page.waitForLoadState('networkidle');}else{await page.goto(origin+routePath,{waitUntil:'networkidle'});}
        await contentReady(page, routePath);
        const expectedPath = routePath === '/painel/templates-whatsapp' ? '/painel/campanhas/templates' : routePath;
        assert.equal(new URL(page.url()).pathname, expectedPath, 'Existing URL/redirect changed');
        if (['/painel/chat', '/painel/inbox', '/painel/conversas'].includes(routePath)) {
          await page.getByText('Contato sintético A', { exact: true }).click(); await page.getByPlaceholder('Digite uma mensagem').waitFor();
          const composer = await page.getByPlaceholder('Digite uma mensagem').boundingBox(); assert.ok(composer.y >= 0 && composer.y + composer.height <= viewport.height + 2, 'Inbox composer outside viewport');
        }
        const metrics = await page.evaluate(() => ({ documentWidth: document.documentElement.scrollWidth, width: innerWidth, rootCount: window.__uxRoots.length, rootContainers: window.__uxRoots, mainWidth: document.querySelector('main').getBoundingClientRect().width, canvas: getComputedStyle(document.body).backgroundColor, sidebar: getComputedStyle(document.querySelector('aside')).backgroundColor }));
        assert.equal(metrics.rootCount, 1, 'React root duplicated');
        let activeHref = null;
        if (!before) {
          await openMenu(page, viewport);
          const nav = page.locator('#tenant-navigation nav[aria-label="Menu principal"]');
          assert.equal(await nav.locator('a[aria-current="page"]').count(), 1, 'Exactly one active module required');
          const active = nav.locator('a[aria-current="page"]'); assert.equal(await active.isVisible(), true, 'Direct URL must reveal active module'); activeHref = await active.getAttribute('href');
          const groupButton = active.locator('xpath=ancestor::*[@data-navigation-group]').getByRole('button').first();
          assert.equal(await groupButton.getAttribute('aria-expanded'), 'true');
          assert.ok((await page.locator('header .fm-topbar-title').innerText()).trim().length > 0, 'Context title missing');
          const breadcrumb = page.locator('nav[aria-label="Breadcrumb"]');
          assert.equal(await breadcrumb.count(), 1, 'Context breadcrumb missing');
          const fullscreenInbox = ['/painel/chat', '/painel/inbox', '/painel/conversas'].includes(routePath) && viewport.width >= 1024;
          assert.equal(await breadcrumb.isVisible(), !fullscreenInbox, 'Breadcrumb visibility must preserve fullscreen Inbox header policy');
          await closeMenu(page, viewport);
          assert.ok(metrics.mainWidth <= viewport.width, 'Shell overflows viewport');
          assert.ok(metrics.documentWidth <= viewport.width + 2, `${routePath} ${viewport.name} horizontal document overflow: ${metrics.documentWidth}`);
        }
        const record = { route: routePath, resolvedPath: expectedPath, viewport: viewport.name, ...metrics, activeHref, directUrl: true, checks: ['rendered-content', 'single-root', 'url-preserved'] };
        record.visibleTextHash = createHash('sha256').update(await page.locator('#tenant-content').innerText()).digest('hex');
        record.localState=await F.exercise(page,routePath,{before,fixtureMode,viewport,output});
        if (screenshotRoutes.has(routePath)) {
          record.screenshot = `${routePath.replace(/^\//, '').replaceAll('/', '-')}-${viewport.name}.png`;
          await page.screenshot({ path: path.join(output, record.screenshot), fullPage: !routePath.includes('/chat') });
        }
        await page.reload({ waitUntil: 'networkidle' }); await contentReady(page, expectedPath);
        assert.equal(new URL(page.url()).pathname, expectedPath); record.reload = true;
        result.routes.push(record);
        } catch (error) {
          if (!before) throw error;
          const screenshot = `legacy-failure-${routePath.replaceAll('/', '-')}-${viewport.name}.png`;
          await page.screenshot({ path: path.join(output, screenshot), fullPage: true });
          result.routes.push({ route: routePath, viewport: viewport.name, directUrl: true, status: 'BASELINE_FAILURE', failure: error.message, screenshot });
        }
      }
      if (!before && !process.env.UX05_PART && fixtureMode==='normal') {
        await page.goto(origin + '/painel', { waitUntil: 'networkidle' }); await contentReady(page, '/painel');
        await openMenu(page, viewport);
        const nav = page.locator('#tenant-navigation nav[aria-label="Menu principal"]');
        const groups = nav.locator('[data-navigation-group]'); assert.equal(await groups.count(), 5, 'Five task groups required');
        const allHrefs = await nav.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
        assert.equal(new Set(allHrefs).size, allHrefs.length, 'Duplicate menu destinations');
        for (const href of allHrefs) {
          if (viewport.width < 1024 && !(await page.getByRole('dialog').isVisible())) await openMenu(page, viewport);
          const link = nav.locator(`a[href="${href}"]`), group = link.locator('xpath=ancestor::*[@data-navigation-group]');
          const toggle = group.getByRole('button').first(); if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
          await link.click(); await page.waitForURL(url => url.pathname === href); await activeModuleReady(page, href); await contentReady(page, href);
          if (viewport.width < 1024) assert.equal(await page.getByRole('dialog').count(), 0, 'Link did not close drawer');
          assert.equal(await nav.locator('a[aria-current="page"]').getAttribute('href'), href);
        }
        result.checks.push(`${viewport.name}:all-${allHrefs.length}-menu-links-clicked`);
        await page.goto(origin + '/painel', { waitUntil: 'networkidle' }); await openMenu(page, viewport);
        const inactive = nav.locator('[data-navigation-group]').filter({ hasNot: page.locator('a[aria-current="page"]') }).first();
        const inactiveToggle = inactive.getByRole('button').first(), inactiveId = await inactive.getAttribute('data-navigation-group');
        if (await inactiveToggle.getAttribute('aria-expanded') !== 'true') await inactiveToggle.click();
        await inactiveToggle.focus(); await page.keyboard.press('Enter'); assert.equal(await inactiveToggle.getAttribute('aria-expanded'), 'false');
        const controlled = await inactiveToggle.getAttribute('aria-controls'); assert.ok(controlled); assert.equal(await page.locator(`#${controlled}`).isVisible(), false);
        await closeMenu(page, viewport); await page.reload({ waitUntil: 'networkidle' }); await openMenu(page, viewport);
        assert.equal(await nav.locator(`[data-navigation-group="${inactiveId}"]`).getByRole('button').first().getAttribute('aria-expanded'), 'false', 'Collapse preference did not persist');
        await closeMenu(page, viewport);
        // Actual router links with browser back and forward, without history injection.
        await openMenu(page, viewport);
        const activeToggle = nav.locator('a[aria-current="page"]').locator('xpath=ancestor::*[@data-navigation-group]').getByRole('button').first();
        if (await activeToggle.getAttribute('aria-expanded') !== 'false') await activeToggle.click();
        const crm = nav.locator('a[href="/painel/crm"]');
        const crmToggle = crm.locator('xpath=ancestor::*[@data-navigation-group]').getByRole('button').first(); if (await crmToggle.getAttribute('aria-expanded') !== 'true') await crmToggle.click();
        assert.equal(await activeToggle.getAttribute('aria-expanded'), 'false', 'Toggling another group erased current manual collapse');
        await crm.click(); await page.waitForURL('**/painel/crm');
        // Browser history changes before React's concurrent/lazy route commit.
        // Exercise Back only after the destination has actually rendered.
        await contentReady(page, '/painel/crm');
        await activeModuleReady(page, '/painel/crm');
        await page.goBack(); await page.waitForURL('**/painel'); await openMenu(page, viewport);
        await page.waitForFunction(() => {
          const current = document.querySelector('#tenant-navigation nav[aria-label="Menu principal"] a[aria-current="page"]');
          return current?.getAttribute('href') === '/painel' && current.closest('[data-navigation-group]')?.querySelector('button')?.getAttribute('aria-expanded') === 'true';
        }, null, { timeout: 12000 });
        assert.equal(await nav.locator('a[aria-current="page"]').getAttribute('href'), '/painel', 'Back did not commit Dashboard navigation');
        await closeMenu(page, viewport); await page.goForward(); await page.waitForURL('**/painel/crm'); await contentReady(page, '/painel/crm');
        await activeModuleReady(page, '/painel/crm');
        result.checks.push(`${viewport.name}:collapse-persistence-keyboard-back-forward`);
        if (viewport.width < 1024) {
          const menu = page.getByRole('button', { name: 'Abrir menu', exact: true }); await menu.focus(); await page.keyboard.press('Enter');
          const dialog = page.getByRole('dialog'); await dialog.waitFor(); await page.waitForFunction(() => !!document.activeElement.closest('[role="dialog"]'));
          assert.equal(await dialog.getAttribute('aria-modal'), 'true'); assert.equal(await page.locator('main').evaluate(node => node.inert), true);
          assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
          for (let count = 0; count < 90; count++) {
            await page.keyboard.press(count < 45 ? 'Tab' : 'Shift+Tab');
            assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role="dialog"]') && document.activeElement.getClientRects().length > 0), true, 'Drawer focus escaped or entered hidden group');
          }
          await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
          await page.waitForFunction(() => document.activeElement === document.querySelector('.fm-menu-toggle'), null, { timeout: 12000 });
          assert.equal(await menu.evaluate(node => document.activeElement === node), true);
          result.checks.push(`${viewport.name}:drawer-keyboard-visible-focus-trap-Escape-return`);
        }
        await page.goto(origin + '/painel/funil', { waitUntil: 'networkidle' });
        await page.getByLabel('Etapa comercial', { exact: true }).selectOption('qualified'); await page.getByRole('button', { name: 'Aplicar filtros', exact: true }).click();
        await page.getByTestId('funnel-total').filter({ hasText: '45' }).waitFor();
        await page.getByRole('button', { name: 'Ver contatos', exact: true }).click();
        await page.getByRole('link', { name: 'Abrir jornada A04', exact: true }).first().click();
        await page.getByTestId('commercial-journey').getByTestId('journey-events').waitFor();
        await page.getByRole('link', { name: '← Voltar ao funil com os filtros', exact: true }).click();
        await page.getByTestId('funnel-total').filter({ hasText: '45' }).waitFor(); assert.equal(await page.getByLabel('Etapa comercial', { exact: true }).inputValue(), 'qualified');
        result.checks.push(`${viewport.name}:Funil-Jornada-return-filter-state`);
        for (const alias of ['/painel/chat/', '/painel/conversas/', '/painel/campanhas/templates/?synthetic=1']) {
          await page.goto(origin + alias, { waitUntil: 'networkidle' }); await openMenu(page, viewport);
          assert.equal(await nav.locator('a[aria-current="page"]').count(), 1, 'Trailing slash/query lost active context');
          await closeMenu(page, viewport);
        }
        result.checks.push(`${viewport.name}:trailing-slash-query-alias-active-context`);
        const otherTenant = '00000000-0000-0000-0000-000000000099';
        await page.evaluate(({ tenant, otherTenant }) => {
          const prefix = 'agenteos:tenant-navigation:v1:';
          localStorage.setItem(`${prefix}${tenant}:admin_empresa`, JSON.stringify({ version: 1, collapsed: ['attendance'] }));
          localStorage.setItem(`${prefix}${otherTenant}:admin_empresa`, JSON.stringify({ version: 1, collapsed: [] }));
          const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = otherTenant; localStorage.setItem('user', JSON.stringify(user));
        }, { tenant, otherTenant });
        await page.goto(origin + '/painel', { waitUntil: 'networkidle' }); await openMenu(page, viewport);
        assert.equal(await nav.locator('[data-navigation-group=attendance]').getByRole('button').getAttribute('aria-expanded'), 'true', 'Another tenant inherited collapsed preferences');
        await closeMenu(page, viewport);
        await page.evaluate(tenant => { const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = tenant; localStorage.setItem('user', JSON.stringify(user)); }, tenant);
        await page.reload({ waitUntil: 'networkidle' }); await openMenu(page, viewport);
        assert.equal(await nav.locator('[data-navigation-group=attendance]').getByRole('button').getAttribute('aria-expanded'), 'false', 'Original tenant preference lost after switching');
        await closeMenu(page, viewport);
        assert.equal(await page.evaluate(tenant => localStorage.getItem(`agenteos:tenant-navigation:v1:${tenant}:admin_empresa`), tenant), JSON.stringify({ version: 1, collapsed: ['attendance'] }));
        result.checks.push(`${viewport.name}:tenant-scoped-collapse-preferences-survive-switch`);
      }
      await context.close();
      if (!before) {
        const reviewer = await createSession(viewport, 'meta_app_reviewer');
        for (const forbidden of routePaths.filter(routePath => !['/painel/conexoes', '/painel/tags'].includes(routePath))) {
          await reviewer.page.goto(origin + forbidden, { waitUntil: 'networkidle' }); await reviewer.page.waitForURL('**/painel/conexoes');
        }
        await openMenu(reviewer.page, viewport);
        const reviewerHrefs = await reviewer.page.locator('#tenant-navigation nav[aria-label="Menu principal"] a').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
        assert.deepEqual(reviewerHrefs.sort(), ['/painel/conexoes', '/painel/tags']);
        await reviewer.page.locator('#tenant-navigation a[href="/painel/tags"]').click(); await reviewer.page.waitForURL('**/painel/tags'); await reviewer.page.reload({ waitUntil: 'networkidle' }); assert.equal(new URL(reviewer.page.url()).pathname, '/painel/tags');
        assert.equal(result.apiRequests.filter(item => item.role === 'meta_app_reviewer' && /\/(jev|commercial-funnel|meta-sender-operations)(\/|$)/.test(item.path)).length, 0, 'Reviewer fetched protected data');
        result.checks.push(`${viewport.name}:reviewer-only-two-menu-URLs-protected-redirect`); await reviewer.context.close();
        const impersonated = await createSession(viewport, 'admin_empresa', true);
        await impersonated.page.goto(origin + '/painel/chat', { waitUntil: 'networkidle' }); await impersonated.page.getByText('Contato sintético A', { exact: true }).click();
        await impersonated.page.getByText('Acessando como:', { exact: false }).waitFor();
        const composer = await impersonated.page.getByPlaceholder('Digite uma mensagem').boundingBox(); assert.ok(composer.y + composer.height <= viewport.height + 2);
        await impersonated.page.getByRole('button', { name: 'Sair e Voltar', exact: true }).click(); await impersonated.page.waitForURL('**/admin');
        const restored = await impersonated.page.evaluate(() => ({ user: JSON.parse(localStorage.getItem('user')), token: localStorage.getItem('token'), impersonating: localStorage.getItem('impersonating'), originalUser: localStorage.getItem('original_user') }));
        assert.equal(restored.user.role, 'super_admin'); assert.equal(restored.token, 'synthetic-original-admin-only'); assert.equal(restored.impersonating, null); assert.equal(restored.originalUser, null);
        result.checks.push(`${viewport.name}:impersonation-Inbox-return-restores-original-session`); await impersonated.context.close();
      }
    }
    if (!before) assert.deepEqual(result.errors, []); assert.deepEqual(result.failedAssets, []); assert.deepEqual(result.externalRequests, []); assert.deepEqual(result.unexpectedWrites, []);
    result.checks.push(`${routePaths.length}-declared-tenant-route-examples-covered-at-${selectedViewports.length}-widths-with-direct-reload`, 'every-api-fulfilled-locally', 'external-and-WebSockets-blocked', 'no-provider-message-Meta-tags-write');
    result.status = before && result.errors.length ? 'BASELINE_RECORDED_WITH_EXISTING_FAILURES' : 'PASS';
  } catch (error) { result.status = 'FAIL'; result.failure = error.stack; process.exitCode = 1; }
  finally {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ status: result.status, phase, routes: result.routes.length, checks: result.checks.length, errors: result.errors, failure: result.failure, output }, null, 2)); await browser.close();
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
