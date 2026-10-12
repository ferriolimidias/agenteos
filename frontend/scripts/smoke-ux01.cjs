// UX01 visual comparison: immutable synthetic data; every API and socket is isolated.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
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
const routes = [{ name: 'admin', url: '/admin', role: 'super_admin' }, { name: 'painel', url: '/painel' }, { name: 'funil', url: '/painel/funil' }, { name: 'inteligencia', url: '/painel/inteligencia' }, { name: 'jev-integracao', url: '/admin/configuracoes/integracoes/jev', role: 'super_admin' }, { name: 'inbox', url: '/painel/chat' }];
async function main() {
  const phase = process.env.UX_PHASE || 'after';
  const before = phase === 'before';
  const origin = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:18828').origin;
  const output = process.env.UX_EVIDENCE_DIR || path.resolve('ux01-evidence');
  fs.mkdirSync(output, { recursive: true });
  const result = { phase, origin, mode: 'ALL_API_INTERCEPTED_SYNTHETIC', dataset: 'ux01.v1', routes: [], errors: [], failedAssets: [], externalRequests: [], unexpectedWrites: [], apiRequests: [], checks: [], status: 'RUNNING' };
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  result.browser = browser.version();
  try {
    for (const viewport of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'mobile', width: 390, height: 844 }]) {
      for (const spec of routes) {
        const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: viewport.width, height: viewport.height }, locale: 'pt-BR', timezoneId: 'UTC', reducedMotion: 'reduce' });
        await context.routeWebSocket('**/*', socket => socket.close());
        let brandMode = 'name';
        await context.route('**/*', async route => {
          const request = route.request(), url = new URL(request.url()), pathname = url.pathname.replace(/\/$/, '');
          if (url.origin !== origin) { result.externalRequests.push({ route: spec.name, kind: 'blocked-external' }); return route.abort(); }
          if (!url.pathname.startsWith('/api/')) return route.continue();
          result.apiRequests.push({ route: spec.name, path: pathname, method: request.method() });
          const syntheticAnalyze = pathname.endsWith('/jev/analyze') && request.method() === 'POST' && request.postDataJSON()?.example_id === EXAMPLE.id && !request.postDataJSON()?.use_provider;
          if (request.method() !== 'GET' && !syntheticAnalyze) { result.unexpectedWrites.push({ path: pathname, method: request.method() }); return route.abort(); }
          let data = {};
          if (pathname.endsWith('/configuracoes-branding') || pathname === '/api/admin/configuracoes') data = brandMode === 'empty' ? {} : { nome_sistema: brand, logo_base64: brandMode === 'logo' ? logo : '' };
          else if (pathname === '/api/empresas') data = companies;
          else if (pathname === '/api/admin/modelos-ia') data = [{ id: 'gpt-4o-mini', nome: 'GPT-4o mini' }];
          else if (pathname === '/api/admin/workers/status') data = { ultima_execucao: fixedHorizon, leads_processados_hoje: 24, erros_recentes: 0, status_lock: 'Livre', enabled: true };
          else if (pathname.includes('/integrations/jev')) data = adminConfig;
          else if (pathname.endsWith('/jev/status')) data = STATUS;
          else if (pathname.endsWith('/jev/examples')) data = { items: [EXAMPLE] };
          else if (pathname.endsWith('/jev/runs')) data = { items: [RUN], total: 1, limit: 50 };
          else if (pathname.includes('/jev/')) data = RUN;
          else if (pathname.includes('/commercial-funnel')) data = pathname.endsWith('/leads') ? syntheticDrill(url) : syntheticDashboard(url);
          else if (pathname.endsWith('/journey')) data = syntheticJourney(leadId);
          else if (pathname.endsWith('/dashboard/stats')) data = { total_mensagens: 147 };
          else if (pathname.endsWith('/whatsapp/status')) data = { status: 'connected', conectado: true, provider: 'meta_whatsapp', display_phone_number: 'Número sintético' };
          else if (pathname.endsWith('/ia-config')) data = { status_openai: 'ok', openai_configurada: false, telefone_notificacao: '' };
          else if (pathname.endsWith('/crm')) data = { etapas: [{ id: 'stage-1', nome: 'Qualificação', leads }] };
          else if (pathname.endsWith('/inbox')) data = leads;
          else if (/\/inbox\/SYNTHETIC_[AB]$/.test(pathname)) data = messages;
          else if (pathname.endsWith('/ai-state')) data = { manual_enabled: true, processing_state: 'allowed' };
          else if (pathname.endsWith('/foto')) data = { foto_url: null };
          else if (/\/(destinos|grupos|oficiais|conexoes|agentes|unidades|transferencias|followups)$/.test(pathname)) data = [];
          return route.fulfill({ json: data });
        });
        await context.addInitScript(({ tenant, role }) => {
          localStorage.setItem('user', JSON.stringify({ id: '00000000-0000-0000-0000-000000000005', empresa_id: tenant, role, nome: 'Operador sintético' }));
          localStorage.setItem('token', 'synthetic-ux01-browser-only');
          const roots = new WeakSet(); window.__uxRoots = [];
          window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__uxRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
        }, { tenant, role: spec.role || 'admin_empresa' });
        const page = await context.newPage();
        page.setDefaultTimeout(12000);
        page.on('pageerror', error => result.errors.push({ route: spec.name, error: error.message }));
        page.on('response', response => { if (/\.(js|css)(\?|$)/.test(response.url()) && response.status() >= 400) result.failedAssets.push({ route: spec.name, status: response.status() }); });
        await page.goto(origin + spec.url, { waitUntil: 'networkidle' });
        if (spec.name === 'admin') await page.getByText('Clínica Horizonte', { exact: true }).waitFor();
        if (spec.name === 'painel') await page.getByText('Contato sintético A', { exact: true }).waitFor();
        if (spec.name === 'funil') { await page.getByTestId('funnel-total').waitFor(); assert.equal(await page.getByTestId('funnel-total').innerText(), '60'); }
        if (spec.name === 'inteligencia') { await page.getByTestId('jev-diagnostics').getByRole('button', { name: 'Analisar exemplo sintético' }).click(); await page.getByTestId('jev-detail').getByText('Resultado SHADOW', { exact: true }).waitFor(); }
        if (spec.name === 'jev-integracao') { await page.getByLabel('Empresa', { exact: true }).selectOption(tenant); await page.getByLabel('Chave da API (nova ou substituição)').waitFor(); assert.equal(await page.getByLabel('Chave da API (nova ou substituição)').getAttribute('type'), 'password'); assert.equal(await page.getByLabel('Chave da API (nova ou substituição)').inputValue(), ''); }
        if (spec.name === 'inbox') { await page.getByText('Contato sintético A', { exact: true }).click(); await page.getByPlaceholder('Digite uma mensagem').waitFor(); await page.getByText(messages[0].texto, { exact: true }).waitFor(); }
        await page.waitForTimeout(250);
        const metrics = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth, rootCount: window.__uxRoots.length, rootContainers: window.__uxRoots, headings: [...document.querySelectorAll('h1')].map(node => node.textContent), sidebarBackground: getComputedStyle(document.querySelector('aside')).backgroundColor, canvasBackground: getComputedStyle(document.body).backgroundColor }));
        assert.equal(metrics.rootCount, 1, 'React root must remain unique');
        if (!before) assert.ok(metrics.documentWidth <= metrics.width + 2, `${spec.name} ${viewport.name} document overflow ${metrics.documentWidth}`);
        if (spec.name === 'inbox' && !before) {
          const composer = await page.getByPlaceholder('Digite uma mensagem').boundingBox(); assert.ok(composer && composer.y >= 0 && composer.y + composer.height <= viewport.height + 2, 'Inbox composer outside viewport');
        }
        const screenshot = `${spec.name}-${viewport.name}.png`;
        await page.screenshot({ path: path.join(output, screenshot), fullPage: spec.name !== 'inbox' });
        const record = { route: spec.url, viewport: viewport.name, ...metrics, screenshot, checks: ['synthetic-content-rendered', 'unique-react-root'] };
        if (viewport.name === 'mobile' && !before) {
          const menu = page.getByRole('button', { name: /abrir.*(menu|navega)|menu.*navega/i });
          await menu.waitFor(); await menu.focus(); await page.keyboard.press('Enter');
          const dialog = page.getByRole('dialog'); await dialog.waitFor();
          assert.equal(await dialog.getAttribute('aria-modal'), 'true');
          await page.waitForFunction(() => !!document.activeElement.closest('[role="dialog"]'));
          assert.equal(await page.locator('main').evaluate(node => node.inert), true, 'Background must be inert');
          const focusOutline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
          assert.notEqual(focusOutline, 'none', 'Keyboard focus must be visible');
          await page.keyboard.press('Shift+Tab');
          assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role="dialog"]')), true, 'Reverse focus escaped drawer');
          for (let i = 0; i < 24; i++) { await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role="dialog"]')), true, 'Focus escaped navigation drawer'); }
          await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
          assert.equal(await menu.evaluate(node => document.activeElement === node), true, 'Menu focus must restore after Escape');
          record.checks.push('mobile-menu-keyboard-focus-trap-escape-restores-focus');
        }
        if (!before && spec.name === 'admin' && viewport.name === 'desktop') {
          assert.equal(await page.locator('aside').getByText(brand, { exact: true }).count(), 1, 'Configured brand has precedence');
          brandMode = 'logo'; await page.evaluate(() => window.dispatchEvent(new Event('configuracoesUpdated'))); await page.locator('aside img').waitFor(); assert.equal(await page.locator('aside img').getAttribute('src'), logo);
          brandMode = 'empty'; await page.evaluate(() => window.dispatchEvent(new Event('configuracoesUpdated'))); await page.locator('aside').getByText('AgenteOS', { exact: true }).waitFor(); record.checks.push('configured-brand-logo-preserved-official-fallback');
        }
        if (!before && spec.name === 'jev-integracao' && viewport.name === 'desktop') {
          const callsBefore = result.apiRequests.filter(item => item.path.includes('/integrations/jev')).length;
          await page.evaluate(() => { const user = JSON.parse(localStorage.getItem('user')); user.role = 'admin_empresa'; localStorage.setItem('user', JSON.stringify(user)); window.dispatchEvent(new Event('storage')); });
          await page.getByText('Configuração JEV exclusiva do Super Admin.', { exact: true }).waitFor();
          assert.equal(result.apiRequests.filter(item => item.path.includes('/integrations/jev')).length, callsBefore); record.checks.push('credential-password-empty-write-only-nonadmin-denied');
        }
        result.routes.push(record); await context.close();
      }
    }
    assert.deepEqual(result.errors, []); assert.deepEqual(result.failedAssets, []); assert.deepEqual(result.unexpectedWrites, []); assert.deepEqual(result.externalRequests, []);
    result.checks.push('12-route-viewport-comparisons', 'all-api-fixtures-no-backend-access', 'sockets-blocked', 'no-external-request', 'no-real-provider-message-tag-meta-write');
    result.status = 'PASS';
  } catch (error) { result.status = 'FAIL'; result.failure = error.message; process.exitCode = 1; }
  finally { fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify({ status: result.status, phase, routes: result.routes.length, failure: result.failure, output }, null, 2)); await browser.close(); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
