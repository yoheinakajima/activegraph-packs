"""Email Assistant Bundle.

Extends the Assistant Bundle with email processing and entity tracking.

Packs included (beyond Assistant Bundle):
  email   — email ingestion, reply drafting, approval-gated external send
  entity  — canonical people/organizations with deduplication
"""

from __future__ import annotations

from activegraph import Runtime

from packs.email import pack as email_pack, EmailSettings
from packs.entity import pack as entity_pack, EntitySettings

from bundles.assistant import (
    ASSISTANT_BUNDLE,
    build_assistant,
    CoreSettings,
    ToolGatewaySettings,
    SecretsSettings,
    MemoryGatewaySettings,
    AgentProfileSettings,
    IdentitySettings,
    CommunicationSettings,
    ChatSettings,
)


EMAIL_ASSISTANT_BUNDLE = ASSISTANT_BUNDLE + [
    email_pack,
    entity_pack,
]

# Alias for backward compatibility
EMAIL_ASSISTANT_PACK_LIST = EMAIL_ASSISTANT_BUNDLE


def build_email_assistant(
    *,
    core_settings: CoreSettings | None = None,
    tool_gateway_settings: ToolGatewaySettings | None = None,
    secrets_settings: SecretsSettings | None = None,
    memory_gateway_settings: MemoryGatewaySettings | None = None,
    agent_profile_settings: AgentProfileSettings | None = None,
    identity_settings: IdentitySettings | None = None,
    communication_settings: CommunicationSettings | None = None,
    chat_settings: ChatSettings | None = None,
    entity_settings: EntitySettings | None = None,
    email_settings: EmailSettings | None = None,
    llm_provider=None,
) -> Runtime:
    """Create a Runtime with the Email Assistant Bundle loaded.

    Includes the full Assistant Bundle plus email ingestion/drafting and
    entity deduplication (people and organizations).

    Args:
        core_settings: Override CoreSettings.
        tool_gateway_settings: Override ToolGatewaySettings.
        secrets_settings: Override SecretsSettings.
        memory_gateway_settings: Override MemoryGatewaySettings.
        agent_profile_settings: Override AgentProfileSettings.
        identity_settings: Override IdentitySettings.
        communication_settings: Override CommunicationSettings.
        chat_settings: Override ChatSettings.
        entity_settings: Override EntitySettings.
        email_settings: Override EmailSettings.
        llm_provider: LLM provider for LLM-backed behaviors.

    Returns:
        A configured Runtime ready to process emails and track entities.
    """
    rt = build_assistant(
        core_settings=core_settings,
        tool_gateway_settings=tool_gateway_settings,
        secrets_settings=secrets_settings,
        memory_gateway_settings=memory_gateway_settings,
        agent_profile_settings=agent_profile_settings,
        identity_settings=identity_settings,
        communication_settings=communication_settings,
        chat_settings=chat_settings,
        llm_provider=llm_provider,
    )

    rt.load_pack(email_pack, settings=email_settings or EmailSettings())
    # Email sources are raw `source` objects that do not flow through the
    # shared extraction layer yet, so this bundle explicitly selects the
    # pack's own source-scanning path (ADR 0026 step 4 keeps it off by
    # default; bridging email onto the shared layer is a follow-up).
    rt.load_pack(
        entity_pack,
        settings=entity_settings
        or EntitySettings(extract_from_raw_sources=True),
    )

    return rt


if __name__ == "__main__":
    print("Building email assistant bundle...")
    rt = build_email_assistant()
    print(f"Loaded {len(EMAIL_ASSISTANT_BUNDLE)} packs:")
    for p in EMAIL_ASSISTANT_BUNDLE:
        print(f"  - {p.name} v{p.version}")
