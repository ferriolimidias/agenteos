const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const real = process.env.JOURNEY_REAL_AUTH_STDIN === '1';
const input = real ? JSON.parse(fs.readFileSync(0, 'utf8')) : null;
const captured = real && Boolean(input.capturedData);
const tenant = real ? input.user.empresa_id : '00000000-0000-0000-0000-000000000001';
const leadId = real ? input.lead.id : '00000000-0000-0000-0000-000000000002';
const otherLead = '00000000-0000-0000-0000-000000000003';
const otherTenant = '00000000-0000-0000-0000-000000000004';
const user = { id: '00000000-0000-0000-0000-000000000005', empresa_id: tenant, role: 'admin_empresa', nome: 'Validação somente leitura' };
const fixtureLead = (id, second = false) => ({ id, conversation_id: real ? input.lead.conversation_id : null, nome_contato: real ? 'Contato real — dados ocultos' : (second ? 'Contato sintético B' : 'Contato sintético A'), telefone_contato: second ? 'READONLY_B' : 'READONLY_A', status_atendimento: 'aberto', ia_ativa: true });
const event = (id, category, status, date, title) => ({
  id, category, status, occurred_at: date, title, type: `${category}_fact`, conversation_id: null,
  evidence: { source: category === 'payment' ? 'payment_events' : category === 'message' ? 'mensagens' : 'followup_jobs', record_id: id.split(':')[1], relationship: 'explicit_lead_id' },
  details: { direction: 'inbound', verified: false, historical_no_send: true, token: 'NEVER_RENDER_PRIVATE', payload: { text: 'NEVER_RENDER_PRIVATE' } },
});
const events = [event('payment:1', 'payment', 'pending', '2026-10-11T12:00:00Z', 'Pagamento em andamento'), event('message:2', 'message', 'observed', '2026-10-11T11:00:00Z', 'Mensagem registrada'), event('followup:3', 'followup', 'not_occurred', '2026-10-11T10:00:00Z', 'Follow-up bloqueado')];
function fixture(url) {
  const category = url.searchParams.get('category');
  const filtered = events.filter(item => !category || item.category === category);
  const more = !category && !url.searchParams.get('cursor');
  return {
    checked_at: '2026-10-11T13:00:00Z', lead_id: url.pathname.split('/leads/')[1].split('/')[0],
    items: more ? filtered.slice(0, 2) : (url.searchParams.get('cursor') ? filtered.slice(1) : filtered), limit: 30, next_cursor: more ? 'opaque-synthetic-cursor' : null, has_more: more,
    summary: {
      crm: { stage: { id: 'stage-1', name: 'Qualificação' }, tags: [{ id: 'tag-1', name: 'Interesse' }], snapshot_only: true, history_complete: false },
      funnel: { version: 'fixture-v1', stages: [], snapshot_only: true, history_complete: false }, commercial_state: null,
      activity: { last_event_at: events[0].occurred_at, inactive_seconds: 3600, state: 'observed', abandonment_inferred: false },
      attribution: { status_counts: { partial: 1 }, ads_identity_verified: false },
    },
    coverage: {
      timezone: 'UTC', filtered: false, history_complete: false,
      sources: [{ source: 'checkout_events', category: 'checkout', records: 0, timeline_records: 0, missing_timestamp: 0, state: 'unknown', limitation: 'Não observado nesta fonte.' }],
      categories: [{ category: 'checkout', records: 0, state: 'unknown' }], limitations: ['Histórico de etapas incompleto.'],
    },
  };
}
async function main() {
  assert.ok(!real || (input.token && tenant && leadId), 'Missing read-only real journey input');
  const origin = new URL(process.env.PANEL_ORIGIN || 'http://127.0.0.1:18086').origin;
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
  await context.routeWebSocket('**/*', socket => socket.close());
  const page = await context.newPage();
  const errors = [], failedAssets = [], realResponses = [];
  let disabled = false, writeRequests = 0, externalRequests = 0, journeyRequests = 0;
  const result = { origin, mode: captured ? 'REAL_CAPTURED_READ_ONLY' : real ? 'REAL_API_READ_ONLY' : 'SYNTHETIC', checks: [], browser: browser.version() };
  const deadline = setTimeout(() => { result.status = 'FAIL'; result.failure = 'Browser validation exceeded 90 seconds'; browser.close().catch(() => {}); }, 90000);
  page.on('pageerror', caught => errors.push(caught.message));
  page.on('dialog', async dialog => { errors.push(dialog.message()); await dialog.dismiss(); });
  page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/assets/') && response.status() !== 200) failedAssets.push(response.status()); });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { externalRequests += 1; return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (request.method() !== 'GET') { writeRequests += 1; return route.abort(); }
    if (url.pathname.endsWith('/journey')) {
      journeyRequests += 1;
      if (real) {
        const expected = `/api/empresas/${tenant}/inbox/leads/${leadId}/journey`;
        assert.ok(url.pathname === expected, 'Unexpected real journey scope');
        if (captured) {
          // Real persisted facts captured by a production READ ONLY service
          // call before deploy, distinct from the invented synthetic fixtures.
          assert.ok(input.capturedData.lead_id === leadId, 'Captured data lead scope mismatch');
          const selectedCategory = url.searchParams.get('category');
          const all = input.capturedData.items.filter(item => !selectedCategory || item.category === selectedCategory);
          const limit = Number(url.searchParams.get('limit') || 30);
          const offset = Number((url.searchParams.get('cursor') || 'captured:0').split(':')[1]);
          assert.ok(Number.isInteger(offset) && offset >= 0 && limit >= 1 && limit <= 100, 'Invalid captured pagination');
          const has_more = offset + limit < all.length;
          const data = { ...input.capturedData, items: all.slice(offset, offset + limit), limit, has_more, next_cursor: has_more ? `captured:${offset + limit}` : null };
          realResponses.push(data);
          return route.fulfill({ json: data });
        }
        const response = await route.fetch({ headers: { ...request.headers(), authorization: `Bearer ${input.token}` }, maxRedirects: 0 });
        assert.equal(response.status(), 200, 'Read-only real journey API did not return 200');
        const data = await response.json(); realResponses.push(data);
        return route.fulfill({ response, json: data });
      }
      if (disabled) return route.fulfill({ status: 404, json: { detail: 'disabled' } });
      if (url.searchParams.get('category') === 'crm' || url.pathname.includes(otherLead)) {
        await new Promise(resolve => setTimeout(resolve, 600));
        try { return await route.fulfill({ json: fixture(url) }); } catch { return; }
      }
      return route.fulfill({ json: fixture(url) });
    }
    // Keep the actual chat intact while all supporting GETs use minimized fixtures.
    // In real mode only journey GET reaches the backend; photos and sockets cannot.
    let data = {};
    const path = url.pathname;
    if (path.endsWith('/configuracoes-branding')) data = { nome_sistema: 'Validação somente leitura' };
    else if (path.endsWith('/inbox')) data = real ? [fixtureLead(leadId)] : [fixtureLead(leadId), fixtureLead(otherLead, true)];
    else if (path.endsWith('/ai-state')) data = { manual_enabled: true, processing_state: 'allowed' };
    else if (path.endsWith('/foto')) data = { foto_url: null };
    else if (/\/(destinos|grupos|oficiais)$/.test(path) || /\/inbox\/READONLY_/.test(path)) data = [];
    return route.fulfill({ json: data });
  });
  await page.addInitScript(({ user }) => {
    if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify(user));
    if (!localStorage.getItem('token')) localStorage.setItem('token', 'synthetic-browser-placeholder-only');
    window.__journeyRoots = [];
    const roots = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__journeyRoots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { user });
  try {
    await page.goto(`${origin}/painel/inbox`, { waitUntil: 'networkidle' });
    await page.getByText(real ? 'Contato real — dados ocultos' : 'Contato sintético A', { exact: true }).click();
    await page.getByRole('button', { name: 'Jornada comercial', exact: true }).click();
    const timeline = page.getByTestId('commercial-journey');
    await timeline.getByTestId('journey-events').waitFor();
    assert.ok(await page.getByPlaceholder('Digite uma mensagem').isVisible(), 'Timeline obstructed the existing composer');
    assert.match(await timeline.innerText(), /Horário de Brasília/);
    assert.equal(await timeline.getByLabel('Fuso horário').count(), 0);
    await timeline.getByText('Ver evidência', { exact: true }).first().click();
    assert.ok(await timeline.getByText('Fonte / registro', { exact: true }).first().isVisible());
    result.checks.push('timeline-in-inbox', 'chat-composer-preserved', 'brasilia-explicit', 'evidence-details');
    if (real) {
      assert.ok(realResponses.length > 0 && realResponses[0].items.length > 0, 'No real persisted journey observed');
      const data = realResponses[0];
      assert.ok(data.lead_id === leadId, 'Real API returned different lead scope');
      assert.equal(await timeline.locator('[data-event-id]').count(), data.items.length);
      assert.deepEqual(await timeline.locator('[data-event-id]').evaluateAll(nodes => nodes.map(node => node.dataset.eventId)), data.items.map(item => item.id));
      for (const item of data.items) assert.ok(await timeline.getByText(item.title, { exact: true }).count() > 0, 'Real event title missing');
      if (data.has_more) {
        const nextPageResponse = page.waitForResponse(response => {
          const url = new URL(response.url()); return url.pathname.endsWith('/journey') && url.searchParams.has('cursor');
        });
        await timeline.getByRole('button', { name: 'Carregar mais fatos' }).click();
        await nextPageResponse;
        await timeline.getByRole('button', { name: 'Carregando fatos...' }).waitFor({ state: 'hidden' });
        assert.ok(realResponses.length > 1, 'Real next page did not arrive');
        result.checks.push('real-cursor-next-page-rendered');
      }
      const observedItems = [...new Map(realResponses.flatMap(response => response.items).map(item => [item.id, item])).values()];
      assert.deepEqual(await timeline.locator('[data-event-id]').evaluateAll(nodes => nodes.map(node => node.dataset.eventId)), observedItems.map(item => item.id));
      result.realJourney = {
        browser_response_status: 200, live_api_http_status: captured ? null : 200,
        provenance: captured ? 'PRODUCTION_READ_ONLY_CAPTURE' : 'PUBLIC_RUNTIME_GET',
        item_count: observedItems.length, first_page_count: data.items.length,
        category_counts: observedItems.reduce((counts, item) => { counts[item.category] = (counts[item.category] || 0) + 1; return counts; }, {}),
        sources: [...new Set(observedItems.map(item => item.evidence.source))], coverage_available: Array.isArray(data.coverage?.sources),
        ordered_ids_match_api: true, ordered_identity_hash: createHash('sha256').update(JSON.stringify(observedItems.map(item => item.id))).digest('hex'),
        first_page_identity_hash: createHash('sha256').update(JSON.stringify(data.items.map(item => item.id))).digest('hex'),
      };
      result.checks.push(captured ? 'real-persisted-events-from-read-only-capture' : 'real-persisted-events-from-live-api', 'real-rendering-order-and-count-match');
    } else {
      assert.ok(!(await timeline.innerText()).includes('NEVER_RENDER_PRIVATE'));
      await timeline.getByText('Resumo do contato', { exact: true }).click();
      await timeline.getByText('Cobertura das fontes', { exact: true }).click();
      assert.match(await timeline.innerText(), /Snapshots atuais não comprovam transições/);
      assert.match(await timeline.innerText(), /Desconhecido · não observado nesta fonte/);
      await timeline.getByRole('button', { name: 'Carregar mais fatos' }).click();
      await timeline.getByText('Follow-up bloqueado', { exact: true }).waitFor();
      assert.equal(await timeline.locator('[data-event-id]').count(), 3);
      result.checks.push('safe-allowlist', 'coverage-unknown-vs-not-occurred', 'snapshot-not-history', 'cursor-append-without-duplicates');
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('payment');
      await timeline.getByText('Pagamento em andamento', { exact: true }).waitFor();
      assert.equal(await timeline.locator('[data-event-id]').count(), 1);
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('crm');
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('message');
      await timeline.getByText('Mensagem registrada', { exact: true }).waitFor();
      await page.waitForTimeout(700);
      assert.equal(await timeline.locator('[data-event-id]').count(), 1);
      result.checks.push('category-filter', 'obsolete-filter-response-rejected');
      await timeline.getByRole('button', { name: 'Fechar jornada comercial', exact: true }).click();
      await page.getByText('Contato sintético B', { exact: true }).click();
      await page.getByRole('button', { name: 'Jornada comercial', exact: true }).click();
      await timeline.getByRole('button', { name: 'Fechar jornada comercial', exact: true }).click();
      await page.getByText('Contato sintético A', { exact: true }).first().click();
      await page.getByRole('button', { name: 'Jornada comercial', exact: true }).click();
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('message');
      await timeline.getByText('Mensagem registrada', { exact: true }).waitFor();
      await page.waitForTimeout(700);
      assert.equal(await timeline.locator('[data-event-id]').count(), 1);
      result.checks.push('obsolete-lead-response-rejected');
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('checkout');
      await timeline.getByText('Nenhum fato observado neste filtro', { exact: true }).waitFor();
      assert.match(await timeline.innerText(), /não comprova que o evento não ocorreu/);
      disabled = true;
      await timeline.getByLabel('Categoria', { exact: true }).selectOption('payment');
      await timeline.getByRole('alert').waitFor();
      assert.match(await timeline.innerText(), /conversa continua disponível/);
      assert.ok(await page.getByPlaceholder('Digite uma mensagem').isVisible());
      result.checks.push('no-evidence-not-non-occurrence', 'disabled-feature-chat-fallback');
      disabled = false;
      await timeline.getByRole('button', { name: 'Atualizar jornada' }).click();
      await timeline.getByText('Pagamento em andamento', { exact: true }).waitFor();
      await page.evaluate(empresa => { const user = JSON.parse(localStorage.getItem('user')); user.empresa_id = empresa; localStorage.setItem('user', JSON.stringify(user)); window.dispatchEvent(new Event('storage')); }, otherTenant);
      await page.waitForFunction(() => !document.querySelector('[data-testid="commercial-journey"]') || document.querySelector('[data-testid="commercial-journey"]').textContent.includes('Tenant alterado.'));
      assert.equal(await timeline.locator('[data-event-id]').count(), 0);
      result.checks.push('tenant-switch-hides-old-facts');
    }
    assert.equal((await page.evaluate(() => window.__journeyRoots)).length, 1, 'Extra React roots');
    assert.deepEqual(errors, []); assert.deepEqual(failedAssets, []); assert.equal(writeRequests, 0); assert.equal(externalRequests, 0);
    result.status = 'PASS';
  } catch (caught) { result.status = 'FAIL'; result.failure = real ? `${caught.name}: real journey validation failed` : caught.message; process.exitCode = 1; }
  finally {
    clearTimeout(deadline);
    try {
      let rootCount = null;
      try { rootCount = (await page.evaluate(() => window.__journeyRoots || [])).length; } catch { /* Preserve the original failure if navigation destroyed its context. */ }
      Object.assign(result, { error_count: errors.length, failed_asset_count: failedAssets.length, write_requests: writeRequests, external_requests: externalRequests, journey_requests: journeyRequests, react_roots: rootCount });
      if (process.env.PANEL_EVIDENCE) fs.writeFileSync(process.env.PANEL_EVIDENCE, JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    } finally { await browser.close(); }
  }
}
main().catch(caught => { console.error(real ? 'Read-only real browser harness failed' : caught.message); process.exitCode = 1; });
