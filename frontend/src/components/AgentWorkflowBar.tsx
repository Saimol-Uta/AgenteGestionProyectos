import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Mail, RefreshCw, ShieldCheck, UserCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { queryKeys, useAnalysisJobs } from "../api/queries";
import type { AnalysisPreparation } from "../api/types";
import {
  analysisExecutionModeLabel,
  analysisJobStatusLabel,
  analysisPreparationIsReady,
  providerAttemptLabel,
  stageNameLabel,
} from "../api/formatters";
import { useWorkspace } from "../app/WorkspaceContext";
import { useSession } from "../features/auth/SessionProvider";
import { EmailDeliveryPanel } from "../features/notifications/EmailDeliveryPanel";
import { useDeliveryPreferences } from "../features/notifications/useDeliveryPreferences";
import { useToast } from "./ToastProvider";

export function AgentWorkflowBar() {
  const { selectedProjectId } = useWorkspace();
  const { session } = useSession();
  return <Workflow key={`${selectedProjectId}:${session?.actor}:${session?.authenticated}`} projectId={selectedProjectId} />;
}

function Workflow({ projectId }: { projectId: string | null }) {
  const { session } = useSession();
  const { recipient: savedRecipient } = useDeliveryPreferences(projectId);
  const recipient = savedRecipient || (session?.authenticated
    ? session.email?.trim() || session.notification_email?.trim() || ""
    : "");
  const { notify } = useToast();
  const client = useQueryClient();
  const canPrepare = Boolean(projectId && session?.authenticated && session.csrf_token && session.capabilities.includes("analysis:prepare"));
  const canConfirm = Boolean(canPrepare && session?.capabilities.includes("analysis:confirm"));
  const jobs = useAnalysisJobs(canPrepare ? projectId : null);
  const active = jobs.data?.items.find((job) => job.status === "queued" || job.status === "running");
  const [preparation, setPreparation] = useState<AnalysisPreparation | null>(null);
  const [remoteConsent, setRemoteConsent] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [emailPanelOpen, setEmailPanelOpen] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(true);
  const key = useRef<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const trackedId = jobId ?? active?.job_id ?? null;
  const tracked = useQuery({
    queryKey: queryKeys.analysisJob(trackedId ?? ""),
    queryFn: () => api.getAnalysisJob(trackedId!),
    enabled: Boolean(canPrepare && trackedId),
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.status ?? "") ? 3000 : false,
  });
  const running = Boolean(tracked.data && ["queued", "running"].includes(tracked.data.status)) || Boolean(active && !tracked.data);
  const refreshedJob = useRef<string | null>(null);
  useEffect(() => {
    const job = tracked.data;
    if (!job || !projectId || ["queued", "running"].includes(job.status) || refreshedJob.current === job.job_id) return;
    refreshedJob.current = job.job_id;
    for (const name of ["summary", "executive-summary", "quality", "proposals", "reports", "history", "runs", "analysis-jobs"]) {
      void client.invalidateQueries({ queryKey: [name, projectId] });
    }
  }, [client, projectId, tracked.data]);


  async function confirm(prepared: AnalysisPreparation, consent: boolean) {
    if (!mounted.current || !canConfirm || !projectId || !session?.csrf_token) throw new Error("Tu sesión no permite confirmar este análisis.");
    if (!analysisPreparationIsReady(prepared) || (prepared.remote.required && (!prepared.remote.server_allowed || !consent))) throw new Error("El análisis todavía no está listo o falta autorizar los servicios en la nube.");
    // Expiry and fingerprint are rechecked authoritatively by the server.
    key.current ??= crypto.randomUUID();
    const result = await api.confirmAnalysis(projectId, {
      preparation_id: prepared.preparation_id,
      configuration_fingerprint: prepared.configuration_fingerprint,
      confirm_analysis: true,
      remote_consent: prepared.remote.required ? consent : false,
    }, session.csrf_token, key.current);
    if (!mounted.current) return;
    setJobId(result.job.job_id);
    setPreparation(null);
    setRemoteConsent(false);
    key.current = null;
    setMessage(result.reused ? "Se retomó un análisis existente." : "Análisis confirmado. El Ejecutor está preparando las evidencias.");
    void client.invalidateQueries({ queryKey: ["analysis-jobs", projectId] });
    void client.invalidateQueries({ queryKey: ["runs", projectId] });
    notify("Análisis confirmado; las decisiones siguen requiriendo tu autorización.", "success");
  }

  async function execute(prepareFirst: boolean) {
    if (lock.current || !canPrepare) return;
    lock.current = true; setBusy(true); setError(null);
    try {
      if (prepareFirst) {
        setMessage("Verificando configuración y permisos…");
        const prepared = await api.prepareAnalysis(projectId!, session!.csrf_token!);
        if (!mounted.current) return;
        key.current = null; setPreparation(prepared); setRemoteConsent(false);
        if (!analysisPreparationIsReady(prepared)) setMessage("El análisis aún requiere ajustes de configuración o permisos.");
        else if (!canConfirm) setMessage("Tu sesión permite revisar el análisis, pero no confirmarlo.");
        else if (!prepared.remote.required) await confirm(prepared, false);
        else setMessage("Revisa los servicios en la nube y autoriza su uso antes de confirmar.");
      } else if (preparation) await confirm(preparation, remoteConsent);
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : "No se pudo iniciar el análisis.");
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }

  return (
    <section className="agent-workflow" aria-label="Flujo de agentes">
      <div className="workflow-heading"><span className="eyebrow">ASISTENTE DE SEGUIMIENTO Y CONTROL</span><Link to="/analysis">Ver actividad y evidencias</Link></div>
      <ol className="workflow-steps">
        <li><span className="workflow-step-icon"><Bot size={18} aria-hidden="true" /></span><div><strong><span className="workflow-number">1.</span> <span>Agente Ejecutor</span></strong><small>Monitorea Trello, analiza GitHub y propone.</small></div></li>
        <li><span className="workflow-step-icon"><ShieldCheck size={18} aria-hidden="true" /></span><div><strong><span className="workflow-number">2.</span> <span>Agente Revisor</span></strong><small>Revisa automáticamente la propuesta; no autoriza su ejecución.</small></div></li>
        <li><span className="workflow-step-icon"><UserCheck size={18} aria-hidden="true" /></span><div><strong><span className="workflow-number">3.</span> <span>Agente Aprobador</span></strong><small>Tú autorizas; la plataforma indica si una acción se ejecuta en la nube, localmente o en modo mixto.</small></div></li>
      </ol>
      <div className="workflow-footer"><div className="workflow-email"><Mail size={16} aria-hidden="true" /><span>Alertas y reportes dirigidos a: <strong>{recipient || "sin destinatario disponible"}</strong><small>El envío requiere confirmar el destinatario y muestra el canal real de entrega.</small></span><Link to="/settings">Cambiar correo</Link></div>
        <button type="button" className="button button-primary" disabled={!canPrepare || busy || running} onClick={() => void execute(true)}><RefreshCw size={15} aria-hidden="true" />{busy ? "Preparando análisis…" : running ? "Análisis en curso…" : "Sincronizar y Analizar Proyecto"}</button>
      </div>
      {projectId && <details className="workflow-email-delivery" onToggle={(event) => setEmailPanelOpen(event.currentTarget.open)}><summary>Enviar alerta / reporte al gestor</summary>{emailPanelOpen && <EmailDeliveryPanel projectId={projectId} />}</details>}
      {!canPrepare && <p className="workflow-status">Para iniciar un análisis necesitas una sesión con permiso de gestión.</p>}
      {message && <p className="workflow-status" role="status">{message}</p>}
      {tracked.data && <p className="workflow-status">Estado del análisis: {analysisJobStatusLabel(tracked.data.status)}.{tracked.data.execution_mode && <> Modo de ejecución: {analysisExecutionModeLabel(tracked.data.execution_mode)}.</>}{tracked.data.fallback_reasons?.length ? <> Alternativas aplicadas: {tracked.data.fallback_reasons.join("; ")}.</> : null} {tracked.data.run_id && <Link to={`/analysis?run_id=${encodeURIComponent(tracked.data.run_id)}`}>Consultar resultados y validación</Link>}</p>}
      {tracked.isError && <p className="workflow-error" role="alert">No se pudo consultar el estado: {tracked.error.message}</p>}
      {error && <p className="workflow-error" role="alert">{error}</p>}
      {preparation && <div className="workflow-confirmation">
        {preparation.execution_mode && <p role="status">Modo de ejecución: {analysisExecutionModeLabel(preparation.execution_mode)}.</p>}
        {preparation.fallback_reasons?.length ? <div><strong>Alternativas aplicadas</strong><ul>{preparation.fallback_reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul></div> : null}
        {preparation.remote.required && <><p>El análisis consultará servicios en la nube{preparation.remote.providers.length ? ` (${preparation.remote.providers.filter((provider) => provider.transport === "remote").map((provider) => `${providerAttemptLabel(provider.provider, provider.model)} · ${stageNameLabel(provider.stage)}`).join(", ")})` : ""}. Parte de la información del proyecto podría procesarse fuera de este equipo.</p><label><input type="checkbox" checked={remoteConsent} onChange={(event) => setRemoteConsent(event.target.checked)} disabled={busy || !preparation.remote.server_allowed} /> Autorizo el uso de servicios en la nube</label>{!preparation.remote.server_allowed && <p>El servidor no permite el uso de servicios en la nube.</p>}</>}
        <div className="workflow-confirm-actions"><button type="button" className="button button-primary" disabled={busy || !canConfirm || !analysisPreparationIsReady(preparation) || (preparation.remote.required && (!remoteConsent || !preparation.remote.server_allowed))} onClick={() => void execute(false)}>Confirmar análisis</button><button type="button" className="button button-secondary" disabled={busy} onClick={() => { setPreparation(null); setRemoteConsent(false); setMessage(""); key.current = null; }}>Cancelar preparación</button></div>
      </div>}
    </section>
  );
}
