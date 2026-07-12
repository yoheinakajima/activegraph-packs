"""Gmail service connector: profiles, bounded sync, polling, and effects."""

from activegraph.packs import Pack
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import GmailSettings
from .tools import TOOLS

# requires=["activity_normalizer", "semantic_extraction", "communication", "usage", "tool_gateway", "connector_control"]
pack = Pack(
    name="gmail",
    version="0.2.0",
    description=(
        "Canonical Gmail service connector: budgeted exploration into a service/account profile, "
        "bounded replayable backfill, history-watermark polling, provider-neutral evidence, local "
        "conversation-family mapping with deterministic hygiene, local draft candidates, reversible "
        "draft writes, and R3-forever sends through an injected route adapter (Composio first)."
    ),
    object_types=tuple(OBJECT_TYPES), relation_types=tuple(RELATION_TYPES),
    behaviors=tuple(BEHAVIORS), tools=tuple(TOOLS), policies=(), prompts=(),
    capabilities=(
        CapabilityDecl(provider="gmail", capability="profile.get", risk_class="low", credential_ref="", action_class="R0"),
        CapabilityDecl(provider="gmail", capability="labels.list", risk_class="low", credential_ref="", action_class="R0"),
        CapabilityDecl(provider="gmail", capability="messages.fetch", risk_class="low", credential_ref="", action_class="R0"),
        CapabilityDecl(provider="gmail", capability="messages.get", risk_class="low", credential_ref="", action_class="R0"),
        CapabilityDecl(provider="gmail", capability="history.list", risk_class="low", credential_ref="", action_class="R0"),
        CapabilityDecl(provider="gmail", capability="drafts.create", risk_class="medium", credential_ref="", action_class="R2"),
        CapabilityDecl(provider="gmail", capability="drafts.send", risk_class="high", credential_ref="", action_class="R3"),
    ),
    settings_schema=GmailSettings,
)

__all__ = ["pack", "GmailSettings"]
