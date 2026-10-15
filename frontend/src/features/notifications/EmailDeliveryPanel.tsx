import { useId, useRef, useState, type FormEvent } from "react";
import { Mail, Save } from "lucide-react";
import { api } from "../../api/client";
import { useProjects, useSendManagerEmailMutation } from "../../api/queries";
import type { SendManagerEmailResponse } from "../../api/types";
import { useSession } from "../auth/SessionProvider";
import { useToast } from "../../components/ToastProvider";
import { Pill } from "../../components/States";
import { ExecutiveEmailPreview, type ExecutiveEmail } from "./ExecutiveEmailPreview";
import { isDeliveryRecipient, useDeliveryPreferences } from "./useDeliveryPreferences";
import "./notifications-phase1.css";

export function EmailDeliveryPanel({ projectId }: { projectId: string }) {
  return <ProjectEmailDeliveryPanel key={projectId} projectId={projectId} />;
}

function RecipientEditor({ recipient, onSave }: { recipient: string; onSave: (value: string) => void }) {
  const [draft, setDraft] = useState(recipient);
  const [invalid, setInvalid] = useState(false);
  const inputId = useId();
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draft.trim();
    if (!isDeliveryRecipient(value)) { setInvalid(true); return; }
    setInvalid(false);
    onSave(value);
  }
  return <form className="delivery-recipient-form" onSubmit={submit} noValidate>
    <label htmlFor={inputId}>Correo del gestor<span>Preferencia local del proyecto</span></label>
    <div className="delivery-recipient-controls"><input id={inputId} type="email" autoComplete="email" maxLength={254} placeholder="Ejemplo: gestor@example.org" value={draft} aria-invalid={invalid} aria-describedby={`${inputId}-help`} onChange={(event) => { setDraft(event.target.value); setInvalid(false); }} /><button className="button button-secondary" type="submit"><Save size={15} aria-hidden="true" />Guardar correo local</button></div>
    <p id={`${inputId}-help`} className={invalid ? "delivery-validation" : "caption"}>{invalid ? "Ingresa una dirección de correo válida, sin espacios; puedes dejarla vacía para borrar la preferencia." : "Se guarda solo en este navegador y proyecto. El envío protegido confirma el destinatario antes de contactar el backend."}</p>
  </form>;
}

