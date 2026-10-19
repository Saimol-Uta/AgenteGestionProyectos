import { Link } from "react-router-dom";
import { formatDate } from "../api/formatters";
import type { ProposalDetail, ProposalExecutionReceipt } from "../api/types";
import { Pill } from "./States";

function decisionLabel(decision: string) {
  const labels: Record<string, string> = {
    approve_offline: "Aprobación sin ejecución",
    reject: "Rechazo",
    authorize_dry_run: "Autorización de prueba local",
    deny: "Denegación",
    revoke: "Revocación",
    approve_and_execute: "Aprobación y ejecución",
    modify_and_execute: "Modificación y ejecución",
    unknown: "Decisión desconocida",
  };
  return labels[decision] ?? "Decisión desconocida";
}

function executionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    not_executed: "Sin ejecución registrada",
    EXECUTED: "Ejecución registrada",
    ALREADY_APPLIED: "El backend informa que ya estaba aplicado",
    DENIED: "Denegada por el backend",
    CONFLICT: "Conflicto informado por el backend",
    FAILED: "Fallida según el backend",
    unknown: "Estado de ejecución desconocido",
  };
  return labels[status] ?? "Estado de ejecución desconocido";
}

function modeLabel(mode: ProposalExecutionReceipt["execution_mode"]) {
  if (mode === "sandbox") return "Sandbox; no acredita una escritura live";
  if (mode === "live") return "Live, según el backend";
  return "Modo desconocido; no se infiere un resultado externo";
}

function verificationLabel(status: string | null | undefined) {
  if (status === "verified") return "Verificación confirmada por el backend";
  if (status === "verification_failed") return "Verificación fallida";
  if (status === "verification_unsupported") return "Verificación no disponible para esta acción";
  return "Verificación desconocida";
}

function receiptExplanation(receipt: ProposalExecutionReceipt) {
  if (receipt.status === "FAILED") return "La acción no se completó según el resultado registrado.";
  if (receipt.status === "DENIED") return "El backend denegó la acción; no se presenta como ejecución exitosa.";
  if (receipt.status === "CONFLICT") return "El backend registró un conflicto; no se presenta como ejecución exitosa.";
  if (!["EXECUTED", "ALREADY_APPLIED"].includes(receipt.status)
    || (receipt.execution_mode !== "live" && receipt.execution_mode !== "sandbox")
    || receipt.verification_status !== "verified") {
    return "El resultado no está confirmado. Modo o verificación desconocidos no se interpretan como éxito.";
  }
  if (receipt.execution_mode === "sandbox") return "Resultado local confirmado; no acredita cambios en Trello live.";
  if (receipt.status === "ALREADY_APPLIED") return "El backend informa un resultado live ya aplicado y verificado.";
  return "El backend informa un resultado live verificado.";
}

