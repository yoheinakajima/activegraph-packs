"""activegraph.packs.email — Email Pack v0.1.

Translates inbound emails into channel-neutral CommMessage objects and handles
the draft → approval → send flow with configurable external write gating.

Object types:
  email_message — Raw inbound email (MIME headers, body, threading refs)
  email_thread  — Email thread grouping messages by In-Reply-To / References
  email_draft   — Formatted outbound draft pending approval

Behaviors:
  email_ingester  — email_message.created → Source + CommMessage + EmailThread
  reply_drafter   — comm_response_candidate.created (channel=email) → EmailDraft
  send_approver   — email_draft.created → approval gate or auto-approve

Behavior map:
  email_message.created
    → email_ingester
        creates: source(kind=email), comm_message(channel=email, inbound),
                 email_thread (created/updated)
        relations: email_thread_contains, derived_from_source
        hook: source.created → Identity Pack principal_resolver (if loaded)

  comm_response_candidate.created [channel=email]
    → reply_drafter
        creates: email_draft(subject=Re:..., to_addrs from original sender)
        relations: draft_from_candidate

  email_draft.created
    → send_approver
        if external AND require_approval_for_external=True:
          creates: action(kind=approval_request, risk_class=high)
          draft stays in 'draft' status
        else (internal or approval not required):
          patches: email_draft.status = "approved"
          patches: comm_response_candidate.status = "approved"
          → triggers response_dispatcher (Communication Pack)

Approval policy:
  require_approval_for_external=True (default): all external sends require owner confirmation.
  owner_email_addresses=[...]: addresses treated as internal.
  trusted_domains=[...]: domains treated as trusted (bypass approval).

Composes with:
  - Core Pack (source creation)
  - Communication Pack (CommMessage, CommResponseCandidate, thread_tracker, intent_detector)
  - Identity Pack (source.sender_ref → principal_resolver creates Principal)
  - Tool Gateway Pack (approval_request Action flows through Tool Gateway)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.communication import pack as comm_pack
    from packs.email import pack as email_pack, EmailSettings
    from packs.email.tools import ingest_email_fn, create_email_response_fn

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(comm_pack)
    rt.load_pack(email_pack, settings=EmailSettings(
        owner_email_addresses=["alice@example.com"],
        require_approval_for_external=True,
    ))

    ingest_email_fn(graph, message_id="<msg1@mail.example.com>",
                    from_addr="founder@startup.com", to_addrs=["alice@example.com"],
                    subject="Investment interest", body_text="Hi Alice, ...")
    rt.run_until_idle()
    # Source + CommMessage + EmailThread are in the graph

Entry point: registered as 'email' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import EmailSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "communication"], composes_with=["identity_auth", "tool_gateway"]
pack = Pack(
    name="email",
    version="0.1.2",
    description=(
        "Email adapter pack. Translates inbound emails into CommMessage(channel=email). "
        "email_ingester handles dedup, threading (In-Reply-To / References), and produces "
        "Source + CommMessage + EmailThread. reply_drafter formats EmailDraft. "
        "send_approver gates external sends with owner approval (policy: external_write_requires_owner_approval)."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=EmailSettings,
)

__all__ = ["pack", "EmailSettings"]
