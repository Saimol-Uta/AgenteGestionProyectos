import { useMemo } from "react";
import { formatDate, formatPercentage, findingVerificationLabel } from "../api/formatters";
import { useRepositoryResults } from "../api/queries";
import type {
  ConfiguredRepositoryView,
  RepositoryCIRunView,
  RepositoryHistoryView,
  RepositoryIdentityView,
} from "../api/types";
import { EmptyState, ErrorState, LoadingState, Pill } from "./States";
import { useSession } from "../features/auth/SessionProvider";
import "./repository-results.css";

interface RepositoryEntry {
  key: string;
  name: string;
  repositoryId: string | null;
  configured: ConfiguredRepositoryView | null;
  observations: RepositoryHistoryView[];
}

function sameRepositoryIdentity(left: RepositoryIdentityView | null, right: RepositoryIdentityView | ConfiguredRepositoryView | null) {
  return Boolean(left
    && right
    && left.repository_id === right.repository_id
    && left.owner.toLocaleLowerCase() === right.owner.toLocaleLowerCase()
    && left.repo.toLocaleLowerCase() === right.repo.toLocaleLowerCase());
}

function accessLabel(status: RepositoryHistoryView["access_status"] | null) {
  if (status === "allowed") return "Acceso reportado como disponible";
  if (status === "denied") return "Acceso denegado";
  return "Permisos desconocidos";
}

function availabilityLabel(status: RepositoryHistoryView["availability_status"] | null) {
  if (status === "available") return "Evidencia disponible";
  if (status === "unavailable") return "Evidencia no disponible";
  return "Disponibilidad desconocida";
}

function commitResolutionLabel(status: RepositoryHistoryView["commit_resolution_status"] | null) {
  if (status === "resolved") return "SHA objetivo resuelto";
  if (status === "pending") return "SHA objetivo pendiente";
  if (status === "failed") return "Resolución del SHA fallida";
  return "Resolución del SHA desconocida";
}

function metricText(value: number | null | undefined) {
  return value === null || value === undefined ? "Sin dato" : String(value);
}

