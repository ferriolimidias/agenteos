// Issue23: all API effects fulfilled locally; never use real auth/contact data.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const expect=require((process.env.PLAYWRIGHT_MODULE_PATH||'playwright')+'/test').expect.configure({timeout:10000});
const F=require('./fixtures-ux04-operations.cjs'),clone=x=>JSON.parse(JSON.stringify(x));
const origin=new URL(process.env.CRM_ORIGIN||'http://127.0.0.1:18871').origin;
const out=process.env.CRM_EVIDENCE_DIR;assert.ok(out,'Explicit evidence directory required');fs.mkdirSync(out,{recursive:true});
const sourceSha=process.env.CRM_SOURCE_SHA;assert.match(sourceSha||'',/^[0-9a-f]{40}$/);
const viewports=[{name:'desktop',width:1440,height:1000},{name:'notebook',width:1280,height:800},{name:'mobile',width:390,height:844}];
async function run(browser,viewport){
 const r={viewport,sourceSha,mode:'ALL_API_AND_SOCKETS_INTERCEPTED',checks:[],errors:[],unexpected:[],writes:[],screenshots:[],status:'RUNNING'};
 const state={tenant:F.TENANT,contacts:clone(F.CONTACTS.slice(0,2)),failure:null,hold:false,releases:[],forceWrongId:false};
 const ctx=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},serviceWorkers:'block',locale:'pt-BR',timezoneId:'UTC',reducedMotion:'reduce'});
 await ctx.routeWebSocket('**/*',socket=>socket.close());
 let closing=false;const timeout=setTimeout(()=>{r.errors.push('Harness deadline exceeded');state.releases.splice(0).forEach(fn=>fn());ctx.close();},180000);
 await ctx.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),endpoint=url.pathname,method=req.method();
  if(url.origin!==origin){r.unexpected.push('external');return route.abort();}
  if(!endpoint.startsWith('/api/'))return route.continue();
  const base=`/api/empresas/${state.tenant}`;
  if(method!=='GET'){
   const lead=state.contacts.find(x=>endpoint===`${base}/crm/leads/${x.id}`);let body;try{body=req.postDataJSON();}catch{body=null;}
   const keys=Object.keys(body||{}).sort().join(',');const tags=keys==='tags';const fields=keys==='historico_resumo,nome_contato,telefone_contato,valor_conversao';
   if(method!=='PUT'||!lead||(!tags&&!fields)){r.unexpected.push({method,endpoint});return route.abort();}
   r.writes.push({kind:tags?'tags':'fields',tenant:state.tenant,leadId:lead.id,payload:clone(body)});
   const failure=state.failure,wrong=state.forceWrongId;
   if(state.hold||failure==='timeout')await new Promise(resolve=>state.releases.push(resolve));
   if(closing)return;
   try{
    if(typeof failure==='number')return await route.fulfill({status:failure,json:{detail:'SYNTHETIC_PRIVATE_SQL_DETAIL_DO_NOT_RENDER'}});
    if(failure==='network'||failure==='offline')return await route.abort(failure==='offline'?'internetdisconnected':'connectionreset');
    if(wrong)return await route.fulfill({json:{id:F.id(999),...body}});
    Object.assign(lead,clone(body));return await route.fulfill({json:{...lead,tags:[]}});
   }catch(e){if(!closing&&failure!=='timeout')r.errors.push('Mock write response failed');}
   return;
  }
  let data=[];
  if(endpoint==='/api/public/configuracoes-branding')data={nome_sistema:'Ferrioli Mídias'};
  else if(endpoint===`${base}/crm`)data={funil_id:F.id(700),etapas:F.STAGES.map((stage,i)=>({...stage,leads:i===1?state.contacts:[]}))};
  else if(endpoint===`${base}/crm/tags/oficiais`)data=F.TAGS;
  else if(endpoint.endsWith('/crm')&&endpoint.startsWith('/api/empresas/'))return route.fulfill({status:403,json:{detail:'Synthetic foreign tenant read denied'}});
  else if(!endpoint.endsWith('/crm/tags/oficiais')){r.unexpected.push({method,endpoint});return route.fulfill({status:404,json:{detail:'Unexpected read blocked'}});}
  return route.fulfill({json:data});
 });
 await ctx.addInitScript(({tenant})=>{localStorage.setItem('user',JSON.stringify({id:'synthetic-operator',role:'admin_empresa',empresa_id:tenant,nome:'Operador sintético'}));localStorage.setItem('token','synthetic-token-a');window.__crmRoots=[];const seen=new WeakSet();window.__REACT_DEVTOOLS_GLOBAL_HOOK__={supportsFiber:true,inject(){return 1;},onCommitFiberRoot(_,root){if(!seen.has(root)){seen.add(root);window.__crmRoots.push(root.containerInfo?.id);}},onCommitFiberUnmount(){},onPostCommitFiberRoot(){}};},{tenant:F.TENANT});
 const page=await ctx.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>r.errors.push(e.message));page.on('dialog',async d=>{r.errors.push('Unexpected native alert');await d.dismiss();});
 page.on('response',res=>{if(new URL(res.url()).pathname.startsWith('/assets/')&&res.status()>=400)r.errors.push('Failed asset');});
 const card=id=>page.locator(`.ux04-crm-card[data-lead-id="${id}"]`),dialog=()=>page.getByRole('dialog');
 const check=name=>{r.checks.push(name);console.log(JSON.stringify({viewport:viewport.name,check:name}));};
 async function open(index=0){await card(state.contacts[index].id).locator('h4').click();await dialog().waitFor();}
 async function draft(){const d=dialog();await d.getByLabel('Nome',{exact:true}).fill('  Rascunho preservado  ');await d.getByLabel('Telefone',{exact:true}).fill('  READONLY_EDITED  ');await d.getByLabel('Resumo do histórico',{exact:true}).fill('  Histórico local preservado  ');await d.getByLabel('Valor da Venda (R$)',{exact:true}).fill('123.45');}
 async function intact(){const d=dialog();await expect(d).toBeVisible();await expect(d.getByLabel('Nome',{exact:true})).toHaveValue('  Rascunho preservado  ');await expect(d.getByLabel('Telefone',{exact:true})).toHaveValue('  READONLY_EDITED  ');await expect(d.getByLabel('Resumo do histórico',{exact:true})).toHaveValue('  Histórico local preservado  ');await expect(d.getByLabel('Valor da Venda (R$)',{exact:true})).toHaveValue('123.45');}
 async function capture(name){const filename=`${viewport.name}-${name}.png`;await page.screenshot({path:path.join(out,filename),fullPage:false});r.screenshots.push(filename);}
 async function release(){state.hold=false;state.failure=null;state.releases.splice(0).forEach(fn=>fn());}
 try{
  await page.goto(origin+'/painel/crm',{waitUntil:'networkidle'});await open();await draft();
  for(const failure of [400,401,403,404,409,422,500,503,'network','offline','timeout']){
   state.failure=failure;const before=r.writes.length;if(failure==='offline'){await ctx.setOffline(true);assert.equal(await page.evaluate(()=>navigator.onLine),false);}
   await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();
   await expect(dialog().getByRole('alert')).toBeVisible({timeout:18000});await expect(dialog().getByRole('alert')).toBeFocused();await intact();
   assert.ok(!await dialog().innerText().then(x=>x.includes('SYNTHETIC_PRIVATE_SQL_DETAIL')));
   assert.equal(new URL(page.url()).pathname,'/painel/crm');assert.equal(await page.getByText('Lead atualizado com sucesso!',{exact:true}).count(),0);
   await expect(dialog().getByRole('button',{name:'Salvar lead',exact:true})).toBeEnabled();
   if(failure==='offline')await ctx.setOffline(false);
   await capture('error-'+failure);check('failure-'+failure+'-open-draft-focused-retry');
   assert.ok(r.writes.length<=before+1,'Automatic retry or duplicate write');await release();
  }
  state.forceWrongId=true;await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();await expect(dialog().getByRole('alert')).toBeVisible();await intact();state.forceWrongId=false;check('invalid-success-response-not-confirmed');
  // Tags remain immediate and distinct; never call a real tags endpoint.
  const beforeTags=r.writes.filter(x=>x.kind==='tags').length;await dialog().getByLabel(F.TAGS[1].nome,{exact:true}).click();await expect(dialog().getByLabel(F.TAGS[1].nome,{exact:true})).toBeChecked();await intact();
  assert.equal(r.writes.filter(x=>x.kind==='tags').length,beforeTags+1);check('immediate-tags-mocked-draft-stage-preserved');
  state.hold=true;const before=r.writes.filter(x=>x.kind==='fields').length;await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();
  await expect(dialog().getByRole('button',{name:'Salvando...',exact:true})).toBeDisabled();await expect(dialog().getByRole('button',{name:'Cancelar',exact:true})).toBeDisabled();await expect(dialog().getByRole('button',{name:'Fechar edição do contato',exact:true})).toBeDisabled();
  await page.keyboard.press('Escape');await expect(dialog()).toBeVisible();await dialog().locator('form').dispatchEvent('submit');await page.waitForTimeout(120);assert.equal(r.writes.filter(x=>x.kind==='fields').length,before+1);check('double-submit-and-close-escape-while-pending-guarded');
  await release();await expect(dialog()).toHaveCount(0);await expect(card(state.contacts[0].id).locator('h4')).toHaveText('Rascunho preservado');assert.ok(await card(state.contacts[0].id).getByText(F.TAGS[1].nome,{exact:true}).count());
  assert.ok(await page.getByRole('region',{name:'Etapa '+F.STAGES[1].nome,exact:true}).locator(`[data-lead-id="${state.contacts[0].id}"]`).count());check('confirmed-success-closes-updates-fields-keeps-stage-tags');
  await open();await dialog().getByLabel('Valor da Venda (R$)',{exact:true}).evaluate(el=>el.setCustomValidity('Valor inválido sintético'));const invalidBefore=r.writes.length;await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();assert.equal(await dialog().getByLabel('Valor da Venda (R$)',{exact:true}).evaluate(el=>el.checkValidity()),false);assert.equal(r.writes.length,invalidBefore);await expect(dialog()).toBeVisible();await dialog().getByRole('button',{name:'Cancelar',exact:true}).click();check('invalid-field-native-validation-no-write');
  await open();await draft();state.hold=true;const pendingBefore=r.writes.length;await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();await expect(dialog().getByRole('button',{name:'Salvando...',exact:true})).toBeDisabled();await expect.poll(()=>r.writes.length).toBe(pendingBefore+1);await card(state.contacts[1].id).locator('h4').dispatchEvent('click');await expect(dialog().getByLabel('Nome',{exact:true})).toHaveValue(state.contacts[1].nome_contato);await release();await page.waitForTimeout(200);await expect(dialog()).toBeVisible();await expect(dialog().getByLabel('Nome',{exact:true})).toHaveValue(state.contacts[1].nome_contato);await expect(dialog().getByRole('button',{name:'Salvar lead',exact:true})).toBeEnabled();await dialog().getByRole('button',{name:'Cancelar',exact:true}).click();check('late-save-does-not-close-or-overwrite-new-lead');
  await open();await draft();state.hold=true;const tenantPendingBefore=r.writes.length;await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();await expect(dialog().getByRole('button',{name:'Salvando...',exact:true})).toBeDisabled();await expect.poll(()=>r.writes.length).toBe(tenantPendingBefore+1);
  state.tenant=F.id(992);state.contacts=clone(F.CONTACTS.slice(0,2));state.contacts[0].nome_contato='Contato exclusivo do tenant B';
  await page.evaluate(tenant=>{const user=JSON.parse(localStorage.getItem('user'));user.empresa_id=tenant;localStorage.setItem('user',JSON.stringify(user));localStorage.setItem('token','synthetic-token-b');document.dispatchEvent(new Event('visibilitychange'));},state.tenant);
  await expect(dialog()).toHaveCount(0);await card(state.contacts[0].id).locator('h4').waitFor();await open();await expect(dialog().getByLabel('Nome',{exact:true})).toHaveValue('Contato exclusivo do tenant B');await release();await page.waitForTimeout(200);await expect(dialog().getByLabel('Nome',{exact:true})).toHaveValue('Contato exclusivo do tenant B');check('tenant-switch-same-lead-id-late-result-isolated');
  await dialog().getByLabel('Nome',{exact:true}).fill('Tenant B rascunho');await dialog().getByRole('button',{name:'Salvar lead',exact:true}).click();await expect(dialog()).toHaveCount(0);assert.equal(r.writes.at(-1).tenant,state.tenant);check('retry-new-tenant-sends-current-tenant-only');
  assert.deepEqual(await page.evaluate(()=>window.__crmRoots),['root']);assert.deepEqual(r.unexpected,[]);assert.deepEqual(r.errors,[]);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));r.status='PASS';
 }catch(e){r.status='FAIL';r.failure=e.message;await capture('failure').catch(()=>{});}
 finally{clearTimeout(timeout);closing=true;state.releases.splice(0).forEach(fn=>fn());await ctx.close();fs.writeFileSync(path.join(out,viewport.name+'.json'),JSON.stringify(r,null,2));}
 return r;
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const results=[];try{for(const viewport of viewports)results.push(await run(browser,viewport));}finally{await browser.close();}
 const summary={status:results.every(x=>x.status==='PASS')?'PASS':'FAIL',sourceSha,origin,mode:'ALL_API_AND_SOCKETS_INTERCEPTED',scriptSha256:createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),fixtureSha256:createHash('sha256').update(fs.readFileSync(path.join(__dirname,'fixtures-ux04-operations.cjs'))).digest('hex'),results:results.map(x=>({viewport:x.viewport,status:x.status,checks:x.checks,errors:x.errors,unexpected:x.unexpected,failure:x.failure,writes:x.writes.length}))};fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));if(summary.status!=='PASS')process.exitCode=1;
})().catch(e=>{console.error(e.message);process.exit(1);});
