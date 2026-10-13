// UX04 deterministic Chromium gate. Every API is synthetic and every socket is
// intercepted; this harness has no real-auth mode and never sends a message.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const expect = require((process.env.PLAYWRIGHT_MODULE_PATH || 'playwright') + '/test').expect.configure({ timeout: process.env.UX_BASELINE === '1' ? 2500 : 10000 });
const F = require('./fixtures-ux04-operations.cjs');
const provenance = {
  scriptSha256: createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),
  fixtureSha256: createHash('sha256').update(fs.readFileSync(path.join(__dirname, 'fixtures-ux04-operations.cjs'))).digest('hex'),
  sourceSha: process.env.UX_SOURCE_SHA || execFileSync('git', ['rev-parse', 'HEAD'], { cwd: path.resolve(__dirname, '../..'), encoding: 'utf8' }).trim(),
};
const clone = value => JSON.parse(JSON.stringify(value));
const baseline = process.env.UX_BASELINE === '1';
const viewports = [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'notebook', width: 1280, height: 800 }, { name: 'mobile', width: 390, height: 844 }].filter(item => !process.env.UX_VIEWPORTS || process.env.UX_VIEWPORTS.split(',').includes(item.name));
assert.ok(viewports.length, 'Select desktop, notebook or mobile in UX_VIEWPORTS');
const origin = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:18849').origin;
const output = process.env.UX_EVIDENCE_DIR || '/tmp/agentos-ux04-browser-evidence';
const base = `/api/empresas/${F.TENANT}`;
const user = { id: F.id(990), nome: 'Operador UX04 sintético', role: 'admin_empresa', empresa_id: F.TENANT };
async function runViewport(browser, viewport) {
  const result = { ...provenance, viewport, mode: baseline ? 'BASELINE_SYNTHETIC' : 'CANDIDATE_SYNTHETIC', checks: [], findings: [], screenshots: [], api: [], writes: [], unexpectedWrites: [], unexpectedReads: [], externalRequests: [], socketConnections: [], socketMessages: [], errors: [], failedAssets: [] };
  const state = { contacts: clone(F.CONTACTS), messages: clone(F.MESSAGES), stage: new Map(F.CONTACTS.map((lead, index) => [lead.id, index === 0 ? F.STAGES[1].id : F.STAGES[0].id])), inboxMode: 'normal', crmMode: 'normal', journeyError: false, failMove: false, pending: null, releases: [], expectedDialog: null };
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, serviceWorkers: 'block', locale: 'pt-BR', timezoneId: 'UTC', reducedMotion: 'reduce' });
  const sockets = [];
  await context.routeWebSocket('**/*', socket => {
    // Do not connectToServer: the actual backend/provider socket stays blocked.
    const socketUrl = new URL(socket.url()); result.socketConnections.push(socketUrl.pathname);
    if (socketUrl.pathname !== `${base}/ws`) result.errors.push(`Unexpected socket scope: ${socketUrl.pathname}`);
    sockets.push(socket);
    socket.onMessage(message => {
      let value;
      try { value = JSON.parse(String(message)); } catch { result.errors.push('Non-JSON client socket message'); return; }
      result.socketMessages.push(value.type || 'unknown');
      if (value.type === 'ping') socket.send(JSON.stringify({ type: 'pong', ts: value.ts }));
      else result.errors.push(`Unexpected client socket action: ${value.type}`);
    });
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), pathname = url.pathname;
    if (url.origin !== origin) { result.externalRequests.push(`${method} ${url.origin}${pathname}`); return route.abort(); }
    if (pathname !== '/api' && !pathname.startsWith('/api/')) return route.continue();
    result.api.push({ method, path: pathname, query: url.search });
    // Explicitly allow three existing synthetic PUT shapes only. A send, upload,
    // delete, transfer, conversion, auth or integration request is always a failure.
    if (method !== 'GET') {
      let payload; try { payload = request.postDataJSON(); } catch { payload = null; }
      const lead = state.contacts.find(item => pathname === `${base}/crm/leads/${item.id}`);
      const keys = Object.keys(payload || {}).sort().join(',');
      const move = keys === 'etapa_id' && F.STAGES.some(item => item.id === payload.etapa_id);
      const tags = keys === 'tags' && Array.isArray(payload.tags) && payload.tags.every(item => F.TAGS.some(tag => tag.id === item));
      const edit = keys === 'historico_resumo,nome_contato,telefone_contato,valor_conversao' && typeof payload.nome_contato === 'string' && typeof payload.telefone_contato === 'string' && typeof payload.historico_resumo === 'string' && Number.isFinite(payload.valor_conversao);
      if (method !== 'PUT' || !lead || !(move || tags || edit)) { result.unexpectedWrites.push({ method, path: pathname, payload }); return route.fulfill({ status: 403, json: { detail: 'Unsafe action blocked by UX04 harness' } }); }
      result.writes.push({ method, path: pathname, payload: clone(payload), kind: move ? 'move' : tags ? 'tags' : 'edit', failed: Boolean(move && state.failMove) });
      if (state.pending === 'write') await new Promise(resolve => state.releases.push(resolve));
      if (move && state.failMove) return route.fulfill({ status: 503, json: { detail: 'Movimento sintético recusado; dados preservados.' } });
      if (move) state.stage.set(lead.id, payload.etapa_id); else Object.assign(lead, clone(payload));
      return route.fulfill({ json: clone(lead) });
    }
    let data;
    if (pathname === '/api/public/configuracoes-branding') data = { nome_sistema: 'Ferrioli Mídias', cor_primaria: '#235568' };
    else if (pathname === `${base}/inbox`) {
      if (state.pending === 'inbox') await new Promise(resolve => state.releases.push(resolve));
      if (state.inboxMode === 'error') return route.fulfill({ status: 503, json: { detail: 'Lista sintética indisponível' } });
      data = state.inboxMode === 'empty' ? [] : state.contacts;
    } else if (pathname === `${base}/crm`) {
      if (state.pending === 'crm') await new Promise(resolve => state.releases.push(resolve));
      if (state.crmMode === 'error') return route.fulfill({ status: 503, json: { detail: 'CRM sintético indisponível' } });
      data = { funil_id: F.id(700), funil_nome: 'Jornada Ferrioli sintética', etapas: state.crmMode === 'empty' ? [] : F.STAGES.map(stage => ({ ...stage, leads: state.contacts.filter(lead => state.stage.get(lead.id) === stage.id).map(lead => ({ ...lead, telefone: lead.telefone_contato })) })) };
    } else if (pathname === `${base}/crm/tags/oficiais`) data = F.TAGS;
    else if (pathname === `${base}/crm/tags/grupos`) data = [{ id: F.id(901), nome: 'Comercial sintético' }];
    else if (pathname === `${base}/transferencias/destinos`) data = [];
    else if (state.contacts.some(lead => pathname === `${base}/inbox/${lead.telefone_contato}/foto`)) data = { foto_url: null };
    else if (state.contacts.some(lead => pathname === `${base}/inbox/${lead.telefone_contato}/ai-state`)) data = { manual_enabled: true, processing_state: 'allowed', effective_state: 'authorized' };
    else if (state.contacts.some(lead => pathname === `${base}/inbox/${lead.telefone_contato}`)) {
      if (state.pending === 'messages') await new Promise(resolve => state.releases.push(resolve));
      data = pathname.endsWith(F.CONTACTS[0].telefone_contato) ? state.messages : [];
    } else if (state.contacts.some(lead => pathname === `${base}/inbox/leads/${lead.id}/journey`)) {
      if (state.pending === 'journey') await new Promise(resolve => state.releases.push(resolve));
      if (state.journeyError) return route.fulfill({ status: 503, json: { detail: 'Jornada sintética indisponível' } });
      data = F.journey(url);
    } else if (pathname === `${base}/analytics/commercial-funnel`) data = F.funnel(url);
    else if (pathname === `${base}/analytics/commercial-funnel/leads`) data = { items: state.contacts.slice(0, 25).map(lead => ({ lead_id: lead.id, display_name: lead.nome_contato, entry_at: lead.criado_em, last_activity_at: lead.ultima_mensagem_data, status: 'active', current_stage_key: 'entry', current_stage_label: 'Entrada', attribution: 'partial', reached_stage_keys: ['entry'] })), total: state.contacts.length, offset: 0, limit: 25, window: { observed_at: url.searchParams.get('observed_at') } };
    else { result.unexpectedReads.push(pathname); return route.fulfill({ status: 404, json: { detail: 'Unknown synthetic read' } }); }
    try { await route.fulfill({ json: clone(data) }); } catch (error) { if (!contextClosing) result.errors.push(`Mock route failed: ${error.message}`); }
  });
  let contextClosing = false;
  const deadline = setTimeout(() => { result.errors.push('Viewport validation exceeded 180 seconds'); state.releases.splice(0).forEach(resolve => resolve()); contextClosing = true; context.close().catch(() => {}); }, 180000);
  await context.addInitScript(({ user }) => {
    localStorage.setItem('user', JSON.stringify(user)); localStorage.setItem('token', 'synthetic-ux04-placeholder-only');
    window.__ux04Roots = []; const roots = new WeakSet();
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, inject() { return 1; }, onCommitFiberRoot(_renderer, root) { if (!roots.has(root)) { roots.add(root); window.__ux04Roots.push(root.containerInfo?.id || null); } }, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {} };
  }, { user });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/assets/') && response.status() !== 200) result.failedAssets.push(response.status()); });
  page.on('dialog', async dialog => { if (!state.expectedDialog || !dialog.message().includes(state.expectedDialog)) result.errors.push(`Unexpected ${dialog.type()} dialog: ${dialog.message()}`); state.expectedDialog = null; await dialog.dismiss(); });
  const check = name => { result.checks.push(name); console.log(JSON.stringify({ viewport: viewport.name, checkpoint: name })); };
  async function ux(name, operation) {
    try { await operation(); check(name); } catch (error) { if (!baseline) throw error; result.findings.push({ check: name, detail: error.message.slice(0, 400) }); }
  }
  async function screenshot(name) { const filename = `${viewport.name}-${name}.png`; await page.screenshot({ path: path.join(output, filename), fullPage: false, animations: 'disabled' }); result.screenshots.push(filename); }
  const search = () => page.getByPlaceholder('Buscar por nome ou telefone');
  const listScroller = () => baseline ? page.locator('aside').filter({ has: page.getByRole('heading', { name: 'Conversas', exact: true }) }).locator(':scope > div').last() : page.getByTestId('inbox-conversations');
  const messageScroller = () => baseline ? page.locator('section').last().locator(':scope > div').first() : page.getByTestId('inbox-message-scroll');
  const conversation = lead => page.locator(`[data-testid="inbox-conversations"] [data-lead-id="${lead.id}"]`).or(page.locator('aside').getByText(state.contacts.find(item => item.id === lead.id)?.nome_contato || lead.nome_contato, { exact: true })).first();
  const composer = () => page.getByPlaceholder('Digite uma mensagem');
  const timeline = () => page.getByTestId('commercial-journey');
  const card = lead => page.locator(`.ux04-crm-card[data-lead-id="${lead.id}"]`).or(page.getByRole('button').filter({ has: page.getByRole('heading', { name: lead.nome_contato, exact: true }) })).first();
  const stage = index => page.getByRole('region', { name: `Etapa ${F.STAGES[index].nome}`, exact: true });
  async function inboxReady() { await search().waitFor(); await conversation(F.CONTACTS[0]).waitFor(); }
  async function openAna() { await conversation(F.CONTACTS[0]).click(); await composer().waitFor(); await page.getByText(state.messages.at(-1).texto, { exact: true }).waitFor(); }
  async function closeJourney() { if (await timeline().count()) await timeline().getByRole('button', { name: 'Fechar jornada comercial', exact: true }).click(); }
  async function listView() {
    if (viewport.name === 'mobile' && await composer().isVisible()) await page.getByRole('button', { name: 'Voltar para conversas', exact: true }).or(page.getByTitle('Voltar para conversas', { exact: true })).first().click();
  }
  async function release() { state.pending = null; state.releases.splice(0).forEach(resolve => resolve()); }
  async function navigate(url) { await page.goto(`${origin}${url}`, { waitUntil: 'networkidle' }); }
  async function dragLead(lead, target) {
    if (viewport.name !== 'mobile' && !state.failMove) {
      const source = card(lead).getByRole('heading', { name: lead.nome_contato, exact: true });
      const destination = stage(target).getByRole('heading', { name: F.STAGES[target].nome, exact: true });
      await destination.scrollIntoViewIfNeeded(); await source.scrollIntoViewIfNeeded();
      await source.dragTo(destination); check('crm-native-pointer-drag-to-stage'); return;
    }
    // Native HTML5 DragEvents with a real Chromium DataTransfer exercise the
    // same React handlers for deterministic failure rollback and mobile, where
    // this verifies handlers rather than claiming a touch gesture was tested.
    const transfer = await page.evaluateHandle(() => new DataTransfer());
    await card(lead).dispatchEvent('dragstart', { dataTransfer: transfer });
    await stage(target).dispatchEvent('dragover', { dataTransfer: transfer });
    await stage(target).dispatchEvent('drop', { dataTransfer: transfer });
    await card(lead).dispatchEvent('dragend', { dataTransfer: transfer });
    await transfer.dispose();
  }
  async function contactForm() {
    await expect(card(state.contacts[0])).toHaveAttribute('draggable', 'true');
    // A card's tags are deliberately interactive and stop parent activation.
    // Click the contact heading, not an arbitrary center inside those controls.
    await card(state.contacts[0]).getByRole('heading', { name: state.contacts[0].nome_contato, exact: true }).click();
    const modal = baseline ? page.locator('form').filter({ has: page.locator('textarea') }).first() : page.getByRole('dialog');
    await modal.waitFor(); return modal;
  }
  function field(modal, name, fallback) { return baseline ? modal.locator(fallback) : modal.getByLabel(name, { exact: true }); }
  try {
    await navigate('/painel/inbox'); await inboxReady(); await screenshot('inbox-list');
    await ux('inbox-layout-no-document-overflow', async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2 && document.documentElement.scrollHeight <= innerHeight + 2)));
    await search().fill('Bruno'); await conversation(F.CONTACTS[1]).waitFor(); assert.equal(await page.getByText(F.CONTACTS[0].nome_contato, { exact: true }).count(), 0);
    await search().fill('READONLY_01'); await conversation(F.CONTACTS[0]).waitFor();
    await search().fill('Contato inexistente'); await page.getByText(/Nenhuma conversa encontrada para os filtros atuais/).waitFor();
    await screenshot('inbox-filter-empty'); await search().fill('');
    await page.getByRole('button', { name: 'Concluídos', exact: true }).click(); await conversation(F.CONTACTS[2]).waitFor(); assert.equal(await conversation(F.CONTACTS[0]).count(), 0);
    await page.getByRole('button', { name: 'Abertos', exact: true }).click();
    const department = page.locator('select').filter({ has: page.locator(`option[value="${F.id(901)}"]`) }).first();
    await department.selectOption(F.id(901)); await conversation(F.CONTACTS[0]).waitFor(); assert.equal(await conversation(F.CONTACTS[1]).count(), 0);
    await department.selectOption('Todos'); check('inbox-name-phone-status-department-filters');
    await ux('long-list-scroll-container', async () => { const list = listScroller(); await expect(list).toBeVisible(); assert.ok(await list.evaluate(el => el.scrollHeight > el.clientHeight)); await list.evaluate(el => { el.scrollTop = el.scrollHeight; }); await expect(conversation(F.CONTACTS[35])).toBeInViewport(); await list.evaluate(el => { el.scrollTop = 0; }); });
    const writesBeforeSelection = result.writes.length;
    await openAna(); await screenshot('inbox-conversation');
    assert.equal(result.writes.length, writesBeforeSelection, 'Selecting a conversation caused a write');
    assert.equal(await page.locator('article').count(), state.messages.length, 'History message count changed');
    assert.match(await page.locator('section').last().innerText(), /IA/); assert.match(await page.locator('section').last().innerText(), /atendente/i);
    await page.getByLabel('Reações', { exact: true }).waitFor(); await page.getByRole('img', { name: /Imagem|mídia|media/i }).first().waitFor();
    check('message-count-authorship-reactions-image-document');
    if (baseline) { const text = await page.locator('article').allInnerTexts(); state.messages.forEach((item, index) => assert.ok(text[index].includes(item.texto), 'Baseline message order changed')); }
    else { const ids = await page.locator('[data-message-id]').evaluateAll(nodes => nodes.map(node => node.dataset.messageId)); assert.deepEqual(ids, state.messages.map(item => item.id)); }
    assert.match(await page.locator('article').last().innerText(), /10:47/); check('message-order-and-time-preserved');
    await ux('message-outgoing-status-readable', async () => assert.match(await page.locator('article').last().innerText(), /Lida/));
    await ux('scroll-and-composer-in-viewport', async () => { const scroller = messageScroller(); assert.ok(await scroller.evaluate(el => el.scrollHeight > el.clientHeight)); await scroller.evaluate(el => { el.scrollTop = 0; }); await composer().click(); await expect(composer()).toBeFocused(); await expect(composer()).toBeInViewport(); await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; }); await expect(composer()).toBeInViewport(); });
    await composer().fill('Rascunho sintético: não enviar');
    if (viewport.name === 'mobile') {
      await listView(); await search().waitFor(); await screenshot('inbox-mobile-return');
      await ux('mobile-preserves-selected-contact-and-draft', async () => { const restore = page.getByRole('button', { name: 'Voltar à conversa selecionada', exact: true }); await expect(restore).toBeVisible(); await restore.click(); await expect(composer()).toHaveValue('Rascunho sintético: não enviar'); });
      if (!(await composer().isVisible())) await openAna();
    }
    await composer().fill('');
    const beforeTags = result.writes.length;
    await page.getByRole('button', { name: 'Tags', exact: true }).click();
    if (await page.getByText('Tags oficiais', { exact: true }).count()) await page.getByText('Tags oficiais', { exact: true }).click();
    const checkbox = page.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true });
    await checkbox.waitFor(); await checkbox.hover(); assert.equal(result.writes.length, beforeTags, 'Render/hover applied tags');
    state.pending = 'write'; await checkbox.click(); await expect.poll(() => result.writes.length).toBe(beforeTags + 1);
    assert.deepEqual(result.writes.at(-1).payload, { tags: F.TAGS.map(item => item.id) }); await expect(checkbox).toBeDisabled();
    await screenshot('inbox-tags-saving'); await release(); await expect(checkbox).toBeEnabled();
    await expect(checkbox).toBeChecked(); await checkbox.click(); await expect.poll(() => result.writes.length).toBe(beforeTags + 2); await expect(checkbox).not.toBeChecked();
    assert.deepEqual(result.writes.at(-1).payload, { tags: [F.TAGS[0].id] });
    await page.getByRole('button', { name: 'Tags', exact: true }).click(); check('inbox-tags-explicit-id-add-remove-no-render-hover-write');
    state.pending = 'journey'; await page.getByRole('button', { name: 'Jornada comercial', exact: true }).click();
    await timeline().getByText('Consultando fatos da jornada...', { exact: true }).waitFor(); await screenshot('journey-loading'); await release();
    await timeline().getByTestId('journey-events').waitFor(); assert.ok(await composer().isVisible());
    await ux('journey-preserves-composer-and-isolates-reader', async () => {
      const dialog = page.getByTestId('journey-dialog'); await expect(dialog).toBeVisible();
      await expect(composer()).toBeInViewport();
      assert.ok(await dialog.evaluate(node => node.matches(':modal')));
      await closeJourney(); await expect(composer()).toBeVisible(); await composer().click(); await expect(composer()).toBeFocused();
      await page.getByRole('button', { name: 'Jornada comercial', exact: true }).click(); await timeline().getByTestId('journey-events').waitFor();
    });
    await timeline().getByText('Resumo do contato', { exact: true }).click(); await timeline().getByText('Cobertura das fontes', { exact: true }).click(); assert.match(await timeline().innerText(), /Snapshots atuais não comprovam transições/); assert.match(await timeline().innerText(), /Desconhecido/);
    assert.ok(!(await timeline().innerText()).includes('NEVER_RENDER_PRIVATE'));
    await timeline().getByText('Ver evidência', { exact: true }).first().click(); await timeline().getByText('Fonte / registro', { exact: true }).first().waitFor();
    await timeline().getByRole('button', { name: 'Carregar mais fatos', exact: true }).click(); await timeline().getByText('Follow-up bloqueado', { exact: true }).waitFor(); assert.equal(await timeline().locator('[data-event-id]').count(), 3);
    await timeline().getByLabel('Categoria', { exact: true }).selectOption('payment'); await timeline().getByText('Pagamento em andamento', { exact: true }).waitFor(); assert.equal(await timeline().locator('[data-event-id]').count(), 1);
    assert.equal(await timeline().getByLabel('Fuso horário', { exact: true }).count(), 0); assert.match(await timeline().innerText(), /Horário de Brasília/); await screenshot('journey-context');
    await timeline().getByLabel('Categoria', { exact: true }).selectOption('checkout'); await timeline().getByText('Nenhum fato observado neste filtro', { exact: true }).waitFor();
    state.journeyError = true; await timeline().getByLabel('Categoria', { exact: true }).selectOption('message'); await timeline().getByRole('alert').waitFor(); await screenshot('journey-error'); assert.ok(await composer().isVisible());
    state.journeyError = false; await timeline().getByRole('button', { name: 'Atualizar jornada', exact: true }).click(); await timeline().getByText('Mensagem registrada', { exact: true }).waitFor();
    await closeJourney(); check('journey-loading-pagination-category-timezone-coverage-safe-evidence-empty-error-retry');
    assert.ok(sockets.length > 0, 'Inbox never opened an intercepted WebSocket');
    const wsMessage = { ...clone(F.MESSAGES[0]), id: F.id(650), texto: 'Atualização WebSocket sintética', criado_em: '2026-10-12T13:30:00Z' };
    state.messages.push(wsMessage);
    const event = { type: 'inbox.message.created', lead_id: F.CONTACTS[0].id, conversation_id: F.CONTACTS[0].conversation_id, telefone: F.CONTACTS[0].telefone_contato, message: wsMessage };
    sockets.at(-1).send(JSON.stringify(event)); await page.getByText(wsMessage.texto, { exact: true }).waitFor();
    sockets.at(-1).send(JSON.stringify(event)); await page.waitForTimeout(150); assert.equal(await page.getByText(wsMessage.texto, { exact: true }).count(), 1);
    sockets.at(-1).send(JSON.stringify({ type: 'inbox.lead.tags.updated', lead_id: F.CONTACTS[0].id, tags: [F.TAGS[0].id] }));
    check('simulated-websocket-message-and-tags-deduplication');
    const socketCount = sockets.length; sockets.at(-1).close({ code: 1012, reason: 'Synthetic reconnect test' });
    await ux('reconnect-feedback-preserves-conversation', async () => { await expect(page.getByTestId('inbox-connection')).toContainText(/reconect|conectando|atualiza/i); await expect(composer()).toBeInViewport(); });
    await expect.poll(() => sockets.length, { timeout: 15000 }).toBeGreaterThan(socketCount); await page.getByText(wsMessage.texto, { exact: true }).waitFor();
    await screenshot('inbox-reconnected'); check('simulated-websocket-disconnect-and-reconnect');
    await navigate('/painel/crm'); await card(F.CONTACTS[0]).waitFor(); await screenshot('crm-kanban');
    assert.equal(await page.getByRole('region', { name: /^Etapa / }).count(), F.STAGES.length);
    assert.match(await stage(1).innerText(), /1\.250,50/); await expect(card(F.CONTACTS[0]).getByText(F.CONTACTS[0].telefone_contato, { exact: true })).toBeVisible(); check('crm-real-stage-order-count-commercial-value-phone-and-empty-stage');
    if (!baseline) { const tagsBeforeDisclosure = result.writes.length; await card(F.CONTACTS[0]).getByText('Tags oficiais', { exact: true }).click(); await card(F.CONTACTS[0]).getByRole('checkbox', { name: F.TAGS[1].nome, exact: true }).hover(); assert.equal(result.writes.length, tagsBeforeDisclosure); assert.equal(await page.getByRole('dialog').count(), 0); await card(F.CONTACTS[0]).getByText('Tags oficiais', { exact: true }).click(); check('crm-card-tag-disclosure-hover-no-write-or-modal'); }
    await ux('crm-no-page-overflow-and-card-keyboard', async () => { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)); await card(F.CONTACTS[0]).focus(); await page.keyboard.press('Enter'); await expect(page.getByRole('dialog')).toBeVisible(); await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(card(F.CONTACTS[0])).toBeFocused(); });
    if (await page.getByRole('button', { name: 'Cancelar', exact: true }).isVisible()) await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
    const priorMove = result.writes.length; state.pending = 'write'; await dragLead(F.CONTACTS[0], 2); await expect.poll(() => result.writes.length).toBe(priorMove + 1);
    assert.deepEqual(result.writes.at(-1).payload, { etapa_id: F.STAGES[2].id }); await expect(stage(2).getByText(F.CONTACTS[0].nome_contato, { exact: true })).toBeVisible(); await screenshot('crm-moving'); await release();
    await page.getByText('Lead movido para a nova etapa.', { exact: true }).waitFor(); check('crm-html5-drag-drop-optimistic-state-and-exact-persistence');
    state.failMove = true; state.expectedDialog = 'Movimento sintético recusado'; await dragLead(F.CONTACTS[0], 1);
    await expect.poll(() => result.writes.length).toBe(priorMove + 2); await expect(stage(2).getByText(F.CONTACTS[0].nome_contato, { exact: true })).toBeVisible(); await expect(stage(1).getByText(F.CONTACTS[0].nome_contato, { exact: true })).toHaveCount(0); state.failMove = false;
    check('crm-failed-drag-drop-restores-server-stage');
    let modal = await contactForm(); const writesBeforeModal = result.writes.length;
    await ux('contact-initial-focus-and-tab-trap', async () => { await expect(modal.getByLabel('Nome', { exact: true })).toBeFocused(); const close = modal.getByRole('button', { name: 'Fechar edição do contato', exact: true }); await close.focus(); await page.keyboard.press('Shift+Tab'); await expect(modal.getByRole('button', { name: 'Salvar lead', exact: true })).toBeFocused(); await page.keyboard.press('Tab'); await expect(close).toBeFocused(); });
    await screenshot('contact-edit');
    await field(modal, 'Nome', 'input:not([type])').first().fill('Nome cancelado'); await modal.getByRole('button', { name: 'Cancelar', exact: true }).click(); assert.equal(result.writes.length, writesBeforeModal);
    modal = await contactForm(); assert.equal(await field(modal, 'Nome', 'input:not([type])').first().inputValue(), F.CONTACTS[0].nome_contato); check('contact-cancel-does-not-write');
    await modal.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true }).hover(); assert.equal(result.writes.length, writesBeforeModal);
    await modal.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true }).click(); await expect.poll(() => result.writes.length).toBe(writesBeforeModal + 1); assert.deepEqual(result.writes.at(-1).payload, { tags: F.TAGS.map(item => item.id) });
    await expect(modal.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true })).toBeChecked(); await modal.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true }).click(); await expect.poll(() => result.writes.length).toBe(writesBeforeModal + 2); await expect(modal.getByRole('checkbox', { name: F.TAGS[1].nome, exact: true })).not.toBeChecked();
    await field(modal, 'Nome', 'input:not([type])').first().fill('  Ana Sintética Editada  ');
    await field(modal, 'Telefone', 'input:not([type])').nth(baseline ? 1 : 0).fill('  READONLY_01  ');
    await field(modal, 'Resumo do histórico', 'textarea').fill('  Resumo sintético editado.  ');
    await field(modal, 'Valor da Venda (R$)', 'input[type="number"]').fill('1500.25');
    state.pending = 'write'; await modal.getByRole('button', { name: 'Salvar lead', exact: true }).click(); await expect.poll(() => result.writes.length).toBe(writesBeforeModal + 3);
    assert.deepEqual(result.writes.at(-1).payload, { nome_contato: 'Ana Sintética Editada', telefone_contato: 'READONLY_01', historico_resumo: 'Resumo sintético editado.', valor_conversao: 1500.25 });
    await expect(modal.getByRole('button', { name: 'Salvando...', exact: true })).toBeDisabled(); await screenshot('contact-saving'); await release();
    await page.getByRole('button', { name: 'Salvar lead', exact: true }).waitFor({ state: 'hidden' }); assert.equal(result.writes.filter(item => item.kind === 'tags').length, 4, 'Saving contact reapplied tags');
    await card(state.contacts[0]).waitFor(); check('contact-trimmed-fields-numeric-value-exact-save-no-tag-reapply');
    await navigate('/painel/funil'); const dashboard = page.getByTestId('funnel-dashboard'); await dashboard.getByTestId('funnel-total').waitFor();
    await dashboard.getByRole('button', { name: 'Ver contatos', exact: true }).click(); await dashboard.getByTestId('funnel-drill').getByTestId('funnel-lead-row').first().waitFor();
    const link = dashboard.getByRole('link', { name: 'Abrir jornada A04' }).first(); assert.match(await link.getAttribute('href'), /^\/painel\/inbox\?lead=[0-9a-f-]+&journey=1$/i);
    const horizon = result.api.filter(item => item.path.endsWith('/commercial-funnel/leads')).at(-1).query;
    await link.click(); await timeline().getByTestId('journey-events').waitFor(); await expect(composer()).toBeVisible(); await screenshot('funnel-journey-inbox');
    await closeJourney(); await page.getByRole('link', { name: '← Voltar ao funil com os filtros', exact: true }).click(); await dashboard.getByTestId('funnel-total').waitFor();
    const observedAt = new URLSearchParams(horizon).get('observed_at'); assert.ok(observedAt);
    assert.equal(new URLSearchParams(result.api.filter(item => item.path.endsWith('/commercial-funnel')).at(-1).query).get('observed_at'), observedAt, 'Return to funnel changed observation horizon');
    check('funnel-to-journey-inbox-deeplink-and-return');
    await navigate('/painel/crm'); state.crmMode = 'empty'; await page.reload({ waitUntil: 'networkidle' }); await page.getByText('Funil sem etapas', { exact: true }).waitFor(); await screenshot('crm-empty');
    state.crmMode = 'error'; await page.reload({ waitUntil: 'networkidle' }); await screenshot('crm-load-error');
    await ux('crm-load-error-feedback', async () => await expect(page.locator('[role="alert"]')).toContainText(/CRM|carregar|dados|funil/i));
    state.crmMode = 'normal'; state.pending = 'crm'; await page.reload({ waitUntil: 'domcontentloaded' }); await page.getByText('Carregando funil...', { exact: true }).waitFor(); await screenshot('crm-loading'); await release(); await card(state.contacts[0]).waitFor();
    state.inboxMode = 'empty'; await navigate('/painel/inbox'); await page.getByText(/Nenhuma conversa encontrada para os filtros atuais/).waitFor(); await screenshot('inbox-empty');
    state.inboxMode = 'error'; await page.reload({ waitUntil: 'networkidle' }); await screenshot('inbox-load-error');
    await ux('inbox-load-error-feedback', async () => await expect(page.locator('[role="alert"]')).toContainText(/conversa|carregar|lista|Inbox/i));
    state.inboxMode = 'normal'; state.pending = 'inbox'; await page.reload({ waitUntil: 'domcontentloaded' });
    await ux('inbox-loading-feedback', async () => await expect(page.getByText(/Carregando conversas|Atualizando conversas/).first()).toBeVisible()); await screenshot('inbox-loading'); await release(); await inboxReady();
    check('inbox-crm-synthetic-loading-empty-error-states');
    await navigate('/painel/conversas'); assert.ok(new URL(page.url()).pathname === '/painel/conversas'); await inboxReady();
    await navigate(`/painel/inbox?lead=${F.CONTACTS[2].id}&journey=1`); await timeline().getByTestId('journey-events').waitFor(); assert.match(await page.locator('.chat-conversation-header').innerText(), new RegExp(F.CONTACTS[2].nome_contato)); check('legacy-alias-and-closed-contact-deeplink-preserved');
    assert.equal((await page.evaluate(() => window.__ux04Roots)).length, 1, 'Multiple React roots');
    assert.deepEqual(result.unexpectedWrites, [], 'Unsafe/unexpected write attempted'); assert.deepEqual(result.unexpectedReads, [], 'API fixture missed endpoint'); assert.deepEqual(result.externalRequests, [], 'External request attempted'); assert.deepEqual(result.errors, [], 'Browser/runtime/socket error'); assert.deepEqual(result.failedAssets, [], 'Failed built asset');
    check('single-react-root-no-real-api-writes-sockets-or-external-actions'); result.status = 'PASS';
  } catch (error) { result.status = 'FAIL'; result.failure = error.message; result.failureCheckpoint = result.checks.at(-1) || 'initial-render'; process.exitCode = 1; try { await screenshot('failure'); } catch {} }
  finally { clearTimeout(deadline); await release(); contextClosing = true; await context.close(); fs.writeFileSync(path.join(output, `${viewport.name}.json`), JSON.stringify(result, null, 2)); }
  return result;
}
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}), args: ['--no-sandbox'] });
  const results = [];
  try { for (const viewport of viewports) { const result = await runViewport(browser, viewport); results.push(result); console.log(JSON.stringify({ viewport: viewport.name, status: result.status, checks: result.checks, findings: result.findings, failure: result.failure, failureCheckpoint: result.failureCheckpoint })); } }
  finally { await browser.close(); }
  const summary = { ...provenance, origin, mode: baseline ? 'BASELINE_SYNTHETIC' : 'CANDIDATE_SYNTHETIC', browser: browser.version(), status: results.every(result => result.status === 'PASS') ? 'PASS' : 'FAIL', results: results.map(({ viewport, status, checks, findings, failure, failureCheckpoint, screenshots, writes, errors, unexpectedWrites, unexpectedReads, externalRequests }) => ({ viewport, status, checks, findings, failure, failureCheckpoint, screenshots, mockedWrites: writes.length, runtimeErrors: errors, unexpectedWrites, unexpectedReads, externalRequests })) };
  fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2)); console.log(JSON.stringify(summary, null, 2));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
