import ContactAvatar from "./ContactAvatar";

function formatMessageTime(criadoEm) {
  if (!criadoEm) return "";

  try {
    return new Date(criadoEm).toLocaleTimeString([], {
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

  return (
    <div
      className={`flex ${isMine ? "justify-end" : "justify-start"} ${isGrouped ? "mt-1" : "mt-3"}`}
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
        className={`relative max-w-[90%] rounded-2xl px-4 py-3 pb-7 shadow-sm sm:max-w-[84%] ${
          isMine
            ? "ml-auto rounded-tr-md bg-[#d9fdd3] text-gray-900"
            : "mr-auto rounded-tl-md bg-white text-gray-800"
        }`}
      >
        {!isMine && !isGrouped && contactName ? (
          <div className="mb-1 text-[11px] font-semibold text-gray-500">
            {contactName}
          </div>
        ) : null}
        {origemLabel ? (
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-500/90">
            {origemLabel}
          </div>
        ) : null}
        <div className="text-[15px] leading-6">{renderContent?.(message)}</div>
        {Array.isArray(message?.reactions) && message.reactions.length ? (
          <div className="absolute -bottom-3 right-2 flex flex-wrap gap-1" aria-label="Reações">
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
        <footer className="absolute bottom-1.5 right-2.5 text-[11px] text-gray-500/80">
          {timeLabel}
        </footer>
      </article>
    </div>
  );
}
