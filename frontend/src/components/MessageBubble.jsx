import ContactAvatar from "./ContactAvatar";

function formatMessageTime(criadoEm) {
  if (!criadoEm) return "";

  try {
    const date = new Date(criadoEm);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

export default function MessageBubble({
  message,
  isGrouped = false,
  renderContent,
  contactName,
  contactPhotoUrl,
}) {
  const isMine = Boolean(message?.from_me);
  const origem = String(message?.origem_mensagem || "").toLowerCase();
  const origemLabel = origem === "ia" ? "IA" : origem === "humano" ? "Atendente" : origem === "campaign" ? "Campanha" : "";
  const timeLabel = formatMessageTime(message?.criado_em);
  const statusLabel = isMine ? { sent: "Enviada", delivered: "Entregue", read: "Lida" }[String(message?.status || "").toLowerCase()] : "";

  return (
    <div
      data-message-id={message?.id}
      className={`inbox-message-row flex ${isMine ? "justify-end" : "justify-start"} ${isGrouped ? "mt-1" : "mt-3"}`}
    >
      {!isMine ? (
        <div className="mr-2 flex items-end">
          <ContactAvatar
            name={contactName}
            photoUrl={contactPhotoUrl}
            size="sm"
            className={isGrouped ? "opacity-0" : ""}
          />
        </div>
      ) : null}
      <article
        aria-label={`Mensagem ${isMine ? "enviada" : "recebida"}${timeLabel ? ` às ${timeLabel}` : ""}`}
        className={`inbox-message-bubble relative max-w-[90%] rounded-2xl px-4 py-3 pb-7 shadow-sm sm:max-w-[84%] ${
          isMine
            ? "inbox-message-outgoing ml-auto rounded-tr-md text-gray-900"
            : "inbox-message-incoming mr-auto rounded-tl-md text-gray-800"
        }`}
      >
        {!isMine && !isGrouped && contactName ? (
          <div className="inbox-message-author mb-1 text-[11px] font-semibold text-gray-500">
            {contactName}
          </div>
        ) : null}
        {origemLabel && !isGrouped ? (
          <div className="inbox-message-origin mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-500/90">
            {origemLabel}
          </div>
        ) : null}
        <div className="inbox-message-content text-[15px] leading-6">{renderContent?.(message)}</div>
        <footer className="inbox-message-meta">
          <time dateTime={message?.criado_em || undefined}>{timeLabel}</time>
          {statusLabel && <span>{statusLabel}</span>}
        </footer>
        {Array.isArray(message?.reactions) && message.reactions.length ? (
          <div className="inbox-message-reactions flex flex-wrap gap-1" aria-label="Reações">
            {message.reactions.map((reaction, index) => (
              <span
                key={`${reaction?.message_id || "reaction"}-${index}`}
                className="rounded-full border border-gray-200 bg-white px-1.5 py-0.5 text-xs shadow-sm"
              >
                {reaction?.emoji}
              </span>
            ))}
          </div>
        ) : null}
      </article>
    </div>
  );
}
