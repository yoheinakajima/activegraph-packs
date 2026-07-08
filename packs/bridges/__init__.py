"""activegraph.packs.bridges — Bridge Packs.

Bridge packs connect existing domain packs to the Core primitive layer
without modifying the source packs. They subscribe to source-pack events
and emit Core equivalents with `derived_from` relations.

Available bridges:
  diligence_core — Maps Diligence pack objects to Core primitives
    document  → source
    claim     → observation
    memo      → artifact
    risk      → evaluation

Usage:
    from packs.bridges import diligence_core_bridge
    rt.load_pack(diligence_pack)          # Diligence goes first
    rt.load_pack(diligence_core_bridge)   # Bridge subscribes to Diligence events

Entry point: registered as 'diligence_core_bridge' in pyproject.toml
"""

from __future__ import annotations

from pydantic import BaseModel

from activegraph.packs import Pack

from .diligence_core import BEHAVIORS


class DiligenceCoreBridgeSettings(BaseModel):
    """Settings for the Diligence-Core bridge (no configuration required in v0.1)."""
    pass


pack = Pack(
    name="diligence_core_bridge",
    version="0.1.1",
    description=(
        "Maps Diligence pack objects to Core primitives so Diligence outputs "
        "appear alongside all other packs in the shared graph. "
        "document→source, claim→observation, memo→artifact, risk→evaluation. "
        "Non-destructive: does not modify the Diligence pack."
    ),
    object_types=[],
    relation_types=[],
    behaviors=BEHAVIORS,
    tools=[],
    policies=[],
    prompts=[],
    settings_schema=DiligenceCoreBridgeSettings,
)

diligence_core_bridge = pack

__all__ = ["pack", "diligence_core_bridge", "DiligenceCoreBridgeSettings"]
