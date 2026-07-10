# WhatsApp Adapter Pack Changelog

## v0.2.0 — send_message classified R3 (2026-07-10)

### Changed
- whatsapp.send_message declares `action_class="R3"`: a delivered
  message cannot be unsent — an outward, irreversible action. Legacy
  risk_class stays "low" (a separate dimension; no mapping), so hosts
  auto-approving low-risk calls keep today's behavior during migration;
  the class dimension itself never automates R3.

## v0.1.1 — Declarative capability surface (2026-07-08)

### Added
- `Pack.capabilities` populated with this pack's `CapabilityDecl`s
  (activegraph v1.4, manifest-spec Q8 chain step 1), so the loader's
  two-way surface check covers gateway capabilities; CI's AST check
  keeps the declaration honest against the registration call sites.

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
