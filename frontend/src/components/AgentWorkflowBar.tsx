import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, RefreshCw, X } from "lucide-react";
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
import { useToast } from "./ToastProvider";

type ActionKind = "sync" | "email";
type RequestStage = "preparing" | "confirming" | null;

export function AgentWorkflowBar() {
  const { selectedProjectId } = useWorkspace();
  const { session } = useSession();
  const sessionScope = JSON.stringify({
    authenticated: session?.authenticated,
    actor: session?.actor,
    email: session?.email,
    notificationEmail: session?.notification_email,
    role: session?.role,
    capabilities: [...(session?.capabilities ?? [])].sort(),
  });
  return <Workflow key={`${selectedProjectId ?? "unselected"}:${sessionScope}`} projectId={selectedProjectId} />;
}

function HeaderActionDialog({
  kind,
  label,
  title,
  description,
  icon,
  open,
  onOpenChange,
  children,
}: {
  kind: ActionKind;
  label: string;
  title: string;
  description: string;
  icon: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className={`icon-button header-action-button header-action-${kind}`}
          aria-label={label}
          title={label}
        >
          {icon}
          <span className="header-action-label">{kind === "sync" ? "Sincronizar" : "Correo"}</span>
        </button>
      </Dialog.Trigger>
      {open && <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay header-action-overlay" />
        <Dialog.Content className="dialog-content header-action-dialog">
          <div className="dialog-heading">
            <span className="dialog-icon" aria-hidden="true">{icon}</span>
            <div className="header-action-dialog-copy">
              <Dialog.Title className="dialog-title">{title}</Dialog.Title>
              <Dialog.Description className="dialog-description">{description}</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="icon-button dialog-close" aria-label={`Cerrar ${title}`}>
                <X aria-hidden="true" size={18} />
              </button>
            </Dialog.Close>
          </div>
          <div className="header-action-dialog-body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>}
    </Dialog.Root>
  );
}

function Workflow({ projectId }: { projectId: string | null }) {
  const { session } = useSession();
  const { notify } = useToast();
  const client = useQueryClient();
  const canPrepare = Boolean(projectId && session?.authenticated && session.csrf_token && session.capabilities.includes("analysis:prepare"));
  const canConfirm = Boolean(canPrepare && session?.capabilities.includes("analysis:confirm"));
  const jobs = useAnalysisJobs(canPrepare ? projectId : null);
  const active = jobs.data?.items.find((job) => job.status === "queued" || job.status === "running");
  const [preparation, setPreparation] = useState<AnalysisPreparation | null>(null);
  const [remoteConsent, setRemoteConsent] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busyStage, setBusyStage] = useState<RequestStage>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [openAction, setOpenAction] = useState<ActionKind | null>(null);
  const [emailBusy, setEmailBusy] = useState(false);
  const lock = useRef(false);
  const requestStage = useRef<RequestStage>(null);
  const operationGeneration = useRef(0);
  const mounted = useRef(true);
  const currentDialog = useRef<ActionKind | null>(null);
  const confirmationKey = useRef<string | null>(null);
  const emailIdempotencyKey = useRef<string | null>(null);
  const refreshedJob = useRef<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const trackedId = jobId ?? active?.job_id ?? null;
  const tracked = useQuery({
    queryKey: queryKeys.analysisJob(trackedId ?? ""),
    queryFn: () => api.getAnalysisJob(trackedId!),
    enabled: Boolean(canPrepare && trackedId),
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.status ?? "") ? 3000 : false,
  });
  const running = Boolean(tracked.data && ["queued", "running"].includes(tracked.data.status))
    || Boolean(active && !tracked.data)
    || Boolean(jobId && !tracked.data);
  useEffect(() => {
    const job = tracked.data;
    if (!job || !projectId || ["queued", "running"].includes(job.status) || refreshedJob.current === job.job_id) return;
    refreshedJob.current = job.job_id;
    for (const name of ["summary", "executive-summary", "quality", "proposals", "reports", "history", "runs", "analysis-jobs", "repository-results"]) {
      void client.invalidateQueries({ queryKey: [name, projectId] });
    }
  }, [client, projectId, tracked.data]);

  function handleDialogOpenChange(kind: ActionKind, open: boolean) {
    if (open) {
      currentDialog.current = kind;
      setOpenAction(kind);
      return;
    }
    if (currentDialog.current === kind) closeDialog();
  }

  function closeDialog() {
    currentDialog.current = null;
    setOpenAction(null);
    if (requestStage.current === "preparing") operationGeneration.current += 1;
    if (requestStage.current !== "confirming") {
      setPreparation(null);
      setRemoteConsent(false);
      setError(null);
      setMessage("");
      confirmationKey.current = null;
    }
  }

  function cancelPreparation() {
    if (requestStage.current || !preparation) return;
    setPreparation(null);
    setRemoteConsent(false);
    setError(null);
    setMessage("");
    confirmationKey.current = null;
  }

  async function confirm(prepared: AnalysisPreparation, consent: boolean) {
    if (!mounted.current || currentDialog.current !== "sync" || !canConfirm || !projectId || !session?.csrf_token) {
      throw new Error("Tu sesión no permite confirmar este análisis.");
    }
    if (!analysisPreparationIsReady(prepared) || (prepared.remote.required && (!prepared.remote.server_allowed || !consent))) {
      throw new Error("El análisis todavía no está listo o falta autorizar los servicios en la nube.");
    }
    confirmationKey.current ??= crypto.randomUUID();
    const result = await api.confirmAnalysis(projectId, {
      preparation_id: prepared.preparation_id,
      configuration_fingerprint: prepared.configuration_fingerprint,
      confirm_analysis: true,
      remote_consent: prepared.remote.required ? consent : false,
    }, session.csrf_token, confirmationKey.current);
    if (!mounted.current) return;
    setJobId(result.job.job_id);
    setPreparation(null);
    setRemoteConsent(false);
    confirmationKey.current = null;
    setMessage(result.reused ? "Se retomó un análisis existente." : "Análisis confirmado. El seguimiento continúa en segundo plano.");
    void client.invalidateQueries({ queryKey: ["analysis-jobs", projectId] });
    void client.invalidateQueries({ queryKey: ["runs", projectId] });
    notify("Análisis confirmado; las decisiones siguen requiriendo tu autorización.", "success");
  }

  async function execute(prepareFirst: boolean) {
    if (lock.current || !canPrepare || running || (prepareFirst && preparation) || (!prepareFirst && !preparation)) return;
    lock.current = true;
    requestStage.current = prepareFirst ? "preparing" : "confirming";
    setBusyStage(requestStage.current);
    setError(null);
    setMessage(prepareFirst ? "Verificando configuración y permisos…" : "");
    const generation = ++operationGeneration.current;
    let confirmationStarted = !prepareFirst;
    try {
      if (prepareFirst) {
        const prepared = await api.prepareAnalysis(projectId!, session!.csrf_token!);
        if (!mounted.current || generation !== operationGeneration.current || currentDialog.current !== "sync") return;
        confirmationKey.current = null;
        setPreparation(prepared);
        setRemoteConsent(false);
        if (!analysisPreparationIsReady(prepared)) setMessage("El análisis aún requiere ajustes de configuración o permisos.");
        else if (!canConfirm) setMessage("Tu sesión permite revisar el análisis, pero no confirmarlo.");
        else if (!prepared.remote.required) {
          confirmationStarted = true;
          requestStage.current = "confirming";
          setBusyStage("confirming");
          await confirm(prepared, false);
        } else setMessage("Revisa los servicios en la nube y autoriza su uso antes de confirmar.");
      } else {
        confirmationStarted = true;
        requestStage.current = "confirming";
        setBusyStage("confirming");
        await confirm(preparation!, remoteConsent);
      }
    } catch (cause) {
      const abandonedPreparation = !confirmationStarted && (generation !== operationGeneration.current || currentDialog.current !== "sync");
      if (mounted.current && !abandonedPreparation) setError(cause instanceof Error ? cause.message : "No se pudo iniciar el análisis.");
    } finally {
      lock.current = false;
      requestStage.current = null;
      if (mounted.current) setBusyStage(null);
    }
  }

  const busy = busyStage !== null;
  const canStart = canPrepare && !busy && !running && !preparation;
  const canConfirmPrepared = Boolean(preparation && canConfirm && !busy && !running && analysisPreparationIsReady(preparation)
    && (!preparation.remote.required || (remoteConsent && preparation.remote.server_allowed)));

  return (
    <div className="header-workflow-actions" aria-label="Acciones del proyecto">
      <HeaderActionDialog
        kind="sync"
        label="Sincronizar proyecto"
        title="Sincronizar y analizar proyecto"
        description="Revisa el estado y prepara una sincronización. No se inicia ningún trabajo al abrir este diálogo."
        icon={<RefreshCw aria-hidden="true" size={17} />}
        open={openAction === "sync"}
        onOpenChange={(open) => handleDialogOpenChange("sync", open)}
      >
        <div className="analysis-dialog-scope"><span>Proyecto activo</span><strong>{projectId ?? "Ningún proyecto seleccionado"}</strong></div>
        {!canPrepare && <p className="analysis-dialog-notice" role="status">Para preparar un análisis necesitas una sesión autenticada con permiso de gestión y un proyecto activo.</p>}
        {active && !tracked.data && <p className="analysis-dialog-status" role="status">Estado del análisis: {analysisJobStatusLabel(active.status)}. Consultando el trabajo protegido.</p>}
        {jobId && !tracked.data && !tracked.isError && <p className="analysis-dialog-status" role="status">Consultando el estado del análisis confirmado…</p>}
        {tracked.data && <div className="analysis-dialog-status" role="status">
          <p>Estado del análisis: {analysisJobStatusLabel(tracked.data.status)}.{tracked.data.execution_mode && <> Modo de ejecución: {analysisExecutionModeLabel(tracked.data.execution_mode)}.</>}{tracked.data.fallback_reasons?.length ? <> Alternativas aplicadas: {tracked.data.fallback_reasons.join("; ")}.</> : null}</p>
          {tracked.data.run_id && <Link className="text-link" to={`/analysis/${encodeURIComponent(tracked.data.run_id)}`}>Consultar resultados y validación</Link>}
        </div>}
        {running && !tracked.isError && <p className="analysis-dialog-note">El trabajo sigue consultándose aunque cierres este diálogo. No se iniciará otro análisis hasta conocer su estado terminal.</p>}
        {tracked.isError && <p className="workflow-error" role="alert">No se pudo consultar el estado: {tracked.error.message}</p>}
        {message && <p className="workflow-status" role="status">{message}</p>}
        {error && <p className="workflow-error" role="alert">{error}</p>}
        {preparation && <section className="workflow-confirmation" aria-label="Preparación del análisis">
          {preparation.execution_mode && <p role="status">Modo de ejecución: {analysisExecutionModeLabel(preparation.execution_mode)}.</p>}
          {preparation.fallback_reasons?.length ? <div><strong>Alternativas aplicadas</strong><ul>{preparation.fallback_reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul></div> : null}
          {preparation.remote.required && <div className="analysis-remote-consent">
            <p>El análisis consultará servicios en la nube{preparation.remote.providers.length ? ` (${preparation.remote.providers.filter((provider) => provider.transport === "remote").map((provider) => `${providerAttemptLabel(provider.provider, provider.model)} · ${stageNameLabel(provider.stage)}`).join(", ")})` : ""}. Parte de la información del proyecto podría procesarse fuera de este equipo.</p>
            <label><input type="checkbox" checked={remoteConsent} onChange={(event) => setRemoteConsent(event.target.checked)} disabled={busy || !preparation.remote.server_allowed} /> Autorizo el uso de servicios en la nube</label>
            {!preparation.remote.server_allowed && <p>El servidor no permite el uso de servicios en la nube.</p>}
          </div>}
        </section>}
        <div className="analysis-dialog-actions">
          <button type="button" className="button button-primary" disabled={!canStart} onClick={() => void execute(true)}>
            {busyStage === "preparing" ? "Preparando análisis…" : running ? tracked.isError ? "Estado no disponible" : "Análisis en curso…" : preparation ? "Preparación lista" : "Preparar análisis"}
          </button>
          {preparation && <>
            <button type="button" className="button button-primary" disabled={!canConfirmPrepared} onClick={() => void execute(false)}>
              {busyStage === "confirming" ? "Confirmando análisis…" : "Confirmar análisis"}
            </button>
            <button type="button" className="button button-secondary" disabled={busy} onClick={cancelPreparation}>Cancelar preparación</button>
          </>}
        </div>
      </HeaderActionDialog>

      <HeaderActionDialog
        kind="email"
        label="Enviar correo"
        title="Enviar correo al gestor"
        description="Revisa el destinatario y el canal. El mensaje solo se solicita después de que confirmes el destinatario."
        icon={<Mail aria-hidden="true" size={17} />}
        open={openAction === "email"}
        onOpenChange={(open) => handleDialogOpenChange("email", open)}
      >
        {projectId
          ? <EmailDeliveryPanel
              projectId={projectId}
              compact
              idempotencyKey={emailIdempotencyKey}
              busyOverride={emailBusy}
              onBusyChange={setEmailBusy}
            />
          : <p className="analysis-dialog-notice" role="status">Selecciona un proyecto antes de enviar un correo.</p>}
      </HeaderActionDialog>
    </div>
  );
}
