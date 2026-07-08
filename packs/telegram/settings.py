"""Telegram Adapter Pack settings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TelegramSettings(BaseModel):
    """Settings for the Telegram Adapter Pack."""

    credential_name: str = Field(
        default="TELEGRAM_BOT_TOKEN",
        description=(
            "Name of the CredentialRef holding the bot token. Resolved and "
            "injected by the Secrets Pack at execution time; the token never "
            "appears in the graph or the model context."
        ),
    )
    outbound_risk_class: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description=(
            "Risk class for outbound send_message capability calls. Default "
            "'low': replying to the person who just messaged creates no new "
            "exposure, so replies auto-approve under the default gateway "
            "policy. Raise to 'medium'/'high' to hold every outbound message "
            "for manual approval."
        ),
    )
    api_base: str = Field(
        default="https://api.telegram.org",
        description="Telegram Bot API base URL (override for testing).",
    )
