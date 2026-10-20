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

test("el historial identifica cada análisis por su fecha y muestra el motivo verificado", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis");

  const rows = page.locator(".run-history-list > li");
  await expect(rows).toHaveCount(2);

  // La fecha y la hora son la identificación principal; el identificador técnico sigue disponible.
  const first = rows.first();
  await expect(first.locator(".run-history-main strong")).toContainText("12 ene 2026");
  await expect(first.locator(".run-history-main code")).toHaveText("run-current");
  await expect(first.getByText("Completada", { exact: true })).toBeVisible();
  await expect(first.getByText("Más reciente", { exact: true })).toBeVisible();
  await expect(first.getByText("Sin motivos registrados", { exact: true })).toBeVisible();

  // Un análisis que requiere atención muestra el motivo concreto, no un estado genérico.
  const second = rows.nth(1);
  await expect(second.getByText("Requiere atención", { exact: true })).toBeVisible();
  await expect(second.getByText("El repositorio backend quedó incompleto en «Revisión de calidad»", { exact: true })).toBeVisible();
  await expect(second.getByText("Más reciente", { exact: true })).toHaveCount(0);
});

test("un trabajo sin ejecución asociada se conserva sin duplicar la lista de análisis", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.route("**/api/v1/analysis/jobs**", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: { items: [analysisJobWithoutRun], total: 1, page: 1, page_size: 100 } });
  });
  await page.goto("/analysis");

  await expect(page.getByRole("heading", { name: "Análisis realizados" })).toBeVisible();
  await expect(page.locator(".run-history-list > li")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Trabajos sin ejecución asociada" })).toBeVisible();
  await expect(page.getByText("webjob-aaaaaaaaaaaaaaaaaaaaaaaa", { exact: true })).toBeVisible();
});

test("un trabajo con ejecución asociada no aparece dos veces en el historial", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.route("**/api/v1/analysis/jobs**", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: {
      items: [{ ...analysisJobWithoutRun, run_id: "run-current", status: "completed", error_code: null }],
      total: 1,
      page: 1,
      page_size: 100,
    } });
  });
  await page.goto("/analysis");

  await expect(page.locator(".run-history-list > li")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Trabajos sin ejecución asociada" })).toHaveCount(0);
});

test("la preparación explica el proyecto, las fuentes y las limitaciones antes de los detalles técnicos", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["analysis:prepare", "analysis:confirm"] });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  const facts = page.locator(".preparation-facts");
  await expect(facts.getByText("Proyecto", { exact: true })).toBeVisible();
  await expect(facts.getByText("Fuentes que se consultarán", { exact: true })).toBeVisible();
  await expect(facts.getByText("Disponibilidad de la configuración", { exact: true })).toBeVisible();
  await expect(facts.getByText("Reglas de propuestas no configuradas", { exact: true })).toBeVisible();
  await expect(facts.getByText("Autorizaciones necesarias", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar análisis" })).toBeVisible();

  // Los proveedores, modelos y credenciales quedan en los detalles técnicos.
  await expect(page.locator(".remote-preflight")).toBeHidden();
  await page.getByText("Detalles técnicos del preflight").click();
  await expect(page.locator(".remote-preflight")).toBeVisible();
  await expect(page.getByText("Gemini · gemini-fixture")).toBeVisible();
});

test("la preparación distingue las reglas de propuestas configuradas de las que no lo están", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    preparePayload: {
      preflight: {
        ...analysisPreparation.preflight,
        proposal_rules_status: "configured",
      },
    },
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  await expect(page.locator(".preparation-facts").getByText("Reglas de propuestas cargadas", { exact: true })).toBeVisible();
  await expect(page.getByText("El análisis puede publicar propuestas correctivas a partir de las reglas del propietario.", { exact: true })).toBeVisible();
});

