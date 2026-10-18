import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Clock3, FilePlus2, ShieldCheck, Sparkles } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import {
  analysisExecutionModeLabel,
  analysisJobStatusLabel,
  analysisPreparationIsReady,
  formatDate,
  formatDurationSeconds,
  providerModelLabel,
  providerNameLabel,
  runStatusLabel,
  stageNameLabel,
} from "../../api/formatters";
import {
  queryKeys, useAnalysisJobs, useProject, useProjects, useRuns,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { ErrorState, EmptyState, LoadingState, Pill } from "../../components/States";
import { RepositoryResultsPanel } from "../../components/RepositoryResultsPanel";
import { useSession } from "../auth/SessionProvider";
import { useToast } from "../../components/ToastProvider";
import type { AnalysisJobStatus, RunStatus } from "../../api/types";
import { RunDetailPanel } from "./RunDetailPanel";

const activeJobStatuses: AnalysisJobStatus[] = ["queued", "running"];

function pageValue(value: string | null) {
  const parsed = Number(value ?? "1");
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

function statusTone(status: RunStatus): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (status === "completed") return "positive";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "needs_attention" || status === "awaiting_human") return "warning";
  if (status === "unknown") return "unknown";
  if (status === "queued") return "neutral";
  return "accent";
}

function preflightStatusLabel(status: string) {
  if (status === "ready") return "Listo";
  if (status === "incomplete") return "Requiere atención";
  if (status === "unknown") return "Sin confirmar";
  return "Estado sin detalle";
}

function credentialStatusLabel(status: string) {
  if (status === "configured") return "Disponible";
  if (status === "missing") return "No disponible";
  if (status === "not_required") return "No se necesita";
  return "Disponibilidad sin confirmar";
}

function preflightCheckLabel(code: string) {
  const labels: Record<string, string> = {
    source_configured: "Fuentes del proyecto",
    local_state: "Estado local del análisis",
  };
  return labels[code] ?? "Verificación adicional";
}

function transportLabel(transport: string) {
  if (transport === "remote") return "Servicios en la nube";
  if (transport === "local") return "Procesamiento local";
  return "Ubicación sin confirmar";
}

