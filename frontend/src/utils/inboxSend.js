export function stableConversationSendPath(empresaId, lead, action = "send") {
  const leadId = String(lead?.id || "").trim();
  const conversationId = String(lead?.active_conversation_id || lead?.conversation_id || "").trim();
  if (!empresaId || !leadId || !conversationId || !["send", "send_media"].includes(action)) return "";
  return `/empresas/${encodeURIComponent(empresaId)}/inbox/leads/${encodeURIComponent(leadId)}/conversations/${encodeURIComponent(conversationId)}/${action}`;
}