function ProjectEmailDeliveryPanel({ projectId }: { projectId: string }) {
  const titleId = useId();
  const { recipient, setRecipient } = useDeliveryPreferences(projectId);
  const projects = useProjects();
  const project = projects.data?.items.find(item => item.id === projectId);
  const { session } = useSession();
  const { notify } = useToast();
  const mutation = useSendManagerEmailMutation();
  const [saved, setSaved] = useState(false);
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const [localPreview, setLocalPreview] = useState<ExecutiveEmail | null>(null);
  const [delivery, setDelivery] = useState<SendManagerEmailResponse | null>(null);
  const [htmlPreview, setHtmlPreview] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const idempotencyKey = useRef<string | null>(null);
  const inFlight = useRef(false);
  const canSend = Boolean(session?.authenticated && session.csrf_token && session.capabilities.includes("notifications:email_send"));

  function previewEmail() {
    if (!recipient) return;
    const summary = project?.summary;
    setLocalPreview({
      subject: `Seguimiento ejecutivo semanal · ${project?.name ?? projectId}`,
      recipient,
      summary: summary ? `Resumen basado en la última captura publicada: ${summary.completed_tasks} de ${summary.total_tasks} tareas completadas. Esta vista no genera un informe nuevo.` : "No hay una captura publicada disponible. Revisar el informe y las fuentes del proyecto antes de tomar decisiones.",
      metrics: summary ?? null,
      risks: summary?.identified_risks ?? [],
    });
    setDelivery(null);
    setHtmlPreview(null);
  }

  function requestSend() {
    if (!recipient || !isDeliveryRecipient(recipient)) return;
    if (!canSend) {
      previewEmail();
      return;
    }
    setLocalPreview(null);
    setPreviewError("");
    setConfirmationOpen(true);
  }

  async function sendEmail() {
    if (!canSend || !session?.csrf_token || !recipient || !isDeliveryRecipient(recipient) || inFlight.current) return;
    inFlight.current = true;
    setPreviewError("");
    setPreviewLoading(false);
    idempotencyKey.current ??= crypto.randomUUID();
    try {
      const result = await mutation.mutateAsync({
        projectId,
        payload: { recipient_email: recipient },
        csrfToken: session.csrf_token,
        idempotencyKey: idempotencyKey.current,
      });
      idempotencyKey.current = null;
      setDelivery(result);
      setConfirmationOpen(false);
      setLocalPreview(null);
      if (result.status === "FAILED" || result.delivery_channel === "smtp_email_failed") {
        const failure = "SMTP no confirmó el envío. El recibo fallido quedó en Outbox; no se reenvía automáticamente.";
        setPreviewError(failure);
        notify(failure, "error");
        return;
      }
      const message = result.delivery_channel === "smtp_email_sent"
        ? `SMTP reportó ${result.status} para ${result.recipient_email}.`
        : `Correo registrado en outbox local (${result.status}); no se entregó a una bandeja SMTP.`;
      notify(message, result.delivery_channel === "smtp_email_sent" ? "success" : "info");
      if (result.html_preview_url) {
        setPreviewLoading(true);
        try {
          setHtmlPreview(await api.getManagerEmailPreview(projectId, result.html_preview_url));
        } catch (error) {
          const problem = error instanceof Error ? error.message : "La vista previa protegida no pudo cargarse.";
          setPreviewError(problem);
          notify(problem, "error");
        } finally {
          setPreviewLoading(false);
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "El envío del correo no pudo completarse.";
      setPreviewError(message);
      notify(message, "error");
    } finally {
      inFlight.current = false;
    }
  }

  const busy = mutation.isPending || inFlight.current;
  return <section className="panel email-delivery-panel" aria-labelledby={titleId}>
    <div className="section-heading"><div><p className="eyebrow">ALERTAS DE RETRASOS · RIESGOS · REPORTES SEMANALES</p><h2 id={titleId}>Configuración de Entrega por Correo Electrónico al Gestor</h2></div><Mail size={21} aria-hidden="true" /></div>
    <div className="delivery-status-row"><Pill tone={canSend ? "positive" : "warning"}>{canSend ? "Envío protegido disponible" : "Vista previa local · envío deshabilitado"}</Pill><span>{canSend ? "El servidor determina si se envía por SMTP o se registra en su outbox local." : session?.authenticated ? "El servidor no concedió notifications:email_send; la vista previa local no entrega correo." : "Para enviar se requiere sesión local y notifications:email_send; la vista previa local no entrega correo."}</span></div>
    <RecipientEditor key={recipient} recipient={recipient} onSave={value => { setRecipient(value); setSaved(true); setLocalPreview(null); setDelivery(null); setHtmlPreview(null); setPreviewError(""); idempotencyKey.current = null; }} />
    {saved && <p role="status" className="delivery-saved">Preferencia local guardada</p>}
    <div className="delivery-preview-actions"><button className="button button-primary" type="button" disabled={!recipient || !isDeliveryRecipient(recipient) || busy} onClick={requestSend}>{canSend ? "Enviar Alerta/Reporte al Correo Ahora" : "Enviar Notificación por Correo al Gestor"}</button><span className="caption">{canSend ? "Requiere confirmar el destinatario antes de enviar." : "No se hace ninguna solicitud de entrega."}</span></div>
    {confirmationOpen && <section className="delivery-confirmation" aria-label="Confirmar destinatario del correo"><p>Confirma el envío a <strong>{recipient}</strong></p><p>El backend devolverá el canal y estado reales; local_outbox_simulated_email no equivale a entrega SMTP.</p><div className="delivery-preview-actions"><button className="button button-primary" type="button" disabled={busy} onClick={() => void sendEmail()}>{busy ? "Enviando correo…" : `Confirmar envío a ${recipient}`}</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => setConfirmationOpen(false)}>Cancelar</button></div></section>}
    {previewError && <p role="alert" className="delivery-validation">{previewError}</p>}
    {delivery && <section className="delivery-result" aria-live="polite"><div className="delivery-status-row"><Pill tone={delivery.delivery_channel === "smtp_email_sent" ? "positive" : "warning"}>{delivery.delivery_channel}</Pill><strong>{delivery.status}</strong><span>Para: {delivery.recipient_email}</span></div><p>Recibo de entrega: <code>{delivery.delivery_id}</code></p>{delivery.delivery_channel === "smtp_email_failed" ? <p>El envío SMTP falló o es incierto; no se confirmó entrega.</p> : delivery.delivery_channel === "smtp_email_sent" ? <p>El relay SMTP aceptó el mensaje con estado <strong>{delivery.status}</strong>; la llegada a la bandeja no está confirmada.</p> : <p>Registrado en outbox local; no se entregó a una bandeja SMTP.</p>}</section>}
    {previewLoading && <p role="status">Cargando vista previa HTML protegida del backend…</p>}
    {htmlPreview && <section className="backend-email-preview" aria-label="Vista previa HTML del backend"><h3>Vista previa HTML del backend</h3><iframe title="Vista previa HTML del correo" sandbox="" referrerPolicy="no-referrer" srcDoc={htmlPreview} style={{ width: "100%", minHeight: "360px", border: "1px solid var(--border-strong)", borderRadius: "8px", background: "white" }} /></section>}
    {localPreview && <><p className="caption">Vista previa local · no enviada</p><ExecutiveEmailPreview email={localPreview} /></>}
  </section>;
}
