# Telegram Adapter Pack Changelog

## v0.1.0 — Initial release (2026-07-08)

### Added
- `telegram_update` object type + `ingested_as` relation.
- `telegram_ingester` — update → chat_input (the Chat Pack owns the
  conversation: sessions, memory, reply gating, agentic responder). DM
  scope in v0.1; in-process update_id dedup for at-least-once pollers.
- `telegram_dispatcher` — approved candidate (channel=telegram) →
  `telegram.send_message` capability_call proposal, linked via `delivers`.
  The gateway records, policy-checks, injects the bot token at execution
  time, and audits; `outbound_risk_class` (default 'low') can hold every
  send for manual approval.
- `capabilities.register_send_capability()` — the Bot API send, stdlib
  HTTP, token via execution_context only.
- `submit_telegram_update` tool (wire-shape normalization) and the
  long-poll driver `python -m packs.telegram.poller` (edge code).
- Fixtures: owner conversation, stranger deflection, dedup, held-outbound
  approval loop — all mock-LLM, no network.

### Design decisions
- Pure transport adapter: conversation machinery is reused from the Chat
  Pack via chat_input (channel rides in metadata), not duplicated.
- The adapter never touches the network or credentials; the ONLY network
  call lives in the gateway-registered send capability.
