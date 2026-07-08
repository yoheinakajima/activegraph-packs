"""activegraph.packs.secrets — Secrets Pack v0.1.

Manages credential references, scopes, and usage auditing.
Secrets never enter model context.

Key invariants:
  - CredentialRef objects contain the credential NAME only — never the value
  - Actual secrets are resolved from env vars at execution time (never stored)
  - Every credential resolution creates a SecretUsageEvent (name only)
  - Secrets Pack does NOT store, transmit, or log actual secret values

Object types: credential_ref, secret_usage_event
Behaviors:    secret_usage_recorder
Tools:        resolve_credential

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.secrets import pack as secrets_pack, SecretsSettings

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(secrets_pack, settings=SecretsSettings())

    # Register a credential reference (no actual secret stored in graph)
    graph.add_object('credential_ref', {
        'name': 'OPENAI_API_KEY',
        'scope': 'read',
        'provider_hint': 'openai',
    })

    # At call time, Tool Gateway resolves it via the tool:
    # secret = resolve_credential('OPENAI_API_KEY')  # reads from env
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import SecretsSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], integrates_with=["tool_gateway"]
pack = Pack(
    name="secrets",
    version="0.1.1",
    description=(
        "Credential reference management and usage auditing. "
        "Secrets never enter model context — CredentialRef contains names only. "
        "Actual values resolved from environment variables at execution time."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=SecretsSettings,
)

__all__ = ["pack", "SecretsSettings"]
