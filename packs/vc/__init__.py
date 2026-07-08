"""activegraph.packs.vc — VC Pack v0.1.

Founder relationship management, deal tracking, and investment memo generation.

Object types:
  company_profile   — Startup being evaluated for investment
  founder_profile   — Founder associated with a company
  deal_round        — Fundraising round under evaluation
  traction_metric   — Business metric (ARR, MRR, DAU, NPS, etc.)
  investment_memo   — Structured investment memorandum
  investment_thesis — The fund's investment thesis
  deal_risk         — Risk identified during evaluation
  followup          — Follow-up action for a company
  lp_update         — Portfolio update for limited partners

Behaviors:
  founder_email_detector — comm_message.created → observation(founder_outreach)
  company_enricher       — observation.created (founder_outreach) → CompanyProfile + FounderProfile
  memo_drafter           — company_profile.created → InvestmentMemo draft
  followup_tracker       — company_profile.created → Followup + Core task
  lp_update_generator    — deal_round.created (notable status) → LPUpdate draft

Composes with: Core Pack, Communication Pack (comm_message), Entity Pack (person/org)
Note: Person/Organization objects use Entity Pack — not duplicated here.
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import VCSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "communication"], composes_with=["entity", "memory_gateway", "identity_auth"]
pack = Pack(
    name="vc",
    version="0.1.1",
    description=(
        "VC deal flow management: founder outreach detection, company profiling, "
        "memo drafting, followup tracking, and LP updates. "
        "Provides 9 object types for investment workflow tracking."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=VCSettings,
)

__all__ = ["pack", "VCSettings"]
