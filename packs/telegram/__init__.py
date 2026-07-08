"""activegraph.packs.telegram — Telegram Adapter Pack v0.1.

A pure transport adapter: inbound Telegram updates become chat_input
objects (the Chat Pack owns everything conversational — sessions, memory,
reply gating, the agentic responder), and approved outbound replies become
Tool Gateway capability calls (recorded, policy-checked, bot token
injected by the Secrets Pack at execution time). The adapter itself never
touches the network or a credential.

Object types: telegram_update
Behaviors:    telegram_ingester, telegram_dispatcher
Tools:        submit_telegram_update
Capabilities: telegram.send_message (capabilities.py)
Driver:       python -m packs.telegram.poller (edge code — long-polls the
              Bot API and posts updates to a running assistant)

Behavior map:
  [driver] submit_telegram_update(graph, raw_update)
    → telegram_update.created
        → telegram_ingester → chat_input (user_ref='telegram:<user_id>',
                              metadata.channel='telegram')
            → [Chat Pack: ingester → gate → responder → candidate(channel=telegram)]
  comm_response_candidate.created [channel=telegram, approved]
    → telegram_dispatcher → capability_call (telegram.send_message, proposed)
        → [Tool Gateway: policy → approval → execute w/ injected token → audit]

Identity: bind the owner with
IdentitySettings.owner_identifiers=["telegram:<user_id>"] (or
register_principal) so reply gating recognizes them — a Telegram bot is
reachable by anyone who finds it, which is exactly what the gate is for.

Entry point: registered as 'telegram' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import TelegramSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "communication", "chat"],
# integrates_with=["tool_gateway", "secrets", "identity_auth"]
pack = Pack(
    name="telegram",
    version="0.1.1",
    description=(
        "Telegram transport adapter: inbound updates become chat_input "
        "(reusing the Chat Pack's conversation machinery); approved outbound "
        "replies become policy-checked Tool Gateway capability calls with the "
        "bot token injected at execution time."
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
        CapabilityDecl(provider='telegram', capability='send_message', risk_class='low', credential_ref='TELEGRAM_BOT_TOKEN'),
    ),
    settings_schema=TelegramSettings,
)

__all__ = ["pack", "TelegramSettings"]
