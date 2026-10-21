"""Explicit, evidence-bound preconditions for append-only corrective comments."""
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, StrictStr


class CommentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    priority_label_id: StrictStr
    expected_due_date: date
    human_evidence_id: StrictStr


def comment_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000 or "\x00" in value:
        raise ValueError("Comment must contain 1-2000 characters without NUL; exact whitespace is preserved")
    return value


def comment_evidence_errors(card, project_id, context, evidence, cutoff: datetime):
    """Human confirmation remains separate from API-observed task facts."""
    errors = []
    if not card.is_included or card.is_archived is not False or card.due_complete is not False or card.has_contradiction:
        errors.append("comment_target_ineligible")
    if (card.due_date is None or card.due_date.date() != context.expected_due_date
            or card.due_date >= cutoff):
        errors.append("comment_deadline_mismatch")
    if card.priority not in {"alta", "high", "critical", "critica"}:
        errors.append("comment_priority_mismatch")
    task_items = [e for e in evidence if e.category == "task" and e.provenance.source == "trello"
                  and e.provenance.project_id == project_id and e.provenance.external_id == card.id]
    if len(task_items) != 1:
        errors.append("comment_task_evidence_required")
    else:
        payload = task_items[0].provenance.payload
        if (context.priority_label_id not in payload.get("priority_label_ids", [])
                or payload.get("priority") != card.priority or payload.get("due_complete") is not False
                or payload.get("is_archived") is not False or payload.get("is_included") is not True
                or payload.get("due_date") != card.model_dump(mode="json")["due_date"]):
            errors.append("comment_task_evidence_mismatch")
    human = [e for e in evidence if e.provenance.id == context.human_evidence_id]
    if len(human) != 1:
        errors.append("comment_human_confirmation_required")
    else:
        item, p = human[0], human[0].provenance
        if (item.category != "human_confirmation" or p.source != "manual"
                or p.project_id != project_id or p.external_id != card.id
                or p.payload.get("implementation_developed") is not True
                or p.payload.get("validation_pending") is not True
                or not isinstance(p.payload.get("confirmed_by"), str) or not p.payload["confirmed_by"].strip()):
            errors.append("comment_human_confirmation_mismatch")
    return errors
