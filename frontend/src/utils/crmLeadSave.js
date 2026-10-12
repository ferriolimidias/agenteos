export const CRM_LEAD_SAVE_TIMEOUT_MS = 15000;
const FIELD_KEYS = ['historico_resumo', 'nome_contato', 'telefone_contato', 'valor_conversao'];

export function crmSaveError(code) {
  return Object.assign(new Error('CRM save not confirmed'), { code });
}

// This flag controls error presentation only. Server authorization remains mandatory.
export function isCrmFieldsSaveRequest(config) {
  const context = config?.crmFieldsSaveContext;
  if (!context || String(config.method).toLowerCase() !== 'put') return false;
  const url = String(config.url || '');
  if (!/^\/empresas\/[^/?#]+\/crm\/leads\/[^/?#]+$/.test(url)) return false;
  if (url !== `/empresas/${context.empresaId}/crm/leads/${context.leadId}`) return false;
  let data = config.data;
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch { return false; }
  }
  return Boolean(data && typeof data === 'object' && !Array.isArray(data)
    && ['nome_contato', 'telefone_contato', 'historico_resumo'].every(key => typeof data[key] === 'string')
    && Number.isFinite(data.valor_conversao)
    && Object.keys(data).sort().join(',') === FIELD_KEYS.join(','));
}

export function confirmedCrmLeadResponse(response, leadId) {
  return response?.status === 200
    && String(response.data?.id || '') === String(leadId);
}

export function crmSaveErrorMessage(error) {
  if (error?.code === 'CRM_CONTEXT_CHANGED') return 'O contato ou a sessão mudou. Reabra o contato no contexto atual antes de salvar.';
  if (error?.code === 'CRM_SAVE_NOT_CONFIRMED') return 'A API não confirmou o salvamento. Os campos foram preservados; tente novamente.';
  if (error?.code === 'CRM_SAVE_INVALID_VALUE') return 'Informe um valor de venda válido. Os demais campos foram preservados.';
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') return 'Tempo limite atingido. Não foi possível confirmar o salvamento. Os campos foram preservados; verifique os dados antes de tentar novamente.';
  const status = error?.response?.status;
  if (status === 401) return 'Sua sessão não foi autorizada. Os campos foram preservados; autentique-se novamente antes de tentar salvar.';
  if (status === 403) return 'Você não tem permissão para salvar este contato. Os campos foram preservados.';
  if (status === 404) return 'O contato não foi encontrado no contexto atual. Os campos foram preservados.';
  if (status === 409) return 'Não foi possível salvar devido a um conflito. Os campos foram preservados; revise os dados e tente novamente.';
  if (status === 400 || status === 422) return 'Os dados foram recusados. Revise os campos e tente novamente.';
  if (status) return 'Não foi possível confirmar o salvamento do contato. Os campos foram preservados; verifique os dados antes de tentar novamente.';
  return 'Não foi possível confirmar o salvamento. Verifique sua conexão e tente novamente; os campos foram preservados.';
}
