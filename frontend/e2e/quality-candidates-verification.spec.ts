import { expect, test } from "@playwright/test";
import type { RepositoryHistoryView, RepositoryResultsCollection } from "../src/api/types";
import { installApi } from "./fixtures";

const BACKEND_SHA = "af6aa59757886d3f1f8437f0a10d9f1e89ed6345";
const FRONTEND_SHA = "68e2b9a13af5193738bd6b4706b0c073918dab7c";

const candidateConfirmedBackend = {
  finding_id: "backend::af6aa597::CQ-001",
  repository_id: "backend",
  commit_sha: BACKEND_SHA,
  category: "syntax_error",
  severity: "high",
  rule_id: "Q3",
  file_path: "src/infrastructure/database/despacho-store.ts",
  line: 75,
  confidence: 0.95,
  claim: "La consulta SQL en obtenerConductoresDisponibles presenta error de sintaxis en subconsulta NOT EXISTS.",
  improvement_summary: "Corrige sintaxis SQL agregando paréntesis de cierre faltante.",
  evidence: "SELECT 1 FROM despachos d WHERE c.activo",
  suggested_code: "async obtenerConductoresDisponibles() {\n  return pool.query(...);\n}",
  verification_status: "confirmed",
  verification_reason: "Defecto confirmado en líneas visibles.",
  suggested_code_validation_status: "not_validated",
};

const candidatePendingFrontend = {
  finding_id: "frontend::68e2b9a1::CQ-001",
  repository_id: "frontend",
  commit_sha: FRONTEND_SHA,
  category: "otros",
  severity: "low",
  rule_id: null,
  file_path: "src/client/quoter-api.ts",
  line: 192,
  confidence: 0.85,
  claim: "Cálculo de volumen local fuerza cantidad mínima de 1 cuando es cero.",
  improvement_summary: "Permitir valores de cero mediante operador nullish coalescing.",
  evidence: "const qty = Math.max(1, detail.cantidad || 1);",
  suggested_code: "const qty = Math.max(0, detail.cantidad ?? 0);",
  verification_status: "pending",
  verification_reason: "No escalado para verificación independiente (severidad baja o sin regla mandatoria).",
  suggested_code_validation_status: "not_validated",
};

const candidateRejected = {
  finding_id: "backend::af6aa597::CQ-REJECTED",
  repository_id: "backend",
  commit_sha: BACKEND_SHA,
  category: "otros",
  severity: "medium",
  rule_id: null,
  file_path: "src/service.ts",
  line: 20,
  confidence: 0.7,
  claim: "Afirmación contradicha por la evidencia visible.",
  improvement_summary: "Rechazado por verificación.",
  evidence: "const x = 10;",
  suggested_code: "const x = 20;",
  verification_status: "rejected",
  verification_reason: "La evidencia contradice la afirmación del defecto.",
  suggested_code_validation_status: "not_validated",
};

const candidateInsufficient = {
  finding_id: "backend::af6aa597::CQ-INSUFFICIENT",
  repository_id: "backend",
  commit_sha: BACKEND_SHA,
  category: "otros",
  severity: "high",
  rule_id: null,
  file_path: "src/missing.ts",
  line: 50,
  confidence: 0.9,
  claim: "Afirmación que requiere archivo completo no disponible.",
  improvement_summary: "Evidencia insuficiente.",
  evidence: "function test() {}",
  suggested_code: "function testFixed() {}",
  verification_status: "insufficient",
  verification_reason: "Se requiere contexto completo para verificar esta afirmación.",
  suggested_code_validation_status: "not_validated",
};

const historicalCandidateNoNewFields = {
  finding_id: "backend::legacy::CQ-HISTORICAL",
  repository_id: "backend",
  commit_sha: BACKEND_SHA,
  category: "calidad",
  severity: "medium",
  rule_id: null,
  file_path: "src/legacy.ts",
  line: 10,
  confidence: 0.8,
  claim: "Hallazgo histórico anterior a c69863a.",
  improvement_summary: "Sugerencia histórica.",
  evidence: "legacyCode()",
  suggested_code: "newCode()",
  verification_status: "pending",
  verification_reason: null,
  // suggested_code_validation_status is intentionally missing (undefined)
};

