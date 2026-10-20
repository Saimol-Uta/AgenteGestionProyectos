import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Code2, FileSearch, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDate, findingVerificationLabel, formatPercentage, stageStatusLabel } from "../../api/formatters";
import {
  useDecideCodeFindingMutation, useEvaluateFindingApprovalMutation, useFinding, usePolicies, useProject, useProjects, useQuality, useRepositoryResults, useRun, useRunFindings, useRuns,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import type {
  ApprovalEligibilityView,
  CodeFindingDecision,
  DecideCodeFindingResponse,
  FindingDetail,
  FindingPublicationView,
  FindingSummary,
  PatchReviewView,
} from "../../api/types";
import { PolicyResults } from "./PolicyResults";
import { useSession } from "../auth/SessionProvider";
import { useToast } from "../../components/ToastProvider";
import { codeSuggestionFor, qualityDisplayFingerprint, safeGitHubBranchUrl, safeGitHubPullRequestUrl } from "./codeSuggestionHelper";
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

function findingRepositoryLabel(finding: FindingSummary) {
  if (finding.repository_owner && finding.repository_name) return `${finding.repository_owner}/${finding.repository_name}`;
  return finding.repository_id ?? "Origen de repositorio desconocido";
}

function reviewVerdictLabel(verdict: PatchReviewView["verdict"]): string {
  return ({
    favorable: "Favorable",
    unfavorable: "Desfavorable",
    insufficient: "Insuficiente",
  } as Record<PatchReviewView["verdict"], string>)[verdict] ?? "Desconocido";
}

function reviewVerdictTone(verdict: PatchReviewView["verdict"]): "positive" | "danger" | "warning" {
  if (verdict === "favorable") return "positive";
  if (verdict === "unfavorable") return "danger";
  return "warning";
}

interface FindingSelection {
  projectId: string;
  runId: string;
  repositoryId: string | null;
  filterRepositoryId: string | null;
  findingId: string;
}

/** The exact patch review, as the backend recorded it for this run. */
function PatchReviewBlock({ review, edited }: { review: PatchReviewView | null; edited: boolean }) {
  if (!review) {
    return (
      <section className="finding-detail-section" aria-label="Revisión del parche exacto">
        <h4>Revisión independiente del parche exacto</h4>
        <p className="capability-note">El backend no registró una revisión que cubra el parche exacto de este código.</p>
      </section>
    );
  }
  return (
    <section className="finding-detail-section" aria-label="Revisión del parche exacto">
      <h4>Revisión independiente del parche exacto</h4>
      {edited && (
        <p className="quality-approval-block-reason" role="note">
          Esta revisión cubre el código original del backend; no cubre el código editado.
        </p>
      )}
      <div className="quality-example-note">
        <div><Pill tone={reviewVerdictTone(review.verdict)}>Veredicto: {reviewVerdictLabel(review.verdict)}</Pill></div>
        <p>{review.reason}</p>
      </div>
      <dl className="finding-facts">
        <div><dt>Revisión</dt><dd><code>{review.review_id}</code></dd></div>
        <div><dt>Parche exacto (SHA-256)</dt><dd><code>{review.patch_sha256}</code></dd></div>
        <div><dt>Fuente original (SHA-256)</dt><dd><code>{review.original_sha256}</code></dd></div>
        <div><dt>Revisado por</dt><dd><code>{review.reviewed_by}</code></dd></div>
        <div><dt>Ejecución revisada</dt><dd><code>{review.run_id}</code></dd></div>
        <div><dt>Repositorio y commit</dt><dd><code>{review.repository}@{review.commit_sha}</code></dd></div>
        <div><dt>Archivo</dt><dd><code>{review.file_path}</code></dd></div>
        <div><dt>Revisado el</dt><dd>{review.reviewed_at ? formatDate(review.reviewed_at, true) : "Sin dato"}</dd></div>
      </dl>
    </section>
  );
}

/** The eligibility, exactly as the backend answered it. */
function EligibilityBlock({ eligibility }: { eligibility: ApprovalEligibilityView | null }) {
  if (!eligibility) {
    return (
      <section className="finding-detail-section" aria-label="Elegibilidad de la aprobación">
        <h4>Elegibilidad de la aprobación</h4>
        <p className="capability-note">El backend no evaluó la elegibilidad de este hallazgo.</p>
      </section>
    );
  }
  return (
    <section className="finding-detail-section" aria-label="Elegibilidad de la aprobación">
      <h4>Elegibilidad de la aprobación</h4>
      <div className="quality-example-note">
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center" }}>
          <Pill tone={eligibility.eligible ? "positive" : "danger"}>
            {eligibility.eligible ? "El backend autoriza la aprobación" : "El backend no autoriza la aprobación"}
          </Pill>
          <Pill tone={eligibility.defect_confirmed ? "positive" : "warning"}>
            {eligibility.defect_confirmed ? "Defecto confirmado" : "Defecto sin confirmar"}
          </Pill>
        </div>
        {eligibility.blocked_reason && (
          <p className="quality-approval-block-reason" role="note">
            {eligibility.blocked_reason_message ?? eligibility.blocked_reason}
            {" "}<code>{eligibility.blocked_reason}</code>
          </p>
        )}
      </div>
      <dl className="finding-facts">
        <div><dt>Parche evaluado (SHA-256)</dt><dd><code>{eligibility.patch_sha256 ?? "Sin parche identificable"}</code></dd></div>
        <div><dt>Revisión que lo cubre</dt><dd><code>{eligibility.review_id ?? "Ninguna"}</code></dd></div>
      </dl>
    </section>
  );
}

/** The publication receipt, or the partial failure that has to be retried. */
function PublicationBlock({
  publication,
  onRetry,
  retrying,
}: {
  publication: FindingPublicationView | null;
  onRetry: () => void;
  retrying: boolean;
}) {
  if (!publication || publication.publication_status === "not_requested") {
    return (
      <section className="quality-decision-result" aria-label="Publicación del pull request">
        <p>El backend no registró ninguna publicación para este hallazgo.</p>
      </section>
    );
  }
  if (publication.publication_status === "partial") {
    return (
      <section className="quality-decision-result" aria-label="Publicación del pull request">
        <Pill tone="warning">Publicación parcial</Pill>
        <p>
          La rama y el commit se conservaron, pero la creación del pull request falló.
          {publication.publication_error_message ? ` ${publication.publication_error_message}` : ""}
        </p>
        {publication.publication_error_code && <span>Motivo: <code>{publication.publication_error_code}</code></span>}
        <button type="button" className="button button-secondary" disabled={retrying} onClick={onRetry}>
          {retrying ? "Reintentando la publicación…" : "Reintentar la publicación"}
        </button>
        <p className="field-help">El reintento reutiliza la misma clave de idempotencia: no requiere una segunda aprobación.</p>
      </section>
    );
  }
  const simulated = publication.pull_request_state === "simulated" || publication.pull_request_simulated === true;
  const realUrl = safeGitHubPullRequestUrl(publication.pull_request_url);
  return (
    <section className="quality-decision-result" aria-label="Publicación del pull request">
      <Pill tone={simulated ? "warning" : "positive"}>
        {simulated ? "Pull request simulado" : "Pull request abierto"}
      </Pill>
      <span>Pull request #{publication.pull_request_number ?? "sin número"}</span>
      {publication.pull_request_url && (
        <span>Referencia: <code>{publication.pull_request_url}</code></span>
      )}
      {simulated && (
        <p>
          La publicación es una simulación de sandbox: no existe un pull request real y su dirección no se puede abrir.
        </p>
      )}
      {realUrl && <a className="text-link" href={realUrl} target="_blank" rel="noopener noreferrer">Ver el pull request informado por GitHub</a>}
    </section>
  );
}

function QualityRecommendation({ detail, projectId, repositoryId }: { detail: FindingDetail; projectId: string; repositoryId: string | null }) {
  const suggestion = useMemo(() => codeSuggestionFor(detail), [detail]);
  const [backendDecision, setBackendDecision] = useState<DecideCodeFindingResponse | null>(null);
  const { session } = useSession();
  const { notify } = useToast();
  const mutation = useDecideCodeFindingMutation();
  const approvalMutation = useEvaluateFindingApprovalMutation();
  const [reasonAction, setReasonAction] = useState<Exclude<CodeFindingDecision, "approve"> | null>(null);
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [editedCode, setEditedCode] = useState<string | null>(null);
  const [editedEligibility, setEditedEligibility] = useState<ApprovalEligibilityView | null>(null);
  const requestKeys = useRef(new Map<string, string>());
  const approveKey = useRef<string | null>(null);
  const submitting = useRef(false);
  const canDecide = Boolean(session?.authenticated && session.csrf_token
    && session.capabilities.includes("quality:decide"));
  const reportedDecisionStatus = backendDecision?.decision_status ?? detail.decision_status;
  const status = ["pending", "approved", "rejected", "changes_requested"].includes(String(reportedDecisionStatus))
    ? reportedDecisionStatus! : "unknown";
  const decisionStatusKnown = status !== "unknown";
  const pending = status === "pending";
  const hasBackendSuggestion = Boolean(suggestion?.code.trim() && suggestion.explanation.trim());
  const backendRecorded = decisionStatusKnown && (status !== "pending" || backendDecision !== null);
  const resultMode = backendDecision?.execution_mode ?? detail.execution_mode ?? null;
  const resultBranch = backendDecision?.branch_name ?? detail.branch_name ?? null;
  const resultCommit = backendDecision?.commit_sha_applied ?? detail.commit_sha_applied ?? null;
  const resultBranchUrl = backendDecision?.branch_url ?? detail.branch_url ?? null;
  const liveBranchUrl = resultMode === "live" ? safeGitHubBranchUrl(resultBranchUrl) : null;
  const displayedCode = backendDecision?.suggested_code ?? suggestion?.code ?? "";
  const displayedExplanation = backendDecision?.improvement_summary ?? suggestion?.explanation ?? "";
  const language = suggestion?.language ?? (/\.pyw?$/i.test(detail.file_path ?? "") ? "Python" : "TypeScript");
  const busy = mutation.isPending;

  // An edited correction is a different patch: the eligibility that was shown
  // for the original code does not apply to it, so the backend is asked again.
  const storedCode = detail.suggested_code ?? "";
  const codeEdited = editing && editedCode !== null && editedCode !== storedCode;
  const effectiveEligibility = codeEdited
    ? editedEligibility
    : detail.approval_eligibility ?? null;
  const eligible = effectiveEligibility?.eligible === true;
  // The review that covers the evaluated patch: an edited correction has its
  // own answer, and the review of the original patch never covers it.
  const effectiveReview = codeEdited
    ? editedEligibility?.review ?? null
    : detail.patch_review ?? null;
  // The reason comes from the backend whenever it evaluated the action. Only a
  // backend that did not answer at all keeps the previous interface message.
  const approvalBlockedMessage = codeEdited
    ? "Aprobación bloqueada: el código editado es otro parche y no tiene revisión vigente."
    : effectiveEligibility?.blocked_reason_message
      ?? (effectiveEligibility?.blocked_reason
        ? `Aprobación bloqueada: el backend la rechazó con el motivo ${effectiveEligibility.blocked_reason}.`
        : "Aprobación bloqueada: falta revisión independiente del parche exacto.");

  useEffect(() => {
    if (!codeEdited || !canDecide || !session?.csrf_token) {
      setEditedEligibility(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      approvalMutation.mutate(
        {
          runId: detail.run_id,
          findingId: detail.finding_id,
          payload: { suggested_code_override: editedCode ?? undefined },
          csrfToken: session.csrf_token!,
        },
        {
          onSuccess: (result) => { if (!cancelled) setEditedEligibility(result.approval_eligibility); },
          onError: () => { if (!cancelled) setEditedEligibility(null); },
        },
      );
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [codeEdited, editedCode, detail.run_id, detail.finding_id, canDecide, session?.csrf_token]);

  async function submitApproval(retry = false) {
    if (!canDecide || !session?.csrf_token || busy || submitting.current) return;
    // A retry of a partial publication is not a new human approval: it
    // reuses the same idempotency key so the backend recovers the
    // publication instead of creating a second one.
    if (!retry && !pending) return;
    if (!retry && !eligible) return;
    const payload = codeEdited
      ? { decision: "approve" as const, suggested_code_override: editedCode ?? undefined }
      : { decision: "approve" as const };
    if (approveKey.current === null) approveKey.current = crypto.randomUUID();
    submitting.current = true;
    try {
      const response = await mutation.mutateAsync({
        runId: detail.run_id,
        findingId: detail.finding_id,
        payload,
        csrfToken: session.csrf_token,
        idempotencyKey: approveKey.current,
        projectId,
        repositoryId,
      });
      setBackendDecision(response);
      notify(`Decisión del backend: ${response.decision_status}.`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No se pudo registrar la decisión del hallazgo.", "error");
    } finally {
      submitting.current = false;
    }
  }

  async function submitBackend(action: Exclude<CodeFindingDecision, "approve">, reasonText: string) {
    if (!canDecide || !session?.csrf_token || !pending || busy || submitting.current || !reasonText.trim()) return;
    const payload = { decision: action, reason: reasonText.trim() };
    const identityKey = JSON.stringify([projectId, repositoryId, detail.run_id, detail.finding_id, payload]);
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
        projectId,
        repositoryId,
      });
      requestKeys.current.delete(identityKey);
      setBackendDecision(response);
      setReasonAction(null);
      notify(`Decisión del backend: ${response.decision_status}.`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No se pudo registrar la decisión del hallazgo.", "error");
    } finally {
      submitting.current = false;
    }
  }

  function decisionLabel(value: string) {
    return ({ pending: "Pendiente", approved: "Aprobada", rejected: "Rechazada", changes_requested: "Cambios solicitados", unknown: "Desconocida" } as Record<string, string>)[value] ?? "Desconocida";
  }

  const publication = backendDecision
    ? {
      publication_status: backendDecision.publication_status ?? "not_requested",
      publication_error_code: backendDecision.publication_error_code ?? null,
      publication_error_message: backendDecision.publication_error_message ?? null,
      pull_request_number: backendDecision.pull_request_number ?? null,
      pull_request_url: backendDecision.pull_request_url ?? null,
      pull_request_state: backendDecision.pull_request_state ?? null,
      pull_request_simulated: backendDecision.pull_request_simulated ?? null,
      patch_review_id: backendDecision.patch_review_id ?? null,
      patch_sha256: backendDecision.patch_sha256 ?? null,
    } as FindingPublicationView
    : detail.publication ?? null;

  return (
    <section className="quality-recommendation" aria-label="Recomendación técnica de calidad de código">
      <h3>Recomendación técnica de Quality</h3>
      <ol className="quality-agent-trace" aria-label="Etapas de la recomendación">
        <li><strong>1. Detectado</strong><span>Hallazgo candidato en {detail.file_path ?? "archivo sin dato"}</span></li>
        <li><strong>2. Defecto confirmado</strong><span>{findingVerificationLabel(detail.verification_status)}</span></li>
        <li><strong>3. Revisión del parche exacto</strong><span>{effectiveReview ? reviewVerdictLabel(effectiveReview.verdict) : codeEdited ? "Sin revisión para el código editado" : "Sin revisión vigente"}</span></li>
        <li><strong>4. Elegibilidad</strong><span>{eligible ? "El backend autoriza la aprobación" : "El backend no autoriza la aprobación"}</span></li>
        <li><strong>5. Decisión de la gestora</strong><span>{decisionStatusKnown ? decisionLabel(status) : "Desconocida"}</span></li>
        <li><strong>6. Rama, commit y pull request</strong><span>{resultBranch ? `${resultBranch} · ${resultCommit ?? "sin commit"}` : "Todavía sin rama"}</span></li>
      </ol>
      <div className="quality-example-note" role="note">
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center" }}>
          <Pill tone="warning">{hasBackendSuggestion ? "Recomendación de código del backend · no validada" : "Sugerencia de código no disponible"}</Pill>
          {detail.suggested_code_validation_status === "not_validated" && <Pill tone="warning">Código sugerido no validado</Pill>}
        </div>
        <p>{displayedExplanation || "El backend no informó un ejemplo de código ni una explicación."}</p>
        <p>La verificación del defecto comprueba la afirmación técnica en su evidencia; no es una revisión independiente del código correctivo y no valida que el parche compile o sea correcto en el archivo completo.</p>
      </div>
      <div className="quality-code-comparison">
        <section className="quality-code-panel quality-code-before" aria-label="Código actual observado">
          <h4>Código actual observado</h4>
          {detail.code_excerpt ? <pre className="code-excerpt"><code>{detail.code_excerpt.content}</code></pre> : <p>Sin fragmento persistido: no se inventa código actual.</p>}
        </section>
        <section className="quality-code-panel quality-code-after" aria-label="Código sugerido por el backend">
          <h4>Cambio recomendado</h4><span className="caption">{language} · respuesta del backend</span>
          {displayedCode ? <div className="code-excerpt quality-example-code"><code>{displayedCode}</code></div> : <p>El backend no entregó código sugerido.</p>}
        </section>
      </div>
      <PatchReviewBlock review={detail.patch_review ?? null} edited={codeEdited} />
      <EligibilityBlock eligibility={effectiveEligibility} />
      <div className="quality-human-decision">
        <h4>Decisión de la gestora</h4>
        <p>La aprobación solo se habilita cuando el backend declara la acción elegible y la sesión de la gestora lo permite. Una revisión favorable cubre un parche exacto: si editas el código, esa revisión deja de aplicar y el backend vuelve a evaluar.</p>
        {!session?.authenticated && <p className="capability-note">Modo de consulta: inicia sesión para enviar una decisión protegida.</p>}
        {session?.authenticated && !canDecide && <p className="capability-note">El servidor no concedió quality:decide; las decisiones protegidas están ocultas.</p>}
        {!decisionStatusKnown && <p className="capability-note" role="status">El estado de decisión es desconocido; no se ofrecen decisiones protegidas.</p>}
        {canDecide && pending && <>
          {eligible ? (
            <button type="button" className="button button-primary" disabled={busy || submitting.current} onClick={() => void submitApproval()}>
              Aprobar y crear rama
            </button>
          ) : (            <>
              <button type="button" className="button button-primary quality-approval-blocked" disabled aria-describedby="quality-approval-block-reason">Aprobar y crear rama</button>
              <p id="quality-approval-block-reason" className="quality-approval-block-reason" role="note">
                {approvalBlockedMessage}
              </p>
            </>
          )}
          {editing && (
            <div className="quality-reject-form">
              <label className="filter-control">
                <span>Corrección editada por la gestora</span>
                <textarea
                  aria-label="Corrección editada por la gestora"
                  maxLength={65536}
                  rows={6}
                  value={editedCode ?? ""}
                  onChange={(event) => setEditedCode(event.target.value)}
                />
              </label>
              <p className="field-help">El backend evalúa este código exacto. La revisión mostrada corresponde al código original y no se extiende a la edición.</p>
              <button type="button" className="button button-secondary" disabled={busy} onClick={() => { setEditing(false); setEditedCode(null); }}>Restablecer el código del backend</button>
            </div>
          )}
          {!editing && (
            <div className="quality-reject-form">
              <button type="button" className="button button-secondary" disabled={busy} onClick={() => { setEditing(true); setEditedCode(storedCode); }}>Editar el código sugerido</button>
              <p className="field-help">Al editar el código, el backend vuelve a evaluar la elegibilidad de ese parche exacto.</p>
            </div>
          )}
          <div className="quality-decision-actions">
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReasonAction("reject")}>Rechazar sugerencia</button>
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReasonAction("request_changes")}>Solicitar cambios</button>
          </div>
          {reasonAction && <div className="quality-reject-form">
            <label className="filter-control"><span>{reasonAction === "reject" ? "Motivo del rechazo" : "Cambios solicitados"}</span><textarea aria-label={reasonAction === "reject" ? "Motivo del rechazo" : "Cambios solicitados"} maxLength={2000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
            <p className="field-help">El motivo es obligatorio y se enviará al backend.</p>
            <button type="button" className="button button-secondary" disabled={!reason.trim() || busy} onClick={() => void submitBackend(reasonAction, reason)}>{reasonAction === "reject" ? "Confirmar rechazo" : "Enviar solicitud de cambios"}</button>
          </div>}
        </>}
        {mutation.isError && <p className="form-error" role="alert">{mutation.error.message}</p>}
        {approvalMutation.isError && codeEdited && <p className="form-error" role="alert">{approvalMutation.error.message}</p>}
        <div className="quality-decision-result" aria-live="polite" role="status">
          {backendRecorded && <>
            <Pill tone={status === "approved" ? "positive" : status === "rejected" ? "danger" : status === "changes_requested" ? "warning" : "neutral"}>{decisionLabel(status)}</Pill>
            {resultBranch && <strong>{resultBranch}</strong>}
            {resultCommit && <span>Commit informado: {resultCommit}</span>}
            {status === "approved" && !publication && <p>La aprobación quedó registrada; el backend todavía no informó una publicación.</p>}
            {liveBranchUrl && <a className="text-link" href={liveBranchUrl} target="_blank" rel="noopener noreferrer">Ver rama informada por GitHub</a>}
            {resultMode === "live" && resultBranchUrl && !liveBranchUrl && <p>Referencia de la rama: <code>{resultBranchUrl}</code></p>}
          </>}
        </div>
        {backendRecorded && status === "approved" && (
          <PublicationBlock
            publication={publication}
            retrying={busy}
            onRetry={() => void submitApproval(true)}
          />
        )}
      </div>
    </section>
  );
}

function FindingDetailDialog({
  runId,
  findingId,
  projectId,
  repositoryId,
  filterRepositoryId,
  onClose,
}: {
  runId: string | null;
  findingId: string | null;
  projectId: string | null;
  repositoryId: string | null;
  filterRepositoryId: string | null;
  onClose: () => void;
}) {
  // The filter ID partitions the read cache only; decision handling receives the finding's own optional identity below.
  const queryRepositoryScopeId = repositoryId ?? filterRepositoryId;
  const detailQuery = useFinding(runId, findingId, projectId, queryRepositoryScopeId);
  const open = Boolean(runId && findingId);
  const detail = detailQuery.data;
  const expectedRepositoryIds = [repositoryId, filterRepositoryId].filter((id): id is string => id !== null);
  const detailMatchesScope = Boolean(detail
    && detail.run_id === runId
    && detail.finding_id === findingId
    && expectedRepositoryIds.every((expectedRepositoryId) => detail.repository_id === expectedRepositoryId));
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
              <Dialog.Title className="drawer-title">{detailMatchesScope && detail ? `Hallazgo candidato · ${detail.finding_id}` : "Hallazgo candidato"}</Dialog.Title>
              <Dialog.Description id="finding-detail-description" className="drawer-task-id">La candidatura no significa que el defecto esté confirmado.</Dialog.Description>
            </div>
            <Dialog.Close asChild><button className="icon-button" type="button" aria-label="Cerrar detalle del hallazgo"><X aria-hidden="true" size={18} /></button></Dialog.Close>
          </div>
          <div className="drawer-content-scroll finding-detail-content">
            {detailQuery.isPending && <LoadingState label="Consultando detalle y evidencia segura…" />}
            {detailQuery.isError && <ErrorState title="No se pudo cargar el hallazgo" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
            {detail && !detailMatchesScope && <ErrorState title="La identidad del hallazgo no coincide" message="El detalle no coincide con el proyecto, ejecución o repositorio consultado; no se habilitan decisiones." />}
            {detail && detailMatchesScope && (
              <>
                <div className="finding-status-row">
                  <Pill tone="accent">Hallazgo candidato</Pill>
                  <Pill tone={verificationTone(detail.verification_status)}>{findingVerificationLabel(detail.verification_status)}</Pill>
                  {detail.severity && <Pill tone={detail.severity === "critical" || detail.severity === "high" ? "danger" : "warning"}>Severidad: {severityLabel(detail.severity)} · <code>{detail.severity}</code></Pill>}
                  {detail.suggested_code_validation_status === "not_validated" && (
                    <Pill tone="warning">Código sugerido no validado</Pill>
                  )}
                </div>
                <dl className="finding-facts">
                  <div><dt>Repositorio de origen</dt><dd>{detail.repository_owner && detail.repository_name ? `${detail.repository_owner}/${detail.repository_name}` : detail.repository_id ?? "Identidad desconocida"}</dd></div>
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
                  ) : (
                    <>
                      <Pill tone={verificationTone(detail.verification_status)}>{findingVerificationLabel(detail.verification_status)}</Pill>
                      <p>{detail.verification_reason ?? "Sin verificación persistida; el hallazgo sigue siendo candidato."}</p>
                    </>
                  )}
                  {detail.suggested_code_validation_status && (
                    <div style={{ marginTop: "8px" }}>
                      <Pill tone="warning">Estado del parche propuesto: <code>{detail.suggested_code_validation_status}</code></Pill>
                      <p className="field-help" style={{ marginTop: "4px" }}>
                        La verificación independiente evalúa la existencia del defecto técnico en el archivo analizado, pero no valida el código de corrección sugerido.
                      </p>
                    </div>
                  )}
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
                  {projectId && <QualityRecommendation key={`${projectId}:${repositoryId ?? "unfiltered"}:${detail.run_id}:${detail.finding_id}:${qualityDisplayFingerprint(JSON.stringify([detail.commit_sha, detail.code_excerpt, detail.suggested_code]))}`} detail={detail} projectId={projectId} repositoryId={repositoryId} />}
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
  const [selectedFinding, setSelectedFinding] = useState<FindingSelection | null>(null);
  const [observedRepositoryOptions, setObservedRepositoryOptions] = useState<{ projectId: string | null; options: Record<string, string> }>({ projectId: null, options: {} });
  const { selectedProjectId } = useWorkspace();
  const { session } = useSession();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const needsProjectLookup = Boolean(selectedProjectId && !listedProject);
  const selectedProjectQuery = useProject(selectedProjectId, needsProjectLookup);
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const requestedRunId = searchParams.get("run_id") || null;
  const requestedRepositoryId = searchParams.get("repository_id") ?? "";
  const findingsPage = Number.isInteger(Number(searchParams.get("findings_page"))) && Number(searchParams.get("findings_page")) > 0
    ? Number(searchParams.get("findings_page"))
    : 1;
  const runsQuery = useRuns(project?.id ?? null, 1, 25);
  const latestRunId = runsQuery.data?.items[0]?.run_id ?? null;
  const runId = requestedRunId ?? latestRunId;
  const selectedRunQuery = useRun(runId, project?.id ?? null);
  const runMatchesProject = Boolean(project && selectedRunQuery.data?.project_id === project.id);
  const canReadRepositoryHistory = Boolean(session?.authenticated && session.capabilities.includes("audit:read"));
  const repositoryResultsQuery = useRepositoryResults(project?.id ?? null, {}, 1, 100, runId ?? undefined, canReadRepositoryHistory);
  const qualityQuery = useQuality(project?.id ?? null);
  const policiesQuery = usePolicies(project?.id ?? null, runId);
  const knownRepositoryOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const repository of repositoryResultsQuery.data?.repositories ?? []) {
      options.set(repository.repository_id, `${repository.owner}/${repository.repo}`);
    }
    const scopedObservedOptions = observedRepositoryOptions.projectId === project?.id ? observedRepositoryOptions.options : {};
    for (const [repositoryId, label] of Object.entries(scopedObservedOptions)) options.set(repositoryId, label);
    return [...options].map(([id, label]) => ({ id, label }));
  }, [observedRepositoryOptions, project?.id, repositoryResultsQuery.data?.repositories]);
  const selectedRepositoryId = knownRepositoryOptions.some((option) => option.id === requestedRepositoryId)
    ? requestedRepositoryId : "";
  const findingsQuery = useRunFindings(
    runId,
    findingsPage,
    25,
    project?.id ?? null,
    selectedRepositoryId ? { repository_id: selectedRepositoryId } : {},
    runMatchesProject,
  );
  const repositoryFilterOptions = useMemo(() => {
    const options = new Map(knownRepositoryOptions.map(({ id, label }) => [id, label]));
    for (const finding of findingsQuery.data?.items ?? []) {
      if (finding.repository_id) {
        options.set(finding.repository_id, finding.repository_owner && finding.repository_name
          ? `${finding.repository_owner}/${finding.repository_name}` : finding.repository_id);
      }
    }
    return [...options].map(([id, label]) => ({ id, label }));
  }, [findingsQuery.data?.items, knownRepositoryOptions]);
  const selectionMatchesCurrentScope = Boolean(selectedFinding
    && selectedFinding.projectId === project?.id
    && selectedFinding.runId === runId
    && selectedFinding.filterRepositoryId === (selectedRepositoryId || null));
  const currentFinding = selectionMatchesCurrentScope ? selectedFinding : null;

  useEffect(() => {
    const discovered: Record<string, string> = {};
    for (const repository of repositoryResultsQuery.data?.repositories ?? []) {
      discovered[repository.repository_id] = `${repository.owner}/${repository.repo}`;
    }
    for (const finding of findingsQuery.data?.items ?? []) {
      if (finding.repository_id) {
        discovered[finding.repository_id] = finding.repository_owner && finding.repository_name
          ? `${finding.repository_owner}/${finding.repository_name}` : finding.repository_id;
      }
    }
    if (Object.keys(discovered).length > 0) {
      setObservedRepositoryOptions((previous) => {
        const previousOptions = previous.projectId === project?.id ? previous.options : {};
        const nextOptions = { ...previousOptions, ...discovered };
        const unchanged = previous.projectId === project?.id
          && Object.keys(discovered).every((id) => previousOptions[id] === discovered[id]);
        return unchanged ? previous : { projectId: project?.id ?? null, options: nextOptions };
      });
    }
  }, [findingsQuery.data?.items, project?.id, repositoryResultsQuery.data?.repositories]);

  useEffect(() => {
    if (!selectedFinding || selectionMatchesCurrentScope) return;
    setSelectedFinding(null);
  }, [selectedFinding, selectionMatchesCurrentScope]);

  useEffect(() => {
    if (!requestedRepositoryId) return;
    const repositoryHistorySettled = !canReadRepositoryHistory || repositoryResultsQuery.isSuccess || repositoryResultsQuery.isError;
    const runCannotProvideFindings = Boolean(selectedRunQuery.isError || (selectedRunQuery.data && !runMatchesProject));
    const findingsSettled = !runId || runCannotProvideFindings || findingsQuery.isSuccess || findingsQuery.isError;
    const repositoryKnown = repositoryFilterOptions.some((option) => option.id === requestedRepositoryId);
    if (!repositoryHistorySettled || !findingsSettled || repositoryKnown) return;
    const next = new URLSearchParams(searchParams);
    next.delete("repository_id");
    setSearchParams(next, { replace: true });
  }, [canReadRepositoryHistory, findingsQuery.isError, findingsQuery.isSuccess, repositoryFilterOptions, repositoryResultsQuery.isError, repositoryResultsQuery.isSuccess, requestedRepositoryId, runId, runMatchesProject, searchParams, selectedRunQuery.data, selectedRunQuery.isError, setSearchParams]);

  function closeFinding() {
    setSelectedFinding(null);
  }

  function changeRepositoryFilter(repositoryId: string) {
    const next = new URLSearchParams(searchParams);
    if (repositoryId) next.set("repository_id", repositoryId);
    else next.delete("repository_id");
    next.delete("findings_page");
    setSearchParams(next, { replace: true });
  }

  function changeRunFilter(nextRunId: string) {
    const next = new URLSearchParams(searchParams);
    if (nextRunId) next.set("run_id", nextRunId);
    else next.delete("run_id");
    next.delete("findings_page");
    setSearchParams(next, { replace: true });
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
        {runId && <Link className="button button-secondary" to={`/analysis/${encodeURIComponent(runId)}`}>Ver ejecución <ArrowRight aria-hidden="true" size={14} /></Link>}
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
              <h3>Integración continua del resumen heredado</h3>
              <p>Este campo no incluye identidad de repositorio; se conserva por compatibilidad y no se asigna a un repositorio de la colección.</p>
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
            <p className="section-subtitle">La candidatura y su estado de verificación son campos independientes; el filtro de repositorio se aplica en la API.</p>
          </div>
          {findingsQuery.data && <Pill tone="neutral">{findingsQuery.data.total} registros</Pill>}
        </div>
        <div className="quality-finding-filters" aria-label="Filtros de hallazgos">
          <label className="filter-control"><span>Filtrar hallazgos por ejecución</span><select aria-label="Filtrar hallazgos por ejecución" value={requestedRunId ?? ""} onChange={(event) => changeRunFilter(event.target.value)}>
            <option value="">Última ejecución disponible{latestRunId ? ` · ${latestRunId}` : ""}</option>
            {(runsQuery.data?.items ?? []).map((run) => <option value={run.run_id} key={run.run_id}>{run.run_id} · {run.status}</option>)}
            {requestedRunId && !(runsQuery.data?.items ?? []).some((run) => run.run_id === requestedRunId) && <option value={requestedRunId}>{requestedRunId} · identidad por verificar</option>}
          </select></label>
          <label className="filter-control"><span>Filtrar hallazgos por repositorio</span><select aria-label="Filtrar hallazgos por repositorio" value={selectedRepositoryId} onChange={(event) => changeRepositoryFilter(event.target.value)}>
            <option value="">Todos los repositorios</option>
            {repositoryFilterOptions.map((repository) => <option value={repository.id} key={repository.id}>{repository.label}</option>)}
          </select></label>
        </div>
        {!runId && runsQuery.isPending && <LoadingState label="Buscando la ejecución más reciente…" />}
        {!runId && runsQuery.isError && <ErrorState title="No se pudieron consultar las ejecuciones" message={runsQuery.error.message} onRetry={() => void runsQuery.refetch()} />}
        {!runId && runsQuery.data?.items.length === 0 && <EmptyState title="Sin ejecuciones" message="No se pueden listar hallazgos sin una ejecución persistida." />}
        {runId && selectedRunQuery.isPending && <LoadingState label="Verificando la ejecución para el proyecto activo…" />}
        {runId && selectedRunQuery.isError && <ErrorState title="No se pudo verificar la ejecución" message={selectedRunQuery.error.message} onRetry={() => void selectedRunQuery.refetch()} />}
        {runId && selectedRunQuery.data && !runMatchesProject && <p className="capability-note" role="status">La ejecución no pertenece al proyecto activo; no se mostrarán sus hallazgos.</p>}
        {requestedRepositoryId && !selectedRepositoryId && (repositoryResultsQuery.isPending || findingsQuery.isPending) && <LoadingState label="Verificando el filtro de repositorio…" />}
        {findingsQuery.isPending && runMatchesProject && !requestedRepositoryId && <LoadingState label="Consultando hallazgos de la ejecución…" />}
        {findingsQuery.isError && <ErrorState title="No se pudieron cargar los hallazgos" message={findingsQuery.error.message} onRetry={() => void findingsQuery.refetch()} />}
        {findingsQuery.data && (findingsQuery.data.items.length ? (
          <>
            <ul className="finding-list">
              {findingsQuery.data.items.map((finding) => (
                <li key={finding.finding_id}>
                  <button type="button" className="finding-row" aria-label={`Revisar hallazgo ${finding.finding_id}`} onClick={() => setSelectedFinding({
                    projectId: project.id,
                    runId: finding.run_id,
                    repositoryId: finding.repository_id ?? null,
                    filterRepositoryId: selectedRepositoryId || null,
                    findingId: finding.finding_id,
                  })}>
                    <span className="finding-row-main">
                      <strong>{finding.file_path ?? finding.finding_id}</strong>
                      <span>
                        <Pill tone="accent">Hallazgo candidato</Pill>
                        <Pill tone={verificationTone(finding.verification_status)}>{findingVerificationLabel(finding.verification_status)}</Pill>
                        {finding.suggested_code_validation_status === "not_validated" && (
                          <Pill tone="warning">Código sugerido no validado</Pill>
                        )}
                      </span>
                      <small>{findingRepositoryLabel(finding)} · {findingMeta(finding).join(" · ")} · Verificación: <code>{finding.verification_status}</code></small>
                      {finding.claim && <p className="finding-row-claim"><strong>Afirmación:</strong> {finding.claim}</p>}
                      {finding.verification_reason && <p className="finding-row-reason"><small>Motivo de verificación: {finding.verification_reason}</small></p>}
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
      {currentFinding && <FindingDetailDialog
        key={`${currentFinding.projectId}:${currentFinding.runId}:${currentFinding.filterRepositoryId ?? "unfiltered"}:${currentFinding.repositoryId ?? "unknown"}:${currentFinding.findingId}`}
        runId={currentFinding.runId}
        findingId={currentFinding.findingId}
        projectId={currentFinding.projectId}
        repositoryId={currentFinding.repositoryId}
        filterRepositoryId={currentFinding.filterRepositoryId}
        onClose={closeFinding}
      />}
    </section>
  );
}
