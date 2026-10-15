import type { ProposalDetail } from "../../api/types";

export function proposalScope(proposal?: ProposalDetail) {
  return proposal ? `proposal:${proposal.project_id}:${proposal.proposal_id}:${proposal.capture_id}:${proposal.proposal_body_sha256}` : "proposal:none";
}

/** Only a current, eligible proposal with a ready automatic review can be sent to /execute. */
export function proposalCanExecute(proposal: ProposalDetail) {
  return proposal.version !== null && Number.isInteger(proposal.version) && proposal.version >= 0
    && proposal.freshness === "current"
    && proposal.technical_eligibility === "eligible"
    && proposal.technical_errors.length === 0
    && proposal.technical_review?.technical_status === "ready"
    && proposal.technical_review.technical_errors.length === 0
    && proposal.execution_status === "not_executed";
}

export function proposalCanModify(proposal: ProposalDetail) {
  return ["due_date", "priority", "assignee_ids"].includes(proposal.field);
}
