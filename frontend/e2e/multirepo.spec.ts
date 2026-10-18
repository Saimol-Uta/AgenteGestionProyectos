import { expect, test, type Page } from "@playwright/test";
import type { RepositoryHistoryView, RepositoryResultsCollection } from "../src/api/types";
import {
  backendRepositorySha,
  candidateFinding,
  findingDetail,
  installApi,
  multiRepoResults,
  project,
  secondProject,
  secondaryRepositoryResults,
} from "./fixtures";

const backendFinding = {
  ...candidateFinding,
  finding_id: "backend:f-1",
  repository_id: "backend",
  repository_owner: "Josu-F1",
  repository_name: "hormigestion-backend",
  commit_sha: backendRepositorySha,
};
const frontendFinding = {
  ...candidateFinding,
  finding_id: "frontend:f-1",
  repository_id: "frontend",
  repository_owner: "Josu-F1",
  repository_name: "hormigestion-frontend",
  commit_sha: null,
};
const previousRunFinding = { ...backendFinding, finding_id: "backend:f-previous", run_id: "run-previous" };
const missingRepositoryIdentityFinding = (() => {
  const { repository_id: _repositoryId, repository_owner: _repositoryOwner, repository_name: _repositoryName, ...finding } = {
    ...backendFinding,
    finding_id: "backend:f-missing-identity",
  };
  return finding;
})();
const contradictoryRepositoryIdentityFinding = (() => {
  const { repository_id: _repositoryId, repository_owner: _repositoryOwner, repository_name: _repositoryName, ...finding } = {
    ...backendFinding,
    finding_id: "backend:f-contradictory-identity",
  };
  return finding;
})();
const findingDetails = new Map([
  [backendFinding.finding_id, {
    ...findingDetail,
    ...backendFinding,
    claim: "Backend finding stays bound to its stored repository.",
    suggested_code: "export const safe = true;",
    improvement_summary: "Backend suggestion from the API fixture.",
    decision_status: "pending",
  }],
  [frontendFinding.finding_id, { ...findingDetail, ...frontendFinding, claim: "Frontend finding from its own repository." }],
  [previousRunFinding.finding_id, { ...findingDetail, ...previousRunFinding, claim: "Finding from a different run." }],
  [missingRepositoryIdentityFinding.finding_id, { ...findingDetail, ...missingRepositoryIdentityFinding, suggested_code: "export const safe = true;", improvement_summary: "Complete backend suggestion.", decision_status: "pending" }],
  [contradictoryRepositoryIdentityFinding.finding_id, { ...findingDetail, ...contradictoryRepositoryIdentityFinding, suggested_code: "export const safe = true;", improvement_summary: "Complete backend suggestion.", decision_status: "pending" }],
  [candidateFinding.finding_id, {
    ...findingDetail,
    ...candidateFinding,
    suggested_code: "export const safe = true;",
    improvement_summary: "Legacy suggestion from the API fixture.",
    decision_status: "pending",
  }],
]);

function repositoryResultsAroundTerminalJob() {
  const current = multiRepoResults as unknown as RepositoryResultsCollection;
  const backendObservation = current.items.find((item) => item.repository?.repository_id === "backend");
  if (!backendObservation) throw new Error("The repository fixture must include its backend observation.");
  const previousObservation: RepositoryHistoryView = {
    ...backendObservation,
    capture_id: "capture-previous",
    snapshot_id: "snapshot-previous",
    analysis_run_id: "run-previous",
    report_id: null,
    coverage_percentage: null,
    coverage_basis: null,
    ci_runs: [],
  };
  const otherObservations = current.items.filter((item) => item !== backendObservation);
  const before: RepositoryResultsCollection = {
    ...current,
    items: [previousObservation, ...otherObservations],
    total: otherObservations.length + 1,
    page: 1,
    page_size: 100,
  };
  const after: RepositoryResultsCollection = {
    ...current,
    items: [backendObservation, previousObservation, ...otherObservations],
    total: otherObservations.length + 2,
    page: 1,
    page_size: 100,
  };
  return { before, after };
}

type FindingRead = { method: string; runId: string; repositoryId: string | null; path: string };
type DecisionWrite = { path: string; body: Record<string, unknown>; csrf: string | undefined; idempotencyKey: string | undefined };

