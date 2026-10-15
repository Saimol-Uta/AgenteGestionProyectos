import { ArrowRight, CalendarDays, GitBranch, ListChecks } from "lucide-react";
import { Link } from "react-router-dom";
import { useExecutiveSummary } from "../../api/queries";
import { useDecisionSummary } from "./useDecisionSummary";
import { useDeliveryPreferences } from "../notifications/useDeliveryPreferences";

export function DecisionCenter({ projectId }: { projectId: string }) {
  const counts = useDecisionSummary(projectId);
  const executive = useExecutiveSummary(projectId);
  const { recipient } = useDeliveryPreferences(projectId);
  const report = executive.data?.latest_report;
  return (
    <section className="decision-center" aria-labelledby="decision-center-heading">
      <div className="section-heading">
        <div><p className="eyebrow">TU CRITERIO, LA ÚLTIMA PALABRA</p><h2 id="decision-center-heading">Centro de Decisiones del Gestor (Requieren tu Autorización)</h2></div>
      </div>
      <div className="manager-decision-grid">
        <article className="manager-decision-card decision-trello">
          <span className="decision-symbol"><ListChecks size={20} aria-hidden="true" /></span>
          <h3>Acciones Correctivas en Trello</h3>
          <strong className="decision-count">{counts.proposals ?? "—"}</strong>
          <p>{counts.proposals === null ? "Disponibilidad no confirmada." : "Propuestas informadas por el backend. Revisa la validación técnica automática antes de decidir."}</p>
          <Link className="button button-secondary" to="/proposals">Revisar propuestas <ArrowRight size={15} aria-hidden="true" /></Link>
        </article>
        <article className="manager-decision-card decision-quality">
          <span className="decision-symbol"><GitBranch size={20} aria-hidden="true" /></span>
          <h3>Mejoras de Calidad de Código</h3>
          <strong className="decision-count">{counts.findings ?? "—"}</strong>
          <p>{counts.confirmedFindings === null ? "Sin conteos verificados disponibles." : `${counts.confirmedFindings} confirmados por el Revisor. Revisa el código sugerido por el backend antes de decidir.`}</p>
          <Link className="button button-secondary" to="/quality">Revisar mejoras <ArrowRight size={15} aria-hidden="true" /></Link>
        </article>
        <article className="manager-decision-card decision-report">
          <span className="decision-symbol"><CalendarDays size={20} aria-hidden="true" /></span>
          <h3>Informe Semanal y Notificaciones</h3>
          <strong className="decision-report-state">{executive.isError ? "No disponible" : report ? "Informe disponible" : executive.isPending ? "Consultando…" : "Sin informe reportado"}</strong>
          <p>{report ? `Decisión: ${report.human_decision === "pending" ? "pendiente de revisión" : report.human_decision}. ` : ""}Correo: {recipient || "sin destinatario configurado"}. Envío real no habilitado; vista previa local.</p>
          <div className="decision-links"><Link to="/reports">Ver informes <ArrowRight size={14} aria-hidden="true" /></Link><Link to="/calendar">Calendario y bandeja</Link></div>
        </article>
      </div>
    </section>
  );
}
