import type { ProposalDetail } from "../../api/types";

export function proposalScope(proposal?: ProposalDetail) {
  return proposal ? `proposal:${proposal.project_id}:${proposal.proposal_id}:${proposal.capture_id}:${proposal.proposal_body_sha256}` : "proposal:none";
}

function hasSupportedVersion(proposal: ProposalDetail) {
  return proposal.version !== null && Number.isInteger(proposal.version) && proposal.version >= 0;
}

const terminalDecisionHistory = new Set(["approve_and_execute", "modify_and_execute", "reject", "deny", "revoke"]);
// Offline approval and dry-run authorization records do not prove a terminal execution.
const knownNonTerminalDecisionHistory = new Set(["approve_offline", "authorize_dry_run"]);

function hasKnownTerminalHistory(proposal: ProposalDetail) {
  return proposal.latest_receipt != null
    || (proposal.decision_history_availability === "available"
      && proposal.decision_history?.some((entry) => terminalDecisionHistory.has(entry.decision)) === true)
    || (proposal.relationship_availability === "available" && (proposal.derived_proposal_ids?.length ?? 0) > 0);
}

/** Reject is a protected manager decision, independent of technical readiness or freshness. */
export function proposalCanReject(proposal: ProposalDetail) {
  return proposal.review_status === "pending"
    && ["not_authorized", "authorized_dry_run"].includes(proposal.authorization_status)
    && proposal.execution_status === "not_executed"
    && !hasKnownTerminalHistory(proposal)
    && !(proposal.relationship_availability === "available" && proposal.original_proposal_id != null);
}

/** Missing audit detail is not evidence that there is no prior or consumed decision. */
export function proposalExecutionBlockReason(proposal: ProposalDetail): string | null {
  if (!hasSupportedVersion(proposal)) return "La versión de la propuesta no está confirmada; no se ofrece ejecutar.";
  if (proposal.freshness !== "current") return "La captura de esta propuesta no es actual; solo se puede rechazar.";
  if (proposal.technical_eligibility !== "eligible" || !Array.isArray(proposal.technical_errors) || proposal.technical_errors.length > 0) return "La propuesta no tiene elegibilidad técnica confirmada; solo se puede rechazar.";
  if (!proposal.technical_review || proposal.technical_review.technical_status !== "ready"
    || !Array.isArray(proposal.technical_review.technical_errors) || proposal.technical_review.technical_errors.length > 0) return "La revisión técnica determinista no está lista; solo se puede rechazar.";
  if (proposal.review_status !== "pending" && proposal.review_status !== "approved_offline") return "La decisión humana no figura como pendiente; no se ofrece otra ejecución.";
  if (!["not_authorized", "authorized_dry_run"].includes(proposal.authorization_status)) return "El estado de autorización no permite confirmar una nueva ejecución.";
  if (proposal.execution_status !== "not_executed") return "Ya existe un resultado de ejecución o su estado es desconocido; no se vuelve a ejecutar.";
  if (proposal.relationship_availability !== "available" || proposal.original_proposal_id === undefined
    || !Array.isArray(proposal.derived_proposal_ids)) return "La relación entre propuestas es desconocida; no se puede confirmar si esta propuesta fue consumida por una modificación.";
  if (proposal.original_proposal_id != null) return "Esta propuesta deriva de otra y no se ofrece una segunda ejecución.";
  if (proposal.derived_proposal_ids.length > 0) return "La propuesta original tiene una modificación derivada; no se ofrece volver a ejecutarla.";
  if (proposal.decision_history_availability !== "available" || !Array.isArray(proposal.decision_history)) return "El historial protegido es desconocido; no se presume que no haya una decisión previa.";
  if (proposal.decision_history.some((entry) => !terminalDecisionHistory.has(entry.decision) && !knownNonTerminalDecisionHistory.has(entry.decision))) return "El historial protegido contiene una decisión desconocida; no se ofrece ejecutar.";
  if (proposal.decision_history.some((entry) => terminalDecisionHistory.has(entry.decision))) return "Existe una decisión final o una ejecución registrada; no se ofrece otra ejecución.";
  // An offline approval is actionable only when protected history confirms it for this exact proposal.
  if (proposal.review_status === "approved_offline"
    && !proposal.decision_history.some((entry) => entry.decision === "approve_offline" && entry.proposal_id === proposal.proposal_id)) {
    return "La aprobación offline no está confirmada en el historial protegido; no se ofrece ejecutar.";
  }
  if (proposal.receipt_availability !== "available" || proposal.latest_receipt === undefined) return "El recibo de ejecución es desconocido; no se interpreta como ausencia de resultado.";
  if (proposal.latest_receipt !== null) return "Existe un recibo de ejecución; no se ofrece otra ejecución.";
  return null;
}

/** Execution requires explicit current readiness and complete protected history/relationship/receipt evidence. */
export function proposalCanExecute(proposal: ProposalDetail) {
  return proposalExecutionBlockReason(proposal) === null;
}

export function proposalCanModify(proposal: ProposalDetail) {
  return ["due_date", "priority", "assignee_ids"].includes(proposal.field);
}
