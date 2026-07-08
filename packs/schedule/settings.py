"""Schedule Pack settings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScheduleSettings(BaseModel):
    """Settings for the Schedule Pack.

    Deliberately small: the pack has no clock of its own, so most timing
    concerns (sweep frequency, timezone of the host) belong to the driver,
    not here.
    """

    catch_up: Literal["skip"] = Field(
        default="skip",
        description=(
            "What to do about due moments missed while no driver was running. "
            "'skip' (the only mode in v0.1): each overdue schedule fires once "
            "and next_due_at advances from the actual firing time — missed "
            "periods are not replayed. A future 'all' mode would emit one tick "
            "per missed period."
        ),
    )
