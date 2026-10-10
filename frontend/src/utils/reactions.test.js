import test from "node:test";
import assert from "node:assert/strict";
import { applyRealtimeReaction } from "./reactions.js";

const target = {
  id: "message-1",
  external_message_id: "wamid.target",
  texto: "Mensagem original",
};

function incoming(emoji, id = "wamid.reaction") {
  return {
    id,
    external_message_id: id,
    direction: "inbound",
    tipo_mensagem: "reaction",
    metadata_json: { reaction: { message_id: "wamid.target", emoji } },
  };
}

test("attaches reaction to the target without appending a message bubble", () => {
  const result = applyRealtimeReaction([target], incoming("❤️"));
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].reactions, [{ emoji: "❤️", message_id: "wamid.reaction", direction: "inbound" }]);
});

test("preserves the received emoji and replaces the current reaction", () => {
  const withPrevious = {
    ...target,
    reactions: [{ emoji: "👍", message_id: "wamid.old", direction: "inbound" }],
  };
  const result = applyRealtimeReaction([withPrevious], incoming("😂", "wamid.new"));
  assert.deepEqual(result[0].reactions, [{ emoji: "😂", message_id: "wamid.new", direction: "inbound" }]);
});

test("does not attach to a target from another conversation or tenant", () => {
  const other = { id: "message-2", external_message_id: "wamid.other", reactions: [] };
  const result = applyRealtimeReaction([other], incoming("❤️"));
  assert.deepEqual(result, [other]);
});

test("duplicate reaction event is idempotent", () => {
  const first = applyRealtimeReaction([target], incoming("❤️"));
  const second = applyRealtimeReaction(first, incoming("❤️"));
  assert.equal(second[0].reactions.length, 1);
});
