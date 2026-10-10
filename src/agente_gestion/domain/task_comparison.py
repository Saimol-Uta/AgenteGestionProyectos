"""Pure snapshot observations, never an external-operation or actor audit log."""
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, normalize_datetime_to_tz

TaskField = Literal[
    "title", "list_id", "list_name", "state", "assignee_ids", "start_date",
    "due_date", "priority", "labels", "due_complete", "is_archived",
    "is_included", "has_contradiction",
]
FIELDS = (
    "title", "list_id", "list_name", "state", "assignee_ids", "start_date",
    "due_date", "priority", "labels", "due_complete", "is_archived",
    "is_included", "has_contradiction",
)
TaskValue = str | bool | datetime | list[str] | None
ComparisonWarning = Literal[
    "unknown_task_scope", "limited_task_enumeration", "conflicting_task_evidence",
]


class TaskChange(BaseModel):
    """One changed fact or enumeration observation; actor is deliberately unknown."""
    id: str
    task_id: str
    kind: Literal["field_changed", "first_observed", "not_observed"]
    field: TaskField | None = None
    before: TaskValue = None
    after: TaskValue = None
    previous_evidence_ids: list[str] = Field(default_factory=list)
    current_evidence_ids: list[str] = Field(default_factory=list)
    actor: None = None


class TaskSnapshotComparison(BaseModel):
    project_id: str
    previous_snapshot_id: str
    current_snapshot_id: str
    previous_captured_at: datetime
    current_captured_at: datetime
    changes: list[TaskChange]
    warnings: list[ComparisonWarning]


def _canonical(value):
    if isinstance(value, datetime):
        # Follow the domain's existing Guayaquil convention for naive dates.
        return normalize_datetime_to_tz(value, DEFAULT_TIMEZONE).astimezone(timezone.utc)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return sorted(set(value))
    return value


def _cards(snapshot):
    result = {}
    for card in snapshot.tasks:
        if card.id in result:
            raise ValueError(f"Duplicate card ID detected: {card.id}")
        result[card.id] = card
    return result


def _matching(card, project_id, evidence):
    identities = {card.id}
    if card.external_id is not None:
        identities.add(card.external_id)
    return [item for item in evidence if item.category == "task"
            and item.provenance.project_id == project_id
            and (item.provenance.external_id in identities
                 or item.provenance.payload.get("card_id") in identities)]


def _facts(card, evidence, warnings):
    """Explicit masks beat payload defaults; legacy cards only prove their title.

    Evidence accepts normalized domain keys, not raw provider aliases. Without
    an evidence mask, only keys actually present in its payload are observations.
    Disagreeing facts are withheld rather than resolved by input order.
    """
    observed = set(card.observed_fields) if card.observed_fields is not None else {"title"}
    candidates = {field: [(_canonical(getattr(card, field)), None)]
                  for field in FIELDS if field in observed}
    for item in evidence:
        payload = item.provenance.payload
        mask = payload.get("observed_fields")
        for field in FIELDS:
            if field not in payload or (mask is not None and field not in mask):
                continue
            try:
                # Validate only the fact under consideration; never treat the
                # resulting TaskCard defaults as additional observed fields.
                parsed = TaskCard.model_validate({"id": card.id, "title": card.title,
                                                  field: payload[field]})
            except ValidationError:
                continue
            candidates.setdefault(field, []).append(
                (_canonical(getattr(parsed, field)), item.provenance.id))
    facts, references = {}, {}
    for field, values in candidates.items():
        value = values[0][0]
        if any(other != value for other, _ in values):
            warnings.add("conflicting_task_evidence")
            continue
        facts[field] = value
        references[field] = sorted({id for _, id in values if id is not None})
    return facts, references


def compare_task_snapshots(
    previous: WeeklySnapshot,
    current: WeeklySnapshot,
    *,
    previous_evidence: Iterable[EvidenceItem] = (),
    current_evidence: Iterable[EvidenceItem] = (),
) -> TaskSnapshotComparison:
    """Compare same-project cuts without I/O, inference, mutation or attribution.

    Supply evidence from the respective cuts separately. Source identity should
    include board/source and mapping version; absent identity limits claims to
    observations. Completeness never upgrades absence to physical deletion.
    """
    if previous.project_id != current.project_id:
        raise ValueError("Cannot compare snapshots from different projects")
    if _canonical(previous.captured_at) > _canonical(current.captured_at):
        raise ValueError("Snapshot capture chronology is reversed")
    old_scope, new_scope = previous.task_source_identity, current.task_source_identity
    if old_scope is not None and new_scope is not None and old_scope != new_scope:
        raise ValueError("Task source/mapper identities differ")
    old_cards, new_cards = _cards(previous), _cards(current)
    old_evidence, new_evidence = tuple(previous_evidence), tuple(current_evidence)
    warnings = set()
    if old_scope is None or new_scope is None:
        warnings.add("unknown_task_scope")
    if previous.tasks_complete is not True or current.tasks_complete is not True:
        warnings.add("limited_task_enumeration")
    changes = []

    def append(task_id, kind, field, before, after, old_ids, new_ids):
        identity = [previous.project_id, previous.id, current.id,
                    _canonical(previous.captured_at).isoformat(),
                    _canonical(current.captured_at).isoformat(),
                    task_id, kind, field, before, after]
        encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                             default=lambda value: value.isoformat())
        changes.append(TaskChange(
            id="task-change-" + sha256(encoded.encode()).hexdigest(), task_id=task_id,
            kind=kind, field=field, before=before, after=after,
            previous_evidence_ids=sorted(set(old_ids)), current_evidence_ids=sorted(set(new_ids)),
        ))

    for task_id in sorted(old_cards.keys() | new_cards.keys()):
        old, new = old_cards.get(task_id), new_cards.get(task_id)
        old_items = _matching(old, previous.project_id, old_evidence) if old else []
        new_items = _matching(new, current.project_id, new_evidence) if new else []
        if old is None or new is None:
            append(task_id, "first_observed" if old is None else "not_observed",
                   None, old is not None, new is not None,
                   [e.provenance.id for e in old_items], [e.provenance.id for e in new_items])
            continue
        old_facts, old_refs = _facts(old, old_items, warnings)
        new_facts, new_refs = _facts(new, new_items, warnings)
        for field in sorted(old_facts.keys() & new_facts.keys()):
            if old_facts[field] != new_facts[field]:
                append(task_id, "field_changed", field, old_facts[field], new_facts[field],
                       old_refs[field], new_refs[field])
    return TaskSnapshotComparison(
        project_id=previous.project_id, previous_snapshot_id=previous.id,
        current_snapshot_id=current.id, previous_captured_at=previous.captured_at,
        current_captured_at=current.captured_at, changes=changes, warnings=sorted(warnings),
    )