test("el detalle de ejecución muestra el resumen ejecutivo antes que la información técnica", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis/run-current");

  await expect(page.getByRole("heading", { name: "Qué quedó de este análisis" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Riesgos, hallazgos y limitaciones" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recomendaciones y acciones pendientes" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Siguientes pasos disponibles" })).toBeVisible();

  // La información técnica existe, pero permanece secundaria hasta que se pide.
  await expect(page.getByText("Información técnica del análisis")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Etapas del pipeline" })).toBeHidden();

  // Las recomendaciones son las del informe, no una conclusión de la interfaz.
  await expect(page.getByText("Confirmar una nueva fecha con el responsable.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Leer el informe completo" })).toHaveAttribute("href", "/reports?report_id=report-current");
});

test("el resumen distingue los candidatos de los defectos confirmados", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis/run-current");

  await expect(page.getByText(/son candidatos/i)).toBeVisible();
  await expect(page.getByText("Candidatos registrados", { exact: true })).toBeVisible();
  await expect(page.getByText("Confirmados por el verificador", { exact: true })).toBeVisible();
  await expect(page.getByText("Sin veredicto", { exact: true })).toBeVisible();
});

test("un análisis fallido explica el motivo registrado en lugar de un estado genérico", async ({ page }) => {
  await installApi(page);
  await page.route((url) => url.pathname.endsWith("/api/v1/runs/run-failed"), async (route) => {
    await route.fulfill({ json: {
      ...runCurrent,
      run_id: "run-failed",
      status: "failed",
      report_id: null,
      attention: [{
        reason_code: "run_failed",
        kind: "failure",
        scope: "run",
        stage_name: "monitoring",
        message: "Monitoring stage failed: Provider failed in stage 'monitoring'.",
      }],
    } });
  });
  await page.goto("/analysis/run-failed");

  await expect(page.getByRole("heading", { name: "Qué quedó de este análisis" })).toBeVisible();
  await expect(page.getByText("Fallo", { exact: true })).toBeVisible();
  await expect(page.getByText("El análisis falló", { exact: true })).toBeVisible();
  await expect(page.getByText("Monitoring stage failed: Provider failed in stage 'monitoring'.", { exact: true })).toBeVisible();
});

test("un análisis sin motivos registrados no se presenta como un resultado satisfactorio", async ({ page }) => {
  await installApi(page);
  await page.route((url) => url.pathname.endsWith("/api/v1/runs/run-unknown"), async (route) => {
    await route.fulfill({ json: {
      ...runCurrent,
      run_id: "run-unknown",
      status: "unknown",
      report_id: null,
      attention: [{
        reason_code: "stage_not_recorded",
        kind: "unknown",
        scope: "stage",
        stage_name: "verifier",
        message: "La etapa no dejó registro persistido; su resultado se desconoce.",
      }],
    } });
  });
  await page.goto("/analysis/run-unknown");

  await expect(page.getByText("Información desconocida", { exact: true })).toBeVisible();
  await expect(page.getByText("La etapa «Verificación de hallazgos» no dejó registro", { exact: true })).toBeVisible();
});

test("el cambio de proyecto y los permisos respetan el aislamiento", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.goto("/analysis");

  // Sin la capacidad de confirmar, la preparación se puede revisar pero no confirmar.
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await expect(page.locator(".preparation-result").getByText(/Tu sesión no tiene permiso para confirmar/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
});

test("la lista de análisis pertenece al proyecto seleccionado", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["audit:read"],
    projectsPayload: { items: [project, { ...project, id: "otro-proyecto", name: "Otro proyecto" }], total: 2, page: 1, page_size: 100 },
  });
  await page.goto("/analysis");
  await expect(page.getByRole("heading", { name: "Analizar HORMIGESTION" })).toBeVisible();

  await page.locator(".topbar").getByLabel("Proyecto activo").selectOption("otro-proyecto");
  await expect(page.getByRole("heading", { name: "Analizar Otro proyecto" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Análisis realizados" })).toBeVisible();
});
