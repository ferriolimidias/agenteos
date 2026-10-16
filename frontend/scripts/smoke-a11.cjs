const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const TENANT = '00000000-0000-0000-0000-000000000011';
const VERSION = '10000000-0000-0000-0000-000000000011';
const AD = 'synthetic_ad_123';
const arm = {
  exposed: 2, assigned: 2, generation_attempts: 0, generation_failed: 0, guard_blocked: 0,
  response_after_window: { numerator: 0, denominator: 0, state: 'unknown' },
  verified_purchase_after_30d: { numerator: 0, denominator: 0, state: 'unknown' },
  commercial_stage_advance_after_30d: { numerator: 0, denominator: 0, state: 'unknown' },
  generation_error_rate: { numerator: 0, denominator: 0, value: null, state: 'unknown' },
  guard_block_rate: { numerator: 0, denominator: 0, value: null, state: 'unknown' },
  latency_p50_ms: { value: null }, input_tokens_mean: { value: null }, cost_usd: { state: 'unknown' },
  ad_assisted_outcomes: {
    state: 'partial', prompt_exposures: 2, unresolved_or_ambiguous_ad_exposures: 0,
    rows: [{
      ad_id: AD, prompt_exposures: 2, mature_exposures: 1,
      commercial_advances_after_30d: { numerator: 1, denominator: 1, state: 'partial' },
      verified_purchases_after_30d: { numerator: 0, denominator: 1, state: 'partial' },
    }],
  },
};
const dashboard = {
  modules: [{
    id: 'qualification', title: 'Qualificação', state: 'OFFLINE',
    stable_version_id: VERSION, active_version_id: VERSION,
    versions: [{
      id: VERSION, version: 1, content: 'Use somente fatos confirmados e peça esclarecimento quando faltar contexto.',
      content_hash: 'a'.repeat(64), source_version_id: null,
      change_summary: 'Referência sintética A07',
      hypothesis: 'Preservar a continuidade sem inventar informações.',
      primary_metric: 'response_after_7d', approved: false,
      created_at: '2026-10-15T12:00:00Z',
    }],
  }],
  experiments: [{
    id: 'synthetic-experiment', module_key: 'qualification', status: 'DRAFT', candidate_percent: 50,
    metrics: { distribution: { control_percent: 50, candidate_percent: 50 }, maturity_days: 7, truncated: false, by_variant: { control: arm, candidate: arm } },
  }], audit: [], evaluator: { model_id: null, cost_state: 'unknown' },
  live: {
    ab_live: false, automatic_promotion_live: false, automatic_rollback_live: false,
    automatic_prompt_mutation: false, pilot_tenant_enabled: false, live_activation: 'OFF',
    reason: 'Flags OFF e nenhum tenant autorizado para piloto.',
  },
};