function RepositoryObservation({ item }: { item: RepositoryHistoryView }) {
  const repositoryCIRuns = item.ci_runs.filter((run) => sameRepositoryIdentity(run.repository, item.repository));
  const unassignedCIRuns = item.ci_runs.filter((run) => !sameRepositoryIdentity(run.repository, item.repository));
  const coverage = item.coverage_percentage;
  const coverageBasis = item.coverage_basis;

  return (
    <div className="repository-observation">
      <p className="repository-observation-scope">
        {item.analysis_run_id ? <>Ejecución <code>{item.analysis_run_id}</code></> : "Ejecución sin identidad recuperable"}
        {item.source_scope !== "repository" && <> · Procedencia histórica: <code>{item.source_scope}</code></>}
      </p>
      <dl className="repository-facts">
        <div><dt>Estado de acceso</dt><dd>{accessLabel(item.access_status)}</dd></div>
        <div><dt>Estado de evidencia</dt><dd>{availabilityLabel(item.availability_status)}</dd></div>
        <div><dt>Resolución de commit</dt><dd>{commitResolutionLabel(item.commit_resolution_status)}</dd></div>
        <div><dt>Commit objetivo</dt><dd><code>{item.target_commit_sha ?? "Sin dato"}</code></dd></div>
        <div><dt>Cambios de código recopilados</dt><dd>{item.code_diff_count}</dd></div>
        <div><dt>Cobertura informada</dt><dd>{formatPercentage(coverage)}</dd></div>
        <div><dt>Alcance de cobertura</dt><dd>{coverageBasis ?? "Sin alcance informado"}</dd></div>
        <div><dt>Completitud de fuentes</dt><dd>{item.source_complete === null ? "Desconocida" : item.source_complete ? "Completa según la fuente" : "Incompleta"}</dd></div>
        <div><dt>Estado de evaluación</dt><dd>{item.evaluation_status ?? "Desconocido"}</dd></div>
      </dl>
      {item.incomplete_reason && <p className="repository-evidence-note">Evidencia insuficiente: {item.incomplete_reason}</p>}
      {item.source_problems.length > 0 && (
        <ul className="repository-problems" aria-label="Problemas reportados por las fuentes">
          {item.source_problems.map((problem, index) => <li key={`${index}-${problem}`}>{problem}</li>)}
        </ul>
      )}
      <div className="repository-ci-list">
        <h4>Integración continua y métricas por workflow</h4>
        {repositoryCIRuns.length === 0 && <p>{item.ci_runs.length === 0 ? "Sin ejecución CI asociada a esta observación; no se infiere un estado verde." : "No hay una identidad de workflow coincidente; las ejecuciones se muestran sin atribuirlas a este repositorio."}</p>}
        {repositoryCIRuns.map((run) => <RepositoryCIRun key={`${run.run_id}:${run.actual_attempt ?? "unknown"}:${run.workflow_name}`} run={run} expectedRepository={item.repository} />)}
      </div>
      {unassignedCIRuns.length > 0 && <div className="repository-unassigned-ci" role="status">
        <h4>Workflows sin asociación confirmada</h4>
        <p>La identidad del workflow no coincide con la observación; no se atribuyen sus métricas a este repositorio.</p>
        {unassignedCIRuns.map((run) => <div className="repository-unassigned-ci-run" key={`${run.run_id}:${run.actual_attempt ?? "unknown"}:${run.workflow_name}`}>
          <strong>{run.workflow_name || "Workflow sin nombre"}</strong>
          <dl className="repository-facts repository-ci-facts">
            <div><dt>Run CI</dt><dd><code>{run.run_id}</code></dd></div>
            <div><dt>Repositorio del workflow</dt><dd>{run.repository ? `${run.repository.owner}/${run.repository.repo} · ${run.repository.repository_id}` : "Identidad desconocida"}</dd></div>
            <div><dt>Intento</dt><dd>{run.actual_attempt === null ? "Sin dato" : `Intento ${run.actual_attempt}`}</dd></div>
            <div><dt>Estado</dt><dd>{run.status || "Sin dato"}</dd></div>
            <div><dt>Commit del workflow</dt><dd><code>{run.commit_sha ?? "Sin dato"}</code></dd></div>
          </dl>
        </div>)}
      </div>}
      <div className="repository-findings-list">
        <h4>Hallazgos candidatos del repositorio ({item.findings.length})</h4>
        {item.findings.length === 0 ? (
          <p className="repository-evidence-note">No se registraron hallazgos candidatos para este repositorio.</p>
        ) : (
          <ul className="repository-findings-items">
            {item.findings.map((finding) => (
              <li className="repository-finding-item" key={finding.finding_id}>
                <div className="repository-finding-item-header">
                  <strong>{finding.file_path ?? finding.finding_id}{finding.line !== null ? `:${finding.line}` : ""}</strong>
                  <Pill tone="accent">Candidato</Pill>
                  <Pill tone={finding.verification_status === "confirmed" ? "positive" : finding.verification_status === "rejected" ? "danger" : "warning"}>
                    {findingVerificationLabel(finding.verification_status)}
                  </Pill>
                  {finding.severity && (
                    <Pill tone={finding.severity === "critical" || finding.severity === "high" ? "danger" : "warning"}>
                      Severidad: {finding.severity}
                    </Pill>
                  )}
                  {finding.confidence !== null && (
                    <Pill tone="neutral">Confianza: {Math.round(finding.confidence * 100)}%</Pill>
                  )}
                  {finding.suggested_code_validation_status === "not_validated" && (
                    <Pill tone="warning">Código sugerido no validado</Pill>
                  )}
                </div>
                {finding.claim && <p className="repository-finding-claim"><strong>Afirmación:</strong> {finding.claim}</p>}
                {finding.verification_reason && (
                  <p className="repository-finding-reason">
                    <strong>Motivo de verificación:</strong> {finding.verification_reason}
                  </p>
                )}
                {finding.suggested_code && (
                  <details className="repository-finding-code-details">
                    <summary>Ver código sugerido (no validado)</summary>
                    <pre><code>{finding.suggested_code}</code></pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RepositoryCIRun({ run, expectedRepository }: { run: RepositoryCIRunView; expectedRepository: RepositoryIdentityView | null }) {
  const metrics = run.report_metrics_eligible === true ? run.report_metrics : null;
  const artifactValidation = run.source_artifact_validation;
  const workflowIdentityMatches = Boolean(expectedRepository
    && run.repository
    && run.repository.repository_id === expectedRepository.repository_id
    && run.repository.owner.toLocaleLowerCase() === expectedRepository.owner.toLocaleLowerCase()
    && run.repository.repo.toLocaleLowerCase() === expectedRepository.repo.toLocaleLowerCase());
  return (
    <article className="repository-ci-run" aria-label={`Workflow ${run.workflow_name}, ejecución ${run.run_id}`}>
      <div className="repository-ci-heading">
        <strong>{run.workflow_name || "Workflow sin nombre"}</strong>
        <Pill tone="neutral">Estado de workflow: {run.status || "Sin dato"}</Pill>
      </div>
      <dl className="repository-facts repository-ci-facts">
        <div><dt>Run CI</dt><dd><code>{run.run_id}</code></dd></div>
        <div><dt>Repositorio del workflow</dt><dd>{run.repository ? `${run.repository.owner}/${run.repository.repo} · ${run.repository.repository_id}` : "Identidad desconocida"}</dd></div>
        <div><dt>Intento</dt><dd>{run.actual_attempt === null ? "Sin dato" : `Intento ${run.actual_attempt}`}</dd></div>
        <div><dt>Workflow path</dt><dd><code>{run.workflow_path ?? "Sin dato"}</code></dd></div>
        <div><dt>Commit del workflow</dt><dd><code>{run.commit_sha ?? "Sin dato"}</code></dd></div>
        <div><dt>Fecha del workflow</dt><dd>{formatDate(run.timestamp, true)}</dd></div>
      </dl>
      <p className="repository-artifact-status">
        Métricas para el informe: {run.report_metrics_eligible === true ? "elegibles según el backend" : "no certificadas para el informe"}
        {run.report_artifact_outcome && <> · Artefacto: <code>{run.report_artifact_outcome}</code></>}
        {artifactValidation && <> · Validación de origen: <code>{artifactValidation.outcome}</code></>}
        · Identidad del workflow: {workflowIdentityMatches ? "coincide con la observación" : "no atribuida a esta observación"}
      </p>
      {metrics ? (
        <dl className="repository-facts repository-ci-metrics">
          <div><dt>Pruebas totales</dt><dd>{metricText(metrics.total_tests)}</dd></div>
          <div><dt>Pruebas fallidas</dt><dd>{metricText(metrics.failed_tests)}</dd></div>
          <div><dt>Cobertura del productor</dt><dd>{formatPercentage(metrics.coverage_percentage)}</dd></div>
          <div><dt>Alcance de cobertura</dt><dd>{metrics.coverage_basis ?? "Sin alcance informado"}</dd></div>
          {metrics.coverage_metric && <div><dt>Métrica de cobertura</dt><dd>{metrics.coverage_metric}</dd></div>}
          {metrics.e2e_status && <div><dt>Estado E2E reportado</dt><dd>{metrics.e2e_status}</dd></div>}
        </dl>
      ) : <p className="repository-evidence-note">Las métricas desconocidas o no certificadas no se presentan como resultado del informe.</p>}
      {run.report_artifact_reason && <p className="repository-evidence-note">Motivo informado por el backend: {run.report_artifact_reason}</p>}
    </article>
  );
}

export function RepositoryResultsPanel({ projectId, runId }: { projectId: string | null; runId?: string | null }) {
  const { session, isLoading: sessionLoading } = useSession();
  const canReadRepositoryHistory = Boolean(session?.authenticated && session.capabilities.includes("audit:read"));
  const resultsQuery = useRepositoryResults(projectId, {}, 1, 100, runId, canReadRepositoryHistory);
  const entries = useMemo<RepositoryEntry[]>(() => {
    const data = resultsQuery.data;
    if (!data) return [];
    const items = data.items.filter((item) => runId === undefined || (runId !== null && item.analysis_run_id === runId));
    const used = new Set<RepositoryHistoryView>();
    const configured = data.repositories.map((repository) => {
      const observations = items.filter((item) => sameRepositoryIdentity(item.repository, repository));
      observations.forEach((item) => used.add(item));
      return {
        key: `configured:${repository.repository_id}`,
        name: `${repository.owner}/${repository.repo}`,
        repositoryId: repository.repository_id,
        configured: repository,
        observations,
      };
    });
    const historical = items.filter((item) => !used.has(item)).map((item, index) => ({
      key: `historical:${item.repository?.repository_id ?? "unknown"}:${item.capture_id}:${index}`,
      name: item.repository ? `${item.repository.owner}/${item.repository.repo}` : "Identidad de repositorio desconocida",
      repositoryId: item.repository?.repository_id ?? null,
      configured: null,
      observations: [item],
    }));
    return [...configured, ...historical];
  }, [resultsQuery.data, runId]);

  if (!projectId) return null;
  if (sessionLoading) {
    return <section className="panel repository-results-panel" aria-labelledby="repository-results-heading"><div className="section-heading"><div><p className="eyebrow">EVIDENCIA POR REPOSITORIO</p><h2 id="repository-results-heading">Repositorios del proyecto</h2></div></div><LoadingState label="Verificando acceso al historial de repositorios…" /></section>;
  }
  if (!canReadRepositoryHistory) {
    return (
      <section className="panel repository-results-panel" aria-labelledby="repository-results-heading">
        <div className="section-heading"><div><p className="eyebrow">EVIDENCIA POR REPOSITORIO</p><h2 id="repository-results-heading">Repositorios del proyecto</h2></div><Pill tone="unknown">Disponibilidad desconocida</Pill></div>
        <p role="status">Disponibilidad de repositorios desconocida: la sesión no concedió <code>audit:read</code>; no se consultó el historial.</p>
      </section>
    );
  }

  return (
    <section className="panel repository-results-panel" aria-labelledby="repository-results-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">EVIDENCIA POR REPOSITORIO · {projectId}</p>
          <h2 id="repository-results-heading">Repositorios del proyecto</h2>
          <p className="section-subtitle">Los resultados y las métricas conservan el alcance y la identidad reportados por el backend.</p>
        </div>
        {resultsQuery.data && <Pill tone={resultsQuery.data.availability === "available" ? "neutral" : "unknown"}>{resultsQuery.data.availability === "available" ? "Historial disponible" : "Disponibilidad desconocida"}</Pill>}
      </div>
      {resultsQuery.isPending && <LoadingState label="Consultando resultados independientes por repositorio…" />}
      {resultsQuery.isError && <ErrorState title="No se pudieron consultar los resultados de repositorios" message={resultsQuery.error.message} onRetry={() => void resultsQuery.refetch()} />}
      {resultsQuery.data && (
        <>
          {resultsQuery.data.message && <p className="repository-evidence-note" role="status">{resultsQuery.data.message}</p>}
          {resultsQuery.data.total > resultsQuery.data.items.length && <p className="repository-evidence-note" role="status">Mostrando {resultsQuery.data.items.length} de {resultsQuery.data.total} observaciones; el historial está paginado y la ausencia fuera de esta página no se infiere.</p>}
          <h3 className="repository-summary-heading">Resumen consolidado del proyecto</h3>
          <p className="repository-summary-note">{resultsQuery.data.repositories.length} repositorios configurados · {entries.filter((entry) => entry.observations.length > 0).length} con observaciones en este alcance · no se calcula cobertura global.</p>
          {entries.length === 0 ? (
            <EmptyState title="No hay identidad de repositorio recuperable" message="El historial no contiene repositorios configurados ni observaciones con identidad verificable." />
          ) : (
            <div className="repository-entry-list">
              {entries.map((entry) => (
                <article className="repository-entry" aria-label={entry.name} key={entry.key}>
                  <div className="repository-entry-heading">
                    <div><h3>{entry.name}</h3>{entry.repositoryId && <p>ID reportado: <code>{entry.repositoryId}</code></p>}</div>
                    {entry.configured && <Pill tone="neutral">Configuración del backend</Pill>}
                  </div>
                  {entry.configured && (
                    <dl className="repository-facts repository-configuration-facts">
                      <div><dt>Rama configurada</dt><dd>{entry.configured.branch ?? "Sin dato"}</dd></div>
                      {entry.configured.quality_workflow_path && <div><dt>Workflow configurado</dt><dd><code>{entry.configured.quality_workflow_path}</code></dd></div>}
                    </dl>
                  )}
                  {entry.observations.length === 0 ? (
                    <p className="repository-evidence-note">{runId === undefined ? "Sin observación histórica devuelta; permisos y evidencia desconocidos." : runId === null ? "La ejecución no tiene identidad recuperable; no se adjunta evidencia de otro run." : resultsQuery.data && resultsQuery.data.total > resultsQuery.data.items.length ? `No se devolvió una observación asociada a ${runId} en la página visible; no se infiere ausencia del historial completo.` : `Sin observación de repositorio asociada a la ejecución ${runId}.`}</p>
                  ) : (
                    <>
                      <RepositoryObservation item={entry.observations[0]} />
                      {entry.observations.length > 1 && (
                        <details className="repository-history-more">
                          <summary>Ver {entry.observations.length - 1} observaciones adicionales devueltas</summary>
                          {entry.observations.slice(1).map((item) => <RepositoryObservation item={item} key={`${item.capture_id}:${item.snapshot_id}`} />)}
                        </details>
                      )}
                    </>
                  )}
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