async function installFindingApi(page: Page, options: { legacy?: boolean } = {}) {
  const reads: FindingRead[] = [];
  const writes: DecisionWrite[] = [];
  await page.route("**/api/v1/runs/**/findings**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const marker = "/findings";
    const markerIndex = url.pathname.lastIndexOf(marker);
    const runId = url.pathname.slice(0, markerIndex).split("/").at(-1) ?? "";
    const suffix = url.pathname.slice(markerIndex + marker.length);
    if (request.method() === "POST") {
      let body: Record<string, unknown> = {};
      try { body = request.postDataJSON() as Record<string, unknown>; } catch { /* The assertion below reports malformed requests. */ }
      writes.push({
        path: url.pathname,
        body,
        csrf: request.headers()["x-csrf-token"],
        idempotencyKey: request.headers()["idempotency-key"],
      });
      await route.fulfill({ json: {
        decision_status: "approved",
        suggested_code: "export const safe = true;",
        improvement_summary: "Backend suggestion from the API fixture.",
        branch_name: "quality/backend-f-1",
        branch_url: null,
        commit_sha_applied: null,
        execution_mode: "sandbox",
      } });
      return;
    }
    if (suffix.startsWith("/")) {
      const findingId = decodeURIComponent(suffix.slice(1));
      reads.push({ method: request.method(), runId, repositoryId: null, path: url.pathname });
      const detail = findingDetails.get(findingId);
      await route.fulfill(detail ? { json: detail } : { status: 404, json: { error: { code: "finding_not_found", message: "Finding not found." } } });
      return;
    }
    const repositoryId = url.searchParams.get("repository_id");
    reads.push({ method: request.method(), runId, repositoryId, path: url.pathname });
    const candidates = options.legacy
      ? [candidateFinding]
      : runId === "run-previous"
        ? [previousRunFinding]
        : repositoryId === "backend" ? [backendFinding]
          : repositoryId === "frontend" ? [frontendFinding]
            : [backendFinding, frontendFinding];
    await route.fulfill({ json: { items: candidates, total: candidates.length, page: 1, page_size: 25 } });
  });
  return { reads, writes };
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

test("project and existing feature views keep repository identity, CI, coverage scope and partial failures separate", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read"], repositoryResultsPayload: multiRepoResults });
  await page.goto("/projects");

  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" }).first()).toBeVisible();
  const backend = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  const frontend = page.getByRole("article", { name: "Josu-F1/hormigestion-frontend" });
  await expect(backend).toBeVisible();
  await expect(frontend).toBeVisible();
  await expect(backend).toContainText("Backend quality");
  await expect(backend).toContainText("38833954051");
  await expect(backend).toContainText("Intento 2");
  await expect(backend).toContainText(backendRepositorySha);
  await expect(backend).toContainText("Backend source line coverage; generated files excluded");
  await expect(backend).toContainText(/0\s?%/);
  await expect(frontend).toContainText(/acceso denegado/i);
  await expect(frontend).toContainText(/sin dato|desconocid/i);
  await expect(page.getByRole("heading", { name: "Resumen consolidado del proyecto" })).toBeVisible();
  await expect(page.getByText(/no se calcula cobertura global/i)).toBeVisible();

  for (const path of ["/analysis?run_id=run-current", "/quality?run_id=run-current", "/reports"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Repositorios del proyecto" }).first()).toBeVisible();
    await expect(page.getByText("hormigestion-backend").first()).toBeVisible();
  }
});

test("repository history refreshes when a tracked analysis job reaches terminal state", async ({ page }) => {
  const { before, after } = repositoryResultsAroundTerminalJob();
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read", "analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["queued", "completed"],
  });
  let repositoryResultsReads = 0;
  await page.route("**/api/v1/projects/hormigestio/repository-results**", async (route) => {
    repositoryResultsReads += 1;
    const payload = api.jobPollRequests >= 2 ? after : before;
    await route.fulfill({ json: payload });
  });
  await page.goto("/projects");

  const backend = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backend).toContainText("Ejecución run-previous");
  const initialHistoryReads = repositoryResultsReads;
  await page.getByRole("button", { name: "Sincronizar y Analizar Proyecto" }).click();
  await page.getByLabel("Autorizo el uso de servicios en la nube").check();
  await page.getByRole("button", { name: "Confirmar análisis", exact: true }).click();

  await expect(page.getByText(/Estado del análisis: Completado/)).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => api.jobPollRequests, { timeout: 5_000 }).toBe(2);
  await expect.poll(() => repositoryResultsReads, { timeout: 10_000 }).toBeGreaterThan(initialHistoryReads);
  await expect(backend).toContainText("Ejecución run-current");
  await expect(backend).toContainText("38833954051");
});

