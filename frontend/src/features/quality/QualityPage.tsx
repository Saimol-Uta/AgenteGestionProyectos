import { useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Code2, FileSearch, GitBranch, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { analysisExecutionModeLabel, formatDate, findingVerificationLabel, formatPercentage, stageStatusLabel } from "../../api/formatters";
import {
  useDecideCodeFindingMutation, useFinding, usePolicies, useProject, useProjects, useQuality, useRunFindings, useRuns,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import type { CodeFindingDecision, DecideCodeFindingResponse, FindingDetail, FindingSummary } from "../../api/types";
import { PolicyResults } from "./PolicyResults";
import { useSession } from "../auth/SessionProvider";
import { useToast } from "../../components/ToastProvider";
import { codeSuggestionFor, defaultQualityBranch, qualityDisplayFingerprint, safeGitHubBranchUrl, validQualityBranch } from "./codeSuggestionHelper";
import "./quality-phase1.css";

function verificationTone(status: string): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (status === "confirmed") return "positive";
  if (status === "rejected") return "danger";
  if (status === "insufficient" || status === "pending") return "warning";
  if (status === "unknown") return "unknown";
  return "neutral";
}

function severityLabel(value: string) {
  const labels: Record<string, string> = { critical: "Crítica", high: "Alta", medium: "Media", low: "Baja" };
  return labels[value] ?? `Severidad del dominio: ${value}`;
}

function findingMeta(finding: FindingSummary) {
  return [
    finding.severity ? `Severidad: ${severityLabel(finding.severity)}` : "Severidad sin dato",
    finding.category ? `Categoría: ${finding.category}` : "Categoría sin dato",
    finding.rule_id ? `Regla: ${finding.rule_id}` : "Sin regla asociada",
  ];
}

function QualityRecommendation({ detail, projectId }: { detail: FindingDetail; projectId: string }) {
  const suggestion = useMemo(() => codeSuggestionFor(detail), [detail]);
  const [suggestedCode, setSuggestedCode] = useState(suggestion?.code ?? "");
  const [branchName, setBranchName] = useState(() => validQualityBranch(detail.branch_name ?? "")
    ? detail.branch_name! : defaultQualityBranch(detail.rule_id, detail.finding_id));
  const [backendDecision, setBackendDecision] = useState<DecideCodeFindingResponse | null>(null);
  const { session } = useSession();
  const { notify } = useToast();
  const mutation = useDecideCodeFindingMutation();
  const [editing, setEditing] = useState(false);
  const [reasonAction, setReasonAction] = useState<Exclude<CodeFindingDecision, "approve"> | null>(null);
  const [reason, setReason] = useState("");
  const [codeEdited, setCodeEdited] = useState(false);
  const requestKeys = useRef(new Map<string, string>());
  const submitting = useRef(false);
  const canDecide = Boolean(session?.authenticated && session.csrf_token
    && session.capabilities.includes("quality:decide"));
  const status = backendDecision?.decision_status ?? detail.decision_status ?? "pending";
  const pending = status === "pending";
  const branchValid = validQualityBranch(branchName);
  const hasBackendSuggestion = Boolean(suggestion?.code.trim() && suggestion.explanation.trim());
  const backendRecorded = status !== "pending";
  const resultMode = backendDecision?.execution_mode ?? detail.execution_mode ?? null;
  const resultBranch = backendDecision?.branch_name ?? detail.branch_name ?? null;
  const resultCommit = backendDecision?.commit_sha_applied ?? detail.commit_sha_applied ?? null;
  const resultBranchUrl = backendDecision?.branch_url ?? detail.branch_url ?? null;
  const liveBranchUrl = resultMode === "live" ? safeGitHubBranchUrl(resultBranchUrl) : null;
  const displayedCode = backendDecision?.suggested_code ?? suggestedCode;
  const displayedExplanation = backendDecision?.improvement_summary ?? suggestion?.explanation ?? "";
  const language = suggestion?.language ?? (/\.pyw?$/i.test(detail.file_path ?? "") ? "Python" : "TypeScript");
  const busy = mutation.isPending;

  async function submitBackend(action: CodeFindingDecision, reasonText = "") {
    if (!canDecide || !session?.csrf_token || !pending || busy || submitting.current) return;
    const payload = action === "approve"
      ? { decision: action, ...(codeEdited ? { suggested_code_override: suggestedCode } : {}), target_branch: branchName }
      : { decision: action, reason: reasonText.trim() };
    const identityKey = JSON.stringify([projectId, detail.run_id, detail.finding_id, payload]);
    const idempotencyKey = requestKeys.current.get(identityKey) ?? crypto.randomUUID();
    requestKeys.current.set(identityKey, idempotencyKey);
    submitting.current = true;
    try {
      const response = await mutation.mutateAsync({
        runId: detail.run_id,
        findingId: detail.finding_id,
        payload,
        csrfToken: session.csrf_token,
        idempotencyKey,
      });
      requestKeys.current.delete(identityKey);
      setBackendDecision(response);
      setSuggestedCode(response.suggested_code);
      setEditing(false);
      setReasonAction(null);
      notify(`Decisión del backend: ${response.decision_status} · modo ${response.execution_mode}.`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No se pudo registrar la decisión del hallazgo.", "error");
    } finally {
      submitting.current = false;
    }
  }

  function decisionLabel(value: string) {
    return ({ pending: "Pendiente", approved: "Aprobada", rejected: "Rechazada", changes_requested: "Cambios solicitados" } as Record<string, string>)[value] ?? value;
  }

  return (
    <section className="quality-recommendation" aria-label="Recomendación técnica de calidad de código">
      <h3>Recomendación técnica de calidad de código</h3>
      <ol className="quality-agent-trace" aria-label="Trazabilidad de la recomendación">
        <li><strong>1. Agente Ejecutor</strong><span>Detección y diagnóstico candidato</span></li>
        <li><strong>2. Agente Revisor / VerifierAgent</strong><span>{findingVerificationLabel(detail.verification_status)}</span></li>
        <li><strong>3. Agente Aprobador</strong><span>Decisión humana · {canDecide ? "backend" : "solo consulta"}</span></li>
      </ol>
      <div className="quality-example-note" role="note">
        <Pill tone="warning">{hasBackendSuggestion ? "Código sugerido por el backend · pendiente de pruebas" : "Sugerencia de código no disponible"}</Pill>
        <p>{displayedExplanation || "El backend no informó un ejemplo de código ni una explicación."}</p>
        <p>La decisión no confirma por sí sola el defecto ni modifica el resultado del Agente Revisor. No se ejecuta código en el navegador.</p>
      </div>
      <div className="quality-code-comparison">
        <section className="quality-code-panel quality-code-before" aria-label="Código actual observado">
          <h4>Código Actual</h4>
          {detail.code_excerpt ? <pre className="code-excerpt"><code>{detail.code_excerpt.content}</code></pre> : <p>Sin fragmento persistido: no se inventa código actual.</p>}
        </section>
        <section className="quality-code-panel quality-code-after" aria-label="Código sugerido por el backend">
          <h4>Código sugerido</h4><span className="caption">{language} · respuesta del backend</span>
          {editing ? <label className="quality-code-editor"><span>Código alternativo para el backend</span><textarea aria-label="Código sugerido editable" maxLength={100_000} rows={13} spellCheck={false} value={suggestedCode} onChange={(event) => { setCodeEdited(true); setSuggestedCode(event.target.value); }} /></label>
            : displayedCode ? <div className="code-excerpt quality-example-code"><code>{displayedCode}</code></div> : <p>El backend no entregó código sugerido.</p>}
          {canDecide && pending && hasBackendSuggestion && <button type="button" className="button button-secondary button-small" disabled={busy} onClick={() => setEditing((value) => !value)}>{editing ? "Ver sugerencia del backend" : "Editar código para enviar"}</button>}
        </section>
      </div>
      <div className="quality-human-decision">
        <h4><GitBranch aria-hidden="true" size={15} /> Decisión del gestor</h4>
        <p>Las decisiones se envían al servidor. La validación del archivo completo y cualquier creación de rama son responsabilidad del backend.</p>
        {!session?.authenticated && <p className="capability-note">Modo de consulta: inicia sesión para enviar una decisión protegida.</p>}
        {session?.authenticated && !canDecide && <p className="capability-note">El servidor no concedió quality:decide; las acciones de decisión están ocultas.</p>}
        {canDecide && pending && <>
          {!hasBackendSuggestion && <p className="capability-note">No se puede aprobar: el backend no entregó código y explicación completos.</p>}
          {hasBackendSuggestion && <label className="filter-control"><span>Rama de destino solicitada</span><input aria-label="Rama separada de verificación" value={branchName} maxLength={128} disabled={busy} aria-invalid={!branchValid} onChange={(event) => setBranchName(event.target.value)} /></label>}
          {!branchValid && hasBackendSuggestion && <p className="form-error" role="alert">Usa un nombre de rama válido, sin espacios, puntos consecutivos ni componentes vacíos.</p>}
          <div className="quality-decision-actions">
            <button type="button" className="button button-primary" disabled={!hasBackendSuggestion || !branchValid || busy} onClick={() => void submitBackend("approve")}><GitBranch aria-hidden="true" size={15} />{busy ? "Enviando decisión…" : "Aprobar y crear rama"}</button>
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReasonAction("reject")}>Rechazar sugerencia</button>
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReasonAction("request_changes")}>Solicitar cambios</button>
          </div>
          {reasonAction && <div className="quality-reject-form"><label className="filter-control"><span>{reasonAction === "reject" ? "Motivo del rechazo" : "Cambios solicitados"}</span><textarea aria-label={reasonAction === "reject" ? "Motivo del rechazo" : "Cambios solicitados"} maxLength={2000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label><p className="field-help">El motivo es obligatorio y se enviará al backend.</p><button type="button" className="button button-secondary" disabled={!reason.trim() || busy} onClick={() => void submitBackend(reasonAction, reason)}>{reasonAction === "reject" ? "Confirmar rechazo" : "Enviar solicitud de cambios"}</button></div>}
        </>}
        {mutation.isError && <p className="form-error" role="alert">{mutation.error.message}</p>}
        <div className="quality-decision-result" aria-live="polite" role="status">
          {backendRecorded && <><Pill tone={status === "approved" ? "positive" : status === "rejected" ? "danger" : status === "changes_requested" ? "warning" : "neutral"}>{decisionLabel(status)} · {analysisExecutionModeLabel(resultMode ?? undefined) ?? "Modo no informado"}</Pill>{resultBranch && <strong>{resultBranch}</strong>}{resultCommit && <span>Commit informado: {resultCommit}</span>}{status === "approved" && <p>La aprobación no confirma por sí sola que exista un cambio en el repositorio.</p>}{liveBranchUrl && <a className="text-link" href={liveBranchUrl} target="_blank" rel="noopener noreferrer">Ver rama informada por GitHub</a>}{resultMode === "live" && resultBranchUrl && !liveBranchUrl && <p>Referencia de la rama: <code>{resultBranchUrl}</code></p>}</>}
        </div>
      </div>
    </section>
  );
}

function FindingDetailDialog({
  runId,
  findingId,
  projectId,
  onClose,
}: {
  runId: string | null;
  findingId: string | null;
  projectId: string | null;
  onClose: () => void;
}) {
  const detailQuery = useFinding(runId, findingId);
  const open = Boolean(runId && findingId);
  const detail = detailQuery.data;
  const excerpt = detail?.code_excerpt;
  const excerptIsPartial = excerpt ? excerpt.is_partial || excerpt.evidence_scope !== "full_file" : false;

  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content finding-dialog" aria-describedby="finding-detail-description">
          <div className="drawer-header">
            <div>
              <p className="eyebrow">REVISIÓN DE CALIDAD</p>
              <Dialog.Title className="drawer-title">{detail ? `Hallazgo candidato · ${detail.finding_id}` : "Hallazgo candidato"}</Dialog.Title>
              <Dialog.Description id="finding-detail-description" className="drawer-task-id">La candidatura no significa que el defecto esté confirmado.</Dialog.Description>
            </div>
            <Dialog.Close asChild><button className="icon-button" type="button" aria-label="Cerrar detalle del hallazgo"><X aria-hidden="true" size={18} /></button></Dialog.Close>
          </div>
          <div className="drawer-content-scroll finding-detail-content">
            {detailQuery.isPending && <LoadingState label="Consultando detalle y evidencia segura…" />}
            {detailQuery.isError && <ErrorState title="No se pudo cargar el hallazgo" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
            {detail && (
              <>
                <div className="finding-status-row">
                  <Pill tone="accent">Hallazgo candidato</Pill>
                  <Pill tone={verificationTone(detail.verification_status)}>{findingVerificationLabel(detail.verification_status)}</Pill>
                  {detail.severity && <Pill tone={detail.severity === "critical" || detail.severity === "high" ? "danger" : "warning"}>Severidad: {severityLabel(detail.severity)} · <code>{detail.severity}</code></Pill>}
                </div>
                <dl className="finding-facts">
                  <div><dt>Regla</dt><dd>{detail.rule_id ?? "Sin dato"}</dd></div>
                  <div><dt>Categoría</dt><dd>{detail.category ?? "Sin dato"}</dd></div>
                  <div><dt>Archivo</dt><dd>{detail.file_path ?? "Sin dato"}</dd></div>
                  <div><dt>Línea reportada</dt><dd>{detail.line ?? "Sin dato"}</dd></div>
                  <div><dt>Commit analizado</dt><dd>{detail.commit_sha ?? "Sin dato"}</dd></div>
                  <div><dt>Confianza reportada</dt><dd>{formatPercentage(detail.confidence === null ? null : detail.confidence * 100)}</dd></div>
                </dl>
                <section className="finding-detail-section">
                  <h3>Afirmación técnica</h3>
                  <p>{detail.claim ?? "El backend no informó una afirmación."}</p>
                  {detail.suggested_fix && <><h4>Corrección sugerida</h4><p>{detail.suggested_fix}</p></>}
                </section>
                <section className="finding-detail-section">
                  <h3>Verificación persistida</h3>
                  {detail.verification ? (
                    <>
                      <Pill tone={verificationTone(detail.verification.verdict)}>{findingVerificationLabel(detail.verification.verdict)}</Pill>
                      <p>{detail.verification.reason}</p>
                      <p>Verificador: <code>{detail.verification.verified_by}</code></p>
                      <p>Evidencias citadas: {detail.verification.evidence_ids.length ? detail.verification.evidence_ids.join(", ") : "Sin referencias"}</p>
                    </>
                  ) : <p>Sin verificación persistida; el hallazgo sigue siendo candidato.</p>}
                </section>
                <section className="finding-detail-section">
                  <h3><FileSearch aria-hidden="true" size={15} /> Evidencia disponible</h3>
                  {detail.evidence.availability === "unknown" && <p>Disponibilidad de evidencia desconocida; no se infiere ausencia de prueba.</p>}
                  {detail.evidence.availability === "available" && detail.evidence.items.length === 0 && <p>La consulta no devolvió referencias de evidencia.</p>}
                  {detail.evidence.items.length > 0 && (
                    <ul className="finding-evidence-list">
                      {detail.evidence.items.map((item) => (
                        <li key={item.id}>
                          <strong>{item.category ?? "Evidencia"} · {item.source}</strong>
                          <p>{item.summary}</p>
                          <span>{item.repository ?? "Repositorio sin dato"} · {item.commit_sha ?? "Commit sin dato"}</span>
                          <span>{item.file_path ?? "Archivo sin dato"}{item.lines?.length ? ` · líneas ${item.lines.join(", ")}` : ""}</span>
                          <span>{formatDate(item.timestamp, true)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section className="finding-detail-section">
                  <h3><Code2 aria-hidden="true" size={15} /> Fragmento de código persistido</h3>
                  {!excerpt && <p>El backend no entregó un fragmento de código compatible con la evidencia guardada.</p>}
                  {excerpt && (
                    <>
                      <div className="excerpt-meta">
                        <code>{excerpt.file_path}</code>
                        <Pill tone={excerptIsPartial ? "warning" : "neutral"}>{excerptIsPartial ? "Fragmento parcial" : "Cobertura de archivo completo reportada"}</Pill>
                        {excerpt.redacted && <Pill tone="neutral">Contenido redactado</Pill>}
                      </div>
                      {excerptIsPartial && <p>El fragmento mostrado es parcial.</p>}
                      <p className="excerpt-lines">Líneas persistidas: {excerpt.lines.length ? excerpt.lines.join(", ") : "Sin dato"} · Alcance: <code>{excerpt.evidence_scope}</code></p>
                    </>
                  )}
                  {projectId && <QualityRecommendation key={`${projectId}:${detail.run_id}:${detail.finding_id}:${qualityDisplayFingerprint(JSON.stringify([detail.commit_sha, detail.code_excerpt, detail.suggested_code]))}`} detail={detail} projectId={projectId} />}
                </section>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function QualityPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const needsProjectLookup = Boolean(selectedProjectId && !listedProject);
  const selectedProjectQuery = useProject(selectedProjectId, needsProjectLookup);
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const requestedRunId = searchParams.get("run_id") || null;
  const findingsPage = Number.isInteger(Number(searchParams.get("findings_page"))) && Number(searchParams.get("findings_page")) > 0
    ? Number(searchParams.get("findings_page"))
    : 1;
  const runsQuery = useRuns(project?.id ?? null, 1, 25);
  const latestRunId = runsQuery.data?.items[0]?.run_id ?? null;
  const runId = requestedRunId ?? latestRunId;
  const qualityQuery = useQuality(project?.id ?? null);
  const policiesQuery = usePolicies(project?.id ?? null);
  const findingsQuery = useRunFindings(runId, findingsPage, 25);

  const findingDetails = useMemo(() => ({ runId, findingId: selectedFindingId }), [runId, selectedFindingId]);

  function closeFinding() {
    setSelectedFindingId(null);
  }

  function changeFindingsPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage <= 1) next.delete("findings_page");
    else next.set("findings_page", String(nextPage));
    setSearchParams(next, { replace: true });
  }

  if (projectsQuery.isPending) return <LoadingState label="Cargando proyectos del backend…" />;
  if (projectsQuery.isError) return <ErrorState title="No se pudieron cargar los proyectos" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  if (needsProjectLookup && selectedProjectQuery.isPending) return <LoadingState label="Consultando proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isError) return <ErrorState title="No se pudo cargar el proyecto activo" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />;
  if (!project) return <EmptyState title="No hay un proyecto disponible" message="El backend no reportó proyectos configurados o almacenados." />;

  const findingPages = findingsQuery.data ? Math.max(1, Math.ceil(findingsQuery.data.total / Math.max(1, findingsQuery.data.page_size))) : 1;

  return (
    <section className="quality-page content-section">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">REVISIÓN TÉCNICA · {project.name}</p>
          <h1>Centro de calidad de código</h1>
          <p className="page-subtitle">Hallazgos candidatos, verificaciones persistidas, evidencia segura y evaluaciones de políticas.</p>
        </div>
        {runId && <Link className="button button-secondary" to={`/analysis?run_id=${encodeURIComponent(runId)}`}>Ver ejecución <ArrowRight aria-hidden="true" size={14} /></Link>}
      </div>

      <section className="panel quality-summary-panel" aria-labelledby="quality-summary-heading">
        <div className="section-heading">
          <div><p className="eyebrow">ÚLTIMO RESUMEN DEL PROYECTO</p><h2 id="quality-summary-heading">Calidad y CI</h2><p className="section-subtitle">El endpoint informa el registro más reciente del proyecto; los hallazgos pueden pertenecer a la ejecución seleccionada.</p></div>
          {qualityQuery.data && <Pill tone={qualityQuery.data.availability === "available" ? "accent" : "unknown"}>{qualityQuery.data.availability === "available" ? "Resultados disponibles" : "Disponibilidad desconocida"}</Pill>}
        </div>
        {qualityQuery.isPending && <LoadingState label="Consultando resumen de calidad…" />}
        {qualityQuery.isError && <ErrorState title="No se pudo cargar el resumen de calidad" message={qualityQuery.error.message} onRetry={() => void qualityQuery.refetch()} />}
        {qualityQuery.data && (
          <>
            <p className="quality-run-ref">Ejecución del resumen: <code>{qualityQuery.data.run_id ?? "Sin dato"}</code> · Estado de Quality: {qualityQuery.data.stage_status ? stageStatusLabel(qualityQuery.data.stage_status) : "Desconocido"}{qualityQuery.data.stage_status && <> · <code>{qualityQuery.data.stage_status}</code></>}</p>
            {qualityQuery.data.finding_counts && (
              <dl className="finding-count-grid">
                <div><dt>Candidatos</dt><dd>{qualityQuery.data.finding_counts.candidate}</dd></div>
                <div><dt>Confirmados por verificación</dt><dd>{qualityQuery.data.finding_counts.confirmed}</dd></div>
                <div><dt>Rechazados</dt><dd>{qualityQuery.data.finding_counts.rejected}</dd></div>
                <div><dt>Evidencia insuficiente</dt><dd>{qualityQuery.data.finding_counts.insufficient}</dd></div>
                <div><dt>Pendientes</dt><dd>{qualityQuery.data.finding_counts.pending}</dd></div>
                <div><dt>Desconocidos</dt><dd>{qualityQuery.data.finding_counts.unknown}</dd></div>
              </dl>
            )}
            {!qualityQuery.data.finding_counts && <p className="capability-note">El backend no dispone de conteos de hallazgos para este resumen.</p>}
            <div className="ci-summary">
              <h3>Integración continua y cobertura</h3>
              {!qualityQuery.data.ci && <p>Resultados de CI y cobertura no disponibles en el registro del backend.</p>}
              {qualityQuery.data.ci && (
                <dl className="finding-facts">
                  <div><dt>Workflow</dt><dd>{qualityQuery.data.ci.workflow_name}</dd></div>
                  <div><dt>Commit</dt><dd>{qualityQuery.data.ci.commit_sha}</dd></div>
                  <div><dt>Estado</dt><dd>{qualityQuery.data.ci.status}</dd></div>
                  <div><dt>Pruebas totales</dt><dd>{qualityQuery.data.ci.total_tests ?? "Sin dato"}</dd></div>
                  <div><dt>Pruebas fallidas</dt><dd>{qualityQuery.data.ci.failed_tests ?? "Sin dato"}</dd></div>
                  <div><dt>Cobertura</dt><dd>{formatPercentage(qualityQuery.data.ci.coverage_percentage)}</dd></div>
                  <div><dt>Fecha</dt><dd>{formatDate(qualityQuery.data.ci.timestamp, true)}</dd></div>
                </dl>
              )}
            </div>
          </>
        )}
      </section>

      <section className="panel policy-results-panel" aria-labelledby="policies-heading">
        <div className="section-heading">
          <div><p className="eyebrow">EVALUACIÓN PERSISTIDA</p><h2 id="policies-heading">Resultados de políticas</h2><p className="section-subtitle">Cada fila proviene de una evaluación real; no se sintetizan reglas ausentes.</p></div>
          {policiesQuery.data && <Pill tone={policiesQuery.data.availability === "available" ? "accent" : "unknown"}>{policiesQuery.data.availability === "available" ? "Evaluación disponible" : "Desconocido"}</Pill>}
        </div>
        {policiesQuery.isPending && <LoadingState label="Consultando evaluaciones de políticas…" />}
        {policiesQuery.isError && <ErrorState title="No se pudieron consultar las políticas" message={policiesQuery.error.message} onRetry={() => void policiesQuery.refetch()} />}
        {policiesQuery.data && <>
          <p className="quality-run-ref">Ejecución: <code>{policiesQuery.data.run_id ?? "Sin dato"}</code></p>
          <PolicyResults availability={policiesQuery.data.availability} items={policiesQuery.data.items} />
        </>}
      </section>

      <section className="panel findings-panel" aria-labelledby="findings-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{runId ? `EJECUCIÓN · ${runId}` : "SIN EJECUCIÓN SELECCIONADA"}</p>
            <h2 id="findings-heading">Hallazgos candidatos</h2>
            <p className="section-subtitle">La candidatura y su estado de verificación son campos independientes.</p>
          </div>
          {findingsQuery.data && <Pill tone="neutral">{findingsQuery.data.total} registros</Pill>}
        </div>
        {!runId && runsQuery.isPending && <LoadingState label="Buscando la ejecución más reciente…" />}
        {!runId && runsQuery.isError && <ErrorState title="No se pudieron consultar las ejecuciones" message={runsQuery.error.message} onRetry={() => void runsQuery.refetch()} />}
        {!runId && runsQuery.data?.items.length === 0 && <EmptyState title="Sin ejecuciones" message="No se pueden listar hallazgos sin una ejecución persistida." />}
        {findingsQuery.isPending && runId && <LoadingState label="Consultando hallazgos de la ejecución…" />}
        {findingsQuery.isError && <ErrorState title="No se pudieron cargar los hallazgos" message={findingsQuery.error.message} onRetry={() => void findingsQuery.refetch()} />}
        {findingsQuery.data && (findingsQuery.data.items.length ? (
          <>
            <ul className="finding-list">
              {findingsQuery.data.items.map((finding) => (
                <li key={finding.finding_id}>
                  <button type="button" className="finding-row" aria-label={`Revisar hallazgo ${finding.finding_id}`} onClick={() => setSelectedFindingId(finding.finding_id)}>
                    <span className="finding-row-main">
                      <strong>{finding.file_path ?? finding.finding_id}</strong>
                      <span><Pill tone="accent">Hallazgo candidato</Pill><Pill tone={verificationTone(finding.verification_status)}>{findingVerificationLabel(finding.verification_status)}</Pill></span>
                      <small>{findingMeta(finding).join(" · ")} · Verificación: <code>{finding.verification_status}</code></small>
                    </span>
                    <span className="finding-row-location"><code>{finding.file_path ?? "Archivo sin dato"}{finding.line === null ? "" : `:${finding.line}`}</code><ArrowRight aria-hidden="true" size={15} /></span>
                  </button>
                </li>
              ))}
            </ul>
            {findingPages > 1 && (
              <div className="pagination-row">
                <span>Página {findingsPage} de {findingPages}</span>
                <div>
                  <button className="button button-secondary button-small" type="button" disabled={findingsPage <= 1} onClick={() => changeFindingsPage(findingsPage - 1)}>Anterior</button>
                  <button className="button button-secondary button-small" type="button" disabled={findingsPage >= findingPages} onClick={() => changeFindingsPage(findingsPage + 1)}>Siguiente</button>
                </div>
              </div>
            )}
          </>
        ) : <EmptyState title="Sin hallazgos para esta ejecución" message="La API no devolvió candidatos persistidos para la ejecución seleccionada." />)}
      </section>

      <div className="quality-security-note"><Pill tone="neutral">Decisión del gestor</Pill><span>La pantalla presenta hallazgos candidatos y evidencia acotada. Las decisiones se aplican en la nube o localmente; un resultado local no crea cambios en el repositorio.</span></div>
      <FindingDetailDialog runId={findingDetails.runId} findingId={findingDetails.findingId} projectId={project.id} onClose={closeFinding} />
    </section>
  );
}
