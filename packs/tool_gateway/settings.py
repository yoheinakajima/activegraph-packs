"""Settings for Tool Gateway Pack."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ToolGatewaySettings(BaseModel):
    """Configuration for Tool Gateway Pack v0.1.

    Controls how capability calls are policy-checked, executed, and recorded.
    """

    auto_approve_risk_classes: list[Literal["low", "medium", "high", "critical"]] = Field(
        default=["low"],
        description=(
            "LEGACY RISK DIMENSION: risk classes that are automatically "
            "approved without human review. All other risk classes require "
            "explicit approval via rt.approve(). Set to ['low', 'medium'] "
            "for a more permissive policy. This dimension is independent of "
            "action_class (ADR 0016) — neither is ever inferred from the "
            "other."
        ),
    )

    capability_action_ceilings: dict[str, Literal["none", "R0", "R1", "R2"]] = Field(
        default_factory=dict,
        description=(
            "ACTION-CLASS DIMENSION, local lowering: per-capability "
            "automatic-authority ceilings, keyed by 'provider.capability'. "
            "A capability listed here auto-approves through the action-class "
            "path only up to the STRICTER of this ceiling and the runtime "
            "instance ceiling — local policy may always lower, never raise "
            "(runtime CONTRACT v1.9 #2). Capabilities not listed use the "
            "instance ceiling alone. The instance ceiling itself lives on "
            "the Runtime (rt.set_authority_ceiling), not in pack settings."
        ),
    )

    record_input_data: bool = Field(
        default=True,
        description=(
            "If True, capability call input_data is recorded in CapabilityCall. "
            "Set to False in production to avoid logging sensitive inputs."
        ),
    )

    record_output_data: bool = Field(
        default=True,
        description=(
            "If True, capability result output_data is recorded in CapabilityResult. "
            "Set to False in production to avoid logging sensitive outputs."
        ),
    )

    max_output_chars: int = Field(
        default=10000,
        ge=100,
        description="Maximum characters to store in CapabilityResult.output_data.",
    )

    create_source_from_result: bool = Field(
        default=True,
        description=(
            "If True, result_sourcer behavior creates a Core source object from "
            "each CapabilityResult so downstream behaviors can extract observations."
        ),
    )

    sanitize_output: bool = Field(
        default=True,
        description=(
            "If True (default), call_executor runs the sanitizer on raw output "
            "before storing in CapabilityResult. Redacts API keys, bearer tokens, "
            "hex secrets, and password field values. Always enabled in production."
        ),
    )

    approver_roles: list[str] = Field(
        default=["owner", "admin"],
        description=(
            "Principal roles allowed to resolve held calls via approve_capability / "
            "deny_capability. Enforced only when the Identity/Auth Pack is loaded and "
            "principals are registered; without it the gateway degrades gracefully and "
            "records the decision with verification='identity_unverified'."
        ),
    )

    injection_scan: bool = Field(
        default=True,
        description=(
            "If True (default), capability output is scanned for known "
            "prompt-injection patterns after sanitization. Matches never "
            "block the result — they are recorded on the capability_result, "
            "as injection_flag audit objects, and as a visible warning in "
            "the LLM-facing untrusted-content envelope."
        ),
    )

    envelope_llm_output: bool = Field(
        default=True,
        description=(
            "If True (default), tool output returned to a model through the "
            "LLM proxies is fenced in the EXTERNAL CONTENT envelope "
            "(data-not-instructions markers, plus the injection warning when "
            "flagged). Disable only for fixtures that assert raw output."
        ),
    )

    inject_credentials: bool = Field(
        default=True,
        description=(
            "If True, call_executor resolves and injects credentials from "
            "Secrets Pack when a credential_ref_name is present on the call. "
            "The resolved value is used for execution and immediately discarded — "
            "never stored in the graph."
        ),
    )