test("known repository scope rejects finding details with missing or contradictory repository identity", async ({ page }) => {
  const missingFinding = missingRepositoryIdentityFinding;
  const contradictoryFinding = contradictoryRepositoryIdentityFinding;
  const missingDetailWithIdentity = {
    ...findingDetail,
    ...missingFinding,
    suggested_code: "export const safe = true;",
    improvement_summary: "Complete backend suggestion.",
    decision_status: "pending",
    claim: "Missing identity detail must not be rendered.",
  };
  const { repository_id: _repositoryId, repository_owner: _repositoryOwner, repository_name: _repositoryName, ...missingDetail } = missingDetailWithIdentity;
  const contradictoryDetail = {
    ...findingDetail,
    ...contradictoryFinding,
    repository_id: "frontend",
    repository_owner: "Josu-F1",
    repository_name: "hormigestion-frontend",
    suggested_code: "export const safe = true;",
    improvement_summary: "Complete backend suggestion.",
    decision_status: "pending",
    claim: "Contradictory identity detail must not be rendered.",
  };
  const details = new Map([
    [missingFinding.finding_id, missingDetail],
    [contradictoryFinding.finding_id, contradictoryDetail],
  ]);
  await installApi(page, { authenticated: true, capabilities: ["audit:read", "quality:decide"], repositoryResultsPayload: multiRepoResults });
  const api = await installFindingApi(page);
  await page.route("**/api/v1/runs/run-current/findings**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname.endsWith("/findings")) {
      await route.fulfill({ json: { items: [missingFinding, contradictoryFinding], total: 2, page: 1, page_size: 25 } });
      return;
    }
    if (request.method() === "GET" && url.pathname.includes("/findings/")) {
      const findingId = decodeURIComponent(url.pathname.slice(url.pathname.lastIndexOf("/") + 1));
      const detail = details.get(findingId);
      await route.fulfill(detail ? { json: detail } : { status: 404, json: { error: { code: "finding_not_found", message: "Finding not found." } } });
      return;
    }
    await route.fallback();
  });
  await page.goto("/quality?run_id=run-current&repository_id=backend");

  for (const finding of [missingFinding, contradictoryFinding]) {
    await page.getByRole("button", { name: `Revisar hallazgo ${finding.finding_id}` }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("La identidad del hallazgo no coincide")).toBeVisible();
    await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios/i })).toHaveCount(0);
    await expect(dialog.getByText(/identity detail must not be rendered/i)).toHaveCount(0);
    await dialog.getByRole("button", { name: "Cerrar detalle del hallazgo" }).click();
  }
  expect(api.writes).toHaveLength(0);
});

test("late finding detail stays closed after repository scope changes", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read", "quality:decide"], repositoryResultsPayload: multiRepoResults });
  const api = await installFindingApi(page);
  let releaseDetail: (() => void) | null = null;
  const delayedDetail = new Promise<void>((resolve) => { releaseDetail = resolve; });
  let requestStartedResolve: (() => void) | null = null;
  const requestStarted = new Promise<void>((resolve) => { requestStartedResolve = resolve; });
  let responseFinishedResolve: (() => void) | null = null;
  const responseFinished = new Promise<void>((resolve) => { responseFinishedResolve = resolve; });
  await page.route("**/api/v1/runs/run-current/findings/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const findingId = decodeURIComponent(url.pathname.slice(url.pathname.lastIndexOf("/") + 1));
    if (request.method() === "GET" && findingId === backendFinding.finding_id) {
      requestStartedResolve?.();
      await delayedDetail;
      await route.fulfill({ json: findingDetails.get(findingId) });
      responseFinishedResolve?.();
      return;
    }
    await route.fallback();
  });
  await page.goto("/quality?run_id=run-current&repository_id=backend");
  await page.getByRole("button", { name: /Revisar hallazgo backend:f-1/ }).click();
  await requestStarted;

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.getByLabel("Filtrar hallazgos por repositorio").selectOption("frontend");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Revisar hallazgo frontend:f-1/ })).toBeVisible();
  releaseDetail?.();
  await responseFinished;
  await expect(dialog).toHaveCount(0);
  expect(api.writes).toHaveLength(0);
});

