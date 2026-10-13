// Synthetic APIs and WebSockets only. Public HTML/assets are the sole live reads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const F = require('./fixtures-ux04-operations.cjs');
const origin = new URL(process.env.UX_ORIGIN || 'http://127.0.0.1:8080').origin;
const output = process.env.UX_EVIDENCE_DIR || '/tmp/inbox29-browser';
const baseline = process.env.INBOX29_BASELINE === '1';
const selectedWidths = process.env.UX_VIEWPORTS ? process.env.UX_VIEWPORTS.split(',') : null;
const widths = [{ name:'screenshot1327', width:1327,height:709 },{name:'desktop',width:1440,height:900},{name:'notebook',width:1280,height:800},{name:'tablet',width:768,height:1024},{name:'mobile',width:390,height:844}];
const hash = p => createHash('sha256').update(fs.readFileSync(p)).digest('hex');
async function run(browser, viewport) {
  const r={viewport, mode:'ALL_API_SOCKETS_INTERCEPTED_SYNTHETIC', checks:[], errors:[],unexpectedReads:[],unexpectedWrites:[],externalRequests:[],failedAssets:[],screenshots:[],counts:{list:0,history:0,ai:0,sockets:0},metrics:{}};
  const context=await browser.newContext({viewport,locale:'pt-BR',reducedMotion:'reduce',serviceWorkers:'block',recordVideo:baseline?{dir:output,size:viewport}:undefined});
  let phase='initial'; let delayedPhone=''; let listError=false; let sendRelease; let expectedSend=false; let explicitReconnects=0; const sockets=[]; const releases=[]; r.syntheticWrites=[];
  const histories=new Map(F.CONTACTS.slice(0,3).map((c,i)=>[c.telefone_contato,F.MESSAGES.map(m=>({...m,id:F.id(400+i*100+(Number(m.id.slice(-3))-400)),lead_id:c.id,conversation_id:c.conversation_id,telefone:c.telefone_contato,texto:i?`Histórico sintético contato ${i+1} item ${m.id.slice(-3)}`:m.texto}))]));
  await context.routeWebSocket('**/*',socket=>{const socketPath=new URL(socket.url()).pathname;if(!/^\/api\/empresas\/[^/]+\/ws$/.test(socketPath)){if(process.env.INBOX29_DEV==='1'){socket.close({code:1000,reason:'synthetic-hmr-blocked'});return;}assert.fail('Unexpected WebSocket path');}sockets.push(socket);r.counts.sockets++; socket.onMessage(raw=>{if(JSON.parse(String(raw)).type==='ping')socket.send(JSON.stringify({type:'pong'}));});});
  await context.route('**/*',async route=>{
    const q=route.request(),u=new URL(q.url()); if(u.origin!==origin){r.externalRequests.push(u.origin);return route.abort();}
    if(!u.pathname.startsWith('/api/'))return route.continue();
    if(q.method()!=='GET'){if(expectedSend && q.method()==='POST' && u.pathname.endsWith('/inbox/READONLY_01/send') && q.postDataJSON()?.texto==='Envio otimista exclusivamente sintético'){r.syntheticWrites.push({method:'POST',kind:'intercepted-optimistic-test'});await new Promise(resolve=>sendRelease=resolve);return route.fulfill({json:{ok:true}});}r.unexpectedWrites.push({method:q.method(),path:u.pathname});return route.abort();}
    let data;
    if(u.pathname.startsWith('/api/empresas/00000000-0000-0000-0000-000000000099/')) return route.fulfill({json:u.pathname.endsWith('/inbox')?[]:u.pathname.endsWith('/ai-state')?{manual_enabled:true,processing_state:'allowed'}:[]});
    if(u.pathname.endsWith('/configuracoes-branding'))data={nome_sistema:'Ferrioli — demonstração sintética'};
    else if(u.pathname.endsWith('/inbox')){r.counts.list++;data=F.CONTACTS.slice(0,3);if(listError)return route.fulfill({status:503,json:{detail:'synthetic-list-transient'}});if(phase==='partial-error')await new Promise(resolve=>setTimeout(resolve,650));}
    else if(u.pathname.endsWith('/ai-state')){r.counts.ai++;data={manual_enabled:true,effective_state:'authorized',processing_state:'allowed',processing_label:phase==='delayed'?'IA sincronizada após mensagem parcial':'IA autorizada'};if(phase==='delayed'||phase==='delayed-ai')await new Promise(resolve=>releases.push(resolve));}
    else if(u.pathname.endsWith('/foto'))data={foto_url:null};
    else if(/\/(destinos|grupos|oficiais)$/.test(u.pathname))data=[];
    else if(u.pathname.includes('/inbox/')){r.counts.history++;const phone=u.pathname.split('/inbox/')[1];data=histories.get(phone)||[];if(phase==='offline-error')return route.abort('internetdisconnected');if(phase==='partial-error'){await new Promise(resolve=>setTimeout(resolve,200));return route.fulfill({status:503,json:{detail:'synthetic-transient-error'}});}if(phase==='delayed'||phone===delayedPhone)await new Promise(resolve=>releases.push(resolve));}
    else{r.unexpectedReads.push(u.pathname);return route.fulfill({status:404,json:{}});}
    try{return await route.fulfill({json:data});}catch{return;}
  });
  await context.addInitScript(({tenant})=>{localStorage.setItem('token','synthetic-inbox29-not-a-credential');localStorage.setItem('user',JSON.stringify({id:'synthetic-user',nome:'Operador sintético',role:'admin_empresa',empresa_id:tenant}));window.__inbox29Roots=[];const roots=new WeakSet();window.__REACT_DEVTOOLS_GLOBAL_HOOK__={supportsFiber:true,inject(){return 1;},onCommitFiberRoot(_renderer,root){if(!roots.has(root)){roots.add(root);window.__inbox29Roots.push(root.containerInfo?.id);}},onCommitFiberUnmount(){},onPostCommitFiberRoot(){}};},{tenant:F.TENANT});
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>r.errors.push(e.message));page.on('response',q=>{if(new URL(q.url()).pathname.startsWith('/assets/')&&q.status()!==200)r.failedAssets.push(q.status());});
  const composer=page.getByPlaceholder('Digite uma mensagem'); const scroll=page.getByTestId('inbox-message-scroll');
  const shot=async name=>{const p=`${viewport.name}-${name}.png`;await page.screenshot({path:path.join(output,p)});r.screenshots.push(p);};
  const emit=async event=>{sockets.at(-1).send(JSON.stringify(event));await page.waitForTimeout(40);};
  const message=(n,lead=F.CONTACTS[0],extra={})=>({id:F.id(9000+n),lead_id:lead.id,conversation_id:lead.conversation_id,from_me:false,tipo_mensagem:'text',texto:`Evento sintético ${n}`,criado_em:new Date(Date.UTC(2026,9,13,10,0,n)).toISOString(),status:'sent',...extra});
  const event=(m,lead=F.CONTACTS[0],extra={})=>({type:'inbox.message.created',lead_id:lead.id,conversation_id:lead.conversation_id,direction:m.from_me?'outbound':'inbound',message:m,...extra});
  try {
    await page.goto(origin+'/painel/chat',{waitUntil:'networkidle'});await page.getByText(F.CONTACTS[0].nome_contato,{exact:true}).click();await composer.waitFor();await page.locator(`[data-message-id="${F.MESSAGES[47].id}"]`).waitFor();await page.waitForTimeout(500);
    await composer.fill('Rascunho sintético preservado, nunca enviado');await scroll.evaluate(n=>{n.scrollTop=130;});await page.waitForTimeout(500);
    await page.evaluate(()=>{const header=document.querySelector('.inbox-contact-identity h3'),history=document.querySelector('[data-testid="inbox-message-scroll"]'),composer=document.querySelector('.inbox-composer textarea');window.__inbox29={header,history,composer,armed:true,headerBlankFrames:0,historyBlankFrames:0,headerBlankMutations:0,historyBlankMutations:0,nodeChanges:0,samples:0,scrollStart:history.scrollTop};const sample=kind=>{const s=window.__inbox29;if(!s.armed)return;const h=document.querySelector('.inbox-contact-identity h3'),m=document.querySelector('[data-testid="inbox-message-scroll"]');const hb=!h?.textContent.trim(),mb=!m?.querySelector('[data-message-id]');if(kind==='raf'){s.samples++;s.headerBlankFrames+=Number(hb);s.historyBlankFrames+=Number(mb);}else{s.headerBlankMutations+=Number(hb);s.historyBlankMutations+=Number(mb);}s.nodeChanges+=Number(h!==s.header||m!==s.history||document.querySelector('.inbox-composer textarea')!==s.composer);};const tick=()=>{sample('raf');if(window.__inbox29.armed)requestAnimationFrame(tick);};requestAnimationFrame(tick);new MutationObserver(()=>sample('mutation')).observe(document.querySelector('.inbox-workspace').parentNode,{childList:true,subtree:true,characterData:true});});
    const initial={...r.counts};const initialVisibleLeads=await page.locator("[data-lead-id]").count(); await shot('stable-before');phase='partial-error';await emit(event(message(1)));await page.waitForTimeout(140);await shot('partial-payload');await page.waitForTimeout(1150);await shot('after-transient-http');
    const first=await page.evaluate(()=>({headerBlankFrames:window.__inbox29.headerBlankFrames,historyBlankFrames:window.__inbox29.historyBlankFrames,headerBlankMutations:window.__inbox29.headerBlankMutations,historyBlankMutations:window.__inbox29.historyBlankMutations,nodeChanges:window.__inbox29.nodeChanges,samples:window.__inbox29.samples,scrollStart:window.__inbox29.scrollStart,scrollNow:document.querySelector('[data-testid="inbox-message-scroll"]').scrollTop}));
    r.metrics={HEADER_FLICKER:first.headerBlankFrames,HISTORY_BLANK_FRAMES:first.historyBlankFrames,FULL_RELOAD_PER_MESSAGE:r.counts.list-initial.list,LATE_FETCH_OVERWRITE:0,UNEXPECTED_WS_RECONNECT:r.counts.sockets-initial.sockets,...first,historyGetsPerFirstMessage:r.counts.history-initial.history};
    if(baseline){assert.ok(r.metrics.HEADER_FLICKER>0,'baseline must reproduce header blank frames');assert.ok(r.metrics.HISTORY_BLANK_FRAMES>0,'baseline must reproduce emptied history');assert.ok(r.metrics.FULL_RELOAD_PER_MESSAGE>0);r.status='REPRODUCED';r.checks.push('baseline-partial-event-undefined-identity-list-refetch-restores-phone-triggers-history503-clears-history');return r;}
    for(const k of ['HEADER_FLICKER','HISTORY_BLANK_FRAMES','FULL_RELOAD_PER_MESSAGE','LATE_FETCH_OVERWRITE','UNEXPECTED_WS_RECONNECT'])assert.equal(r.metrics[k],0,k);
    assert.equal(await composer.inputValue(),'Rascunho sintético preservado, nunca enviado');assert.equal(first.scrollNow,first.scrollStart);phase='normal';r.checks.push('partial-inbound-no-header-history-reload-scroll-draft-loss');
    const stableCounts={...r.counts};
    await emit({tipo_evento:'atualizacao_lead',lead:{id:F.CONTACTS[0].id,status_atendimento:'aberto'}});
    assert.equal(await page.locator('.inbox-contact-identity h3').innerText(),F.CONTACTS[0].nome_contato);
    assert.equal(await page.locator('.inbox-contact-identity p').first().innerText(),F.CONTACTS[0].telefone_contato);
    assert.match(await page.locator('.inbox-contact-stage').innerText(),/Qualificação/);
    r.checks.push('partial-lead-preserves-canonical-name-phone-stage');
    const second=message(2,F.CONTACTS[0],{external_message_id:'synthetic-wamid-2'});
    await emit(event(second));await emit(event(second));assert.equal(await page.locator(`[data-message-id="${second.id}"]`).count(),1);
    await emit({...event({...second,status:'delivered',texto:'Evento sintético atualizado'}),type:'inbox.message.updated'});
    assert.match(await page.locator(`[data-message-id="${second.id}"]`).innerText(),/Evento sintético atualizado/);
    const reaction=message(3,F.CONTACTS[0],{tipo_mensagem:'reaction',external_message_id:'synthetic-reaction-3',metadata_json:{reaction:{message_id:'synthetic-wamid-2',emoji:'🔥'}}});
    await emit(event(reaction));await emit(event(reaction));assert.equal(await page.locator(`[data-message-id="${second.id}"]`).getByLabel('Reações').innerText(),'🔥');
    const media=message(4,F.CONTACTS[0],{from_me:true,tipo_mensagem:'image',media_url:F.MESSAGES[44].media_url,texto:'Mídia sintética em tempo real'});
    await emit({...event(media),tipo_evento:'nova_mensagem_outbound',type:undefined});await page.locator(`[data-message-id="${media.id}"] img`).waitFor();
    await emit({...event({...media,texto:'Legenda sintética atualizada'}),type:'inbox.message.updated'});assert.match(await page.locator(`[data-message-id="${media.id}"]`).innerText(),/Legenda sintética atualizada/);
    r.checks.push('inbound-outbound-update-reaction-dedup-image-keeps-existing-fields');
    for(const size of [10,50]){const start=size===10?10:100;for(let i=0;i<size;i++){const m=message(start+i);sockets.at(-1).send(JSON.stringify(event(m)));sockets.at(-1).send(JSON.stringify(event(m)));}await page.locator(`[data-message-id="${F.id(9000+start+size-1)}"]`).waitFor();assert.equal(await page.locator(`[data-message-id="${F.id(9000+start)}"]`).count(),1);}
    assert.equal(r.counts.list,stableCounts.list);assert.equal(r.counts.history,stableCounts.history);assert.equal(r.counts.sockets,stableCounts.sockets);
    assert.equal(await scroll.evaluate(n=>n.scrollTop),first.scrollStart);
    assert.equal(await composer.inputValue(),'Rascunho sintético preservado, nunca enviado');
    r.checks.push('ten-and-fifty-double-bursts-no-http-reload-no-socket-reconnect-reading-position-preserved');
    const beforeOther=await page.locator('[data-message-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId));
    await emit(event(message(500,F.CONTACTS[1]),F.CONTACTS[1]));assert.deepEqual(await page.locator('[data-message-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId)),beforeOther);assert.equal(r.counts.history,stableCounts.history);
    r.checks.push('other-contact-updates-preview-without-touching-open-history');
    await scroll.evaluate(n=>n.scrollTop=n.scrollHeight);await emit(event(message(501)));await page.waitForTimeout(450);assert.ok(await scroll.evaluate(n=>n.scrollHeight-n.clientHeight-n.scrollTop<100));
    r.checks.push('near-bottom-autoscroll-continues');
    // Explicit transport interruption is the only reconnect counted as expected.
    histories.get(F.CONTACTS[0].telefone_contato).push({...second, texto:'Snapshot HTTP anterior ao texto editado',status:'sent',reactions:[]});
    phase='delayed';const beforeReconnect={...r.counts};const oldSocket=sockets.at(-1);oldSocket.close({code:1012,reason:'synthetic-restart'});explicitReconnects++;
    for(let i=0;i<40&&r.counts.sockets===beforeReconnect.sockets;i++)await page.waitForTimeout(100);assert.equal(r.counts.sockets,beforeReconnect.sockets+1);
    for(let i=0;i<30&&r.counts.history===beforeReconnect.history;i++)await page.waitForTimeout(50);
    assert.ok(r.counts.history>beforeReconnect.history,'reconnect must reconcile missed history');
    const latest=message(502);await emit(event(latest));await page.locator(`[data-message-id="${latest.id}"]`).waitFor();
    phase='normal';releases.splice(0).forEach(resolve=>resolve());await page.waitForTimeout(350);
    const latestPresent=await page.locator(`[data-message-id="${latest.id}"]`).count();r.metrics.LATE_FETCH_OVERWRITE+=Number(latestPresent!==1);assert.equal(latestPresent,1,'late HTTP history replaced a newer WS message');await page.locator('.chat-header-ai').filter({hasText:'IA sincronizada após mensagem parcial'}).waitFor();
    assert.equal(await page.locator(`[data-message-id="${F.id(9149)}"]`).count(),1,'reconnect GET discarded burst');assert.match(await page.locator(`[data-message-id="${second.id}"]`).innerText(),/Evento sintético atualizado/);assert.equal(await page.locator(`[data-message-id="${second.id}"]`).getByLabel('Reações').innerText(),'🔥');
    r.checks.push('explicit-reconnect-background-http-late-snapshot-keeps-new-ws-and-existing-history');
    // A real AI-state event supersedes only its own older pending HTTP result.
    phase='delayed-ai';const beforeAi=r.counts.ai;sockets.at(-1).close({code:1012,reason:'synthetic-ai-race'});explicitReconnects++;
    for(let i=0;i<50&&r.counts.ai===beforeAi;i++)await page.waitForTimeout(100);
    await emit({type:'inbox.ai_state.updated',lead_id:F.CONTACTS[0].id,ai_state:{manual_enabled:true,effective_state:'authorized',processing_state:'processing'}});
    phase='normal';releases.splice(0).forEach(resolve=>resolve());await page.waitForTimeout(200);
    assert.match(await page.locator('.chat-header-ai').innerText(),/IA processando/);r.checks.push('slow-ai-snapshot-accepts-unrelated-message-but-not-overwrites-new-ai-event');
    // HTTP error recovery must never clear valid history.
    phase='partial-error';listError=true;const errorSocket=sockets.at(-1);const beforeError=r.counts.history;errorSocket.close({code:1012,reason:'synthetic-http-error'});explicitReconnects++;
    for(let i=0;i<50&&r.counts.history===beforeError;i++)await page.waitForTimeout(100);await page.waitForTimeout(300);
    assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),1);await page.getByRole('alert').filter({hasText:'histórico'}).waitFor();phase='normal';listError=false;await page.getByRole('button',{name:'Tentar novamente',exact:true}).last().click();await page.waitForTimeout(200);assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),1);
    assert.equal(await page.locator('[data-lead-id]').count(),initialVisibleLeads,'list503 removed valid conversations');r.checks.push('transient-list-and-history503-retain-data-accessible-error-retry');
    phase='offline-error';const beforeOffline=r.counts.history;sockets.at(-1).close({code:1012,reason:'synthetic-offline'});explicitReconnects++;for(let i=0;i<50&&r.counts.history===beforeOffline;i++)await page.waitForTimeout(100);await page.waitForTimeout(300);assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),1);await page.getByRole('alert').filter({hasText:'histórico'}).waitFor();phase='normal';await page.getByRole('button',{name:'Tentar novamente',exact:true}).last().click();await page.waitForTimeout(200);assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),1);r.checks.push('network-offline-error-keeps-history-retry-restores-background-sync');
    // Controlled synthetic send tests only the existing optimistic confirmation path.
    expectedSend=true;await composer.fill('Envio otimista exclusivamente sintético');await composer.press('Enter');await page.locator('[data-message-id^="temp-"]').waitFor();
    const confirmed=message(503,F.CONTACTS[0],{from_me:true,texto:'Envio otimista exclusivamente sintético',status:'delivered'});await emit(event(confirmed));assert.equal(await page.locator('[data-message-id^="temp-"]').count(),0);assert.equal(await page.locator(`[data-message-id="${confirmed.id}"]`).count(),1);sendRelease();expectedSend=false;await page.waitForTimeout(200);
    r.checks.push('synthetic-only-optimistic-outbound-confirmed-once');
    await composer.fill('Rascunho sintético restaurado');
    // Intentional selection switches are excluded from blank-frame instrumentation.
    const running=await page.evaluate(()=>{window.__inbox29.armed=false;const s=window.__inbox29;return{header:s.headerBlankFrames,history:s.historyBlankFrames,nodeChanges:s.nodeChanges};});
    r.metrics.HEADER_FLICKER=running.header;r.metrics.HISTORY_BLANK_FRAMES=running.history;r.metrics.nodeChanges=running.nodeChanges;r.metrics.UNEXPECTED_WS_RECONNECT=r.counts.sockets-initial.sockets-explicitReconnects;
    for(const k of ['HEADER_FLICKER','HISTORY_BLANK_FRAMES','FULL_RELOAD_PER_MESSAGE','LATE_FETCH_OVERWRITE','UNEXPECTED_WS_RECONNECT'])assert.equal(r.metrics[k],0,k);assert.equal(running.nodeChanges,0,'composer/history/header was remounted');
    await shot('continuous-after-events');
    const select=async lead=>{if(viewport.width<1024&&await composer.isVisible())await page.getByRole('button',{name:'Voltar para conversas',exact:true}).click();await page.locator(`[data-lead-id="${lead.id}"]`).click();};
    const beforeA=await page.locator('[data-message-id]').evaluateAll(n=>n.map(n=>n.dataset.messageId));delayedPhone=F.CONTACTS[1].telefone_contato;await select(F.CONTACTS[1]);await page.locator('.inbox-contact-identity h3').filter({hasText:F.CONTACTS[1].nome_contato}).waitFor();await select(F.CONTACTS[0]);delayedPhone='';releases.splice(0).forEach(resolve=>resolve());await page.waitForTimeout(200);
    assert.equal(await page.locator('.inbox-contact-identity h3').innerText(),F.CONTACTS[0].nome_contato);assert.ok((await page.locator('[data-message-id]').evaluateAll(n=>n.map(n=>n.dataset.messageId))).every(id=>beforeA.includes(id)));assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),1);await composer.waitFor();assert.equal(await composer.inputValue(),'Rascunho sintético restaurado');
    r.checks.push('rapid-contact-switch-aborts-late-other-history-cached-selected-history-draft');
    await select(F.CONTACTS[1]);await page.getByText(histories.get(F.CONTACTS[1].telefone_contato)[47].texto,{exact:true}).waitFor();assert.equal(await page.locator(`[data-message-id="${latest.id}"]`).count(),0);await select(F.CONTACTS[0]);await page.locator(`[data-message-id="${latest.id}"]`).waitFor();r.checks.push('real-selection-loads-correct-contact-without-history-mixing');
    delayedPhone=F.CONTACTS[1].telefone_contato;await select(F.CONTACTS[1]);await page.evaluate(()=>{const u=JSON.parse(localStorage.getItem('user'));u.empresa_id='00000000-0000-0000-0000-000000000099';localStorage.setItem('user',JSON.stringify(u));window.dispatchEvent(new Event('storage'));});delayedPhone='';releases.splice(0).forEach(resolve=>resolve());await page.waitForTimeout(400);assert.equal(await page.locator('[data-message-id]').count(),0,'previous tenant history leaked after switching');
    assert.equal((await page.evaluate(()=>window.__inbox29Roots)).length,1);for(const k of ['errors','unexpectedReads','unexpectedWrites','externalRequests','failedAssets'])assert.deepEqual(r[k],[],k);r.checks.push('tenant-switch-old-results-discarded-single-root-zero-real-operations');
    r.status='PASS';
  }catch(e){r.status='FAIL';r.failure=e.message;r.failureStack=e.stack;try{await shot('failure');}catch{}}
  finally{releases.splice(0).forEach(f=>f());try{await page.evaluate(()=>{if(window.__inbox29)window.__inbox29.armed=false;});}catch{}await context.close();fs.writeFileSync(path.join(output,`${viewport.name}.json`),JSON.stringify(r,null,2));}
  return r;
}
(async()=>{fs.mkdirSync(output,{recursive:true});const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const results=[];try{for(const viewport of baseline?widths.slice(0,1):widths.filter(v=>!selectedWidths||selectedWidths.includes(v.name))){const r=await run(browser,viewport);results.push(r);console.log(JSON.stringify({viewport:viewport.name,status:r.status,metrics:r.metrics,failure:r.failure}));}}finally{await browser.close();}const summary={status:results.every(r=>r.status===(baseline?'REPRODUCED':'PASS'))?(baseline?'REPRODUCED':'PASS'):'FAIL',baseline,origin,sourceSha:process.env.UX_SOURCE_SHA,scriptSha256:hash(__filename),fixtureSha256:hash(path.join(__dirname,'fixtures-ux04-operations.cjs')),results};fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify(summary,null,2));if(summary.status==='FAIL')process.exitCode=1;})();
