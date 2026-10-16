const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const TENANT = '00000000-0000-0000-0000-000000000012';
const ACCOUNT = 'act_123456';
const PAGE = 'page_123';
const IG = 'ig_456';
const IMAGE = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="200"%3E%3Crect width="320" height="200" fill="%23dbeafe"/%3E%3C/svg%3E';
const pages = [{ id: PAGE, name: 'Página Sintética', instagram_account_id: IG, instagram_username: 'marca_teste' }];
const instagramPost = {
  source_id: 'ig_media_456', source_platform: 'instagram', caption: 'Publicação sintética de Reels',
  created_at: '2026-10-01T12:00:00Z', media_type: 'VIDEO', media_product_type: 'REELS',
  media_url: 'https://media.synthetic.invalid/reel.mp4', thumbnail_url: IMAGE, selection_token: 'synthetic-selection-token-instagram',
  id_mapping: { page_id: PAGE, post_id: null, media_id: 'ig_media_456', creative_id: null, comment_id: null },
  compatibility: { status: 'preliminary', selectable_for_mock: true, meta_acceptance_verified: false },
};
const facebookPost = {
  source_id: `${PAGE}_post_789`, source_platform: 'facebook', caption: 'Publicação sintética do Facebook',
  created_at: '2026-10-02T12:00:00Z', media_type: 'IMAGE', media_product_type: null,
  media_url: IMAGE, thumbnail_url: IMAGE, selection_token: 'synthetic-selection-token-facebook',
  id_mapping: { page_id: PAGE, post_id: `${PAGE}_post_789`, media_id: null, creative_id: null, comment_id: null },
  compatibility: { status: 'preliminary', selectable_for_mock: true, meta_acceptance_verified: false },
};
const facebookVideoPost = {
  source_id: `${PAGE}_post_video_790`, source_platform: 'facebook', caption: 'Vídeo sintético do Facebook',
  created_at: '2026-10-03T12:00:00Z', media_type: 'VIDEO', media_product_type: null,
  media_url: null, thumbnail_url: IMAGE, selection_token: 'synthetic-selection-token-facebook-video',
  id_mapping: { page_id: PAGE, post_id: `${PAGE}_post_video_790`, media_id: null, creative_id: null, comment_id: null },
  compatibility: { status: 'preliminary', selectable_for_mock: true, meta_acceptance_verified: false },
};

