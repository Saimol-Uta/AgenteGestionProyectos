import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Clock3, FilePlus2, ShieldCheck, Sparkles } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { analysisJobStatusLabel, formatDate, formatDurationSeconds, runStatusLabel } from "../../api/formatters";
import {
  queryKeys, useAnalysisJobs, useProject, useProjects, useRuns,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { ErrorState, EmptyState, LoadingState, Pill } from "../../components/States";
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
  if (status === "ready") return "Verificado como listo";
  if (status === "incomplete") return "Configuración incompleta";
  if (status === "unknown") return "Desconocido";
  return `Estado del dominio: ${status}`;
}

function credentialStatusLabel(status: string) {
  if (status === "configured") return "Configurada (presencia informada)";
  if (status === "missing") return "No configurada";
  if (status === "not_required") return "No requerida";
  return `Estado del dominio: ${status}`;
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
      if (!project || !session?.csrf_token) throw new Error("Se requiere una sesión con credencial CSRF para preparar el análisis.");
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
      notify(result.reused ? "Se reutilizó el trabajo de análisis existente." : "El backend aceptó el trabajo de análisis.", "success");
    },
  });

  const runSeries = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data?.items]);

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

  if (projectsQuery.isPending) return <LoadingState label="Cargando proyectos del backend…" />;
  if (projectsQuery.isError) return <ErrorState title="No se pudieron cargar los proyectos" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  if (needsProjectLookup && selectedProjectQuery.isPending) return <LoadingState label="Consultando el proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isError) return <ErrorState title="No se pudo consultar el proyecto activo" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />;
  if (!project) return <EmptyState title="No hay un proyecto disponible" message="El backend no reportó proyectos configurados o almacenados para consultar." />;

  const runPages = runsQuery.data ? Math.max(1, Math.ceil(runsQuery.data.total / Math.max(1, runsQuery.data.page_size))) : 1;
  const jobPages = jobsQuery.data ? Math.max(1, Math.ceil(jobsQuery.data.total / Math.max(1, jobsQuery.data.page_size))) : 1;
  const trackedJob = trackedJobQuery.data;
  const remoteConsentRequired = preparation?.remote.required ?? false;
  const confirmationAllowed = Boolean(
    preparation
    && preparation.eligible
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
        <Pill tone="neutral">GET de consulta · POST solo por acción</Pill>
      </div>

      <section className="panel analysis-preparation-panel" aria-labelledby="preparation-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">CONTROL DE EJECUCIÓN</p>
            <h2 id="preparation-heading">Preparar un análisis</h2>
            <p className="section-subtitle">Abrir esta página no inicia agentes, sondeos remotos ni trabajos.</p>
          </div>
          <span className="preparation-icon"><Sparkles aria-hidden="true" size={18} /></span>
        </div>

        {!canPrepare && (
          <p className="capability-note" role="status">
            {sessionLoading
              ? "Consultando la sesión y sus capacidades de servidor…"
              : !session?.authenticated
                ? "La consulta es pública y de solo lectura. Inicia sesión para solicitar una preparación protegida."
                : "La sesión no informa la capacidad de servidor analysis:prepare; no se enviará la solicitud."}
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
          {prepareMutation.isPending ? "Consultando precondiciones…" : "Preparar análisis"}
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
              <div><p className="eyebrow">PREFLIGHT DEL SERVIDOR</p><h3 id="preparation-result-heading">Revisión de configuración y fuentes</h3></div>
              <Pill tone={preparation.eligible ? "positive" : "warning"}>{preparation.eligible ? "Elegible según preflight" : "No elegible"}</Pill>
            </div>
            <dl className="preparation-facts">
              <div><dt>Proyecto</dt><dd>{preparation.project_id}</dd></div>
              <div><dt>Modo de origen</dt><dd>{preparation.preflight.source_mode}</dd></div>
              <div><dt>Configuración</dt><dd>{preflightStatusLabel(preparation.preflight.configuration_status)}</dd></div>
              <div><dt>Preparación válida hasta</dt><dd>{formatDate(preparation.expires_at, true)}</dd></div>
              <div><dt>Capacidades informadas por preflight</dt><dd>{preparation.capabilities.length ? preparation.capabilities.join(", ") : "Sin capacidades reportadas"}</dd></div>
            </dl>
            <ul className="preflight-checks">
              {preparation.preflight.checks.map((check) => (
                <li key={check.code}>
                  <span>{check.code}</span>
                  <Pill tone={check.status === "ready" ? "positive" : check.status === "incomplete" ? "warning" : "unknown"}>{preflightStatusLabel(check.status)}</Pill>
                </li>
              ))}
            </ul>
            <div className="remote-preflight">
              <h4><ShieldCheck aria-hidden="true" size={15} /> Uso de proveedores remotos</h4>
              <p>La disponibilidad remota no se comprueba durante la preparación.</p>
              <p>
                Requerido: <strong>{preparation.remote.required ? "Sí" : "No"}</strong>
                <span aria-hidden="true"> · </span>
                Permitido por servidor: <strong>{preparation.remote.server_allowed ? "Sí" : "No"}</strong>
              </p>
              {preparation.remote.providers.length ? (
                <ul className="provider-preflight-list">
                  {preparation.remote.providers.map((provider, index) => (
                    <li key={`${provider.stage}-${provider.provider}-${index}`}>
                      <span>{provider.stage}: {provider.provider} · {provider.model}</span>
                      <span>Transporte: {provider.transport}</span>
                      <span>Credencial: {credentialStatusLabel(provider.credential_status)}</span>
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
                <span>Otorgo mi consentimiento explícito para usar proveedores remotos en este análisis.</span>
              </label>
            )}
            {preparation.remote.required && !preparation.remote.server_allowed && (
              <p className="capability-note" role="status">El servidor no permite el procesamiento remoto requerido; no se puede confirmar este análisis.</p>
            )}
            {!canConfirm && <p className="capability-note" role="status">La sesión no informa la capacidad analysis:confirm; la API rechazará cualquier intento no autorizado.</p>}
            {!preparation.eligible && <p className="capability-note" role="status">El preflight no habilita la ejecución. No se ha creado ningún trabajo.</p>}
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
          {trackedJob.error_code && <p className="stage-error">Código seguro del backend: <code>{trackedJob.error_code}</code></p>}
          {trackedJob.run_id && <Link className="text-link" to={`/analysis?run_id=${encodeURIComponent(trackedJob.run_id)}`}>Abrir ejecución persistida <ArrowRight aria-hidden="true" size={14} /></Link>}
        </section>
      )}
      {trackedJobQuery.isError && <ErrorState title="No se pudo consultar el trabajo de análisis" message={trackedJobQuery.error.message} onRetry={() => void trackedJobQuery.refetch()} />}

      <section className="panel run-history-section" aria-labelledby="run-history-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">REGISTROS DEL BACKEND</p>
            <h2 id="run-history-heading">Historial de ejecuciones</h2>
            <p className="section-subtitle">El listado está ordenado por el servidor; no se reconstruyen ejecuciones desde capturas.</p>
          </div>
          <span className="history-source"><Clock3 aria-hidden="true" size={15} /> {runsQuery.data ? `${runsQuery.data.total} registros` : "Consulta API"}</span>
        </div>
        {runsQuery.isPending && <LoadingState label="Consultando ejecuciones persistidas…" />}
        {runsQuery.isError && <ErrorState title="No se pudo cargar el historial" message={runsQuery.error.message} onRetry={() => void runsQuery.refetch()} />}
        {runsQuery.data && (runSeries.length ? (
          <>
            <ul className="run-history-list">
              {runSeries.map((run) => (
                <li key={run.run_id}>
                  <Link to={`/analysis?run_id=${encodeURIComponent(run.run_id)}`} aria-label={`Abrir ejecución ${run.run_id}`}>
                    <span className="run-history-main"><strong>{run.run_id}</strong><code>{run.status}</code></span>
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
        ) : <EmptyState title="Sin ejecuciones registradas" message="La API no encontró runs persistidos para este proyecto." />)}
      </section>

      <section className="panel job-history-section" aria-labelledby="job-history-heading">
        <div className="section-heading compact-heading">
          <div><p className="eyebrow">COLA LOCAL ASÍNCRONA</p><h2 id="job-history-heading">Trabajos de análisis</h2></div>
          <span className="caption">Estados terminales: completado, fallido o interrumpido.</span>
        </div>
        {!canReadJobs && <EmptyState title="Historial de trabajos protegido" message="El servidor limita los trabajos a sesiones con capacidad de análisis habilitada para este proyecto." />}
        {canReadJobs && jobsQuery.isPending && <LoadingState label="Consultando trabajos locales…" />}
        {canReadJobs && jobsQuery.isError && <ErrorState title="No se pudieron consultar los trabajos" message={jobsQuery.error.message} onRetry={() => void jobsQuery.refetch()} />}
        {canReadJobs && jobsQuery.data && (jobsQuery.data.items.length ? (
          <>
            <ul className="job-history-list">
              {jobsQuery.data.items.map((job) => (
                <li key={job.job_id}>
                  <span><strong>{job.job_id}</strong><code>{job.status}</code></span>
                  <span>{analysisJobStatusLabel(job.status)}</span>
                  <span>Actualizado: {formatDate(job.updated_at, true)}</span>
                  <span>{job.remote_consent ? "Consentimiento remoto registrado" : "Sin consentimiento remoto"}</span>
                  {job.run_id && <Link to={`/analysis?run_id=${encodeURIComponent(job.run_id)}`}>Abrir run <ArrowRight aria-hidden="true" size={13} /></Link>}
                  {job.error_code && <code>{job.error_code}</code>}
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

      {requestedRunId && <RunDetailPanel key={requestedRunId} runId={requestedRunId} onClose={closeRunDetail} />}

      <div className="analysis-security-note">
        <Pill tone="neutral">Sin escrituras externas</Pill>
        <span>La sesión y las capacidades se consultan en el servidor. Toda confirmación exige CSRF, autorización y una clave de idempotencia.</span>
      </div>
    </section>
  );
}
