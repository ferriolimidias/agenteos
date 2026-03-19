function formatMessageTime(criadoEm) {
  if (!criadoEm) return "";

  try {
    return new Date(criadoEm).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (_) {
    return "";
  }
}

export default function MessageBubble({
  message,
  isGrouped = false,
  renderContent,
}) {
  const isMine = Boolean(message?.from_me);
  const timeLabel = formatMessageTime(message?.criado_em);

  return (
    <div
      className={`flex ${isMine ? "justify-end" : "justify-start"} ${isGrouped ? "mt-1" : "mt-3"}`}
    >
      <article
        className={`relative max-w-[85%] rounded-2xl px-3.5 py-2.5 pb-6 shadow-sm sm:max-w-[80%] ${
          isMine
            ? "ml-auto rounded-tr-md bg-[#d9fdd3] text-gray-900"
            : "mr-auto rounded-tl-md bg-white text-gray-800"
        }`}
      >
        <div className="text-sm leading-6">{renderContent?.(message)}</div>
        <footer className="absolute bottom-1.5 right-2.5 text-[11px] text-gray-500/80">
          {timeLabel}
        </footer>
      </article>
    </div>
  );
}
