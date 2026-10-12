// All APIs/sockets/external destinations intercepted. No real tenant/provider/action.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const pw=process.env.PLAYWRIGHT_MODULE_PATH||'playwright',{chromium}=require(pw),expect=require(pw+'/test').expect.configure({timeout:10000});
const F=require('./fixtures-a07-modular.cjs');
const origin=new URL(process.env.A07_ORIGIN||'http://127.0.0.1:18880').origin,out=process.env.A07_EVIDENCE_DIR,sha=process.env.A07_SOURCE_SHA;
assert.ok(out,'Explicit evidence directory required');assert.match(sha||'',/^[a-f0-9]{40}$/);fs.mkdirSync(out,{recursive:true});
const clone=x=>JSON.parse(JSON.stringify(x));
async function run(browser,v){
 const r={viewport:v.name,status:'RUNNING',checks:[],errors:[],unexpected:[],requests:[]},state={tenant:F.TENANT,failure:null,hold:false,releases:[],providerError:false,loadFailure:false,configured:false};
 const context=await browser.newContext({viewport:{width:v.width,height:v.height},locale:'pt-BR',serviceWorkers:'block',reducedMotion:'reduce'});let closing=false;
 await context.routeWebSocket('**/*',socket=>socket.close());
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),endpoint=url.pathname,method=req.method();
  if(url.origin!==origin){r.unexpected.push('external_destination');return route.abort();}
  if(!endpoint.startsWith('/api/'))return route.continue();
  const match=endpoint.match(/^\/api\/empresas\/([^/]+)\/atendimento-modular\/(status|examples|preview)$/);
  if(match){
   const tenant=match[1],suffix=match[2];
   if(method==='GET'&&['status','examples'].includes(suffix)){if(state.loadFailure)return route.fulfill({status:503,json:{detail:'PRIVATE_LOAD_DETAIL'}});const status=clone(F.status);status.provider.jev_configured=state.configured;status.provider.status=state.configured?'configured':'not_configured';return route.fulfill({json:suffix==='status'?status:{items:clone(F.examples)}});}
   if(method==='POST'&&suffix==='preview'){
    const body=req.postDataJSON();assert.deepEqual(Object.keys(body).sort(),[body.example_id?'example_id':'message','context_mode','channel','use_jev'].sort());
    assert.equal(Boolean(body.example_id)===Boolean(body.message),false);if(body.message)assert.equal(body.use_jev,false);
    r.requests.push({tenant,body});const failure=state.failure,providerError=state.providerError;
    if(state.hold||failure==='timeout')await new Promise(resolve=>state.releases.push(resolve));
    if(closing)return;
    try{if(typeof failure==='number')return await route.fulfill({status:failure,json:{detail:'PRIVATE_PROVIDER_SQL_DETAIL'}});if(failure==='network')return await route.abort('connectionreset');return await route.fulfill({json:F.preview(body,tenant,{providerError})});}catch{ /* Aborted old-scope/timeout request is expected. */ }return;
   }
  }
  if(method!=='GET'){r.unexpected.push({method,endpoint});return route.abort();}
  let data={};
  if(endpoint==='/api/public/configuracoes-branding')data={nome_sistema:'Ferrioli Mídias'};
  else if(endpoint.endsWith('/agentes'))data=[];
  else if(endpoint.endsWith('/ia-config'))data={nome_agente:'Agente sintético'};
  else if(endpoint.endsWith('/whatsapp/status'))data={conectado:false};
  else{r.unexpected.push({method,endpoint});return route.fulfill({status:404,json:{detail:'Blocked unexpected API'}});}
  return route.fulfill({json:data});
 });
 await context.addInitScript(({tenant})=>{localStorage.setItem('user',JSON.stringify({id:'synthetic-operator',role:'admin_empresa',empresa_id:tenant}));localStorage.setItem('token','synthetic-a07-token');window.__a07Roots=[];const seen=new WeakSet();window.__REACT_DEVTOOLS_GLOBAL_HOOK__={supportsFiber:true,inject(){return 1},onCommitFiberRoot(_,root){if(!seen.has(root)){seen.add(root);window.__a07Roots.push(root.containerInfo?.id)}},onCommitFiberUnmount(){},onPostCommitFiberRoot(){}};},{tenant:F.TENANT});
 const page=await context.newPage();page.on('pageerror',e=>r.errors.push(e.message));page.on('dialog',async d=>{r.errors.push('Unexpected native dialog');await d.dismiss()});
 const area=()=>page.locator('.a07-preview'),result=()=>area().getByRole('region',{name:'Resultado da simulação modular'}),button=()=>area().getByRole('button',{name:'Simular resposta',exact:true});
 const check=name=>{r.checks.push(name);console.log(JSON.stringify({viewport:v.name,check:name}));};
 const release=()=>{state.hold=false;state.failure=null;state.releases.splice(0).forEach(fn=>fn());};
 const submit=async()=>{await button().click();await result().waitFor();await expect(result()).toBeFocused();};
 const select=async(id,mode='complete')=>{await area().getByLabel('Entrada da simulação').selectOption('example');await area().getByLabel('Exemplo',{exact:true}).selectOption(id);await area().getByLabel('Contexto sintético').selectOption(mode);};
 try{
  await page.goto(origin+'/painel/agentes',{waitUntil:'networkidle'});
  if(process.env.A07_PHASE==='before'){await expect(page.getByRole('heading',{name:'Agente de Atendimento',exact:true})).toBeVisible();assert.equal(await area().count(),0);await page.screenshot({path:path.join(out,v.name+'-before.png')});check('baseline-controls-present-preview-absent');r.status='PASS';return r;}
  await expect(area().getByText('Produção: LEGACY',{exact:true})).toBeVisible();await expect(area().getByText('JEV não configurado',{exact:true})).toBeVisible();assert.equal(await area().getByRole('button',{name:/Consultar JEV/}).count(),0);check('legacy-readonly-no-production-or-provider-toggle');
  await select('multi_price_trust_availability');await submit();for(const text of ['Preços e condições','Confiança e garantias','Disponibilidade'])await expect(result().getByLabel('Módulos selecionados').getByText(new RegExp(text))).toBeVisible();
  await expect(result().getByText('Etapa principal: Consideração · sintética',{exact:true})).toBeVisible();await expect(result().getByText('Assuntos simultâneos: Preços e condições, Confiança e garantias, Disponibilidade',{exact:true})).toBeVisible();await expect(result().getByLabel('Módulos selecionados').getByText('Preços e condições · 1.0.0',{exact:true})).toBeVisible();await expect(result().getByText('Seleção local determinística; JEV não utilizado',{exact:true})).toBeVisible();assert.ok(await result().getByText('Não medido',{exact:true}).count()>=6);await expect(result().getByText('Resposta legada simulada',{exact:true})).toBeVisible();check('three-modules-single-response-stage-version-local-unknownmetrics');
  await page.screenshot({path:path.join(out,v.name+'-complete.png')});
  await area().getByLabel('Comparar resposta legada').uncheck();await expect(result().getByText('Resposta legada simulada',{exact:true})).toHaveCount(0);await area().getByLabel('Comparar resposta legada').check();check('legacy-comparison');
  await select('incomplete','incomplete');await submit();await expect(result().getByText(/Faltam fatos comerciais/)).toBeVisible();await expect(result().getByText(/Preciso confirmar preço/)).toBeVisible();check('incomplete-abstention');
  await select('conflicting','conflicting');await submit();await expect(result().getByText(/fatos conflitantes/)).toBeVisible();await expect(result().getByText(/Fallback seguro/)).toBeVisible();check('conflict-fallback');
  await select('human_handoff');await submit();await expect(result().getByLabel('Módulos selecionados').getByText(/Transferência humana/)).toBeVisible();assert.equal(await result().getByLabel('Módulos selecionados').getByText(/Oferta e fechamento/).count(),0);check('human-priority');
  await select('injection','incomplete');await submit();await expect(result().getByText(/política de segurança limitou/)).toBeVisible();assert.ok(!(await result().innerText()).includes('PRIVATE'));check('injection-safety');
  state.configured=true;await page.evaluate(()=>{localStorage.setItem('token','synthetic-configured-session');document.dispatchEvent(new Event('visibilitychange'));});await expect(area().getByText('JEV configurado',{exact:true})).toBeVisible();await select('schedule_multi');await expect(area().getByRole('button',{name:/Consultar JEV/})).toBeVisible();await area().getByRole('button',{name:/Consultar JEV/}).click();await expect(result().getByText(/JEV: decisão externa observada em exemplo sintético/)).toBeVisible();await expect(result().getByText('Assuntos simultâneos: Preço, Confiança, Disponibilidade',{exact:true})).toBeVisible();assert.equal(r.requests.at(-1).body.use_jev,true);check('explicit-eligible-provider-mocked');
  state.providerError=true;await area().getByRole('button',{name:/Consultar JEV/}).click();await expect(result().getByText(/Fallback seguro/)).toBeVisible();await expect(result().getByText(/JEV não utilizado/)).toBeVisible();state.providerError=false;check('provider-error-local-fallback-mocked');
  await area().getByLabel('Entrada da simulação').selectOption('custom');await area().getByLabel('Mensagem personalizada',{exact:true}).fill('Quanto custa, tem garantia e quando começa?');assert.equal(await area().getByRole('button',{name:/Consultar JEV/}).count(),0);
  for(const failure of [400,403,422,500,503,'network','timeout']){state.failure=failure;await button().click();await expect(area().getByRole('alert')).toBeFocused({timeout:18000});await expect(area().getByLabel('Mensagem personalizada',{exact:true})).toHaveValue('Quanto custa, tem garantia e quando começa?');assert.ok(!(await area().innerText()).includes('PRIVATE_PROVIDER_SQL_DETAIL'));await expect(button()).toBeEnabled();release();check('error-'+failure+'-draft-focus-retry');}
  await submit();assert.equal(r.requests.at(-1).body.use_jev,false);check('custom-retry-local-only');
  await area().getByLabel('Canal do cenário').selectOption('instagram');await submit();assert.equal(r.requests.at(-1).body.channel,'instagram');await area().getByLabel('Canal do cenário').selectOption('whatsapp');check('channel-simulation-isolated');
  state.hold=true;const n=r.requests.length;await button().click();await expect(area().getByRole('button',{name:'Simulando...',exact:true})).toBeDisabled();await area().locator('form').dispatchEvent('submit');await expect.poll(()=>r.requests.length).toBe(n+1);release();await result().waitFor();check('synchronous-double-submit-guard');
  state.hold=true;await button().click();await expect.poll(()=>state.releases.length).toBe(1);state.tenant=F.TENANT_B;await page.evaluate(tenant=>{const user=JSON.parse(localStorage.getItem('user'));user.empresa_id=tenant;localStorage.setItem('user',JSON.stringify(user));localStorage.setItem('token','synthetic-a07-token-b');document.dispatchEvent(new Event('visibilitychange'));},state.tenant);
  await expect(area().getByLabel('Entrada da simulação')).toHaveValue('example');await expect(button()).toBeEnabled();release();await expect(result()).toHaveCount(0);await select('multi_price_trust_availability');await submit();assert.equal(r.requests.at(-1).tenant,F.TENANT_B);await expect(result().getByText(/Tenant 2/)).toBeVisible();check('late-tenant-result-dropped-new-scope-only');
  state.hold=true;await button().click();await expect.poll(()=>state.releases.length).toBe(1);await page.evaluate(()=>{localStorage.setItem('token','synthetic-new-session-same-tenant');document.dispatchEvent(new Event('visibilitychange'));});await expect(button()).toBeEnabled();release();await expect(result()).toHaveCount(0);check('late-session-result-dropped');
  await page.screenshot({path:path.join(out,v.name+'-final.png')});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert.deepEqual(await page.evaluate(()=>window.__a07Roots),['root']);assert.deepEqual(r.errors,[]);assert.deepEqual(r.unexpected,[]);check('single-root-responsive-no-external-or-commercial-effects');r.status='PASS';
 }catch(e){r.status='FAIL';r.failure=e.message;await page.screenshot({path:path.join(out,v.name+'-failure.png')}).catch(()=>{});}
 finally{closing=true;release();await context.close();fs.writeFileSync(path.join(out,v.name+'.json'),JSON.stringify(r,null,2));}
 return r;
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const results=[];try{for(const v of [{name:'desktop',width:1440,height:1000},{name:'notebook',width:1280,height:800},{name:'mobile',width:390,height:844}])results.push(await run(browser,v));}finally{await browser.close();}const summary={status:results.every(r=>r.status==='PASS')?'PASS':'FAIL',source_sha:sha,origin,phase:process.env.A07_PHASE||'after',script_sha256:crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),fixture_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'fixtures-a07-modular.cjs'))).digest('hex'),mode:'ALL_API_SOCKETS_EXTERNAL_INTERCEPTED',results};fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));if(summary.status!=='PASS')process.exitCode=1;})().catch(e=>{console.error(e.message);process.exitCode=1;});
