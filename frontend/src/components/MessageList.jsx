import MessageBubble from "./MessageBubble";
import { Fragment } from "react";
import { EmptyState } from "./ui";

function messageDay(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" });
}

export default function MessageList({
  messages = [],
  renderMessageContent,
  messagesEndRef,
  contactName,
  contactPhotoUrl,
  hideEmptyState = false,
}) {
  return (
    <div
      className="inbox-message-list mx-auto flex w-full max-w-6xl flex-col"
    >
      {messages.map((message, index) => {
        const previousMessage = index > 0 ? messages[index - 1] : null;
        const day = messageDay(message?.criado_em);
        const startsDay = day && day !== messageDay(previousMessage?.criado_em);
        const isGrouped =
          Boolean(previousMessage) &&
          !startsDay &&
          Boolean(previousMessage?.from_me) === Boolean(message?.from_me) &&
          String(previousMessage?.origem_mensagem || "") === String(message?.origem_mensagem || "");

        return (
          <Fragment key={message.id || `${message?.criado_em || "msg"}-${index}`}>
          {startsDay && <div className="inbox-message-day"><span>{day}</span></div>}
          <MessageBubble
            message={message}
            isGrouped={isGrouped}
            renderContent={renderMessageContent}
            contactName={contactName}
            contactPhotoUrl={contactPhotoUrl}
          />
          </Fragment>
        );
      })}

      <div ref={messagesEndRef} />

      {messages.length === 0 && !hideEmptyState ? (
        <EmptyState title="Nenhuma mensagem neste histórico" description="As mensagens desta conversa aparecerão aqui." />
      ) : null}
    </div>
  );
}
