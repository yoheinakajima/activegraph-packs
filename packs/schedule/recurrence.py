"""Validated wall-calendar rules; UTC instants remain the clock boundary."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, StrictInt, model_validator

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CalendarTimezone = Literal["UTC", "Asia/Seoul"]
MonthDay = Annotated[StrictInt, Field(ge=1, le=31)] | Literal["last"]
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CalendarRule(BaseModel):
    kind: Literal["daily", "weekly", "monthly"]
    at_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: CalendarTimezone = "UTC"
    weekdays: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    day_of_month: MonthDay | None = None

    @model_validator(mode="after")
    def matching_fields(self):
        if (self.weekdays is not None) != (self.kind == "weekly"):
            raise ValueError("weekdays is required only for weekly schedules")
        if self.weekdays and len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekdays must be unique")
        if (self.day_of_month is not None) != (self.kind == "monthly"):
            raise ValueError("day_of_month is required only for monthly schedules")
        return self

    def next_after(self, after: datetime) -> datetime:
        zone = ZoneInfo(self.timezone)
        local = after.astimezone(zone)
        hour, minute = map(int, self.at_time.split(":"))
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if self.kind == "monthly":
            # Recompute from the declared day every month; February must not
            # move a 31st-of-month rule permanently onto the 28th.
            year, month = local.year, local.month
            for _ in range(2):
                last = monthrange(year, month)[1]
                day = last if self.day_of_month == "last" else min(self.day_of_month, last)
                candidate = candidate.replace(year=year, month=month, day=day)
                if candidate.astimezone(timezone.utc) > after:
                    return candidate.astimezone(timezone.utc)
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        else:
            for offset in range(8):
                due = candidate + timedelta(days=offset)
                if self.kind == "weekly" and WEEKDAYS[due.weekday()] not in self.weekdays:
                    continue
                if due.astimezone(timezone.utc) > after:
                    return due.astimezone(timezone.utc)
        raise ValueError("no next calendar occurrence in supported date range")
