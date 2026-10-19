"""Task entities, state mappings, and deterministic metrics."""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, StrictBool, StrictStr, field_validator

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


class TaskState(str, Enum):
    pending = "pending"
    in_progress = "en_curso"
    completed = "completada"
    blocked = "bloqueada"
    unknown = "desconocido"


class TaskMappingConfig(BaseModel):
    """Configuration mapping board lists and labels to domain states."""
    pending_list_ids: list[str] = Field(default_factory=list)
    in_progress_list_ids: list[str] = Field(default_factory=list)
    completed_list_ids: list[str] = Field(default_factory=list)
    blocked_labels: list[str] = Field(default_factory=lambda: ["bloqueada", "blocked"])
    included_list_ids: Optional[list[str]] = Field(
        default=None,
        description="If specified, only cards in these list IDs count in the progress denominator"
    )


class TaskCard(BaseModel):
    """Normalized task entity representing a Trello card or issue."""
    id: str = Field(description="Unique task identifier")
    title: str = Field(description="Task title")
    list_id: Optional[str] = Field(default=None, description="External list/column ID")
    list_name: Optional[str] = Field(default=None, description="External list/column name")
    labels: list[str] = Field(default_factory=list, description="Labels associated with task")
    state: TaskState = Field(default=TaskState.unknown, description="Mapped domain state")
    due_date: Optional[datetime] = Field(default=None, description="Due date and time")
    detail: Optional[str] = Field(default=None, description="Task details or comments")
    external_id: Optional[str] = Field(default=None, description="External provider ID")
    is_included: bool = Field(default=True, description="Whether this card counts for completion progress")
    has_contradiction: bool = Field(default=False, description="Flag indicating contradictory state signals")
    assignee_ids: Optional[list[StrictStr]] = Field(default=None, strict=True)
    assignee_names: dict[str, StrictStr] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
        description=(
            "Visible name of each responsible member, keyed by the member identifier. "
            "The identifier stays the identity; the name is only a display projection. "
            "It is omitted from the serialized capture while no name was resolved, so a "
            "capture without names keeps the exact content and identity it had before."
        ),
    )
    start_date: Optional[datetime] = None
    due_complete: Optional[StrictBool] = None
    is_archived: Optional[StrictBool] = None
    priority: Optional[StrictStr] = Field(default=None, description="Explicit priority only; never inferred from labels")
    last_activity_at: Optional[datetime] = None
    observed_fields: Optional[list[StrictStr]] = Field(
        default=None, strict=True,
        description="Observed domain field names, including explicit null/empty values; None means legacy unknown",
    )

    @field_validator("start_date", "last_activity_at", mode="before")
    @classmethod
    def validate_metadata_datetime(cls, value):
        """Accept ISO strings/datetimes, not numeric timestamps or coercible booleans."""
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Metadata dates must be ISO strings or datetimes")
        return value

    @field_validator("assignee_ids", "observed_fields")
    @classmethod
    def normalize_metadata_ids(cls, value):
        """Stable set ordering keeps serialized snapshots reproducible."""
        return sorted(set(value)) if value is not None else None

    @field_validator("assignee_names")
    @classmethod
    def normalize_metadata_names(cls, value):
        """Sorted keys keep the serialized snapshot reproducible."""
        if value is None:
            return None
        return {key: value[key] for key in sorted(value)}


class DeterministicTaskMetrics(BaseModel):
    """Deterministically calculated metrics over task cards snapshot."""
    total_cards: int = Field(description="Total cards evaluated in snapshot")
    included_cards: int = Field(description="Denominator cards included for progress")
    completed_cards: int = Field(description="Cards in completed state among included")
    progress_percentage: Optional[float] = Field(
        default=None,
        description="Completion percentage (completed / included * 100). None when included_cards is 0."
    )
    overdue_card_ids: list[str] = Field(
        default_factory=list,
        description="IDs of eligible unfinished cards due before cutoff"
    )
    due_soon_card_ids: list[str] = Field(
        default_factory=list,
        description="IDs of eligible unfinished cards due in the inclusive cutoff window"
    )
    blocked_card_ids: list[str] = Field(
        default_factory=list,
        description="IDs of cards in blocked state"
    )
    unknown_card_ids: list[str] = Field(
        default_factory=list,
        description="IDs of cards with unknown/unmapped state"
    )
    contradictory_card_ids: list[str] = Field(
        default_factory=list,
        description="IDs of cards with conflicting or contradictory state signals"
    )


def normalize_datetime_to_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    """Ensure datetime is timezone-aware and converted to target timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def validate_due_soon_days(value: int) -> int:
    """Deadline windows are nonnegative integers, never booleans or floats."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("due_soon_days must be a nonnegative integer")
    return value


def calculate_task_metrics(
    cards: list[TaskCard],
    cutoff: datetime,
    timezone_name: str = "America/Guayaquil",
    due_soon_days: int = 7,
) -> DeterministicTaskMetrics:
    """
    Deterministically computes progress, deadline, blocked, and unknown task metrics.

    Enforces domain invariants:
    - Zero progress is None (when included denominator is 0)
    - Unknown states are NOT overdue, even if due_date < cutoff
    - Completed states are NOT overdue, even if due_date < cutoff
    - Deadline risks require included, non-archived, pending/in_progress/blocked cards
      without raw due_complete=True; contradictory signals do not change state
    - Overdue: due < cutoff; due soon: cutoff <= due <= cutoff + days (inclusive)
    - Timezone-aware cutoff comparison in configured timezone (default America/Guayaquil)
    - Rejects duplicate task IDs
    """
    validate_due_soon_days(due_soon_days)
    tz = ZoneInfo(timezone_name)
    normalized_cutoff = normalize_datetime_to_tz(cutoff, tz)
    window_end = normalized_cutoff + timedelta(days=due_soon_days)

    seen_ids: set[str] = set()
    total_cards = len(cards)
    included_count = 0
    completed_count = 0
    overdue_ids: list[str] = []
    due_soon_ids: list[str] = []
    blocked_ids: list[str] = []
    unknown_ids: list[str] = []
    contradictory_ids: list[str] = []

    for card in cards:
        if card.id in seen_ids:
            raise ValueError(f"Duplicate card ID detected: {card.id}")
        seen_ids.add(card.id)

        if card.is_included:
            included_count += 1
            if card.state == TaskState.completed:
                completed_count += 1

        if card.state == TaskState.blocked:
            blocked_ids.append(card.id)

        if card.state == TaskState.unknown:
            unknown_ids.append(card.id)

        if card.has_contradiction:
            contradictory_ids.append(card.id)

        deadline_eligible = (
            card.is_included
            and card.is_archived is not True
            and card.due_complete is not True
            and card.state in (TaskState.pending, TaskState.in_progress, TaskState.blocked)
        )
        if deadline_eligible and card.due_date is not None:
            card_due = normalize_datetime_to_tz(card.due_date, tz)
            if card_due < normalized_cutoff:
                overdue_ids.append(card.id)
            elif normalized_cutoff <= card_due <= window_end:
                due_soon_ids.append(card.id)

    progress: Optional[float] = None
    if included_count > 0:
        progress = round((completed_count / included_count) * 100, 1)

    return DeterministicTaskMetrics(
        total_cards=total_cards,
        included_cards=included_count,
        completed_cards=completed_count,
        progress_percentage=progress,
        overdue_card_ids=sorted(overdue_ids),
        due_soon_card_ids=sorted(due_soon_ids),
        blocked_card_ids=sorted(blocked_ids),
        unknown_card_ids=sorted(unknown_ids),
        contradictory_card_ids=sorted(contradictory_ids),
    )
