import { expect, test, type Page } from "@playwright/test";
import type { RepositoryHistoryView, RepositoryResultsCollection } from "../src/api/types";
import {
  assertNoBrowserNetworkAttempts,
  backendRepositorySha,
  candidateFinding,
  findingDetail,
  installApi,
  installBrowserNetworkGuard,
  multiRepoResults,
  project,
  runCurrent,
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
    verification_status: "confirmed",
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
      await route.fulfill({ status: 409, json: { error: {
        code: "revision_independiente_requerida",
        message: "Aprobación bloqueada: falta revisión independiente del parche exacto.",
      } } });
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
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

test("project and existing feature views keep repository identity, CI, coverage scope and partial failures separate", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read"], repositoryResultsPayload: multiRepoResults });
  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toHaveCount(0);
  await page.goto("/analysis");
  await page.locator('a[href="/analysis/run-current"]').first().click();
  await expect(page).toHaveURL(/\/analysis\/run-current$/);
  await page.getByText("Información técnica del análisis").click();

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

  for (const path of ["/analysis", "/quality?run_id=run-current", "/reports"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toHaveCount(0);
    await page.goto("/analysis");
    await page.locator('a[href="/analysis/run-current"]').first().click();
    await expect(page).toHaveURL(/\/analysis\/run-current$/);
    await page.getByText("Información técnica del análisis").click();
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
  await page.goto("/analysis/run-previous");
  await page.getByText("Información técnica del análisis").click();

  const backend = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backend).toContainText("Ejecución run-previous");
  await page.goto("/analysis/run-current");
  await page.getByText("Información técnica del análisis").click();
  await expect(backend).not.toContainText("Ejecución run-previous");
  await expect(backend).toContainText("Sin observación de repositorio asociada a la ejecución run-current.");
  const initialHistoryReads = repositoryResultsReads;
  const header = page.getByRole("banner");
  const trigger = header.getByRole("button", { name: "Sincronizar proyecto" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Sincronizar y analizar proyecto" });
  expect(api.analysisWrites).toHaveLength(0);
  await dialog.getByRole("button", { name: "Preparar análisis" }).click();
  await dialog.getByLabel("Autorizo el uso de servicios en la nube").check();
  await dialog.getByRole("button", { name: "Confirmar análisis", exact: true }).click();
  await expect.poll(() => api.jobPollRequests, { timeout: 5_000 }).toBeGreaterThan(0);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeEnabled();
  await expect.poll(() => api.jobPollRequests, { timeout: 10_000 }).toBe(2);
  await expect.poll(() => repositoryResultsReads, { timeout: 10_000 }).toBeGreaterThan(initialHistoryReads);
  await expect(backend).toContainText("Ejecución run-current");
  await expect(backend).toContainText("38833954051");

  await trigger.click();
  const statusDialog = page.getByRole("dialog", { name: "Sincronizar y analizar proyecto" });
  await expect(statusDialog.getByText(/Estado del análisis: Completado/)).toBeVisible();
  expect(api.analysisWrites.map((write) => write.path)).toEqual([
    "/api/v1/projects/hormigestio/analysis/prepare",
    "/api/v1/projects/hormigestio/analysis/confirm",
  ]);
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
  await page.goto("/analysis/run-current");
  await page.getByText("Información técnica del análisis").click();

  const backend = page.getByRole("article", { name: "Josu-F1/hormigestion-backend" });
  await expect(backend.getByRole("heading", { name: "Workflows sin asociación confirmada" })).toBeVisible();
  await expect(backend).toContainText("Josu-F1/hormigestion-frontend · frontend");
  await expect(backend).toContainText(/no se atribuyen sus métricas a este repositorio/i);
  await expect(backend).not.toContainText("Métricas para el informe");
});

test("quality filters bind repository and run while a confirmed defect cannot approve its patch", async ({ page }) => {
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
  await expect(approve).toBeDisabled();
  await expect(dialog.getByText("Aprobación bloqueada: falta revisión independiente del parche exacto.", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Rechazar sugerencia", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Solicitar cambios", exact: true })).toBeVisible();
  expect(api.writes).toHaveLength(0);

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
  const secondaryRun = { ...runCurrent, run_id: "run-secondary", project_id: secondProject.id };
  await page.route((url) => url.pathname === "/api/v1/runs" && url.searchParams.get("project_id") === secondProject.id, async (route) => {
    await route.fulfill({ json: { items: [secondaryRun], total: 1, page: 1, page_size: 25 } });
  });
  await page.route("**/api/v1/runs/run-secondary**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/run-secondary")) {
      await route.fulfill({ json: secondaryRun });
    } else if (path.endsWith("/stages") || path.endsWith("/evidence")) {
      await route.fulfill({ json: { run_id: "run-secondary", items: [], total: 0, page: 1, page_size: 100 } });
    } else {
      await route.fallback();
    }
  });
  await page.goto("/quality?run_id=run-current");
  const repositoryFilter = page.getByLabel("Filtrar hallazgos por repositorio");
  await expect(repositoryFilter).toBeVisible();
  await expect(repositoryFilter).toContainText("hormigestion-backend");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const staleDialog = page.getByRole("dialog");
  await expect(staleDialog).toBeVisible();

  // A native project change must invalidate the detail even while the modal hides the shell from the accessibility tree.
  await page.locator(".topbar").getByLabel("Proyecto activo").selectOption("proyecto-secundario");
  await expect(staleDialog).toHaveCount(0);
  await expect(page.getByText("hormigestion-backend")).toHaveCount(0);
  await expect(page.locator(".topbar").getByLabel("Proyecto activo")).toHaveValue(secondProject.id);
  await page.getByRole("navigation", { name: "Navegación principal", exact: true }).getByRole("link", { name: "Análisis de IA", exact: true }).click();
  await page.locator('a[href="/analysis/run-secondary"]').first().click();
  await page.getByText("Información técnica del análisis").click();
  await expect(page).toHaveURL(/\/analysis\/run-secondary$/);
  await expect(page.locator(".topbar").getByLabel("Proyecto activo")).toHaveValue(secondProject.id);
  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toBeVisible();
  await expect(page.getByText(/disponibilidad.*desconocida/i)).toBeVisible();
  await expect(page.getByText("hormigestion-backend")).toHaveCount(0);
});

test("the legacy project keeps unknown repository history instead of an invented primary or green result", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["audit:read"] });
  await installFindingApi(page, { legacy: true });
  await page.goto("/quality");

  await expect(page.getByRole("heading", { name: "Hallazgos candidatos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revisar hallazgo finding-candidate" })).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios/i })).toHaveCount(0);
  await expect(dialog.getByText("El servidor no concedió quality:decide; las decisiones protegidas están ocultas.", { exact: true })).toBeVisible();
  await page.goto("/analysis/run-current");
  await page.getByText("Información técnica del análisis").click();
  await expect(page.getByRole("heading", { name: "Repositorios del proyecto" }).first()).toBeVisible();
  await expect(page.getByText(/disponibilidad.*desconocida/i)).toBeVisible();
  await expect(page.getByText(/hormigestion-backend|hormigestion-frontend/)).toHaveCount(0);
});
