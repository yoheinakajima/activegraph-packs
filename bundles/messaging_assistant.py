"""Messaging Assistant Bundle — the always-on, reachable assistant.

The Assistant Bundle plus the messenger transport adapters:

  assistant bundle — core, tool_gateway, secrets, memory_gateway,
                     agent_profile, identity_auth, communication,
                     schedule, chat
  telegram         — Telegram Bot API adapter (long-poll driver)
  whatsapp         — WhatsApp Cloud API adapter (webhook driver)

The adapters are pure transport: inbound updates become chat_input (the
Chat Pack owns the conversation), outbound replies become policy-checked
Tool Gateway capability calls. Reaching the assistant from a phone changes
nothing about how it is governed.

Reply gating matters here: a bot token or business number is reachable by
strangers, so bind the owner (IdentitySettings.owner_identifiers=
["telegram:<user_id>", "whatsapp:<phone>"]) and pick a reply_policy.
"""

from __future__ import annotations

from activegraph import Runtime

from packs.telegram import pack as telegram_pack, TelegramSettings
from packs.whatsapp import pack as whatsapp_pack, WhatsAppSettings

from .assistant import (
    ASSISTANT_BUNDLE,
    build_assistant,
    load_assistant_packs,
)

MESSAGING_BUNDLE = ASSISTANT_BUNDLE + [telegram_pack, whatsapp_pack]


def load_messaging_packs(
    rt: Runtime,
    *,
    telegram_settings: TelegramSettings | None = None,
    whatsapp_settings: WhatsAppSettings | None = None,
    **assistant_kwargs,
) -> Runtime:
    """Register the Messaging Assistant Bundle onto an existing Runtime.

    Same resume-safety contract as load_assistant_packs (which this
    delegates to for the base packs).
    """
    load_assistant_packs(rt, **assistant_kwargs)
    rt.load_pack(telegram_pack, settings=telegram_settings or TelegramSettings())
    rt.load_pack(whatsapp_pack, settings=whatsapp_settings or WhatsAppSettings())
    return rt


def build_messaging_assistant(
    *,
    telegram_settings: TelegramSettings | None = None,
    whatsapp_settings: WhatsAppSettings | None = None,
    **assistant_kwargs,
) -> Runtime:
    """Create a Runtime with the Messaging Assistant Bundle loaded.

    Accepts every build_assistant keyword (settings overrides, llm_provider,
    persist_to, seed_profile) plus the two adapter settings.
    """
    rt = build_assistant(**assistant_kwargs)
    rt.load_pack(telegram_pack, settings=telegram_settings or TelegramSettings())
    rt.load_pack(whatsapp_pack, settings=whatsapp_settings or WhatsAppSettings())
    return rt
