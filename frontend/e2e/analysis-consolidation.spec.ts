import { expect, test } from "@playwright/test";
import { analysisPreparation, installApi, project, runCurrent } from "./fixtures";

/**
 * The consolidated analysis centre: one history, one verified reason, one
 * indicator and an executive summary that a manager can read before any
 * technical detail.
 */

test("el indicador global sigue el análisis al cambiar de sección y tras recargar", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["running"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();

  const indicator = page.locator(".analysis-status-indicator");
  await expect(indicator).toBeVisible();
  await expect(indicator).toContainText("Análisis en curso");
  await expect(indicator).toContainText("hormigestio");

  await page.goto("/tasks");
  await expect(page.locator(".analysis-status-indicator")).toContainText("Análisis en curso");

  await page.reload();
  await expect(page.locator(".analysis-status-indicator")).toContainText("Análisis en curso");
});

test("al terminar el análisis el aviso ofrece el acceso a los resultados", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["running", "completed"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();

  const notice = page.locator(".toast").filter({ hasText: "El análisis terminó" });
  await expect(notice).toBeVisible();
  await expect(notice.getByRole("link", { name: "Ver resultados" })).toHaveAttribute("href", "/analysis/run-current");
  await expect(page.locator(".analysis-status-indicator")).toContainText("Análisis finalizado");
});


const analysisJobWithoutRun = {
  job_id: "webjob-aaaaaaaaaaaaaaaaaaaaaaaa",
  project_id: "hormigestio",
  status: "failed",
  created_at: "2026-01-11T09:00:00-05:00",
  updated_at: "2026-01-11T09:05:00-05:00",
  run_id: null,
  error_code: "pipeline_failed",
  remote_consent: true,
  execution_mode: "sandbox",
  fallback_reasons: [],
  stage_progress_available: false,
};

test("el indicador recupera tras recargar el estado del último análisis, aunque ya haya terminado", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.route((url) => url.pathname.endsWith("/api/v1/analysis/jobs"), async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: {
      items: [{
        job_id: "webjob-bbbbbbbbbbbbbbbbbbbbbbbb",
        project_id: "hormigestio",
        status: "completed",
        created_at: "2026-01-12T10:10:00-05:00",
        updated_at: "2026-01-12T10:12:00-05:00",
        run_id: "run-current",
        error_code: null,
        remote_consent: true,
        execution_mode: "sandbox",
        fallback_reasons: [],
        stage_progress_available: true,
      }],
      total: 1,
      page: 1,
      page_size: 100,
    } });
  });
  await page.route((url) => url.pathname.endsWith("/api/v1/analysis/jobs/webjob-bbbbbbbbbbbbbbbbbbbbbbbb"), async (route) => {
    await route.fulfill({ json: {
      job_id: "webjob-bbbbbbbbbbbbbbbbbbbbbbbb",
      project_id: "hormigestio",
      status: "completed",
      created_at: "2026-01-12T10:10:00-05:00",
      updated_at: "2026-01-12T10:12:00-05:00",
      run_id: "run-current",
      error_code: null,
      remote_consent: true,
      execution_mode: "sandbox",
      fallback_reasons: [],
      stage_progress_available: true,
    } });
  });

  await page.goto("/tasks");
  const indicator = page.locator(".analysis-status-indicator");
  await expect(indicator).toContainText("Análisis finalizado");
  await expect(indicator).toHaveAttribute("href", "/analysis/run-current");

  await page.reload();
  await expect(page.locator(".analysis-status-indicator")).toContainText("Análisis finalizado");
  await expect(page.locator(".analysis-status-indicator")).toHaveAttribute("href", "/analysis/run-current");
});
