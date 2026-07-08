# WhatsApp Adapter Pack v0.1

> The mirror of the Telegram Adapter Pack, for Meta's WhatsApp Cloud API.

## Purpose

Inbound WhatsApp messages become `chat_input` objects — the Chat Pack owns
everything conversational (sessions, cross-session memory, reply gating,
the agentic responder). Approved outbound replies become Tool Gateway
capability calls with the access token injected by the Secrets Pack at
execution time. **The adapter never touches the network or a credential**;
the two packs differ from each other only in wire shapes (webhook envelope
vs. long-poll updates) and identity refs.

## Behavior Map

```
[Meta webhook → POST /channels/whatsapp/webhook]
  → whatsapp_message.created  (one per text message in the envelope)
      → whatsapp_ingester
          creates: chat_input (user_ref="whatsapp:<phone>",
                   metadata.channel="whatsapp", to_number routing data)
          relations: wa_ingested_as
          dedup: in-process wamid guard (Meta delivers at-least-once)
      → [Chat Pack: session → reply gate → responder]
          → comm_response_candidate (channel=whatsapp, approved)

comm_response_candidate.created [channel=whatsapp, approved]
  → whatsapp_dispatcher
      creates: capability_call (whatsapp.send_message, status=proposed)
      relations: delivers
      → [Tool Gateway: policy → approval → execute w/ injected token → audit]
```

## Object & Relation Types

| Type | Description |
|------|-------------|
| `whatsapp_message` | Raw inbound message (wamid, from_number, text; trimmed raw for audit) |
| `wa_ingested_as` | whatsapp_message → chat_input |

## Dependencies

```python
requires = ["core", "communication", "chat"]
integrates_with = ["tool_gateway", "secrets", "identity_auth"]
```

## Setup (Meta Cloud API)

1. Create a Meta app with WhatsApp product; note the **phone number id**
   and generate an **access token**.
2. Register the token: `POST /secrets {"name": "WHATSAPP_ACCESS_TOKEN", ...}`;
   set `WHATSAPP_PHONE_NUMBER_ID` (configuration, not a secret).
3. Point the app's webhook at `POST /channels/whatsapp/webhook` on a public
   HTTPS URL; set `WHATSAPP_VERIFY_TOKEN` for the GET hub-challenge
   handshake (the demo server implements both).
4. Bind the owner: `IdentitySettings(owner_identifiers=["whatsapp:<phone>"])`
   (demo server: `ACTIVEGRAPH_OWNER=whatsapp:<phone>`), and pick a
   `ChatSettings.reply_policy` — a business number is reachable by anyone
   who has it.

## Settings

| Field | Default | Description |
|-------|---------|-------------|
| `credential_name` | `WHATSAPP_ACCESS_TOKEN` | CredentialRef name for the Cloud API token |
| `phone_number_id` | `None` | Business phone-number id sends originate from (deployment config) |
| `outbound_risk_class` | `low` | Risk class for sends; raise to hold for manual approval. WhatsApp's own 24-hour customer-service window applies regardless |
| `api_base` | `https://graph.facebook.com/v20.0` | Cloud API base (test override) |

## Fixtures

```bash
python packs/whatsapp/fixtures/run_fixtures.py
```

Owner conversation end-to-end (real webhook envelope shape → reply →
gateway delivery); stranger deflection (zero LLM calls); wamid dedup
(Meta retries safely); non-text messages skipped cleanly. Mock send
capability + mock LLM — no token, no network, no API key.

## CHANGELOG

See [`CHANGELOG.md`](CHANGELOG.md).
