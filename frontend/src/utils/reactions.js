function reactionIdentity(reaction) {
  return String(reaction?.message_id || reaction?.id || "");
}

export function applyRealtimeReaction(messages, reactionMessage) {
  const reaction = reactionMessage?.metadata_json?.reaction;
  const targetWamid = String(reaction?.message_id || "").trim();
  const emoji = reaction?.emoji;
  if (!targetWamid || emoji === undefined || emoji === null || String(emoji) === "") {
    return messages;
  }

  const reactionEventId = String(
    reactionMessage?.external_message_id || reactionMessage?.id || "",
  );
  const direction = String(reactionMessage?.direction || "inbound");

  return (messages || []).map((message) => {
    if (String(message?.external_message_id || "") !== targetWamid) return message;

    const current = Array.isArray(message?.reactions) ? message.reactions : [];
    const next = current.filter((item) => {
      const itemId = reactionIdentity(item);
      if (reactionEventId && itemId === reactionEventId) return false;
      // A one-to-one WhatsApp conversation has one current reaction per side.
      return String(item?.direction || "") !== direction;
    });

    return {
      ...message,
      reactions: [
        ...next,
        {
          emoji: String(emoji),
          message_id: reactionEventId,
          direction,
        },
      ],
    };
  });
}
