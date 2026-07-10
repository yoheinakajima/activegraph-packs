"""activegraph.packs.whatsapp — WhatsApp Adapter Pack v0.1.

A pure transport adapter for Meta's WhatsApp Cloud API, structurally the
mirror of the Telegram Adapter Pack: inbound messages become chat_input
objects (the Chat Pack owns everything conversational — sessions, memory,
reply gating, the agentic responder), and approved outbound replies become
Tool Gateway capability calls (recorded, policy-checked, access token
injected by the Secrets Pack at execution time). The adapter itself never
touches the network or a credential.

Object types: whatsapp_message
Behaviors:    whatsapp_ingester, whatsapp_dispatcher
Tools:        submit_whatsapp_webhook
Capabilities: whatsapp.send_message (capabilities.py; needs phone_number_id)
Driver:       any HTTPS webhook receiver — the demo server ships
              POST /channels/whatsapp/webhook (+ GET hub-challenge
              verification for Meta's setup handshake)

Behavior map:
  [webhook] submit_whatsapp_webhook(graph, envelope)
    → whatsapp_message.created (per text message in the envelope)
        → whatsapp_ingester → chat_input (user_ref='whatsapp:<phone>',
                              metadata.channel='whatsapp')
            → [Chat Pack: ingester → gate → responder → candidate(channel=whatsapp)]
  comm_response_candidate.created [channel=whatsapp, approved]
    → whatsapp_dispatcher → capability_call (whatsapp.send_message, proposed)
        → [Tool Gateway: policy → approval → execute w/ injected token → audit]

Identity: bind the owner with
IdentitySettings.owner_identifiers=["whatsapp:<phone>"] (or
register_principal) so reply gating recognizes them.

Entry point: registered as 'whatsapp' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import WhatsAppSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "communication", "chat"],
# integrates_with=["tool_gateway", "secrets", "identity_auth"]
pack = Pack(
    name="whatsapp",
    version="0.2.0",
    description=(
        "WhatsApp Cloud API transport adapter: inbound messages become "
        "chat_input (reusing the Chat Pack's conversation machinery); approved "
        "outbound replies become policy-checked Tool Gateway capability calls "
        "with the access token injected at execution time."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    # Declarative capability surface (Q8 mechanism chain, step 1):
    # mirrors this pack's register_local_capability host wiring so the
    # loader's two-way surface check covers capabilities too. CI's AST
    # check (tests/test_manifests.py) keeps this honest against the code.
    capabilities=(
        CapabilityDecl(provider='whatsapp', capability='send_message', risk_class='low', credential_ref='WHATSAPP_ACCESS_TOKEN', action_class='R3'),
    ),
    settings_schema=WhatsAppSettings,
)

__all__ = ["pack", "WhatsAppSettings"]