function buildMockRepositoryResults(findings: any[]): RepositoryResultsCollection {
  const backendFindings = findings.filter((f) => f.repository_id === "backend");
  const frontendFindings = findings.filter((f) => f.repository_id === "frontend");

  return {
    project_id: "hormigestio",
    availability: "available",
    repositories: [
      {
        repository_id: "backend",
        owner: "Josu-F1",
        repo: "hormigestion-backend",
        branch: "develop",
        commit_sha: BACKEND_SHA,
        quality_workflow_path: ".github/workflows/quality.yml",
      },
      {
        repository_id: "frontend",
        owner: "Josu-F1",
        repo: "hormigestion-frontend",
        branch: "develop",
        commit_sha: FRONTEND_SHA,
        quality_workflow_path: ".github/workflows/quality.yml",
      },
    ],
    items: [
      {
        capture_id: "cap-backend",
        snapshot_id: "snap-backend",
        source_scope: "repository",
        source_item_key: "backend",
        analysis_run_id: "run-e2e-test",
        report_id: "rep-e2e-test",
        repository: {
          repository_id: "backend",
          owner: "Josu-F1",
          repo: "hormigestion-backend",
        },
        branch: "develop",
        target_commit_sha: BACKEND_SHA,
        commit_resolution_status: "resolved",
        access_status: "allowed",
        availability_status: "available",
        source_complete: true,
        incomplete_reason: null,
        code_diff_count: 1,
        coverage_percentage: 97.9,
        coverage_basis: "loaded TypeScript unit-test lines",
        ci_runs: [],
        source_problems: [],
        quality_status: "completed",
        quality_reason: null,
        verification_status: "completed",
        verification_reason: null,
        evaluation_status: "completed",
        evaluation_reason: null,
        findings: backendFindings,
        verifications: [],
      },
      {
        capture_id: "cap-frontend",
        snapshot_id: "snap-frontend",
        source_scope: "repository",
        source_item_key: "frontend",
        analysis_run_id: "run-e2e-test",
        report_id: "rep-e2e-test",
        repository: {
          repository_id: "frontend",
          owner: "Josu-F1",
          repo: "hormigestion-frontend",
        },
        branch: "develop",
        target_commit_sha: FRONTEND_SHA,
        commit_resolution_status: "resolved",
        access_status: "allowed",
        availability_status: "available",
        source_complete: true,
        incomplete_reason: null,
        code_diff_count: 1,
        coverage_percentage: 92.73,
        coverage_basis: "loaded TypeScript lines excluding TSX and UI",
        ci_runs: [],
        source_problems: [],
        quality_status: "completed",
        quality_reason: null,
        verification_status: "skipped",
        verification_reason: null,
        evaluation_status: "completed",
        evaluation_reason: null,
        findings: frontendFindings,
        verifications: [],
      },
    ],
    total: 2,
    page: 1,
    page_size: 25,
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "127.0.0.1" && url.port === "4173") {
      await route.continue();
      return;
    }
    await route.abort();
  });
});

test("visualiza candidatos confirmados, pendientes y la advertencia de código no validado por repositorio", async ({ page }) => {
  const payload = buildMockRepositoryResults([candidateConfirmedBackend, candidatePendingFrontend]);
  await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read"],
    repositoryResultsPayload: payload,
  });

  await page.goto("/projects");

  // 1. Verificar presencia de ambos repositorios
  const backendCard = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  const frontendCard = page.getByRole("article", { name: "Josu-F1/hormigestion-frontend" });
  await expect(backendCard).toBeVisible();
  await expect(frontendCard).toBeVisible();

  // 2. Candidato confirmado en Backend
  await expect(backendCard).toContainText("src/infrastructure/database/despacho-store.ts:75");
  await expect(backendCard).toContainText(/Confirmado/i);
  await expect(backendCard).toContainText("Código sugerido no validado");
  await expect(backendCard).toContainText("Defecto confirmado en líneas visibles.");

  // Expandir código sugerido en backend
  const backendCodeDetails = backendCard.locator("details.repository-finding-code-details");
  await expect(backendCodeDetails).toBeVisible();
  await expect(backendCodeDetails).toContainText("Ver código sugerido (no validado)");
  await backendCodeDetails.locator("summary").click();
  await expect(backendCodeDetails.locator("pre")).toContainText("obtenerConductoresDisponibles");

  // 3. Candidato pendiente en Frontend
  await expect(frontendCard).toContainText("src/client/quoter-api.ts:192");
  await expect(frontendCard).toContainText(/Pendiente/i);
  await expect(frontendCard).toContainText("Código sugerido no validado");
  await expect(frontendCard).toContainText("No escalado para verificación independiente");
});

test("visualiza hallazgos rechazados e insuficientes sin acreditar parche ni distorsionar veredictos", async ({ page }) => {
  const payload = buildMockRepositoryResults([candidateRejected, candidateInsufficient]);
  await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read"],
    repositoryResultsPayload: payload,
  });

  await page.goto("/projects");
  const backendCard = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backendCard).toBeVisible();

  // Candidato rechazado
  await expect(backendCard).toContainText("src/service.ts");
  await expect(backendCard).toContainText(/Rechazado|Descartado/i);
  await expect(backendCard).toContainText("La evidencia contradice la afirmación del defecto.");

  // Candidato insuficiente
  await expect(backendCard).toContainText("src/missing.ts");
  await expect(backendCard).toContainText(/Insuficiente/i);
  await expect(backendCard).toContainText("Se requiere contexto completo para verificar esta afirmación.");
});

test("soporta datos históricos sin campos nuevos y estados sin candidatos de forma segura", async ({ page }) => {
  // Caso 1: datos históricos (suggested_code_validation_status no presente)
  const legacyPayload = buildMockRepositoryResults([historicalCandidateNoNewFields]);
  await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read"],
    repositoryResultsPayload: legacyPayload,
  });

  await page.goto("/projects");
  const backendCard = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backendCard).toBeVisible();
  await expect(backendCard).toContainText("src/legacy.ts");
  await expect(backendCard).toContainText("Hallazgo histórico anterior a c69863a.");

  // Caso 2: lista de candidatos vacía (sin candidatos)
  const emptyPayload = buildMockRepositoryResults([]);
  await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read"],
    repositoryResultsPayload: emptyPayload,
  });

  await page.goto("/projects");
  const backendEmpty = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backendEmpty).toBeVisible();
  await expect(backendEmpty.locator(".repository-finding-card")).toHaveCount(0);
});
