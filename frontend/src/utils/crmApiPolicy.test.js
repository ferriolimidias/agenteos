import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { crmSaveError, isCrmFieldsSaveRequest } from './crmLeadSave.js';
const source=readFileSync(new URL('../services/api.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace('import.meta.env.VITE_API_BASE_URL','undefined').replace('export default api;','api;');
const data={nome_contato:'Synthetic',telefone_contato:'READONLY',historico_resumo:'Synthetic',valor_conversao:12.5};
function setup(){
 const storage=new Map([['token','synthetic-token-a']]);let tenant='tenant-a';const redirects=[];let request,error;
 const axios={create(config){return {defaults:config,interceptors:{request:{use(fn){request=fn;}},response:{use(_,fn){error=fn;}}}};}};
 vm.runInNewContext(source,{axios,crmSaveError,isCrmFieldsSaveRequest,getActiveEmpresaId:()=>tenant,localStorage:{getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},window:{location:{pathname:'/painel/crm',replace:x=>redirects.push(x)}},console:{warn(){}}});
 const config={baseURL:'/api',method:'put',url:'/empresas/tenant-a/crm/leads/lead-a',data,headers:{Authorization:'Bearer synthetic-token-a'},crmFieldsSaveContext:{empresaId:'tenant-a',leadId:'lead-a'}};
 return {storage,redirects,request,error,config,setTenant:x=>{tenant=x;}};
}
test('401 fields-save rejects but preserves editor authentication context',async()=>{
 const x=setup();await x.request(x.config);const failure={response:{status:401},config:{...x.config,data:JSON.stringify(data)}};
 await assert.rejects(x.error(failure),err=>err===failure);assert.deepEqual(x.redirects,[]);assert.equal(x.storage.get('token'),'synthetic-token-a');
});
test('all non-opt-in, tag, foreign URL/base, method and payload 401 retain global logout',async()=>{
 for(const patch of [{crmFieldsSaveContext:undefined},{data:{tags:['tag-a']}},{method:'get'},{url:'https://evil.invalid/x'},{baseURL:'https://evil.invalid'},{data:{...data,etapa_id:'stage-a'}}]){
  const x=setup();await assert.rejects(x.error({response:{status:401},config:{...x.config,...patch}}));assert.deepEqual(x.redirects,['/']);assert.equal(x.storage.has('token'),false);
 }
});
test('tenant/token drift or malformed opt-in is rejected before transport',async()=>{
 for(const change of ['tenant','token','url','base','payload']){
  const x=setup();if(change==='tenant')x.setTenant('tenant-b');if(change==='token')x.storage.set('token','synthetic-token-b');if(change==='url')x.config.url='https://evil.invalid/x';if(change==='base')x.config.baseURL='https://evil.invalid';if(change==='payload')x.config.data={tags:[]};
  await assert.rejects(x.request(x.config),err=>err.code==='CRM_CONTEXT_CHANGED');assert.deepEqual(x.redirects,[]);
 }
});
test('ordinary request interceptor and non-401 error behavior remain unchanged',async()=>{
 const x=setup();const ordinary=x.request({headers:{},url:'/empresas/tenant-a/crm',method:'get'});assert.equal(ordinary.headers.Authorization,'Bearer synthetic-token-a');
 for(const status of [400,403,422,500])await assert.rejects(x.error({response:{status},config:ordinary}));assert.deepEqual(x.redirects,[]);assert.equal(x.storage.has('token'),true);
});
