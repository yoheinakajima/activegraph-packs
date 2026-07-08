# Telegram Adapter Pack v0.1

> A pure transport adapter: your assistant, reachable from your phone,
> governed exactly like everywhere else.

## Purpose

Inbound Telegram updates become `chat_input` objects — from there the Chat
Pack owns everything conversational (sessions, cross-session memory, reply
gating, the agentic responder). Approved outbound replies become Tool
Gateway capability calls: recorded before sending, policy-checked, executed
with the bot token injected by the Secrets Pack at execution time. **The
adapter itself never touches the network or a credential** — its two
behaviors translate transport, nothing more.

## Behavior Map

```
[driver: python -m packs.telegram.poller  →  POST /channels/telegram/update]
  → telegram_update.created
      → telegram_ingester
          creates: chat_input (user_ref="telegram:<user_id>",
                   metadata.channel="telegram", chat_id routing data)
          relations: ingested_as
          dedup: in-process update_id guard (at-least-once pollers are safe)
      → [Chat Pack: session → reply gate → responder]
          → comm_response_candidate (channel=telegram, approved)

comm_response_candidate.created [channel=telegram, approved]
  → telegram_dispatcher
      creates: capability_call (telegram.send_message, status=proposed)
      relations: delivers
      → [Tool Gateway: policy → approval → execute w/ injected token → audit]
```

## Object & Relation Types

| Type | Description |
|------|-------------|
| `telegram_update` | Raw inbound update (update_id, chat_id, user_id, text; trimmed raw for audit) |
| `ingested_as` | telegram_update → chat_input |

(`delivers` — capability_call → comm_response_candidate — is declared by the
Communication Pack; this adapter creates instances.)

## Dependencies

```python
requires = ["core", "communication", "chat"]
integrates_with = ["tool_gateway", "secrets", "identity_auth"]
```

## Setup

1. Create a bot with @BotFather; register the token:
   `POST /secrets {"name": "TELEGRAM_BOT_TOKEN", "value": "..."}` (or env).
2. Bind the owner so reply gating recognizes them:
   `IdentitySettings(owner_identifiers=["telegram:<your_user_id>"])`
   (demo server: `ACTIVEGRAPH_OWNER=telegram:<id>`), and pick a
   `ChatSettings.reply_policy` — a bot is reachable by anyone who finds it.
3. Run the driver: `python -m packs.telegram.poller --server http://localhost:7788`.

## Settings

| Field | Default | Description |
|-------|---------|-------------|
| `credential_name` | `TELEGRAM_BOT_TOKEN` | CredentialRef name for the bot token |
| `outbound_risk_class` | `low` | Risk class for sends. 'low' = replies auto-approve (answering the person who just messaged adds no exposure); raise it to hold every outbound message for manual approval |
| `api_base` | `https://api.telegram.org` | Bot API base (test override) |

## Fixtures

```bash
python packs/telegram/fixtures/run_fixtures.py
```

Owner conversation end-to-end (update → reply → gateway delivery with the
credential seam exercised); stranger deflection (polite template, zero LLM
calls); update dedup; held outbound (risk=high → refused unverified
approver → owner approves → delivered). Mock send capability + mock LLM —
no token, no network, no API key.

## CHANGELOG

See [`CHANGELOG.md`](CHANGELOG.md).
