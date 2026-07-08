"""activegraph.packs.evolution — Evolution Pack v0.1.

Self-modification with provenance (docs/evolution-design.md): the
assistant authors candidate packs, static gates check them without
executing anything, fork trials run them against replayed history in
isolation, a verified owner approves adoption through the gateway, and
the runtime's quiescent promote adopts the trial's state. Every step is
graph state; the bundle hash pins what was reviewed to what loads;
disable is immediate deregistration plus boot-time exclusion.

Shipped default: EvolutionSettings.enabled = False. Self-modification
is opt-in, and adoption REFUSES to register without a verified approver
or with a gateway policy that would auto-approve critical calls.

Object types: capability_gap, mod_proposal, gate_result, mod_trial,
              mod_promotion, mod_rollback, adoption_ticket
Behaviors:    gap_detector, proposal_gatekeeper, promotion_recorder
Capabilities: evolution.adopt_proposal (critical),
              evolution.disable_promotion (high), registered by the host
              via adopt.register_adoption_capabilities (which refuses
              unsafe configurations)
Host surface: trial.run_trial, adopt.process_adoption_tickets,
              boot.reload_adopted_packs (all run BETWEEN frames)

Entry point: registered as 'evolution' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import EvolutionSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "tool_gateway", "identity_auth"]
# integrates_with=["schedule", "chat"]
pack = Pack(
    name="evolution",
    version="0.5.2",
    description=(
        "Agent-authored packs under governance: static gates, fork trials "
        "against replayed history, bundle-hash pins, verified owner approval, "
        "quiescent promote adoption, immediate disable. Ships disabled."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    # Declarative capability surface (manifest-spec Q8 chain step 1). The
    # registrations themselves happen in adopt.register_adoption_capabilities,
    # which refuses unsafe configurations.
    capabilities=(
        CapabilityDecl(provider="evolution", capability="adopt_proposal",
                       risk_class="critical", credential_ref=""),
        CapabilityDecl(provider="evolution", capability="disable_promotion",
                       risk_class="high", credential_ref=""),
    ),
    settings_schema=EvolutionSettings,
)

__all__ = ["pack", "EvolutionSettings"]
