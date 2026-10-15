import { Mail } from "lucide-react";
import { Link } from "react-router-dom";
import { formatPercentage } from "../../api/formatters";
import type { OutboxMetrics } from "../../api/types";
import { Pill } from "../../components/States";
import { SafeMarkdown } from "../../components/SafeMarkdown";
import "./notifications-phase1.css";

export interface ExecutiveEmail {
  subject: string;
  recipient: string | null;
  summary: string;
  metrics: Pick<OutboxMetrics, "progress_percentage" | "overdue_count" | "blocked_count"> | null;
  risks: string[];
  bodyMarkdown?: string;
  reportId?: string | null;
  receipt?: { channel: string; status: string; delivery_channel?: string | null };
}

export function ExecutiveEmailPreview({ email }: { email: ExecutiveEmail }) {
  return (
    <article className="executive-email" aria-label={email.receipt ? "Correo ejecutivo del recibo" : "Vista previa de correo ejecutivo"}>
      <div className="executive-email-heading">
        <span className="delivery-icon"><Mail size={20} aria-hidden="true" /></span>
        <div><p className="eyebrow">{email.receipt ? "CONTENIDO EJECUTIVO DEL MENSAJE ALMACENADO" : "VISTA PREVIA LOCAL · NO ENVIADO"}</p><h3>{email.subject}</h3></div>
        <Pill tone="neutral">{email.receipt ? `${email.receipt.delivery_channel ?? email.receipt.channel} · ${email.receipt.status}` : "No enviado"}</Pill>
      </div>
      <dl className="executive-email-envelope">
        <div><dt>De:</dt><dd>Agente IA de Gestión <span>(identidad visual, no dirección SMTP)</span></dd></div>
        <div><dt>Para:</dt><dd>{email.recipient ?? "Destinatario no informado por el servidor"}</dd></div>
        <div><dt>Asunto:</dt><dd>{email.subject}</dd></div>
      </dl>
      <p className="delivery-boundary">{email.receipt
        ? `Estado original: ${email.receipt.status}; canal del recibo: ${email.receipt.delivery_channel ?? email.receipt.channel}. Este registro no acredita entrega en una bandeja de correo. El destinatario almacenado no se reemplaza por la preferencia local.`
        : "Vista previa generada en este navegador. No se ha enviado un correo ni se ha conectado con un servidor SMTP."}</p>
      <div className="executive-email-body">
        <p>Estimado Gestor del Proyecto:</p>
        <p>{email.summary}</p>
        <div className="executive-email-signals">
          <section><h4>Avance del proyecto</h4><strong>{email.metrics?.progress_percentage == null ? "Sin datos publicados" : formatPercentage(email.metrics.progress_percentage)}</strong></section>
          <section><h4>Riesgos y retrasos</h4><p>Vencidos: {email.metrics?.overdue_count ?? "sin dato"} · Bloqueados: {email.metrics?.blocked_count ?? "sin dato"}</p></section>
        </div>
        {email.risks.length > 0 && <ul>{email.risks.map((risk, index) => <li key={`${index}-${risk}`}>{risk}</li>)}</ul>}
        {email.bodyMarkdown && <SafeMarkdown source={email.bodyMarkdown} className="safe-markdown outbox-markdown" />}
        <section><h4>Acciones recomendadas</h4><p>Revisar retrasos y evidencia disponible antes de autorizar acciones correctivas. Las decisiones sobre Trello y código requieren aprobación independiente.</p></section>
        <div className="executive-email-actions"><Link className="button button-secondary" to="/proposals">Revisar acciones correctivas</Link><Link className="text-link" to={email.reportId ? `/reports?report_id=${encodeURIComponent(email.reportId)}` : "/reports"}>Consultar informe {email.reportId ? "vinculado" : "semanal"}</Link></div>
      </div>
    </article>
  );
}
