const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const real = process.env.FUNNEL_REAL_AUTH_STDIN === '1';
const input = real ? JSON.parse(fs.readFileSync(0, 'utf8')) : {};
const tenant = real ? input.user.empresa_id : '00000000-0000-0000-0000-000000000001';
const otherTenant = '00000000-0000-0000-0000-000000000099';
const origin = new URL(process.env.PANEL_ORIGIN || 'http://127.0.0.1:18092').origin;
const fixedHorizon = '2026-10-11T12:00:00Z';
const user = { id: '00000000-0000-0000-0000-000000000005', empresa_id: tenant, role: 'admin_empresa', nome: 'Validação A05' };
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
function sanitizeDashboard(data) {
  const copy = structuredClone(data);
  for (const field of ['campaigns', 'ads']) (copy.options?.[field] || []).forEach((row, index) => { row.label = `${field === 'campaigns' ? 'Campanha' : 'Anúncio'} identificado ${index + 1}`; });
  (copy.ads_comparison?.rows || []).forEach((row, index) => {
    for (const key of ['account_id', 'ad_account_id', 'ad_id', 'campaign_id', 'ad_name', 'campaign_name', 'adset_id', 'adset_name']) if (row[key] !== null && row[key] !== undefined) row[key] = `Identificação ocultada ${index + 1}`;
  });
  return copy;
}
function sanitizeJourney(data) {
  const copy = structuredClone(data);
  copy.items = (copy.items || []).map(item => ({ ...item, title: 'Evidência comercial registrada', evidence: { ...item.evidence, record_id: 'Identificação ocultada' }, details: {} }));
  if (copy.summary?.crm?.tags) copy.summary.crm.tags = copy.summary.crm.tags.map((tag, index) => ({ ...tag, name: `Tag registrada ${index + 1}` }));
  return copy;
}
async function main() {
  assert.ok(!real || (input.token && tenant), 'Missing read-only credentials input');
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1100 }, acceptDownloads: true });
  await context.routeWebSocket('**/*', socket => socket.close());
  const page = await context.newPage();
  const errors = [], failedAssets = [], dashboards = [], drillResponses = [], journeyResponses = [], drillQueries = [], screenshots = [];
  const allowedLeads = new Set(); let selectedLeadId = null, disabled = false, writeRequests = 0, externalRequests = 0, apiRequests = 0;
  const result = { mode: real ? 'REAL_API_READ_ONLY' : 'SYNTHETIC', checks: [], browser: browser.version() };
  const deadline = setTimeout(() => browser.close().catch(() => {}), 150000);
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', async dialog => { errors.push(dialog.message()); await dialog.dismiss(); });
  page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/assets/') && response.status() !== 200) failedAssets.push(response.status()); });
  async function realFetch(route) {
    const response = await route.fetch({ headers: { ...route.request().headers(), authorization: `Bearer ${input.token}` }, maxRedirects: 0 });
    assert.equal(response.status(), 200, 'Authenticated read-only API request failed'); return response;
  }
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { externalRequests += 1; return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (request.method() !== 'GET') { writeRequests += 1; return route.abort(); }
    const base = `/api/empresas/${tenant}`;
    if (url.pathname.includes('/commercial-funnel')) {
      apiRequests += 1; if (!url.pathname.startsWith(`${base}/analytics/commercial-funnel`)) { if (!real) return route.fulfill({ status: 403, json: { detail: 'scope changed' } }); throw new Error('Unexpected tenant request'); }
      if (disabled) return route.fulfill({ status: 404, json: { detail: 'disabled' } });
      if (url.pathname.endsWith('/export')) {
        assert.ok(dashboards.length && url.searchParams.get('observed_at'), 'Export lost observation horizon');
        if (real) { const response = await realFetch(route); const body = await response.text(); assert.ok(!body.includes('display_name') && !body.includes('lead_id') && !body.includes('telefone'), 'Export includes PII schema'); return route.fulfill({ response, body }); }
        return route.fulfill({ contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename=funil.csv' }, body: 'section,metric,value,numerator,denominator,state,currency\nsummary,total_leads,60,,60,observed,\n' });
      }
      if (url.pathname.endsWith('/leads')) {
        const response = real ? await realFetch(route) : null; const data = response ? await response.json() : syntheticDrill(url);
        drillQueries.push({ offset: Number(url.searchParams.get('offset') || 0), horizon: url.searchParams.get('observed_at') }); drillResponses.push(data);
        (data.items || []).forEach(item => allowedLeads.add(item.lead_id));
        const masked = { ...data, items: (data.items || []).map((item, index) => ({ ...item, display_name: real ? `Contato real — dados ocultos ${index + 1}` : item.display_name })) };
        return route.fulfill({ ...(response ? { response } : {}), json: masked });
      }
      const response = real ? await realFetch(route) : null; const data = response ? await response.json() : syntheticDashboard(url); dashboards.push(data);
      if (!real && url.searchParams.get('stage') === 'entry') await new Promise(resolve => setTimeout(resolve, 600));
      try { return await route.fulfill({ ...(response ? { response } : {}), json: sanitizeDashboard(data) }); } catch { return; }
    }
    if (/\/inbox\/leads\/[^/]+\/journey$/.test(url.pathname)) {
      const id = url.pathname.split('/leads/')[1].split('/')[0]; assert.ok(url.pathname.startsWith(`${base}/inbox/leads/`) && allowedLeads.has(id), 'Unexpected A04 tenant/lead scope'); selectedLeadId = id;
      const response = real ? await realFetch(route) : null; const data = response ? await response.json() : syntheticJourney(id); journeyResponses.push(data);
      return route.fulfill({ ...(response ? { response } : {}), json: sanitizeJourney(data) });
    }
    let data = {};
    if (url.pathname.endsWith('/configuracoes-branding')) data = { nome_sistema: 'Validação A05' };
    else if (url.pathname === `${base}/inbox`) {
      if (real) {
        const response = await realFetch(route), actual = await response.json();
        data = actual.filter(item => allowedLeads.has(item.id)).map((item, index) => ({ ...item, nome_contato: `Contato real — dados ocultos ${index + 1}`, telefone_contato: `READONLY_${index}`, ultima_mensagem: '', historico_resumo: '', dados_adicionais: {}, foto_url: null, tags: [] }));
        assert.ok(data.length > 0, 'Real Inbox did not return any selected cohort contact');
      } else data = [...allowedLeads].map((id, index) => ({ id, nome_contato: `Contato sintético ${index + 1}`, telefone_contato: `READONLY_${index}`, status_atendimento: 'encerrado', ia_ativa: true, tags: [] }));
    } else if (url.pathname.endsWith('/ai-state')) data = { manual_enabled: true, processing_state: 'allowed' };
    else if (url.pathname.endsWith('/foto')) data = { foto_url: null };
    else if (/\/(destinos|grupos|oficiais)$/.test(url.pathname) || url.pathname.includes('/inbox/READONLY_')) data = [];
    return route.fulfill({ json: data });
  });
  await page.addInitScript(({ user, filters }) => {
    localStorage.setItem('user', JSON.stringify(user)); localStorage.setItem('token', 'read-only-browser-placeholder');
    if (filters && location.pathname === '/painel/funil') history.replaceState({ usr: { funnelContext: { tenant: user.empresa_id, filters } }, key: 'default', idx: 0 }, '', location.href);
    window.__funnelRoots = []; const roots = new WeakSet(); window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__funnelRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { user, filters: input.filters || null });
  const dashboard = page.getByTestId('funnel-dashboard');
  async function ready() { await dashboard.getByTestId('funnel-total').waitFor(); await dashboard.getByRole('button', { name: 'Atualizar dados' }).waitFor({ state: 'visible' }); }
  async function shot(name) {
    if (!process.env.PANEL_SCREENSHOT_DIR) return;
    fs.mkdirSync(process.env.PANEL_SCREENSHOT_DIR, { recursive: true });
    const file = path.join(process.env.PANEL_SCREENSHOT_DIR, `${name}.png`); await page.screenshot({ path: file, fullPage: true }); screenshots.push(path.basename(file));
  }
  try {
    await page.goto(`${origin}/painel/funil`, { waitUntil: 'networkidle' }); await ready();
    const first = dashboards.at(-1); assert.ok(first && first.window.observed_at);
    assert.equal(await dashboard.getByTestId('funnel-total').innerText(), Number(first.summary.total_leads).toLocaleString('pt-BR'));
    assert.match(await dashboard.innerText(), /UTC/); assert.match(await dashboard.innerText(), /numerador \/ denominador/);
    assert.match(await dashboard.getByTestId('funnel-revenue').innerText(), /desconhecida|BRL|USD/);
    assert.ok(await page.getByRole('link', { name: 'Funil e gargalos', exact: true }).isVisible());
    result.checks.push('visible-tenant-route-navigation', 'unique-contact-total-matches-api', 'denominators-horizon-utc-explicit', 'unknown-revenue-not-zero', 'configured-stage-volume-not-forced-monotonic');
    await shot('dashboard-desktop-sanitized');
    await dashboard.getByRole('button', { name: 'Ver contatos', exact: true }).click();
    const drill = dashboard.getByTestId('funnel-drill'); await drill.getByTestId('funnel-lead-row').first().waitFor();
    const firstDrill = drillResponses.at(-1); assert.equal(await drill.getByTestId('funnel-lead-row').count(), firstDrill.items.length); assert.equal(firstDrill.total, first.summary.total_leads);
    assert.ok(drillQueries.every(query => query.horizon === first.window.observed_at));
    if (firstDrill.total > 25) {
      const response = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/commercial-funnel/leads') && new URL(response.url()).searchParams.get('offset') === '25');
      await drill.getByRole('button', { name: 'Próxima página' }).click(); await response; await drill.getByTestId('funnel-lead-row').first().waitFor();
      const second = drillResponses.at(-1); assert.equal(await drill.getByTestId('funnel-lead-row').count(), second.items.length);
      assert.ok(!second.items.some(item => firstDrill.items.some(previous => previous.lead_id === item.lead_id)));
      assert.equal(drillQueries.at(-1).horizon, first.window.observed_at);
      result.checks.push('drill-pagination-without-contact-duplicates', 'observation-horizon-preserved-between-pages');
    }
    await shot('drilldown-sanitized');
    const link = drill.getByRole('link', { name: 'Abrir jornada A04' }).first(); const href = await link.getAttribute('href');
    assert.match(href, /^\/painel\/inbox\?lead=[0-9a-f-]+&journey=1$/i);
    await link.click(); const timeline = page.getByTestId('commercial-journey'); await timeline.getByTestId('journey-events').waitFor();
    assert.ok(journeyResponses.at(-1).items.length > 0, 'No persisted journey events available');
    assert.equal(await timeline.locator('[data-event-id]').count(), journeyResponses.at(-1).items.length);
    assert.ok(await page.getByPlaceholder('Digite uma mensagem').isVisible());
    assert.ok(allowedLeads.has(selectedLeadId)); await shot('a04-journey-sanitized');
    await page.getByRole('link', { name: '← Voltar ao funil com os filtros', exact: true }).click(); await ready();
    assert.equal(dashboards.at(-1).window.observed_at, first.window.observed_at);
    result.checks.push('a04-deeplink-selects-returned-inbox-contact-including-closed', 'a04-real-or-synthetic-timeline-rendered', 'inbox-composer-preserved', 'return-to-funnel-retains-filters-and-horizon');
    const downloaded = page.waitForEvent('download'); await dashboard.getByRole('button', { name: 'Exportar agregado CSV' }).click(); const download = await downloaded;
    const downloadFile = await download.path(); const csv = fs.readFileSync(downloadFile, 'utf8'); assert.ok(!csv.includes('display_name') && !csv.includes('lead_id') && !csv.includes('telefone')); result.checks.push('authenticated-aggregate-export-without-pii-schema');
    const bottleneck = dashboard.getByRole('button', { name: 'Investigar gargalo' }).first();
    if (await bottleneck.count()) {
      await bottleneck.click(); await dashboard.getByTestId('funnel-drill').waitFor();
      await page.waitForFunction(() => !document.querySelector('[data-testid="funnel-drill"]')?.textContent.includes('Consultando contatos...'));
      result.checks.push('bottleneck-drilldown-visible');
    }
    const stages = first.stages.filter(stage => stage.reached_leads > 0);
    if (stages.length) {
      const stage = stages.at(-1); await dashboard.getByLabel('Etapa comercial', { exact: true }).selectOption(stage.key); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready();
      assert.ok(dashboards.at(-1).summary.total_leads <= first.summary.total_leads); result.checks.push('stage-cohort-filter-applies');
    }
    // Identified acquisition filters use persisted option IDs, never probable joins.
    for (const [field, label, list] of [['origin', 'Origem comprovada', first.options?.origins], ['campaign_id', 'Campanha Ads identificada', first.options?.campaigns], ['ad_id', 'Anúncio identificado', first.options?.ads]]) {
      if (!list?.length) continue;
      for (const clearLabel of ['Etapa comercial', 'Origem comprovada', 'Campanha Ads identificada', 'Anúncio identificado']) await dashboard.getByLabel(clearLabel, { exact: true }).selectOption('');
      await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready();
      const available = await dashboard.getByLabel(label, { exact: true }).locator('option').evaluateAll(nodes => nodes.map(node => node.value).filter(Boolean));
      if (!available.length) continue;
      await dashboard.getByLabel(label, { exact: true }).selectOption(available[0]); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready();
      assert.ok(dashboards.at(-1).summary.total_leads <= first.summary.total_leads);
      result.checks.push(`${field}-identified-cohort-filter-applies`);
    }
    for (const clearLabel of ['Etapa comercial', 'Origem comprovada', 'Campanha Ads identificada', 'Anúncio identificado']) await dashboard.getByLabel(clearLabel, { exact: true }).selectOption('');
    await dashboard.getByLabel('Estado do contato', { exact: true }).selectOption('inactive'); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready();
    if (real) assert.equal(dashboards.at(-1).summary.active, 0);
    result.checks.push('inactive-contact-status-filter');
    await dashboard.getByLabel('Estado do contato', { exact: true }).selectOption('');
    if (real && input.secondaryFilters) {
      for (const [key, label] of [['start', 'Entrada a partir de (UTC)'], ['end', 'Entrada antes de (UTC)']]) if (input.secondaryFilters[key]) await dashboard.getByLabel(label, { exact: true }).fill(input.secondaryFilters[key].slice(0, 16));
      await dashboard.getByLabel('Etapa comercial', { exact: true }).selectOption(''); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready(); result.checks.push('real-secondary-period-filter');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await dashboard.getByRole('button', { name: 'Aplicar filtros' }).isVisible());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2), 'Mobile page overflows viewport');
    await shot('dashboard-mobile-sanitized'); result.checks.push('mobile-navigation-and-filters-usable');
    if (!real) {
      await dashboard.getByLabel('Origem comprovada', { exact: true }).selectOption('no_event'); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready();
      assert.equal(await dashboard.getByTestId('funnel-total').innerText(), '0'); assert.match(await dashboard.innerText(), /Nenhum contato nesta coorte/); result.checks.push('zero-cohort-not-inferred-no-event');
      await dashboard.getByLabel('Origem comprovada', { exact: true }).selectOption(''); await dashboard.getByLabel('Etapa comercial', { exact: true }).selectOption('entry'); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click();
      await dashboard.getByLabel('Etapa comercial', { exact: true }).selectOption('qualified'); await dashboard.getByRole('button', { name: 'Aplicar filtros' }).click(); await ready(); await page.waitForTimeout(700);
      assert.equal(await dashboard.getByTestId('funnel-total').innerText(), '45'); result.checks.push('obsolete-filter-response-not-rendered');
      disabled = true; await dashboard.getByRole('button', { name: 'Atualizar dados' }).click(); await dashboard.getByRole('alert').waitFor(); assert.match(await dashboard.innerText(), /Inbox continuam disponíveis/); result.checks.push('disabled-feature-safe-fallback');
      disabled = false; await dashboard.getByRole('button', { name: 'Atualizar dados' }).click(); await ready();
      await page.evaluate(id => { const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = id; localStorage.setItem('user', JSON.stringify(user)); window.dispatchEvent(new Event('storage')); }, otherTenant);
      await page.waitForTimeout(100); assert.equal(await dashboard.getByTestId('funnel-total').count(), 0); result.checks.push('tenant-switch-hides-old-cohort');
    }
    assert.equal((await page.evaluate(() => window.__funnelRoots)).length, 1); assert.deepEqual(errors, []); assert.deepEqual(failedAssets, []); assert.equal(writeRequests, 0); assert.equal(externalRequests, 0);
    result.status = 'PASS'; result.realCohort = real ? { total_leads: first.summary.total_leads, inbound_leads: first.summary.inbound_leads, stage_counts: first.stages.map(stage => ({ key: stage.key, count: stage.reached_leads })), exact_attribution_leads: first.attribution.exact_leads, attribution_denominator: first.attribution.denominator, returned_after_followup: first.followup.returned_leads, sent_followup_leads: first.followup.sent_leads, drill_first_page: firstDrill.items.length, drill_total: firstDrill.total, first_page_identity_hash: createHash('sha256').update(JSON.stringify(firstDrill.items.map(item => item.lead_id))).digest('hex'), journey_facts: journeyResponses.reduce((total, response) => total + response.items.length, 0), actual_authenticated_api: true } : null;
  } catch (error) { result.status = 'FAIL'; result.failure = real ? `${error.name}: read-only cohort browser validation failed` : error.message; result.failure_checkpoint = result.checks.at(-1) || 'initial-render'; process.exitCode = 1; }
  finally {
    clearTimeout(deadline); let roots = null; try { roots = (await page.evaluate(() => window.__funnelRoots)).length; } catch { /* original failure retained */ }
    Object.assign(result, { error_count: errors.length, failed_asset_count: failedAssets.length, write_requests: writeRequests, external_requests: externalRequests, api_requests: apiRequests, react_roots: roots, screenshots });
    if (process.env.PANEL_EVIDENCE) fs.writeFileSync(process.env.PANEL_EVIDENCE, JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2)); await browser.close();
  }
}
main().catch(error => { console.error(real ? 'Read-only real funnel browser harness failed' : error.message); process.exitCode = 1; });
