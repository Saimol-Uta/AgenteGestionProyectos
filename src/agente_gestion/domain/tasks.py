"""Task entities, state mappings, and deterministic metrics."""

from datetime import datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

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
        description="IDs of overdue cards (due before cutoff, not completed, state known)"
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


def calculate_task_metrics(
    cards: list[TaskCard],
    cutoff: datetime,
    timezone_name: str = "America/Guayaquil",
) -> DeterministicTaskMetrics:
    """
    Deterministically computes progress, overdue, blocked, and unknown task metrics.

    Enforces domain invariants:
    - Zero progress is None (when included denominator is 0)
    - Unknown states are NOT overdue, even if due_date < cutoff
    - Completed states are NOT overdue, even if due_date < cutoff
    - Overdue requires due_date < cutoff AND pending/in_progress/blocked state
    - Timezone-aware cutoff comparison in configured timezone (default America/Guayaquil)
    - Rejects duplicate task IDs
    """
    tz = ZoneInfo(timezone_name)
    normalized_cutoff = normalize_datetime_to_tz(cutoff, tz)

    seen_ids: set[str] = set()
    total_cards = len(cards)
    included_count = 0
    completed_count = 0
    overdue_ids: list[str] = []
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

        # Overdue evaluation:
        # A card is overdue IF and ONLY IF:
        # 1. It has a due_date
        # 2. due_date < normalized_cutoff
        # 3. state is NOT completed
        # 4. state is NOT unknown (unknown states must not be flagged overdue)
        if card.due_date is not None:
            card_due = normalize_datetime_to_tz(card.due_date, tz)
            if card_due < normalized_cutoff:
                if card.state not in (TaskState.completed, TaskState.unknown):
                    overdue_ids.append(card.id)

    progress: Optional[float] = None
    if included_count > 0:
        progress = round((completed_count / included_count) * 100, 1)

    return DeterministicTaskMetrics(
        total_cards=total_cards,
        included_cards=included_count,
        completed_cards=completed_count,
        progress_percentage=progress,
        overdue_card_ids=sorted(overdue_ids),
        blocked_card_ids=sorted(blocked_ids),
        unknown_card_ids=sorted(unknown_ids),
        contradictory_card_ids=sorted(contradictory_ids),
    )
