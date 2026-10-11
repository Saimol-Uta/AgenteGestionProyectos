"""Pure opt-in weekly window planning, without scheduling or capture authority."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class WeeklySchedule(BaseModel):
    """Owner-supplied policy only: no project, weekday, time or rule defaults."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    project_id: str
    weekday: int = Field(ge=0, le=6)
    local_time: str
    timezone: Literal["America/Guayaquil"]
    window_rule: Literal["previous_scheduled_week"]

    @field_validator("project_id")
    @classmethod
    def nonblank_project(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Invalid project identifier")
        return value

    @field_validator("local_time")
    @classmethod
    def exact_local_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Invalid local time")
        return value

    @classmethod
    def model_construct(cls, _fields_set=None, **values):
        """Retain unknown raw fields so planner revalidation cannot lose them."""
        model = super().model_construct(_fields_set=_fields_set, **values)
        object.__setattr__(model, "__pydantic_extra__",
                           {k: v for k, v in values.items() if k not in cls.model_fields})
        return model


class WeeklyWindow(BaseModel):
    """A candidate interval, not a job, captured snapshot or event identity."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    window_id: str
    elapsed_boundary_local: datetime
    elapsed_boundary_utc: datetime
    next_boundary_local: datetime
    next_boundary_utc: datetime
    window_start_local: datetime
    window_start_utc: datetime
    window_end_local: datetime
    window_end_utc: datetime
    cutoff_local: datetime
    cutoff_utc: datetime

    @computed_field
    @property
    def planning_only(self) -> bool:
        """Constant even for unsafe construction; never execution authority."""
        return True


def plan_weekly_window(schedule: WeeklySchedule, now: datetime) -> WeeklyWindow:
    """Plan the latest elapsed boundary <= injected aware evaluation time.

    The explicit previous_scheduled_week policy means [boundary-7 local days,
    boundary), with cutoff equal to boundary and next boundary +7 local days.
    Before this week's boundary the previous boundary is used; later storage
    must deduplicate this candidate. No capture completeness or historical
    reconstruction is implied. Identity is SHA256 of canonical UTF-8 JSON:
    full schedule plus the UTC window endpoints, not a real capture hash.
    """
    if type(schedule) is not WeeklySchedule:
        raise ValueError("Invalid weekly schedule")
    try:
        raw = dict(schedule.__dict__)
        if schedule.__pydantic_extra__:
            raise ValueError
        validated = WeeklySchedule.model_validate(raw)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid weekly schedule") from None
    if type(now) is not datetime:
        raise ValueError("Invalid evaluation time")
    try:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        local = now.astimezone(ZoneInfo(validated.timezone))
        hour, minute = map(int, validated.local_time.split(":"))
        boundary = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        boundary -= timedelta(days=(local.weekday() - validated.weekday) % 7)
        if boundary > local:
            boundary -= timedelta(days=7)
        boundaries = {"elapsed_boundary": boundary, "next_boundary": boundary + timedelta(days=7),
                      "window_start": boundary - timedelta(days=7), "window_end": boundary,
                      "cutoff": boundary}
        fields = {}
        for name, value in boundaries.items():
            fields[name + "_local"] = value
            fields[name + "_utc"] = value.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        raise ValueError("Invalid evaluation time") from None
    payload = {"schedule": validated.model_dump(),
               "window_start_utc": fields["window_start_utc"].isoformat(),
               "window_end_utc": fields["window_end_utc"].isoformat()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    window_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return WeeklyWindow(window_id=window_id, **fields)
