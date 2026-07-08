"""Settings for the Evolution Pack."""

from __future__ import annotations

from pydantic import BaseModel, Field

# The stage-2 import allow-list (docs/evolution-design.md §3 gate 4).
DEFAULT_IMPORT_ALLOW = [
    "__future__", "typing", "datetime", "json", "re", "math", "dataclasses",
    "pydantic", "activegraph.packs",
]
# Fixtures are test harness, exempted per the design (gate 4 exemption).
DEFAULT_FIXTURE_EXTRA_ALLOW = ["sys", "pathlib", "activegraph"]

# Reserved namespaces an authored pack may never register into (gate 6).
DEFAULT_RESERVED_NAMESPACES = ["tool_gateway", "evolution", "mcp"]


class EvolutionSettings(BaseModel):
    """Configuration for Evolution Pack v0.1.

    Shipped default is OFF (docs/evolution-design.md §5, threat T6):
    self-modification is opt-in, never ambient.
    """

    enabled: bool = Field(
        default=False,
        description="Master switch. Off: gap detection and gating no-op.",
    )

    max_total_source_bytes: int = Field(default=24_000, ge=1_000)
    max_file_bytes: int = Field(default=12_000, ge=500)

    gap_failure_threshold: int = Field(
        default=3, ge=1,
        description="Consecutive failures of one capability that open a gap.",
    )

    trial_max_new_events: int = Field(
        default=2_000, ge=10,
        description="Fork trial budget: max events the trial may add "
                    "(enforced by the runtime's budget net inside the child).",
    )
    trial_fixture_timeout_seconds: float = Field(
        default=30.0, gt=0,
        description="Wall clock for the fixture-gate child.")
    trial_wall_clock_seconds: float = Field(
        default=120.0, gt=0,
        description="Wall clock for the replay-trial child.")
    trial_max_rss_bytes: int = Field(
        default=536_870_912, ge=64_000_000,
        description="Address-space cap (RLIMIT_AS) for trial children.")
    max_conflict_retries: int = Field(
        default=2, ge=0,
        description=(
            "Automatic re-trials the chassis may attempt after a promote "
            "conflict before the proposal goes terminal (needs_owner). "
            "Zero means every conflict is the owner's problem immediately."
        ),
    )
    heldout_fraction: float = Field(
        default=0.5, gt=0.0, lt=1.0,
        description=(
            "Fraction of the replay segment reserved as held-out, touched "
            "exactly once at the final trial gate (regimes discipline)."
        ),
    )
    replay_object_types: list[str] = Field(
        default=["chat_input"],
        description="Recorded input types re-injected during fork trials.",
    )

    import_allow_list: list[str] = Field(default=DEFAULT_IMPORT_ALLOW)
    fixture_extra_allow: list[str] = Field(default=DEFAULT_FIXTURE_EXTRA_ALLOW)
    reserved_namespaces: list[str] = Field(default=DEFAULT_RESERVED_NAMESPACES)

    allowed_files: list[str] = Field(
        default=[
            "manifest.toml", "__init__.py", "object_types.py", "behaviors.py",
            "tools.py", "settings.py", "fixtures/run_fixtures.py",
            "fixtures/trial_scenario.py",
        ],
        description="The fixed authored file set (design §3 stage 1). "
                    "trial_scenario.py is the chassis driver, included "
                    "verbatim and gate-verified byte for byte.",
    )