test("a workflow with a contradictory repository identity is not attributed to the history row", async ({ page }) => {
  const mismatchedRepositoryResults = {
    ...multiRepoResults,
    items: multiRepoResults.items.map((item) => item.repository?.repository_id === "backend"
      ? { ...item, ci_runs: item.ci_runs.map((run) => ({
          ...run,
          repository: { repository_id: "frontend", owner: "Josu-F1", repo: "hormigestion-frontend" },
        })) }
      : item),
  };
  await installApi(page, { authenticated: true, capabilities: ["audit:read"], repositoryResultsPayload: mismatchedRepositoryResults });
  await page.goto("/projects");

  const backend = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backend.getByRole("heading", { name: "Workflows sin asociación confirmada" })).toBeVisible();
  await expect(backend).toContainText("Josu-F1/hormigestion-frontend · frontend");
  await expect(backend).toContainText(/no se atribuyen sus métricas a este repositorio/i);
  await expect(backend).not.toContainText("Métricas para el informe");
});

test("quality filters bind repository and run, preserve namespaced finding IDs, and retain human decision controls", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read", "quality:decide"], repositoryResultsPayload: multiRepoResults });
  const api = await installFindingApi(page);
  await page.goto("/quality?run_id=run-current");

  const repositoryFilter = page.getByLabel("Filtrar hallazgos por repositorio");
  await repositoryFilter.selectOption("backend");
  await expect.poll(() => new URL(page.url()).searchParams.get("repository_id")).toBe("backend");
  const backendRow = page.getByRole("button", { name: /Revisar hallazgo backend:f-1/ });
  await expect(backendRow).toBeVisible();
  await backendRow.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Hallazgo candidato · backend:f-1")).toBeVisible();
  const detailRead = api.reads.find((read) => read.path.endsWith("backend%3Af-1"));
  expect(detailRead?.path).toContain("backend%3Af-1");
  const approve = dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true });
  await expect(approve).toBeVisible();
  await approve.click();
  await expect(dialog.locator(".quality-decision-result")).toContainText("Aprobada");
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].path).toContain("backend%3Af-1/decision");
  expect(api.writes[0].csrf).toBe("csrf-fixture");
  expect(api.writes[0].idempotencyKey).toBeTruthy();
  expect(api.writes[0].body).not.toHaveProperty("repository_id");
  expect(api.writes[0].body).not.toHaveProperty("commit_sha");

  await repositoryFilter.selectOption("frontend");
  await expect.poll(() => new URL(page.url()).searchParams.get("repository_id")).toBe("frontend");
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("button", { name: /Revisar hallazgo frontend:f-1/ })).toBeVisible();
  await repositoryFilter.selectOption("backend");
  await expect(page.getByRole("button", { name: /Revisar hallazgo backend:f-1/ })).toBeVisible();

  await page.getByLabel("Filtrar hallazgos por ejecución").selectOption("run-previous");
  await expect.poll(() => new URL(page.url()).searchParams.get("run_id")).toBe("run-previous");
  await expect(page.getByRole("button", { name: /Revisar hallazgo backend:f-previous/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Revisar hallazgo backend:f-1/ })).toHaveCount(0);
  expect(api.reads).toContainEqual(expect.objectContaining({ runId: "run-previous", repositoryId: "backend" }));
});

test("live project selection closes a stale finding and isolates repository history", async ({ page }) => {
  await installApi(page, {
    projectsPayload: { items: [project, secondProject], total: 2, page: 1, page_size: 100 },
    authenticated: true,
    capabilities: ["audit:read"],
    repositoryResultsPayload: multiRepoResults,
    repositoryResultsByProject: { [secondaryRepositoryResults.project_id]: secondaryRepositoryResults },
  });
  await installFindingApi(page, { legacy: true });
  await page.goto("/quality?run_id=run-current");
  await expect(page.getByText("hormigestion-backend").first()).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const staleDialog = page.getByRole("dialog");
  await expect(staleDialog).toBeVisible();

  await page.getByLabel("Proyecto activo").first().selectOption("proyecto-secundario");
  await expect(staleDialog).toHaveCount(0);
  await expect(page.getByText(/disponibilidad.*desconocida/i)).toBeVisible();
  await expect(page.getByText("hormigestion-backend")).toHaveCount(0);
});

test("the legacy project keeps unknown repository history instead of an invented primary or green result", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read"] });
  await installFindingApi(page, { legacy: true });
  await page.goto("/quality");

  await expect(page.getByRole("heading", { name: "Hallazgos candidatos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" }).first()).toBeVisible();
  await expect(page.getByText(/disponibilidad.*desconocida/i)).toBeVisible();
  await expect(page.getByText(/hormigestion-backend|hormigestion-frontend/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revisar hallazgo finding-candidate" })).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios/i })).toHaveCount(0);
  await expect(dialog.getByText("El servidor no concedió quality:decide; las acciones de decisión están ocultas.", { exact: true })).toBeVisible();
});
