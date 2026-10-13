// Synthetic, read-only facts. No customer records, credentials or provider calls.
const F = require('./fixtures-ux04-operations.cjs');
const events = Array.from({ length: 45 }, (_, index) => ({
  id: `journey28:${index}`, category: ['booking', 'message', 'payment'][index % 3],
  title: `Acontecimento sintético ${String(index + 1).padStart(2, '0')} — evidência persistida com título longo para consultar a jornada comercial sem comprimir a conversa`,
  status: ['observed', 'pending', 'unknown', 'not_occurred', 'future_unknown_state'][index % 5],
  occurred_at: index === 44 ? '2018-11-04T03:30:00Z' : new Date(Date.UTC(2026, 9, 12, 0, 30 - index)).toISOString(),
  evidence: { source: 'synthetic_readonly_events', record_id: `fixture-${index}`, relationship: 'explicit_lead_id' },
  details: { scheduled_at: '2026-10-13T03:01:00Z', started_at: '2019-02-17T01:30:00Z', ended_at: '2026-10-13T04:00:00Z', direction: 'inbound', token: 'NEVER_RENDER_PRIVATE', payload: { text: 'NEVER_RENDER_PRIVATE' } },
}));
function journey(url, empty = false) {
  const original = F.journey(url);
  const leadId = url.pathname.split('/leads/')[1].split('/')[0];
  const category = url.searchParams.get('category');
  const filtered = empty ? [] : events.filter(item => !category || item.category === category);
  const cursor = url.searchParams.get('cursor');
  const offset = cursor ? 29 : 0;
  const selected = filtered.slice(offset, offset + 30).map(item => ({ ...item, id: `${leadId}:${item.id}` }));
  const hasMore = offset + 30 < filtered.length;
  return { ...original, lead_id: leadId, checked_at: '2026-10-12T00:30:00Z', items: selected, has_more: hasMore, next_cursor: hasMore ? 'opaque-next-page' : null,
    summary: { ...original.summary, bookings: [{ id: 'synthetic-booking', status: 'pending', scheduled_at: '2026-10-13T03:01:00Z', ended_at: '2026-10-13T04:00:00Z' }], activity: { last_event_at: '2026-10-12T00:30:00Z', first_message_at: '2018-11-04T03:30:00Z', inactive_seconds: null, inbound_messages: null, outbound_messages: null, recorded_response_intervals: null } },
  };
}
module.exports = { F, events, journey };
