# Tenant OpenAI reasoning effort

`modelo_ia_reasoning_effort` is an optional tenant setting shared by the
conversation agent and Conversion Monitor, matching the existing shared
`modelo_ia` setting. A database `NULL` or API value `default` means the AgentOS
does not send an effort parameter. Existing rows are not backfilled.

The single capability policy lives in `app/core/model_catalog.py`. It is based
on exact OpenAI model IDs and official model documentation, not on `/v1/models`
or model-name prefixes. Unknown models expose only `default` until reviewed.
The dynamic model catalog attaches the policy response so the UI does not
duplicate compatibility rules. Update the policy version and add tests when
official model documentation changes.

The current AgentOS runtime sends OpenAI Chat Completions requests and returns
the assistant message text. `tools_context` is encoded as internal prompt
context; it is not native OpenAI function calling, and no structured-output or
tool-call response contract is active in this provider. Therefore this change
uses Chat Completions `reasoning_effort` and does not migrate to Responses.
If native function calling is introduced, GPT-6 Astra and GPT-6.1 Sol require
the Responses API for tool calling. GPT-6 Sol and GPT-6 Luna allow Chat
Completions function calling only with effort `none`. Any future adapter must
preserve the AgentLLM text contract, guardrails, retries, and error handling.

For an explicit effort other than `none`, sampling `temperature` is omitted.
The existing legacy sampling behavior remains for `default` where compatible;
profiles marked `chat_minimal` continue to omit it. No automatic model fallback
is performed.

Migration `2026_10_15_002_model_reasoning_effort.sql` adds a nullable column and
an allowed-value check. It is additive; no model or reasoning configuration is
changed. Apply it before deploying backend code that reads the new column.

Official references:

- [Reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
- [Responses API create](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Chat Completions create](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Deployment checklist](https://developers.openai.com/api/docs/guides/deployment-checklist)
