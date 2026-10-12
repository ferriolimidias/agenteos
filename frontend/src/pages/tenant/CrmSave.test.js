import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { CRM_LEAD_SAVE_TIMEOUT_MS, confirmedCrmLeadResponse, crmSaveError, crmSaveErrorMessage } from '../../utils/crmLeadSave.js';

const crm = readFileSync(new URL('./Crm.jsx', import.meta.url), 'utf8');
const modal = readFileSync(new URL('../../components/LeadDetailsModal.jsx', import.meta.url), 'utf8');
function handler(source, name, boundary, context) {
  const start = source.indexOf(`const ${name} = `);
  const end = source.indexOf(boundary, start);
  assert.ok(start >= 0 && end > start);
  return vm.runInNewContext(`(${source.slice(start + `const ${name} = `.length, end).trim().replace(/;$/, '')})`, context);
}
function setup(reject = true) {
  const calls = { close: 0, updates: [], toast: [], errors: [], requests: [] };
  const context = {
    empresaId: 'tenant-a', selectedLead: { id: 'lead-a' },
    api: { async put(...args) { calls.requests.push(args); if (reject) throw Object.assign(new Error('synthetic rejection'), { response: { status: 500 } }); return { status: 200, data: { id: 'lead-a' } }; } },
    updateLeadInState: (...args) => calls.updates.push(args),
    setSelectedLead: () => { calls.close++; }, showToast: msg => calls.toast.push(msg),
    alert: msg => calls.errors.push(msg), console: { error() {} },
    getStoredToken: () => 'synthetic-token-a', getActiveEmpresaId: () => 'tenant-a',
    leadSaveScopeRef: { current: { empresaId: 'tenant-a', leadId: 'lead-a' } },
    CRM_LEAD_SAVE_TIMEOUT_MS, confirmedCrmLeadResponse, crmSaveError,
  };
  const save = handler(crm, 'handleLeadSave', '\n\n  const handleDeleteLead', context);
  const modalContext = {
    lead: { id: 'lead-a' }, formData: { nome_contato: ' Draft ', telefone_contato: ' SYNTHETIC ', historico_resumo: ' Summary ' },
    valorConversao: '12.50', onSaveLead: save, onClose: () => { calls.close++; },
    setSaving() {}, setError: message => { if (message) calls.errors.push(message); }, console: { error() {} },
    savingRef: { current: false }, scopeRef: { current: { leadId: 'lead-a', scopeId: 'tenant-a' } }, scopeId: 'tenant-a',
    crmSaveError, crmSaveErrorMessage,
  };
  const submit = handler(modal, 'handleSubmit', '\n\n  const handleClose', modalContext);
  return { calls, save, submit, context, modalContext };
}
test('CRM callback rejects when API rejects; local state and success remain untouched', async () => {
  const { calls, save } = setup();
  await assert.rejects(save('lead-a', { nome_contato: 'Draft' }));
  assert.equal(calls.updates.length, 0); assert.equal(calls.toast.length, 0);
});
test('modal failure preserves editor instead of closing after rejected PUT', async () => {
  const { calls, submit } = setup();
  await submit({ preventDefault() {} });
  assert.equal(calls.close, 0); assert.ok(calls.errors.length > 0);
});
test('confirmed success closes once and merges only the submitted fields', async () => {
  const { calls, submit } = setup(false); await submit({ preventDefault() {} });
  assert.equal(calls.close, 1); assert.equal(calls.toast.length, 1); assert.equal(calls.updates.length, 1);
  assert.deepEqual(Object.keys(calls.requests[0][1]).sort(), ['historico_resumo','nome_contato','telefone_contato','valor_conversao']);
  assert.equal(calls.requests[0][1].nome_contato, 'Draft'); assert.equal(calls.requests[0][1].valor_conversao, 12.5);
  assert.equal(calls.requests[0][2].timeout, 15000);
});
test('200 without matching API lead id cannot show success or close', async () => {
  const { calls, context, submit } = setup(false); context.api.put=async()=>({status:200,data:{id:'another-lead'}});
  await submit({preventDefault(){}}); assert.equal(calls.close,0);assert.equal(calls.updates.length,0);assert.equal(calls.toast.length,0);
});
test('missing callback or missing explicit receipt cannot close editor', async () => {
  for(const callback of [undefined,async()=>undefined,async()=>({saved:true,empresaId:'other-tenant',leadId:'lead-a'})]){
    const {calls,modalContext,submit}=setup(false);modalContext.onSaveLead=callback;await submit({preventDefault(){}});assert.equal(calls.close,0);assert.ok(calls.errors.length);
  }
});
test('synchronous lock prevents duplicate saves even before React rerenders', async () => {
  const {calls,context,submit}=setup(false);let release;context.api.put=async(...args)=>{calls.requests.push(args);return new Promise(r=>{release=r;});};
  const pending=submit({preventDefault(){}});await submit({preventDefault(){}});assert.equal(calls.requests.length,1);
  release({status:200,data:{id:'lead-a'}});await pending;assert.equal(calls.close,1);
});
test('late response after tenant/token/lead scope changes cannot patch, toast or close', async () => {
  for(const change of ['tenant','token','lead','unmount']){
    const {calls,context,submit}=setup(false);let release;context.api.put=async()=>new Promise(r=>{release=r;});const pending=submit({preventDefault(){}});
    if(change==='tenant')context.getActiveEmpresaId=()=> 'tenant-b';
    else if(change==='token')context.getStoredToken=()=> 'synthetic-token-b';
    else context.leadSaveScopeRef.current=change==='unmount'?null:{empresaId:'tenant-a',leadId:'lead-b'};
    release({status:200,data:{id:'lead-a'}});await pending;assert.equal(calls.updates.length,0);assert.equal(calls.toast.length,0);assert.equal(calls.close,0);
  }
});
test('outdated modal completion cannot alter replacement editor state or focus', async () => {
  const {calls,context,modalContext,submit}=setup(false);let reject;context.api.put=async()=>new Promise((_,r)=>{reject=r;});const pending=submit({preventDefault(){}});
  modalContext.scopeRef.current=null;reject(new Error('network failure'));await pending;assert.equal(calls.errors.length,0);assert.equal(calls.close,0);
});
test('non-finite value is rejected locally without a request', async () => {
  const {calls,modalContext,submit}=setup(false);modalContext.valorConversao=Infinity;await submit({preventDefault(){}});assert.equal(calls.requests.length,0);assert.equal(calls.close,0);assert.ok(calls.errors.length);
});
test('failed request unlocks editor for an explicit successful retry', async () => {
  const {calls,context,modalContext,submit}=setup();await submit({preventDefault(){}});assert.equal(modalContext.savingRef.current,false);
  context.api.put=async()=>({status:200,data:{id:'lead-a'}});await submit({preventDefault(){}});assert.equal(calls.close,1);
});