async function main() {
  const origin = new URL(process.env.A11_PANEL_ORIGIN || 'http://127.0.0.1:4173').origin;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const results = { origin, viewports: [], apiWrites: [], pageErrors: [], externalRequests: [] };
  try {
    for (const viewport of [
      { name: 'desktop', width: 1440, height: 900 },
      { name: 'mobile', width: 390, height: 844 },
    ]) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      await context.addInitScript(({ tenant }) => {
        localStorage.setItem('token', 'synthetic-browser-placeholder-only');
        localStorage.setItem('user', JSON.stringify({ id: 'synthetic-admin', nome: 'Admin Sintético', role: 'admin_empresa', empresa_id: tenant }));
      }, { tenant: TENANT });
      const page = await context.newPage();
      page.on('pageerror', error => results.pageErrors.push(`${viewport.name}:${error.message}`));
      await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== origin) {
          results.externalRequests.push(url.origin);
          return route.abort();
        }
        if (!url.pathname.startsWith('/api/')) return route.continue();
        if (request.method() !== 'GET') {
          results.apiWrites.push({ method: request.method(), path: url.pathname });
          return route.fulfill({ status: 405, json: { detail: 'Synthetic browser smoke permits GET only.' } });
        }
        let data = {};
        if (url.pathname.endsWith('/public/configuracoes-branding')) data = { nome_sistema: 'AgentOS — teste sintético' };
        else if (url.pathname === `/api/empresas/${TENANT}/prompt-optimizer`) data = dashboard;
        else if (url.pathname === `/api/empresas/${TENANT}/prompt-optimizer/evaluator/models`) data = { items: [], credential_status: 'not_configured', stale: false };
        else if (url.pathname === `/api/empresas/${TENANT}/prompt-optimizer/suggestions`) data = { state: 'unknown', source: 'synthetic-fixture', limitation: 'Sem dados reais nesta simulação.', items: [] };
        else if (url.pathname === '/api/admin/configuracoes') data = { nome_sistema: 'AgentOS — teste sintético' };
        else if (url.pathname === '/api/empresas/') data = [{ id: TENANT, nome_empresa: 'Tenant Sintético', openai_api_key_configurada: true, credenciais_canais: {} }];
        else if (url.pathname === `/api/empresas/${TENANT}/ia-config`) data = { modelo_ia: 'gpt-4o-mini', openai_configurada: true };
        else if (url.pathname === `/api/empresas/${TENANT}/crm/tags/oficiais`) data = [];
        else if (url.pathname === `/api/empresas/${TENANT}/monitor/funnel`) data = { status: 'unknown' };
        else if (url.pathname === `/api/admin/empresas/${TENANT}/openai-models`) data = { items: [
          { id: 'gpt-4o-mini', name: 'gpt-4o-mini', availability: 'listed', compatibility: 'known_compatible', validation_required: false },
          { id: 'synthetic-new-chat-model', name: 'synthetic-new-chat-model', availability: 'listed', compatibility: 'needs_validation', validation_required: true },
        ], credential_status: 'configured', updated_at: '2026-10-15T12:00:00Z', stale: false, source: 'synthetic-fixture' };
        else if (/\/empresas\/?$/.test(url.pathname)) data = [];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
      });
      await page.goto(`${origin}/painel/otimizador-prompts`, { waitUntil: 'networkidle' });
      await page.getByRole('heading', { name: 'Prompt Optimizer · A11' }).waitFor();
      await page.getByText('Atendimento LIVE permanece desligado.').waitFor();
      await page.getByText('Módulos e versões imutáveis').waitFor();
      await page.getByText('Evidências por anúncio').first().waitFor();
      await page.getByText(AD, { exact: false }).first().waitFor();
      await page.getByText('Resultado temporal; não prova causalidade do anúncio nem do prompt.').first().waitFor();
      assert.equal(await page.locator('#root').count(), 1, 'single React root');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(overflow <= 1, `${viewport.name} horizontal overflow: ${overflow}px`);
      if (viewport.name === 'mobile') {
        await page.getByRole('button', { name: 'Abrir menu' }).click();
        await page.getByRole('link', { name: 'Prompt Optimizer A11' }).waitFor();
        await page.getByRole('dialog', { name: 'Navegação do cliente' }).getByRole('button', { name: 'Fechar menu' }).click();
      }
      await page.screenshot({ path: `/tmp/a11-${viewport.name}.png`, fullPage: true });
      await page.getByText(AD, { exact: false }).first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: `/tmp/issue33-a11-prompt-ad-${viewport.name}.png` });
      results.viewports.push({ name: viewport.name, width: viewport.width, height: viewport.height, overflow_px: overflow, root_count: 1, live_activation: 'OFF' });
      await page.goto(`${origin}/admin/empresas/${TENANT}?tab=agente`, { waitUntil: 'networkidle' });
      await page.getByText('Modelo principal').waitFor();
      await page.getByText('Este modelo é compartilhado pelo atendimento e pelo Monitor de Conversão.').waitFor();
      await page.getByRole('button', { name: 'Atualizar modelos' }).waitFor();
      await page.getByLabel('Buscar modelos OpenAI').fill('synthetic-new-chat-model');
      await page.locator('#company-field-6').selectOption('synthetic-new-chat-model');
      await page.getByRole('button', { name: 'Validar modelo' }).waitFor();
      assert.equal(await page.locator('#root').count(), 1, 'single React root on Super Admin catalog');
      const catalogOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(catalogOverflow <= 1, `${viewport.name} catalog horizontal overflow: ${catalogOverflow}px`);
      results.viewports.at(-1).catalog_overflow_px = catalogOverflow;
      results.viewports.at(-1).unknown_model_requires_explicit_validation = true;
      await page.screenshot({ path: `/tmp/a11-catalog-${viewport.name}.png`, fullPage: true });
      await context.close();
    }
    assert.deepEqual(results.apiWrites, [], 'no POST/PUT/DELETE was issued');
    assert.deepEqual(results.externalRequests, [], 'external APIs are intercepted');
    assert.deepEqual(results.pageErrors, [], 'browser has no page errors');
    console.log(JSON.stringify({ ...results, synthetic_only: true, result: 'A11_BROWSER_SMOKE_PASS' }));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
