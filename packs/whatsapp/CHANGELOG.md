# WhatsApp Adapter Pack Changelog

## v0.1.0 — Initial release (2026-07-08)

### Added
- `whatsapp_message` object type + `wa_ingested_as` relation.
- `whatsapp_ingester` — message → chat_input (the Chat Pack owns the
  conversation). In-process wamid dedup for Meta's at-least-once webhook
  delivery; non-text messages skipped cleanly in v0.1.
- `whatsapp_dispatcher` — approved candidate (channel=whatsapp) →
  `whatsapp.send_message` capability_call proposal, linked via `delivers`.
- `capabilities.register_send_capability(phone_number_id=...)` — the Cloud
  API send, stdlib HTTP, token via execution_context only; refuses with the
  fix named when phone_number_id/credential are missing.
- `submit_whatsapp_webhook` tool — unwraps Meta's
  entry[].changes[].value.messages[] envelope.
- Demo server webhook receiver: POST /channels/whatsapp/webhook + the GET
  hub-challenge verification handshake (WHATSAPP_VERIFY_TOKEN).
- Fixtures: owner conversation (real envelope shape), stranger deflection,
  wamid dedup, non-text skip — all mock-LLM, no network.

### Design decisions
- Structural mirror of the Telegram Adapter Pack: same two-behavior shape,
  same chat_input reuse, same gateway-governed outbound path. The pair
  demonstrates that a new channel is ~two behaviors + one capability.
