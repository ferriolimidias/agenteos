// All business APIs and sockets are intercepted. Only public HTML/assets load.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const { expect } = require((process.env.PLAYWRIGHT_MODULE_PATH || 'playwright') + '/test');
const { F, events, journey } = require('./fixtures-journey-workspace.cjs');
const origin = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:18884').origin;
const output = process.env.UX_EVIDENCE_DIR || '/tmp/journey28-browser';
const baseline = process.env.JOURNEY28_BASELINE === '1';
const widths = [{ name: 'screenshot1327', width: 1327, height: 709 }, { name: 'desktop', width: 1440, height: 900 }, { name: 'notebook', width: 1280, height: 800 }, { name: 'tablet', width: 768, height: 1024 }, { name: 'mobile', width: 390, height: 844 }];
const hash = p => createHash('sha256').update(fs.readFileSync(p)).digest('hex');
async function run(browser, viewport) {
  const r = { viewport, mode: 'ALL_API_SOCKETS_INTERCEPTED_SYNTHETIC', checks: [], errors: [], unexpectedReads: [], unexpectedWrites: [], externalRequests: [], failedAssets: [], queries: [], screenshots: [] };
  const context = await browser.newContext({ viewport, locale: 'pt-BR', timezoneId: 'Asia/Tokyo', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = { empty: false, error: false, delayedCategory: false, releases: [] };
  const sockets = [];
  const base = `/api/empresas/${F.TENANT}`;
  await context.routeWebSocket('**/*', socket => {
    assert.equal(new URL(socket.url()).pathname, `${base}/ws`);
    sockets.push(socket); socket.onMessage(raw => { const data = JSON.parse(String(raw)); if (data.type === 'ping') socket.send(JSON.stringify({ type: 'pong' })); });
  });
  await context.route('**/*', async route => {
    const q = route.request(), url = new URL(q.url());
    if (url.origin !== origin) { r.externalRequests.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (q.method() !== 'GET') { r.unexpectedWrites.push({ method: q.method(), path: url.pathname }); return route.abort(); }
    let data;
    if (url.pathname.endsWith('/journey')) {
      r.queries.push({ path: url.pathname, query: url.search });
      assert.equal(url.searchParams.get('limit'), '30');
      if (state.error) return route.fulfill({ status: 503, json: { detail: 'PRIVATE_API_DETAIL' } });
      const response = journey(url, state.empty);
      if ((url.searchParams.get('category') === 'crm' && state.delayedCategory) || url.pathname.includes(F.CONTACTS[1].id)) await new Promise(resolve => state.releases.push(resolve));
      try { return await route.fulfill({ json: response }); } catch { return; }
    }
    if (url.pathname.endsWith('/configuracoes-branding')) data = { nome_sistema: 'Ferrioli — teste sintético' };
    else if (url.pathname.endsWith('/inbox')) data = F.CONTACTS.slice(0, 3);
    else if (url.pathname.endsWith('/ai-state')) data = { manual_enabled: true, processing_state: 'allowed' };
    else if (url.pathname.endsWith('/foto')) data = { foto_url: null };
    else if (/\/(destinos|grupos|oficiais)$/.test(url.pathname)) data = [];
    else if (F.CONTACTS.some(lead => url.pathname === `${base}/inbox/${lead.telefone_contato}`)) data = F.MESSAGES;
    else { r.unexpectedReads.push(url.pathname); return route.fulfill({ status: 404, json: {} }); }
    return route.fulfill({ json: data });
  });
  await context.addInitScript(({ tenant }) => {
    localStorage.setItem('token', 'synthetic-journey28-not-a-credential');
    localStorage.setItem('user', JSON.stringify({ id: 'synthetic-user', nome: 'Operador sintético', role: 'admin_empresa', empresa_id: tenant }));
    window.__journey28Roots = []; const roots = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__journey28Roots.push(root.containerInfo?.id); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { tenant: F.TENANT });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => r.errors.push(error.message));
  page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/assets/') && response.status() !== 200) r.failedAssets.push(response.status()); });
  const timeline = page.getByTestId('commercial-journey'), dialog = page.getByTestId('journey-dialog');
  const composer = page.getByPlaceholder('Digite uma mensagem'), trigger = page.getByRole('button', { name: 'Jornada comercial', exact: true });
  const close = () => timeline.getByRole('button', { name: 'Fechar jornada comercial', exact: true }).click();
  const check = name => r.checks.push(name);
  const shot = async name => { const p = `${viewport.name}-${name}.png`; await page.screenshot({ path: path.join(output, p) }); r.screenshots.push(p); };
  const release = () => state.releases.splice(0).forEach(resolve => resolve());
  try {
    await page.goto(origin + '/painel/chat', { waitUntil: 'networkidle' });
    await page.getByText(F.CONTACTS[0].nome_contato, { exact: true }).click(); await composer.waitFor();
    await page.getByText(F.MESSAGES.at(-1).texto, { exact: true }).waitFor();
    await composer.fill('Rascunho sintético não enviado — preservar ao consultar jornada');
    const scroll = page.getByTestId('inbox-message-scroll');
    await scroll.evaluate(node => { node.scrollTop = 130; window.__journey28MessageNode = node; window.__journey28ComposerNode = document.querySelector('.inbox-composer textarea'); });
    const previous = await scroll.evaluate(node => ({ width: node.getBoundingClientRect().width, top: node.scrollTop }));
    const socketCount = sockets.length;
    await trigger.click(); await timeline.getByTestId('journey-events').waitFor();
    const box = await timeline.boundingBox(); r.layout = { journey: box, messageWidthBefore: previous.width, messageWidthDuring: (await scroll.boundingBox())?.width ?? null };
    await shot(baseline ? 'before' : 'after');
    if (baseline) { for (const key of ['errors','unexpectedReads','unexpectedWrites','externalRequests','failedAssets']) assert.deepEqual(r[key], [], key); check('baseline-layout-captured-five-widths'); r.status = 'PASS'; return r; }
    await expect(dialog).toBeVisible(); assert.ok(await dialog.evaluate(node => node.matches(':modal')));
    assert.ok(box.width >= Math.min(viewport.width * 0.86, 1150));
    assert.equal(r.layout.messageWidthDuring, previous.width);
    assert.equal(await composer.inputValue(), 'Rascunho sintético não enviado — preservar ao consultar jornada');
    assert.ok(await page.evaluate(() => window.__journey28MessageNode === document.querySelector('[data-testid="inbox-message-scroll"]') && window.__journey28ComposerNode === document.querySelector('.inbox-composer textarea')));
    assert.equal(await scroll.evaluate(node => node.scrollTop), previous.top); assert.equal(sockets.length, socketCount);
    assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
    await expect(timeline.getByRole('button', { name: 'Fechar jornada comercial' })).toBeFocused();
    // Native modality must reject even programmatic attempts to focus background.
    await composer.evaluate(node => node.focus()); assert.ok(await dialog.evaluate(node => node.contains(document.activeElement)));
    for (let i = 0; i < 12; i++) { await page.keyboard.press('Tab'); assert.ok(await dialog.evaluate(node => node.contains(document.activeElement) || document.activeElement === node)); }
    check('wide-reader-preserves-chat-nodes-draft-scroll-socket-background-inert-tab-scrolllock');
    assert.match(await timeline.innerText(), /Horário de Brasília/); assert.equal(await timeline.getByLabel('Fuso horário').count(), 0);
    const first = timeline.locator('[data-event-id]').first(); assert.match(await first.innerText(), /11\/10\/2026.*21:30:00/);
    await first.getByText('Ver evidência', { exact: true }).click();
    assert.match(await first.innerText(), /13\/10\/2026.*00:01:00/); assert.equal(await first.locator('time[datetime="2019-02-17T01:30:00Z"]').getAttribute('datetime'), '2019-02-17T01:30:00Z'); assert.match(await first.innerText(), /Horário técnico \(UTC\)/);
    await timeline.getByText('Resumo do contato', { exact: true }).click(); await timeline.getByText('Cobertura das fontes', { exact: true }).click();
    assert.match(await timeline.innerText(), /Desconhecido · não observado nesta fonte/); assert.match(await timeline.innerText(), /Snapshots atuais não comprovam transições/);
    assert.ok(!(await timeline.innerText()).includes('NEVER_RENDER_PRIVATE'));
    check('brasilia-in-tokyo-original-utc-details-summary-unknown-source-semantics');
    assert.equal(await timeline.locator('[data-event-id]').count(), 30);
    await timeline.getByRole('button', { name: 'Carregar mais fatos', exact: true }).click(); await expect(timeline.locator('[data-event-id]')).toHaveCount(45);
    assert.deepEqual(await timeline.locator('[data-event-id]').evaluateAll(nodes => nodes.map(n => n.dataset.eventId)), events.map(e => `${F.CONTACTS[0].id}:${e.id}`));
    const oldest = timeline.locator('[data-event-id]').last(); assert.match(await oldest.innerText(), /04\/11\/2018.*01:30:00/);
    await oldest.scrollIntoViewIfNeeded(); await shot('long-history');
    assert.ok(await dialog.evaluate(node => [...node.querySelectorAll('header,li,select,.inbox-journey-scroll')].every(n => n.scrollWidth <= n.clientWidth + 2)));
    check('long45-events-cursor-dedup-original-order-historical-dst-no-horizontal-overflow');
    state.delayedCategory = true; await timeline.getByLabel('Categoria', { exact: true }).selectOption('crm');
    await timeline.getByLabel('Categoria', { exact: true }).selectOption('message'); await expect(timeline.locator('[data-event-id]')).toHaveCount(15); release();
    await page.waitForTimeout(100); assert.equal(await timeline.locator('[data-event-id]').count(), 15);
    check('category-limit-query-stale-request-ignored');
    await timeline.getByLabel('Categoria', { exact: true }).selectOption('acquisition'); await timeline.getByText('Nenhum fato observado neste filtro', { exact: true }).waitFor();
    state.error = true; await timeline.getByLabel('Categoria', { exact: true }).selectOption('booking'); await timeline.getByRole('alert').waitFor(); assert.ok(!(await timeline.innerText()).includes('PRIVATE_API_DETAIL'));
    state.error = false; await timeline.getByRole('button', { name: 'Atualizar jornada', exact: true }).click(); await expect(timeline.locator('[data-event-id]')).toHaveCount(15);
    check('empty-error-retry-no-false-nonoccurrence-no-private-api-detail');
    const added = { ...F.MESSAGES[0], id: F.id(9999), texto: 'Atualização sintética durante leitura da jornada' };
    sockets.at(-1).send(JSON.stringify({ type: 'inbox.message.created', lead_id: F.CONTACTS[0].id, telefone: F.CONTACTS[0].telefone_contato, conversation_id: F.CONTACTS[0].conversation_id, message: added }));
    await page.getByText(added.texto, { exact: true }).waitFor({ state: 'attached' }); assert.equal(sockets.length, socketCount);
    await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); await expect(trigger).toBeFocused();
    assert.equal(await page.evaluate(() => document.body.style.overflow), ''); await expect(composer).toHaveValue('Rascunho sintético não enviado — preservar ao consultar jornada'); await composer.click(); await expect(composer).toBeFocused();
    check('websocket-updates-while-reading-escape-focus-draft-retained');
    await trigger.click(); await timeline.getByTestId('journey-events').waitFor(); await close(); await expect(trigger).toBeFocused();
    if (viewport.width < 1024) await page.getByRole('button', { name: 'Voltar para conversas', exact: true }).click();
    await page.getByText(F.CONTACTS[1].nome_contato, { exact: true }).click(); await trigger.click(); await timeline.getByText('Consultando fatos da jornada...', { exact: true }).waitFor(); await close();
    if (viewport.width < 1024) await page.getByRole('button', { name: 'Voltar para conversas', exact: true }).click();
    await page.getByText(F.CONTACTS[0].nome_contato, { exact: true }).click(); await trigger.click(); await timeline.getByTestId('journey-events').waitFor(); release(); await page.waitForTimeout(100);
    assert.ok((await timeline.locator('[data-event-id]').evaluateAll(nodes => nodes.map(n => n.dataset.eventId))).every(id => id.startsWith(F.CONTACTS[0].id)));
    check('rapid-contact-change-pending-old-contact-aborted');
    await close(); await page.goto(origin + `/painel/inbox?lead=${F.CONTACTS[2].id}&journey=1`, { waitUntil: 'networkidle' }); await timeline.getByTestId('journey-events').waitFor();
    await page.reload({ waitUntil: 'networkidle' }); await timeline.getByTestId('journey-events').waitFor(); assert.match(await timeline.innerText(), new RegExp(F.CONTACTS[2].nome_contato));
    check('closed-contact-deeplink-and-reload');
    state.delayedCategory = true; await timeline.getByLabel('Categoria', { exact: true }).selectOption('crm');
    await page.evaluate(() => { const u = JSON.parse(localStorage.getItem('user')); u.empresa_id = '00000000-0000-0000-0000-000000000099'; localStorage.setItem('user', JSON.stringify(u)); window.dispatchEvent(new Event('storage')); });
    release(); await page.waitForFunction(() => !document.querySelector('[data-event-id]'));
    check('tenant-switch-old-facts-never-render');
    assert.equal((await page.evaluate(() => window.__journey28Roots)).length, 1);
    for (const field of ['errors', 'unexpectedReads', 'unexpectedWrites', 'externalRequests', 'failedAssets']) assert.deepEqual(r[field], [], field);
    check('single-root-readonly-no-commercial-effects'); r.status = 'PASS';
  } catch (error) { r.status = 'FAIL'; r.failure = error.message; r.checkpoint = r.checks.at(-1); try { await shot('failure'); } catch {} }
  finally { release(); await context.close(); fs.writeFileSync(path.join(output, `${viewport.name}.json`), JSON.stringify(r, null, 2)); }
  return r;
}
(async () => {
  fs.mkdirSync(output, { recursive: true }); const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] }); const results = [];
  try { for (const v of widths) { const r = await run(browser, v); results.push(r); console.log(JSON.stringify({ viewport: v.name, status: r.status, failure: r.failure })); } } finally { await browser.close(); }
  const summary = { status: results.every(r => r.status === 'PASS') ? 'PASS' : 'FAIL', baseline, origin, sourceSha: process.env.UX_SOURCE_SHA, scriptSha256: hash(__filename), fixtureSha256: hash(path.join(__dirname, 'fixtures-journey-workspace.cjs')), results };
  fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2)); if (summary.status !== 'PASS') process.exitCode = 1;
})();
