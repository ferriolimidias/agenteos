import assert from 'node:assert/strict';
import { test } from 'node:test';
import { confirmedCrmLeadResponse, crmSaveErrorMessage, isCrmFieldsSaveRequest } from './crmLeadSave.js';
const data={nome_contato:'Synthetic',telefone_contato:'READONLY',historico_resumo:'Synthetic',valor_conversao:12.5};
const config={method:'put',url:'/empresas/tenant-a/crm/leads/lead-a',crmFieldsSaveContext:{empresaId:'tenant-a',leadId:'lead-a'},data};
test('only exact opt-in fields-save request gets contextual error policy',()=>{
 assert.equal(isCrmFieldsSaveRequest(config),true);assert.equal(isCrmFieldsSaveRequest({...config,data:JSON.stringify(data)}),true);
 for(const other of [{method:'get'},{url:'https://evil.invalid/empresas/tenant-a/crm/leads/lead-a'},{url:config.url+'?x=1'},{data:{tags:['tag-a']}},{data:{...data,etapa_id:'stage-a'}},{crmFieldsSaveContext:undefined},{data:'invalid JSON'},{data:null}])assert.equal(isCrmFieldsSaveRequest({...config,...other}),false);
});
test('declared API contract requires 200 and the same lead id; accepted or empty is not confirmation',()=>{
 assert.equal(confirmedCrmLeadResponse({status:200,data:{id:'lead-a'}},'lead-a'),true);
 for(const response of [{status:202,data:{id:'lead-a'}},{status:204},{status:500,data:{id:'lead-a'}},{status:200,data:{id:'lead-b'}},{status:200,data:{}}])assert.equal(confirmedCrmLeadResponse(response,'lead-a'),false);
});
test('accessible errors never render arbitrary backend/provider/SQL details',()=>{
 const secret='SYNTHETIC_SQL_PRIVATE_DETAIL';
 for(const status of [400,401,403,404,409,422,500,503]){
  const message=crmSaveErrorMessage({message:secret,response:{status,data:{detail:secret}}});assert.ok(message.length>20);assert.ok(!message.includes(secret));
 }
 for(const code of ['ECONNABORTED','ETIMEDOUT','ERR_NETWORK','CRM_CONTEXT_CHANGED','CRM_SAVE_NOT_CONFIRMED'])assert.ok(!crmSaveErrorMessage({code,message:secret}).includes(secret));
});
