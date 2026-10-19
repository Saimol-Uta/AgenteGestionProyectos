import { CircleHelp } from "lucide-react";
import { proposalOutcomeExplanation, proposalOutcomeLabel, proposalOutcomeTone } from "../../api/formatters";
import type { ProposalStageDiagnostic } from "../../api/types";
import { Pill } from "../../components/States";
import "./proposal-diagnostic.css";

export function ProposalStageDiagnosticCard({ diagnostic }: { diagnostic: ProposalStageDiagnostic }) {
  const technicalCodes = [...new Set([...diagnostic.limitation_codes, ...diagnostic.failure_codes])];

  return (
    <div className="proposal-diagnostic">
      <div className="proposal-diagnostic-head">
        <Pill tone={proposalOutcomeTone(diagnostic.outcome)}>{proposalOutcomeLabel(diagnostic.outcome)}</Pill>
      </div>
      <p className="proposal-diagnostic-explanation">{proposalOutcomeExplanation(diagnostic)}</p>
      <dl className="proposal-diagnostic-metrics">
        <div><dt>Candidatos ofrecidos</dt><dd>{diagnostic.offered ?? "Sin dato"}</dd></div>
        <div><dt>Seleccionados</dt><dd>{diagnostic.selected ?? "Sin dato"}</dd></div>
        <div><dt>Guardados</dt><dd>{diagnostic.saved ?? "Sin dato"}</dd></div>
        <div><dt>Revisión técnica lista</dt><dd>{diagnostic.technical_ready ?? "Sin dato"}</dd></div>
        <div><dt>Revisión técnica bloqueada</dt><dd>{diagnostic.technical_blocked ?? "Sin dato"}</dd></div>
        <div><dt>Descartados</dt><dd>{diagnostic.discarded}</dd></div>
      </dl>
      {technicalCodes.length > 0 && (
        <details className="proposal-diagnostic-codes">
          <summary>Códigos técnicos de auditoría ({technicalCodes.length})</summary>
          <ul>
            {technicalCodes.map((code) => <li key={code}><code>{code}</code></li>)}
          </ul>
        </details>
      )}
      <p className="proposal-diagnostic-note">
        <CircleHelp aria-hidden="true" size={13} />
        Los códigos técnicos se conservan para auditoría; el motivo explicado arriba es el que los datos permiten demostrar.
      </p>
    </div>
  );
}