export function ProposalDecisionTrail({
  proposal,
  proposalHref,
}: {
  proposal: ProposalDetail;
  proposalHref: (proposalId: string) => { pathname: string; search: string };
}) {
  const receipt = proposal.latest_receipt ?? null;
  const historyKnown = proposal.decision_history_availability === "available" && Array.isArray(proposal.decision_history);
  const relationshipKnown = proposal.relationship_availability === "available"
    && proposal.original_proposal_id !== undefined && Array.isArray(proposal.derived_proposal_ids);
  const receiptKnown = receipt !== null || (proposal.receipt_availability === "available" && proposal.latest_receipt !== undefined);
  const relatedProposalIds = new Set<string>();
  if (proposal.original_proposal_id && proposal.original_proposal_id !== proposal.proposal_id) relatedProposalIds.add(proposal.original_proposal_id);
  for (const id of proposal.derived_proposal_ids ?? []) if (id !== proposal.proposal_id) relatedProposalIds.add(id);
  if (receipt?.proposal_id && receipt.proposal_id !== proposal.proposal_id) relatedProposalIds.add(receipt.proposal_id);
  if (receipt?.original_proposal_id && receipt.original_proposal_id !== proposal.proposal_id) relatedProposalIds.add(receipt.original_proposal_id);

  return (
    <section className="proposal-decision-trail" aria-labelledby="proposal-decision-trail-title">
      <div className="proposal-trail-heading">
        <div><p className="eyebrow">RESULTADO PERSISTIDO</p><h3 id="proposal-decision-trail-title">Resultado de ejecución e historial</h3></div>
      </div>
      {!receiptKnown && (
        <div className="proposal-receipt-state unknown-note" role="status">
          <strong>{executionStatusLabel(proposal.execution_status)}</strong>
          <p>El recibo no está disponible o su historial es desconocido. No se infiere una ejecución exitosa ni su modo.</p>
        </div>
      )}
      {receiptKnown && !receipt && (
        <div className="proposal-receipt-state">
          <strong>No hay un recibo de ejecución conocido.</strong>
          <p>El backend no reportó un resultado durable para esta propuesta.</p>
        </div>
      )}
      {receipt && (
        <div className="proposal-receipt-state">
          <div className="proposal-receipt-title">
            <Pill tone={receipt.status === "FAILED" || receipt.status === "DENIED" || receipt.status === "CONFLICT" ? "danger" : !["EXECUTED", "ALREADY_APPLIED"].includes(receipt.status) || (receipt.execution_mode !== "live" && receipt.execution_mode !== "sandbox") || receipt.verification_status !== "verified" ? "unknown" : "neutral"}>
              {executionStatusLabel(receipt.status)}
            </Pill>
            <span>{modeLabel(receipt.execution_mode)}</span>
          </div>
          <p>{receiptExplanation(receipt)}</p>
          <p>{verificationLabel(receipt.verification_status)} · {formatDate(receipt.recorded_at, true)}</p>
        </div>
      )}

      <details className="proposal-history-details">
        <summary>Ver decisiones, relaciones y datos de auditoría disponibles</summary>
        <section className="proposal-history-section">
          <h4>Historial de decisiones humanas</h4>
          {!historyKnown && <p className="unknown-note">Disponibilidad del historial desconocida. No se supone que no haya decisiones previas.</p>}
          {historyKnown && proposal.decision_history!.length === 0 && <p>No hay decisiones humanas registradas en el historial disponible.</p>}
          {historyKnown && proposal.decision_history!.length > 0 && (
            <ol className="proposal-history-list">
              {proposal.decision_history!.map((event) => (
                <li key={`${event.event_id}:${event.proposal_id}`}>
                  <strong>{decisionLabel(event.decision)}</strong>
                  <span>{formatDate(event.recorded_at, true)}</span>
                  <p>{event.reason ?? "No hay motivo disponible."}</p>
                  <p>Actor: {event.actor_id ?? "Desconocido"} · {event.actor_verification === "verified_local_session" ? "sesión verificada" : event.actor_verification === "asserted_offline" ? "registro offline" : "verificación desconocida"}</p>
                  {(event.execution_mode || event.execution_status || event.verification_status) && (
                    <p>Resultado: {event.execution_status ? executionStatusLabel(event.execution_status) : "Estado desconocido"} · {modeLabel(event.execution_mode)} · {verificationLabel(event.verification_status)}</p>
                  )}
                  {event.audit_event_id && (
                    <p><Link to={`/audit?event_id=${encodeURIComponent(event.audit_event_id)}`}>Ver evento en Auditoría</Link></p>
                  )}
                  <details className="proposal-raw-audit-details">
                    <summary>Identificadores y referencias técnicas</summary>
                    <dl><div><dt>Evento</dt><dd><code>{event.event_id}</code></dd></div><div><dt>Propuesta</dt><dd><code>{event.proposal_id}</code></dd></div>
                      {event.related_proposal_id && <div><dt>Propuesta relacionada</dt><dd><code>{event.related_proposal_id}</code></dd></div>}
                      {event.audit_event_id && <div><dt>Evento de auditoría</dt><dd><code>{event.audit_event_id}</code></dd></div>}
                      {event.reasons.length > 0 && <div><dt>Códigos informados</dt><dd>{event.reasons.map((reason) => <code key={reason}>{reason}</code>)}</dd></div>}
                    </dl>
                  </details>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="proposal-history-section">
          <h4>Relación entre propuestas</h4>
          {!relationshipKnown && <p className="unknown-note">La relación entre propuesta original y derivadas es desconocida; no se infieren vínculos.</p>}
          {relationshipKnown && relatedProposalIds.size === 0 && <p>No hay una propuesta original o derivada conocida.</p>}
          {relationshipKnown && relatedProposalIds.size > 0 && (
            <ul className="proposal-related-links">
              {proposal.original_proposal_id && proposal.original_proposal_id !== proposal.proposal_id && (
                <li><Link to={proposalHref(proposal.original_proposal_id)}>Volver a la propuesta original</Link><code>{proposal.original_proposal_id}</code></li>
              )}
              {(proposal.derived_proposal_ids ?? []).filter((id) => id !== proposal.proposal_id).map((id, index) => (
                <li key={id}><Link to={proposalHref(id)}>Abrir propuesta derivada {index + 1}</Link><code>{id}</code></li>
              ))}
              {receipt?.proposal_id && receipt.proposal_id !== proposal.proposal_id && !(proposal.derived_proposal_ids ?? []).includes(receipt.proposal_id)
                && <li><Link to={proposalHref(receipt.proposal_id)}>Abrir propuesta del recibo</Link><code>{receipt.proposal_id}</code></li>}
            </ul>
          )}
          {relationshipKnown && proposal.original_proposal_id && <p>Propuesta original: <code>{proposal.original_proposal_id}</code></p>}
        </section>

        <section className="proposal-history-section">
          <h4>Recibo durable</h4>
          {!receiptKnown && <p className="unknown-note">Disponibilidad del recibo desconocida; la ausencia de datos no prueba éxito ni ausencia de una ejecución previa.</p>}
          {receiptKnown && !receipt && <p>No hay un recibo conocido.</p>}
          {receipt && <dl><div><dt>Identificador de ejecución</dt><dd><code>{receipt.execution_id}</code></dd></div>
            <div><dt>Propuesta del recibo</dt><dd><code>{receipt.proposal_id}</code></dd></div>
            {receipt.original_proposal_id && <div><dt>Propuesta original</dt><dd><code>{receipt.original_proposal_id}</code></dd></div>}
            <div><dt>Decisión</dt><dd>{decisionLabel(receipt.decision)}</dd></div>
            <div><dt>Modo</dt><dd>{modeLabel(receipt.execution_mode)}</dd></div>
            <div><dt>Verificación</dt><dd>{verificationLabel(receipt.verification_status)}</dd></div>
            <div><dt>Actor</dt><dd>{receipt.actor_id ?? "Desconocido"} · {receipt.actor_verification === "verified_local_session" ? "sesión verificada" : "verificación desconocida"}</dd></div>
            {receipt.reason && <div><dt>Motivo</dt><dd>{receipt.reason}</dd></div>}
            {receipt.audit_event_id && <div><dt>Evento de auditoría</dt><dd><code>{receipt.audit_event_id}</code></dd></div>}
            {receipt.reasons.length > 0 && <div><dt>Códigos informados</dt><dd>{receipt.reasons.map((reason) => <code key={reason}>{reason}</code>)}</dd></div>}
          </dl>}
        </section>
        <div className="proposal-detail-raw"><span>Propuesta actual</span><code>{proposal.proposal_id}</code><span>Hash SHA-256</span><code>{proposal.proposal_body_sha256}</code></div>
      </details>
    </section>
  );
}
