import MessageBubble from "./MessageBubble";

export default function MessageList({
  messages = [],
  renderMessageContent,
  messagesEndRef,
  contactName,
  contactPhotoUrl,
}) {
  return (
    <div
      className="mx-auto flex max-w-4xl flex-col"
      style={{
        backgroundImage: "radial-gradient(rgba(0,0,0,0.03) 1px, transparent 1px)",
        backgroundSize: "14px 14px",
      }}
    >
      {messages.map((message, index) => {
        const previousMessage = index > 0 ? messages[index - 1] : null;
        const isGrouped =
          Boolean(previousMessage) &&
          Boolean(previousMessage?.from_me) === Boolean(message?.from_me);

        return (
          <MessageBubble
            key={message.id || `${message?.criado_em || "msg"}-${index}`}
            message={message}
            isGrouped={isGrouped}
            renderContent={renderMessageContent}
            contactName={contactName}
            contactPhotoUrl={contactPhotoUrl}
          />
        );
      })}

      <div ref={messagesEndRef} />

      {messages.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-500">
          Nenhuma mensagem neste histórico.
        </div>
      ) : null}
    </div>
  );
}
