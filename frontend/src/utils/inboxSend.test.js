import test from "node:test";
import assert from "node:assert/strict";
import { stableConversationSendPath } from "./inboxSend.js";

test("stable send path scopes the selected lead and conversation to the tenant", () => {
  assert.equal(
    stableConversationSendPath("tenant/one", { id: "lead-1", conversation_id: "conversation-1" }),
    "/empresas/tenant%2Fone/inbox/leads/lead-1/conversations/conversation-1/send",
  );
});

test("stable media path uses the same tenant-bound conversation identity", () => {
  assert.equal(
    stableConversationSendPath("tenant-1", { id: "lead-1", conversation_id: "conversation-1" }, "send_media"),
    "/empresas/tenant-1/inbox/leads/lead-1/conversations/conversation-1/send_media",
  );
});

test("missing identity and unsupported operations never fall back to a phone path", () => {
  assert.equal(stableConversationSendPath("tenant-1", { id: "lead-1" }), "");
  assert.equal(stableConversationSendPath("tenant-1", { conversation_id: "conversation-1" }), "");
  assert.equal(stableConversationSendPath("tenant-1", { id: "lead-1", conversation_id: "conversation-1" }, "retry"), "");
});