async function main() {
  const origin = new URL(process.env.A12_PANEL_ORIGIN || 'http://127.0.0.1:4173').origin;
  const screenshotDir = process.env.A12_SCREENSHOT_DIR || '/tmp';
  fs.mkdirSync(screenshotDir, { recursive: true });
  const launchOptions = { headless: true, args: ['--no-sandbox'] };
  if (process.env.A12_CHROMIUM_EXECUTABLE_PATH) launchOptions.executablePath = process.env.A12_CHROMIUM_EXECUTABLE_PATH;
  const browser = await chromium.launch(launchOptions);
  const results = { origin, viewports: [], mock_preview_calls: 0, existing_post_list_calls: 0, existing_post_list_requests: [], forbidden_writes: [], external_requests: [], page_errors: [] };
  try {
    for (const viewport of [
      { name: 'desktop', width: 1440, height: 900 },
      { name: 'notebook', width: 1280, height: 720 },
      { name: 'mobile', width: 390, height: 844 },
    ]) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      const scenarios = {
        featureEnabled: viewport.name !== 'desktop',
        assetFailures: 0,
        listRateLimits: 0,
        listMode: 'success',
        previewFailures: 0,
      };
      await context.addInitScript(({ tenant }) => {
        localStorage.setItem('token', 'synthetic-browser-session-only');
        localStorage.setItem('user', JSON.stringify({ id: 'synthetic-admin', nome: 'Admin Sintético', role: 'admin_empresa', empresa_id: tenant }));
      }, { tenant: TENANT });
      const page = await context.newPage();
      await page.exposeFunction('__setA12Scenario', (key, value) => { scenarios[key] = value; });
      page.on('pageerror', (error) => results.page_errors.push(`${viewport.name}:${error.message}`));
      await page.route('**/*', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin === 'https://media.synthetic.invalid') {
          return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(0) });
        }
        if (url.origin !== origin) {
          results.external_requests.push(url.origin);
          return route.abort();
        }
        if (!url.pathname.startsWith('/api/')) return route.continue();
        if (request.method() !== 'GET') {
          const isLocalPreview = request.method() === 'POST' && url.pathname === `/api/empresas/${TENANT}/traffic/meta/existing-posts/campaign-preview`;
          if (isLocalPreview) {
            results.mock_preview_calls += 1;
            if (scenarios.previewFailures > 0) {
              scenarios.previewFailures -= 1;
              return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'META_READ_TEMPORARILY_UNAVAILABLE', message: 'synthetic outage' } }) });
            }
            const body = JSON.parse(request.postData() || '{}');
            const isInstagram = String(body.selection_token).includes('instagram');
            const post = isInstagram ? instagramPost : facebookPost;
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
              state: 'simulated', creation_mode: 'local_mock', meta_write_performed: false,
              database_write_performed: false, external_side_effects: false,
              id_mapping: post.id_mapping,
              compatibility: { status: 'preliminary_compatible', meta_acceptance_verified: false, live_creation_allowed: false },
              builder_preview: {
                campaign: { name: 'Prévia A12 · campanha', objective: 'OUTCOME_SALES', status: 'PAUSED' },
                adset: { name: 'Prévia A12 · conjunto', daily_budget: '3000' },
                creative: isInstagram
                  ? { name: 'Synthetic Instagram creative', source_instagram_media_id: post.id_mapping.media_id, instagram_user_id: IG }
                  : { name: 'Synthetic Facebook creative', object_story_id: post.id_mapping.post_id },
                ad: { name: 'Prévia A12 · anúncio', creative: { creative_id: 'PREVIEW_CREATIVE_ID' }, status: 'PAUSED' },
              },
              graph_api_version: 'v26.0',
            }) });
          }
          results.forbidden_writes.push({ method: request.method(), path: url.pathname });
          return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic A12 smoke blocks all Meta writes.' }) });
        }
        let data = {};
        const prefix = `/api/empresas/${TENANT}/traffic/meta`;
        if (url.pathname.endsWith('/public/configuracoes-branding')) data = { nome_sistema: 'AgentOS — smoke sintético' };
        else if (url.pathname === `${prefix}/config`) data = {
          marketing_graph_version: 'v26.0',
          config: { configured: true, status: 'active', business_id: 'biz_synthetic', ad_account_id: ACCOUNT, page_id: PAGE, instagram_account_id: IG, whatsapp_connection_id: '11111111-1111-4111-8111-111111111111', dataset_id: null },
          active_official_whatsapps: [{ connection_id: '11111111-1111-4111-8111-111111111111', display_number: '+55••••0000', page_compatible: true, status: 'connected' }],
          existing_post_ads_enabled: scenarios.featureEnabled,
          official_whatsapp: { detected: true }, official_whatsapps: [],
          meta_write_enabled: false, campaign_create_enabled: false, adset_create_enabled: false, creative_create_enabled: false, ad_create_enabled: false,
        };
        else if (url.pathname === `${prefix}/adaccounts/123456`) data = { adaccount: { id: ACCOUNT, account_id: '123456', name: 'Conta Sintética', account_status: 1, currency: 'BRL', timezone_name: 'America/Sao_Paulo' } };
        else if (url.pathname === `${prefix}/assets`) {
          if (scenarios.assetFailures > 0) {
            scenarios.assetFailures -= 1;
            return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'META_READ_PERMISSION_REQUIRED' } }) });
          }
          data = {
            ad_account: { id: ACCOUNT, name: 'Conta Sintética' }, pages, selected_page: pages[0],
            instagram: { account_id: IG, username: 'marca_teste' }, whatsapps: [{ connection_id: '11111111-1111-4111-8111-111111111111', page_compatible: true }],
          };
        }
        else if (url.pathname === `${prefix}/credentials/status`) data = { marketing_readiness_status: 'READY_TENANT_OAUTH', token_present: true, permissions: { ads_read: true } };
        else if (url.pathname === `${prefix}/conversions`) data = { status: 'unknown', mappings: [] };
        else if (url.pathname === `${prefix}/conversions/optimization-eligibility`) data = { status: 'unknown', eligible_goals: [] };
        else if (url.pathname === `${prefix}/conversions/history`) data = { items: [] };
        else if (url.pathname === `${prefix}/campaigns`) data = { campaigns: [] };
        else if (url.pathname === `${prefix}/pages`) data = { pages };
        else if (url.pathname === `${prefix}/campaigns/conversion-readiness`) data = { ready: false, selected_goal: null, eligible_goals: [] };
        else if (url.pathname === `${prefix}/existing-posts`) {
          results.existing_post_list_calls += 1;
          results.existing_post_list_requests.push({ viewport: viewport.name, platform: url.searchParams.get('platform'), page_id: url.searchParams.get('page_id') });
          if (scenarios.listRateLimits > 0) {
            scenarios.listRateLimits -= 1;
            return route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'META_RATE_LIMIT' } }) });
          }
          const isInstagram = url.searchParams.get('platform') === 'instagram';
          const cursor = url.searchParams.get('after');
          if (scenarios.listMode === 'empty') {
            data = { items: [], next_cursor: null, has_more: false, query_scope: 'current_graph_page', write_performed: false };
          } else {
            const firstPage = isInstagram ? [instagramPost] : [facebookPost, facebookVideoPost];
            const nextPage = isInstagram ? [instagramPost, { ...instagramPost, source_id: 'ig_media_457', caption: 'Segunda publicação sintética' }] : [facebookPost];
            data = { items: cursor ? nextPage : firstPage, next_cursor: cursor ? null : 'cursor-synthetic-next', has_more: !cursor, query_scope: 'current_graph_page', write_performed: false };
          }
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
      });

      await page.goto(`${origin}/painel/trafego/meta`, { waitUntil: 'networkidle' });
      await page.getByRole('heading', { name: 'Tráfego / Meta Ads' }).waitFor();
      await page.getByRole('button', { name: 'Nova campanha' }).click();
      if (viewport.name === 'desktop') {
        const existingOption = page.getByRole('radio', { name: /Publicação existente/ });
        await existingOption.waitFor();
        assert.equal(await existingOption.isDisabled(), true, 'the unverified integration defaults off');
        assert.equal(results.existing_post_list_calls, 0, 'no Graph listing occurs while feature flag is off');
        await page.evaluate(() => window.__setA12Scenario('featureEnabled', true));
        await page.evaluate(() => window.__setA12Scenario('assetFailures', 1));
        await page.reload({ waitUntil: 'networkidle' });
        await page.getByRole('heading', { name: 'Tráfego / Meta Ads' }).waitFor();
        await page.getByRole('button', { name: 'Nova campanha' }).click();
        await page.getByRole('radio', { name: /Publicação existente/ }).check();
        await page.getByRole('alert').filter({ hasText: /não tem permissão/ }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Tentar novamente' }).count(), 0, 'permission failures require authorization rather than blind retry');
        await page.evaluate(() => window.__setA12Scenario('assetFailures', 0));
        await page.evaluate(() => window.__setA12Scenario('listRateLimits', 1));
        await page.reload({ waitUntil: 'networkidle' });
        await page.getByRole('heading', { name: 'Tráfego / Meta Ads' }).waitFor();
        await page.getByRole('button', { name: 'Nova campanha' }).click();
        await page.getByRole('radio', { name: /Publicação existente/ }).check();
        await page.getByRole('alert').filter({ hasText: /limitou temporariamente/ }).waitFor();
        await page.getByRole('button', { name: 'Tentar novamente' }).click();
        await page.getByRole('button', { name: /Publicação sintética de Reels/ }).waitFor();
      }
      await page.getByRole('radio', { name: /Publicação existente/ }).waitFor();
      await page.getByRole('radio', { name: /Publicação existente/ }).check();
      await page.getByLabel('Orçamento diário').fill('30,00');
      const instagramCard = page.getByRole('button', { name: /Publicação sintética de Reels/ });
      await instagramCard.click();
      await page.getByText('Publicação selecionada', { exact: true }).waitFor();
      await page.locator('video[aria-label="Prévia de vídeo"]').waitFor();
      const instagramSimulate = page.getByRole('button', { name: 'Simular campanha e anúncio' });
      if (!(await instagramSimulate.count())) {
        throw new Error(`campaign preview button missing: ${await page.locator('[aria-label="Selecionar publicação existente"]').innerText()}`);
      }
      assert.equal(await instagramSimulate.isDisabled(), false, `campaign preview should be enabled: ${await page.locator('[aria-label="Selecionar publicação existente"]').innerText()}`);
      if (viewport.name === 'desktop') {
        await page.evaluate(() => window.__setA12Scenario('previewFailures', 1));
        await instagramSimulate.click();
        await page.getByRole('alert').filter({ hasText: /temporariamente indisponível/ }).waitFor();
        assert.equal(await page.getByTestId('existing-post-preview-result').count(), 0, 'an API error is never displayed as a successful preview');
        await page.getByRole('button', { name: 'Tentar validar novamente' }).click();
      } else {
        await instagramSimulate.click();
      }
      await page.getByTestId('existing-post-preview-result').waitFor();
      const instagramPreview = await page.getByTestId('existing-post-preview-result').innerText();
      assert.match(instagramPreview, /nada foi salvo ou criado na Meta/i);
      assert.match(instagramPreview, /campaign/);
      assert.match(instagramPreview, /adset/);
      assert.match(instagramPreview, /creative/);
      assert.match(instagramPreview, /ad/);
      assert.match(instagramPreview, /source_instagram_media_id/);
      assert.doesNotMatch(instagramPreview, /object_story_id/);
      if (viewport.name === 'desktop') {
        const loadMore = page.getByRole('button', { name: 'Carregar mais publicações' });
        await loadMore.click();
        await page.getByText('Segunda publicação sintética').waitFor();
        assert.equal(await page.getByRole('button', { name: /Publicação sintética de Reels/ }).count(), 1, 'pagination deduplicates overlapping items');
        await page.evaluate(() => window.__setA12Scenario('listMode', 'empty'));
        await page.getByRole('button', { name: 'Atualizar lista' }).click();
        await page.getByText('Nenhuma publicação retornada para esta conta.').waitFor();
        await page.evaluate(() => window.__setA12Scenario('listMode', 'success'));
        await page.getByRole('button', { name: 'Atualizar lista' }).click();
        await page.getByRole('button', { name: /Publicação sintética de Reels/ }).waitFor();
      }
      await page.getByPlaceholder('Buscar nas publicações carregadas').fill('texto inexistente');
      await page.getByText(/Nenhuma publicação carregada corresponde à busca/).waitFor();
      await page.getByPlaceholder('Buscar nas publicações carregadas').fill('');
      await page.getByLabel('Origem').selectOption('facebook');
      await page.getByLabel('Página \/ conta vinculada').selectOption(PAGE);
      const facebookVideoCard = page.getByRole('button', { name: /Vídeo sintético do Facebook/ });
      await facebookVideoCard.waitFor();
      assert.equal(await facebookVideoCard.isEnabled(), true, 'Facebook video with poster is selectable for a local mock');
      await page.getByText(/Vídeo compatível · exibindo miniatura; reprodução indisponível/).waitFor();
      assert.equal(await facebookVideoCard.locator('video').count(), 0, 'a Facebook thumbnail is never treated as a playable video');
      await page.getByRole('button', { name: /Publicação sintética do Facebook/ }).click();
      await page.getByRole('button', { name: 'Simular campanha e anúncio' }).click();
      await page.getByTestId('existing-post-preview-result').waitFor();
      const facebookPreview = await page.getByTestId('existing-post-preview-result').innerText();
      assert.match(facebookPreview, /object_story_id/);
      assert.doesNotMatch(facebookPreview, /source_instagram_media_id/);
      assert.equal(await page.locator('#root').count(), 1, 'single React root');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(overflow <= 1, `${viewport.name} horizontal overflow: ${overflow}px`);
      await page.screenshot({ path: path.join(screenshotDir, `a12-existing-posts-${viewport.name}.png`), fullPage: true });
      if (viewport.name === 'mobile') {
        await page.getByLabel('Origem').selectOption('instagram');
      }
      await page.getByRole('radio', { name: /Novo criativo/ }).check();
      await page.locator('input[type=file][accept="image/jpeg,image/png"]').waitFor();
      await page.screenshot({ path: path.join(screenshotDir, `a12-new-creative-${viewport.name}.png`), fullPage: true });
      results.viewports.push({ name: viewport.name, width: viewport.width, height: viewport.height, overflow_px: overflow, root_count: 1, facebook_and_instagram_preview: true, existing_post_screenshot: true, new_creative_form_visible: true });
      await context.close();
    }
    assert.equal(results.mock_preview_calls, 7, 'only local campaign-builder preview POSTs were allowed, including one failed read-only simulation retry');
    assert.deepEqual(results.forbidden_writes, [], 'no external or live Meta write path was called');
    assert.deepEqual(results.external_requests, [], 'all APIs and media use synthetic fixtures');
    assert.deepEqual(results.page_errors, [], 'browser has no unhandled JavaScript exceptions');
    console.log(JSON.stringify({ ...results, synthetic_only: true, result: 'A12_BROWSER_SMOKE_PASS' }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
