"""WhatsApp Adapter Pack settings."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class WhatsAppSettings(BaseModel):
    """Settings for the WhatsApp Adapter Pack (Meta Cloud API)."""

    credential_name: str = Field(
        default="WHATSAPP_ACCESS_TOKEN",
        description=(
            "Name of the CredentialRef holding the Cloud API access token. "
            "Resolved and injected by the Secrets Pack at execution time; the "
            "token never appears in the graph or the model context."
        ),
    )
    phone_number_id: Optional[str] = Field(
        default=None,
        description=(
            "The business phone-number id sends originate from (Cloud API "
            "path parameter). Configuration, not a secret — but with no "
            "default: it is deployment-specific."
        ),
    )
    outbound_risk_class: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description=(
            "Risk class for outbound send_message capability calls. Default "
            "'low': replying to the person who just messaged creates no new "
            "exposure. Raise to hold outbound messages for manual approval. "
            "NOTE: WhatsApp's own 24-hour customer-service window applies to "
            "business-initiated messages regardless of this setting."
        ),
    )
    api_base: str = Field(
        default="https://graph.facebook.com/v20.0",
        description="Cloud API base URL (override for testing).",
    )
