import { Link } from "react-router-dom";
import {
  fieldLabel,
  proposalAuthorizationStatusLabel,
  proposalExecutionStatusLabel,
  proposalFreshnessLabel,
  proposalOutcomeExplanation,
  proposalOutcomeLabel,
  proposalOutcomeTone,
  proposalReviewStatusLabel,
  proposalStatusTone,
  proposalTechnicalStatusLabel,
} from "../api/formatters";
import type { ProposalStageDiagnostic, TaskRisk } from "../api/types";
import { Pill } from "./States";

/**
 * Proposals that a deterministic risk actually motivated. A linked proposal is never presented
 * as proof that the risk disappeared: the risk stays vigente until a later capture proves
 * otherwise. A relation that belongs to another capture is marked as historical, and a risk
 * without a linked proposal declares the absence instead of inventing a recommendation.
 */
export function RiskProposalLinks({
  risk,
  captureId,
  diagnostic,
}: {
  risk: TaskRisk;
  captureId: string | null;
  diagnostic?: ProposalStageDiagnostic | null;
}) {
  const proposals = risk.proposals;

  if (proposals === undefined) {
    return (
      <div className="risk-proposals">
        <p className="risk-proposals-unknown">La respuesta del backend no informa si este riesgo tiene propuestas vinculadas.</p>
      </div>
    );
  }

  if (proposals.length === 0) {
    return (
      <div className="risk-proposals">
        <p className="risk-proposals-empty">No hay una propuesta vinculada a este riesgo</p>
        {diagnostic && (
          <div className="risk-proposals-diagnostic">
            <Pill tone={proposalOutcomeTone(diagnostic.outcome)}>{proposalOutcomeLabel(diagnostic.outcome)}</Pill>
            <p>{proposalOutcomeExplanation(diagnostic)}</p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="risk-proposals">
      <p className="risk-item-tasks">Propuestas vinculadas ({proposals.length}):</p>
      <ul className="risk-proposal-links">
        {proposals.map((proposal) => {
          const historical = Boolean(captureId && proposal.capture_id && proposal.capture_id !== captureId);
          return (
            <li key={proposal.proposal_id} className="risk-proposal-link">
              <Link to={`/proposals?proposal_id=${encodeURIComponent(proposal.proposal_id)}`}>{proposal.proposal_id}</Link>
              <span className="risk-proposal-field">{fieldLabel(proposal.field)}</span>
              <Pill tone={proposalStatusTone(proposal.review_status)}>{proposalReviewStatusLabel(proposal.review_status)}</Pill>
              <Pill tone={proposalStatusTone(proposal.execution_status)}>{proposalExecutionStatusLabel(proposal.execution_status)}</Pill>
              <Pill tone={proposalStatusTone(proposal.technical_status)}>{proposalTechnicalStatusLabel(proposal.technical_status)}</Pill>
              <span className="risk-proposal-meta">
                Vigencia {proposalFreshnessLabel(proposal.freshness).toLocaleLowerCase("es-EC")} · {proposalAuthorizationStatusLabel(proposal.authorization_status)}
              </span>
              {historical && <Pill tone="unknown">Histórica</Pill>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