export function AnalysisPage() {
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const needsProjectLookup = Boolean(selectedProjectId && !listedProject);
  const selectedProjectQuery = useProject(selectedProjectId, needsProjectLookup);
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const [searchParams, setSearchParams] = useSearchParams();
  const { session, isLoading: sessionLoading } = useSession();
  const canReadJobs = Boolean(session?.authenticated && session.capabilities.includes("analysis:prepare"));
  const canPrepare = Boolean(
    session?.authenticated
    && session.csrf_token
    && session.capabilities.includes("analysis:prepare"),
  );
  const canConfirm = Boolean(
    session?.authenticated
    && session.csrf_token
    && session.capabilities.includes("analysis:confirm"),
  );
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [preparation, setPreparation] = useState<Awaited<ReturnType<typeof api.prepareAnalysis>> | null>(null);
  const [remoteConsent, setRemoteConsent] = useState(false);
  const [confirmAttempted, setConfirmAttempted] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [pollAfterMs, setPollAfterMs] = useState(3_000);
  const idempotencyKeyRef = useRef<string | null>(null);

  const runsPage = pageValue(searchParams.get("runs_page"));
  const jobsPage = pageValue(searchParams.get("jobs_page"));
  const requestedRunId = searchParams.get("run_id") || null;
  const runsQuery = useRuns(project?.id ?? null, runsPage, 25);
  const jobsQuery = useAnalysisJobs(canReadJobs ? project?.id ?? null : null, jobsPage, 25);
  const persistedActiveJob = jobsQuery.data?.items.find((job) => activeJobStatuses.includes(job.status));
  const trackedJobId = canReadJobs ? activeJobId ?? persistedActiveJob?.job_id ?? null : null;
  const trackedJobQuery = useQuery({
    queryKey: queryKeys.analysisJob(trackedJobId ?? ""),
    queryFn: () => api.getAnalysisJob(trackedJobId!),
    enabled: Boolean(trackedJobId),
    staleTime: 3_000,
    refetchInterval: (query) => query.state.data && activeJobStatuses.includes(query.state.data.status) ? pollAfterMs : false,
  });

  useEffect(() => {
    const status = trackedJobQuery.data?.status;
    if (canReadJobs && project?.id && status && !activeJobStatuses.includes(status)) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisJobs(project.id, jobsPage, 25), exact: true });
    }
  }, [canReadJobs, jobsPage, project?.id, queryClient, trackedJobQuery.data?.status]);

  useEffect(() => {
    setPreparation(null);
    setRemoteConsent(false);
    setConfirmAttempted(false);
    setActiveJobId(null);
  }, [project?.id]);

  const prepareMutation = useMutation({
    mutationFn: async () => {
      if (!project || !session?.csrf_token) throw new Error("Inicia sesión para revisar las condiciones del análisis.");
      return api.prepareAnalysis(project.id, session.csrf_token);
    },
    onSuccess: (result) => {
      idempotencyKeyRef.current = null;
      setPreparation(result);
      setRemoteConsent(false);
      setConfirmAttempted(false);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: async () => {
      if (!project || !session?.csrf_token || !preparation) {
        throw new Error("La sesión o la preparación del análisis ya no están disponibles.");
      }
      idempotencyKeyRef.current ??= crypto.randomUUID();
      setConfirmAttempted(true);
      return api.confirmAnalysis(project.id, {
        preparation_id: preparation.preparation_id,
        configuration_fingerprint: preparation.configuration_fingerprint,
        confirm_analysis: true,
        remote_consent: preparation.remote.required ? remoteConsent : false,
      }, session.csrf_token, idempotencyKeyRef.current);
    },
    onSuccess: (result) => {
      idempotencyKeyRef.current = null;
      setActiveJobId(result.job.job_id);
      setPollAfterMs(result.poll_after_ms);
      setPreparation(null);
      setRemoteConsent(false);
      if (project) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.analysisJobs(project.id, jobsPage, 25) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.runs(project.id, 1, 25) });
      }
      notify(result.reused ? "Se retomó un análisis existente." : "El análisis fue confirmado.", "success");
    },
  });

  const runSeries = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data?.items]);
  const repositoryScopeRunId = requestedRunId ?? runSeries[0]?.run_id ?? undefined;

  function updatePage(key: "runs_page" | "jobs_page", nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage <= 1) next.delete(key);
    else next.set(key, String(nextPage));
    setSearchParams(next, { replace: true });
  }

  function closeRunDetail() {
    const next = new URLSearchParams(searchParams);
    next.delete("run_id");
    setSearchParams(next, { replace: true });
  }

  if (projectsQuery.isPending) return <LoadingState label="Cargando proyectos…" />;
  if (projectsQuery.isError) return <ErrorState title="No se pudieron cargar los proyectos" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  if (needsProjectLookup && selectedProjectQuery.isPending) return <LoadingState label="Consultando el proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isError) return <ErrorState title="No se pudo consultar el proyecto activo" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />;
  if (!project) return <EmptyState title="No hay un proyecto disponible" message="No hay proyectos disponibles para consultar." />;

  const runPages = runsQuery.data ? Math.max(1, Math.ceil(runsQuery.data.total / Math.max(1, runsQuery.data.page_size))) : 1;
  const jobPages = jobsQuery.data ? Math.max(1, Math.ceil(jobsQuery.data.total / Math.max(1, jobsQuery.data.page_size))) : 1;
  const trackedJob = trackedJobQuery.data;
  const remoteConsentRequired = preparation?.remote.required ?? false;
  const confirmationAllowed = Boolean(
    preparation
    && analysisPreparationIsReady(preparation)
    && canConfirm
    && (!preparation.remote.required || preparation.remote.server_allowed)
    && (!preparation.remote.required || remoteConsent),
  );

  return (
    <section className="analysis-page content-section">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">CENTRO DE INTELIGENCIA · {project.name}</p>
          <h1>Centro de análisis de IA</h1>
          <p className="page-subtitle">Consulta ejecuciones persistidas y prepara nuevos trabajos solo mediante confirmación explícita.</p>
        </div>
        <Pill tone="neutral">Consulta de ejecuciones · inicio solo con confirmación</Pill>
      </div>

      <RepositoryResultsPanel projectId={project.id} runId={repositoryScopeRunId} />

      <section className="panel analysis-preparation-panel" aria-labelledby="preparation-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">CONTROL DE EJECUCIÓN</p>
            <h2 id="preparation-heading">Preparar un análisis</h2>
            <p className="section-subtitle">Consultar esta página no inicia el análisis ni contacta servicios externos.</p>
          </div>
          <span className="preparation-icon"><Sparkles aria-hidden="true" size={18} /></span>
        </div>

        {!canPrepare && (
          <p className="capability-note" role="status">
            {sessionLoading
              ? "Verificando tus permisos de acceso…"
              : !session?.authenticated
                ? "La consulta es pública y de solo lectura. Inicia sesión para solicitar una preparación protegida."
                : "Tu sesión no tiene permiso para revisar un nuevo análisis; no se enviará la solicitud."}
          </p>
        )}
        <button
          className="button button-primary"
          type="button"
          disabled={!canPrepare || prepareMutation.isPending}
          onClick={() => {
            idempotencyKeyRef.current = null;
            setPreparation(null);
            setRemoteConsent(false);
            setConfirmAttempted(false);
            confirmMutation.reset();
            prepareMutation.mutate();
          }}
        >
          <FilePlus2 aria-hidden="true" size={16} />
          {prepareMutation.isPending ? "Revisando condiciones…" : "Preparar análisis"}
        </button>
        {prepareMutation.isError && (
          <ErrorState
            title="No se pudo preparar el análisis"
            message={prepareMutation.error.message}
            onRetry={() => prepareMutation.mutate()}
          />
        )}

        {preparation && (
          <div className="preparation-result" aria-labelledby="preparation-result-heading">
            <div className="section-heading compact-heading">
              <div><p className="eyebrow">ANTES DE INICIAR</p><h3 id="preparation-result-heading">Revisión de configuración y fuentes</h3></div>
              <Pill tone={analysisPreparationIsReady(preparation) ? "positive" : "warning"}>{analysisPreparationIsReady(preparation) ? "Listo para confirmar" : "Requiere atención"}</Pill>
            </div>
            <dl className="preparation-facts">
              <div><dt>Proyecto</dt><dd>{preparation.project_id}</dd></div>
              <div><dt>Fuentes del proyecto</dt><dd>{providerNameLabel(preparation.preflight.source_mode)}</dd></div>
              <div><dt>Revisión de configuración</dt><dd>{preflightStatusLabel(preparation.preflight.configuration_status)}</dd></div>
              <div><dt>Preparación válida hasta</dt><dd>{formatDate(preparation.expires_at, true)}</dd></div>
            </dl>
            {preparation.execution_mode && <p className="section-subtitle" role="status">Modo de ejecución: {analysisExecutionModeLabel(preparation.execution_mode)}</p>}
            <ul className="preflight-checks">
              {preparation.preflight.checks.map((check) => (
                <li key={check.code}>
                  <span>{preflightCheckLabel(check.code)}</span>
                  <Pill tone={check.status === "ready" ? "positive" : check.status === "incomplete" ? "warning" : "unknown"}>{preflightStatusLabel(check.status)}</Pill>
                </li>
              ))}
            </ul>
            <div className="remote-preflight">
              <h4><ShieldCheck aria-hidden="true" size={15} /> Servicios en la nube</h4>
              <p>La revisión no contacta servicios en la nube.</p>
              <p>
                Se necesitan para este análisis: <strong>{preparation.remote.required ? "Sí" : "No"}</strong>
                <span aria-hidden="true"> · </span>
                Autorizados para el proyecto: <strong>{preparation.remote.server_allowed ? "Sí" : "No"}</strong>
              </p>
              {preparation.remote.providers.length ? (
                <ul className="provider-preflight-list">
                  {preparation.remote.providers.map((provider, index) => (
                    <li key={`${provider.stage}-${provider.provider}-${index}`}>
                      <span>{stageNameLabel(provider.stage)}: {providerNameLabel(provider.provider)}{providerModelLabel(provider.provider, provider.model) ? ` · ${providerModelLabel(provider.provider, provider.model)}` : ""}</span>
                      <span>{transportLabel(provider.transport)}</span>
                      <span>Acceso: {credentialStatusLabel(provider.credential_status)}</span>
                    </li>
                  ))}
                </ul>
              ) : <p>Sin proveedores informados por la preparación.</p>}
            </div>

            {remoteConsentRequired && (
              <label className="consent-check">
                <input
                  type="checkbox"
                  checked={remoteConsent}
                  disabled={!preparation.remote.server_allowed || confirmMutation.isPending || confirmAttempted}
                  onChange={(event) => setRemoteConsent(event.target.checked)}
                />
                <span>Autorizo el uso de servicios en la nube para este análisis.</span>
              </label>
            )}
            {preparation.remote.required && !preparation.remote.server_allowed && (
              <p className="capability-note" role="status">Este proyecto no tiene autorización para usar los servicios en la nube necesarios; no se puede confirmar este análisis.</p>
            )}
            {!canConfirm && <p className="capability-note" role="status">Tu sesión no tiene permiso para confirmar este análisis.</p>}
            {!analysisPreparationIsReady(preparation) && <p className="capability-note" role="status">La revisión aún no cumple las condiciones para ejecutar el análisis. No se ha creado ningún trabajo.</p>}
            {preparation.fallback_reasons?.length ? <div className="capability-note"><strong>Alternativas aplicadas</strong><ul>{preparation.fallback_reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul></div> : null}
            {confirmMutation.isError && (
              <ErrorState title="No se pudo confirmar el análisis" message={confirmMutation.error.message} onRetry={() => confirmMutation.mutate()} />
            )}
            <div className="preparation-actions">
              <button
                className="button button-primary"
                type="button"
                disabled={!confirmationAllowed || confirmMutation.isPending}
                onClick={() => confirmMutation.mutate()}
              >
                {confirmMutation.isPending ? "Enviando confirmación…" : "Confirmar análisis"}
              </button>
              <button className="button button-ghost" type="button" disabled={confirmMutation.isPending} onClick={() => { idempotencyKeyRef.current = null; setPreparation(null); setRemoteConsent(false); setConfirmAttempted(false); }}>
                Cancelar preparación
              </button>
            </div>
          </div>
        )}
      </section>

      {trackedJob && (
        <section className="panel active-job-panel" aria-labelledby="active-job-heading">
          <div className="section-heading compact-heading">
            <div><p className="eyebrow">TRABAJO ASÍNCRONO</p><h2 id="active-job-heading">Trabajo {trackedJob.job_id}</h2></div>
            <Pill tone={trackedJob.status === "completed" ? "positive" : trackedJob.status === "failed" || trackedJob.status === "interrupted" ? "danger" : "accent"}>
              {analysisJobStatusLabel(trackedJob.status)}
            </Pill>
          </div>
          <p role="status">Trabajo de análisis: {analysisJobStatusLabel(trackedJob.status).toLocaleLowerCase("es-EC")}</p>
          <p className="section-subtitle">Última actualización: {formatDate(trackedJob.updated_at, true)}. No se estima porcentaje de progreso.</p>
          {trackedJob.error_code && <p className="stage-error">El trabajo no se completó correctamente. Revisa el historial o solicita ayuda al administrador del proyecto.</p>}
          {trackedJob.execution_mode && <p>Modo de ejecución: {analysisExecutionModeLabel(trackedJob.execution_mode)}</p>}
          {trackedJob.fallback_reasons?.length ? <p>Alternativas aplicadas: {trackedJob.fallback_reasons.join("; ")}</p> : null}
          {trackedJob.run_id && <Link className="text-link" to={`/analysis?run_id=${encodeURIComponent(trackedJob.run_id)}`}>Abrir análisis <ArrowRight aria-hidden="true" size={14} /></Link>}
        </section>
      )}
      {trackedJobQuery.isError && <ErrorState title="No se pudo consultar el trabajo de análisis" message={trackedJobQuery.error.message} onRetry={() => void trackedJobQuery.refetch()} />}

      <section className="panel run-history-section" aria-labelledby="run-history-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ACTIVIDAD DEL PROYECTO</p>
            <h2 id="run-history-heading">Historial de análisis</h2>
            <p className="section-subtitle">Consulta los análisis registrados y revisa sus evidencias antes de tomar decisiones.</p>
          </div>
          <span className="history-source"><Clock3 aria-hidden="true" size={15} /> {runsQuery.data ? `${runsQuery.data.total} registros` : "Historial"}</span>
        </div>
        {runsQuery.isPending && <LoadingState label="Consultando el historial de análisis…" />}
        {runsQuery.isError && <ErrorState title="No se pudo cargar el historial" message={runsQuery.error.message} onRetry={() => void runsQuery.refetch()} />}
        {runsQuery.data && (runSeries.length ? (
          <>
            <ul className="run-history-list">
              {runSeries.map((run) => (
                <li key={run.run_id}>
                  <Link to={`/analysis?run_id=${encodeURIComponent(run.run_id)}`} aria-label={`Abrir ejecución ${run.run_id}`}>
                    <span className="run-history-main"><strong>{run.run_id}</strong></span>
                    <span>{formatDate(run.created_at, true)}</span>
                    <Pill tone={statusTone(run.status)}>{runStatusLabel(run.status)}</Pill>
                    <span>Duración: {formatDurationSeconds(run.duration_seconds)}</span>
                    {run.report_id && <span>Informe: {run.report_id}</span>}
                  </Link>
                </li>
              ))}
            </ul>
            {runPages > 1 && (
              <div className="pagination-row">
                <span>Página {runsPage} de {runPages}</span>
                <div>
                  <button className="button button-secondary button-small" type="button" disabled={runsPage <= 1} onClick={() => updatePage("runs_page", runsPage - 1)}>Anterior</button>
                  <button className="button button-secondary button-small" type="button" disabled={runsPage >= runPages} onClick={() => updatePage("runs_page", runsPage + 1)}>Siguiente</button>
                </div>
              </div>
            )}
          </>
        ) : <EmptyState title="Sin análisis registrados" message="Aún no hay análisis guardados para este proyecto." />)}
      </section>

      <section className="panel job-history-section" aria-labelledby="job-history-heading">
        <div className="section-heading compact-heading">
          <div><p className="eyebrow">ANÁLISIS DEL PROYECTO</p><h2 id="job-history-heading">Trabajos recientes</h2></div>
          <span className="caption">El historial refleja el estado informado para cada análisis.</span>
        </div>
        {!canReadJobs && <EmptyState title="Historial de análisis protegido" message="Inicia sesión con permiso de gestión para consultar los análisis de este proyecto." />}
        {canReadJobs && jobsQuery.isPending && <LoadingState label="Consultando análisis recientes…" />}
        {canReadJobs && jobsQuery.isError && <ErrorState title="No se pudieron consultar los trabajos" message={jobsQuery.error.message} onRetry={() => void jobsQuery.refetch()} />}
        {canReadJobs && jobsQuery.data && (jobsQuery.data.items.length ? (
          <>
            <ul className="job-history-list">
              {jobsQuery.data.items.map((job) => (
                <li key={job.job_id}>
                  <span><strong>{job.job_id}</strong></span>
                  <span>{analysisJobStatusLabel(job.status)}</span>
                  <span>Actualizado: {formatDate(job.updated_at, true)}</span>
                  <span>{job.remote_consent ? "Autorización de servicios en la nube registrada" : "Sin autorización para servicios en la nube"}</span>
                  {job.execution_mode && <span>Modo de ejecución: {analysisExecutionModeLabel(job.execution_mode)}</span>}
                  {job.fallback_reasons?.length ? <span>Alternativas: {job.fallback_reasons.join("; ")}</span> : null}
                  {job.run_id && <Link to={`/analysis?run_id=${encodeURIComponent(job.run_id)}`}>Abrir análisis <ArrowRight aria-hidden="true" size={13} /></Link>}
                  {job.error_code && <span>El análisis no se completó correctamente.</span>}
                </li>
              ))}
            </ul>
            {jobPages > 1 && (
              <div className="pagination-row">
                <span>Página {jobsPage} de {jobPages}</span>
                <div>
                  <button className="button button-secondary button-small" type="button" disabled={jobsPage <= 1} onClick={() => updatePage("jobs_page", jobsPage - 1)}>Anterior</button>
                  <button className="button button-secondary button-small" type="button" disabled={jobsPage >= jobPages} onClick={() => updatePage("jobs_page", jobsPage + 1)}>Siguiente</button>
                </div>
              </div>
            )}
          </>
        ) : <EmptyState title="Sin trabajos asíncronos" message="La consulta del ledger no encontró trabajos almacenados." />)}
      </section>

      {requestedRunId && <RunDetailPanel key={`${project.id}:${requestedRunId}`} runId={requestedRunId} projectId={project.id} onClose={closeRunDetail} />}

      <div className="analysis-security-note">
        <Pill tone="neutral">Sin escrituras externas</Pill>
        <span>Solo las sesiones autorizadas pueden iniciar un análisis. Cada trabajo requiere una confirmación explícita.</span>
      </div>
    </section>
  );
}
