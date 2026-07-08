"""ActiveGraph Bundles.

Pre-assembled pack collections for common assistant configurations.
A bundle is a preset list of packs + default settings — not a new pack.

Available bundles:
  ASSISTANT_BUNDLE        — core infrastructure for any interactive assistant
  MESSAGING_BUNDLE        — assistant + telegram + whatsapp transport adapters
  EMAIL_ASSISTANT_BUNDLE  — assistant + email + entity
  RESEARCH_BUNDLE         — focused research pipeline (headless-friendly)

Factory functions:
  build_assistant()         — returns Runtime with ASSISTANT_BUNDLE loaded
  build_messaging_assistant()— returns Runtime with MESSAGING_BUNDLE loaded
  build_email_assistant()   — returns Runtime with EMAIL_ASSISTANT_BUNDLE loaded
  build_research_assistant()— returns Runtime with RESEARCH_BUNDLE loaded

Usage:
    from bundles import build_assistant
    rt = build_assistant()
    rt.run_goal("Diligence: Northwind Robotics")
"""

from __future__ import annotations

from bundles.assistant import (
    ASSISTANT_BUNDLE,
    ASSISTANT_PACK_LIST,
    build_assistant,
    load_assistant_packs,
    seed_default_profile,
    seed_owner_principals,
)
from bundles.messaging_assistant import (
    MESSAGING_BUNDLE,
    build_messaging_assistant,
    load_messaging_packs,
)
from bundles.email_assistant import (
    EMAIL_ASSISTANT_BUNDLE,
    EMAIL_ASSISTANT_PACK_LIST,
    build_email_assistant,
)
from bundles.research_bundle import RESEARCH_BUNDLE, RESEARCH_PACK_LIST, build_research_assistant

__all__ = [
    "ASSISTANT_BUNDLE",
    "ASSISTANT_PACK_LIST",
    "build_assistant",
    "load_assistant_packs",
    "seed_default_profile",
    "seed_owner_principals",
    "MESSAGING_BUNDLE",
    "build_messaging_assistant",
    "load_messaging_packs",
    "EMAIL_ASSISTANT_BUNDLE",
    "EMAIL_ASSISTANT_PACK_LIST",
    "build_email_assistant",
    "RESEARCH_BUNDLE",
    "RESEARCH_PACK_LIST",
    "build_research_assistant",
]
