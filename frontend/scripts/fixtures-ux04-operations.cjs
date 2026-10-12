// Invented contacts only. Never load authentication or production contact data.
const TENANT = '00000000-0000-0000-0000-000000000002';
const id = n => `10000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const TAGS = [{ id: id(801), nome: 'Interesse sintético', grupo_id: id(901), cor: '#235568' }, { id: id(802), nome: 'Retorno sintético', grupo_id: id(901), cor: '#7c3aed' }];
const CONTACTS = Array.from({ length: 36 }, (_, i) => ({
  id: id(i + 1), conversation_id: id(100 + i), contact_id: id(200 + i),
  nome_contato: i === 0 ? 'Ana Sintética — Atendimento Integrado' : i === 1 ? 'Bruno Sintético' : `Contato sintético ${String(i + 1).padStart(2, '0')}`,
  telefone_contato: `READONLY_${String(i + 1).padStart(2, '0')}`, foto_url: null,
  status_atendimento: i === 2 ? 'concluido' : i === 1 ? 'manual' : 'aberto', ia_ativa: i !== 1, bot_pausado: i === 1,
  tags: i === 0 ? [TAGS[0].id] : [], historico_resumo: i === 0 ? 'Contato de demonstração com histórico longo e contexto comercial legível.' : 'Histórico exclusivamente sintético.',
  etapa_crm: i === 0 ? 'Qualificação' : 'Entrada', valor_conversao: i === 0 ? 1250.5 : 0,
  criado_em: '2026-10-11T09:00:00Z', ultima_mensagem_data: new Date(Date.UTC(2026, 9, 12, 12, 0, -i)).toISOString(),
}));
const STAGES = [{ id: id(701), nome: 'Entrada', ordem: 1 }, { id: id(702), nome: 'Qualificação', ordem: 2 }, { id: id(703), nome: 'Proposta com acompanhamento comercial', ordem: 3 }, { id: id(704), nome: 'Fechamento', ordem: 4 }];
const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jP1kAAAAASUVORK5CYII=';
const MESSAGES = Array.from({ length: 48 }, (_, i) => ({
  id: id(400 + i), lead_id: CONTACTS[0].id, conversation_id: CONTACTS[0].conversation_id,
  telefone: CONTACTS[0].telefone_contato, from_me: i % 3 !== 0, origem_mensagem: i % 3 === 1 ? 'ia' : i % 3 === 2 ? 'humano' : 'whatsapp',
  tipo_mensagem: 'text', texto: `Mensagem sintética ${String(i + 1).padStart(2, '0')} — conteúdo de demonstração para leitura e navegação de um histórico longo.`,
  criado_em: new Date(Date.UTC(2026, 9, 12, 10, i)).toISOString(), status: 'read', reactions: i === 47 ? [{ emoji: '👍', message_id: id(447) }] : [],
}));
MESSAGES[44] = { ...MESSAGES[44], tipo_mensagem: 'image', media_url: PIXEL, texto: 'Imagem sintética' };
MESSAGES[45] = { ...MESSAGES[45], tipo_mensagem: 'document', media_url: PIXEL, media_filename: 'proposta-sintetica.pdf', texto: 'Documento sintético' };
const EVENTS = [
  { id: 'payment:synthetic', category: 'payment', title: 'Pagamento em andamento', status: 'pending', occurred_at: '2026-10-12T12:00:00Z' },
  { id: 'message:synthetic', category: 'message', title: 'Mensagem registrada', status: 'observed', occurred_at: '2026-10-12T11:00:00Z' },
  { id: 'followup:synthetic', category: 'followup', title: 'Follow-up bloqueado', status: 'not_occurred', occurred_at: '2026-10-12T10:00:00Z' },
].map(item => ({ ...item, type: `${item.category}_fact`, evidence: { source: item.category === 'message' ? 'mensagens' : `${item.category}_events`, record_id: 'synthetic', relationship: 'explicit_lead_id' }, details: { direction: 'inbound', token: 'NEVER_RENDER_PRIVATE', payload: { text: 'NEVER_RENDER_PRIVATE' } } }));
function journey(url) {
  const category = url.searchParams.get('category');
  const more = !category && !url.searchParams.get('cursor');
  return { lead_id: url.pathname.split('/leads/')[1].split('/')[0], checked_at: '2026-10-12T13:00:00Z', items: category ? EVENTS.filter(item => item.category === category) : more ? EVENTS.slice(0, 2) : EVENTS.slice(1), next_cursor: more ? 'synthetic-cursor' : null, has_more: more, limit: 30,
    summary: { crm: { stage: { id: STAGES[1].id, name: STAGES[1].nome }, tags: [{ id: TAGS[0].id, name: TAGS[0].nome }], snapshot_only: true, history_complete: false }, funnel: { version: 'synthetic-v1', stages: [], snapshot_only: true, history_complete: false }, activity: { inactive_seconds: 3600, state: 'observed', abandonment_inferred: false }, attribution: { status_counts: { partial: 1 }, ads_identity_verified: false } },
    coverage: { timezone: 'UTC', history_complete: false, sources: [{ source: 'checkout_events', category: 'checkout', records: 0, timeline_records: 0, missing_timestamp: 0, state: 'unknown', limitation: 'Não observado nesta fonte.' }], categories: [{ category: 'checkout', records: 0, state: 'unknown' }], limitations: ['Histórico de etapas incompleto.'] },
  };
}
function funnel(url) {
  const rate = { numerator: 36, denominator: 36, value: 1, state: 'observed' };
  return { window: { start: url.searchParams.get('start'), end: url.searchParams.get('end'), observed_at: url.searchParams.get('observed_at') || '2026-10-12T13:00:00Z', inactivity_hours: 168, return_window_days: 7, entry_definition: 'Conversa elegível', unit: 'CRMLead.id' }, summary: { total_leads: 36, conversations: 36, inbound_leads: 36, active: 26, inactive: 10, converted: 0, lost: 0, indeterminate: 0, closed_without_conversion: 1, revenue: { status: 'unknown', by_currency: [] } }, stages: [{ key: 'entry', label: 'Entrada', order: 1, source: 'tag', reached_leads: 36, state: 'observed', rate }], transitions: [], bottlenecks: [], series: [], crm_snapshot: [], followup: { sent_leads: 0, returned_leads: 0, numerator: 0, denominator: 0, rate: null, window_days: 7, relation: 'temporal_not_causal' }, attribution: { exact_leads: 0, partial_leads: 36, unknown_leads: 0, denominator: 36, coverage: { value: 0, numerator: 0, denominator: 36 } }, ads_comparison: { state: 'unknown', rows: [], limitations: [] }, coverage: { entry: { state: 'observed', denominator: 36 }, stage_history: { state: 'partial', history_complete: false }, financial: { state: 'unknown' }, loss: { state: 'unknown' }, prompt_exposure: { state: 'unknown' }, limitations: ['Histórico parcial.'] }, options: { stages: [{ key: 'entry', label: 'Entrada' }], origins: [], campaigns: [], ads: [] } };
}
module.exports = { TENANT, id, TAGS, CONTACTS, STAGES, MESSAGES, journey, funnel };
