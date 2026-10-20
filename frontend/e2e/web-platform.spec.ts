import { expect, test, type Page, type Route } from "@playwright/test";
import { summary, project, secondProject, tasks, allTasks, history, runCurrent, runPrevious, runHistory, stageRecords, candidateFinding, findingDetail, quality, policies, executiveSummary, analysisPreparation, analysisJob, comparison, proposalSummary, proposalDetail, reportSummary, reportDetail, auditEvent, unknownAuditEvent, outboxReceipt, weeklySchedule, weeklyWindow, scheduleView, outboxMessage, outboxDetail, schedulePreparation, weeklyCycleJob, projectConfiguration, auditCollection, installApi, installBrowserNetworkGuard, assertNoBrowserNetworkAttempts, managerCredentials } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

test("el dashboard enlaza estados y señales desde conteos del backend sin sumar categorías solapadas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("0", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Próximas a vencer/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Requieren atención/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Señales de alerta por tipo" })).toBeVisible();
  await expect(page.getByText("Las categorías de alerta se solapan; no deben sumarse como un total.")).toBeVisible();
  await expect(page.getByRole("link", { name: /Requieren atención/ })).toHaveAttribute("href", "/tasks?critical_only=true");

  await page.getByRole("link", { name: /Pendientes/ }).click();
  await expect(page).toHaveURL(/state=pending/);
  const selectedState = await page.locator('select[aria-label="Estado"]').evaluate((element) => (element as HTMLSelectElement).value);
  expect(selectedState).toBe("pending");
});

test("mantiene visibles los KPI y gráficos del dashboard en una pantalla móvil", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Requieren atención/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Señales de alerta por tipo" })).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(hasHorizontalOverflow).toBe(false);
});

test("los centros de análisis y calidad conservan el layout móvil sin desbordamiento", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/analysis?run_id=run-current");
  await expect(page.getByRole("heading", { name: /^Análisis del / })).toBeVisible();
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);

  await page.goto("/quality");
  await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  await expect(page.getByRole("dialog", { name: /Hallazgo candidato/ })).toBeVisible();
  overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("no convierte conteos de estado ausentes en ceros locales", async ({ page }) => {
  await installApi(page, { summaryPayload: { ...summary, state_counts: { en_curso: 1 } } });
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("Sin dato")).toBeVisible();
  await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
});

test("no dibuja tendencias temporales a partir de una sola captura", async ({ page }) => {
  await installApi(page, { historyPayload: { ...history, items: [history.items[0]], total: 1 } });
  await page.goto("/");

  const historySection = page.locator(".history-section");
  await expect(historySection.getByRole("heading", { name: "Una sola captura disponible" })).toBeVisible();
  await expect(historySection.getByRole("img")).toHaveCount(0);
});

test("un error en la consulta crítica permanece desconocido y no se presenta como cero", async ({ page }) => {
  await installApi(page, { criticalStatus: 503 });
  await page.goto("/");

  const criticalMetric = page.getByRole("link", { name: /Requieren atención/ });
  await expect(criticalMetric.getByText("Sin dato")).toBeVisible();
  await expect(criticalMetric.getByText("0", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No se pudieron cargar las tareas que requieren atención" })).toBeVisible();
});

test("el dashboard presenta políticas e historial de ejecuciones solo con registros reales", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Informe y políticas de calidad" })).toBeVisible();
  await expect(page.getByText("Revisar los hallazgos con evidencia insuficiente.", { exact: true })).toBeVisible();
  const policyPanel = page.locator(".executive-policies-card");
  await expect(policyPanel.getByText("Q1", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Q2", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Q3", { exact: true })).toHaveCount(0);
  await expect(policyPanel.getByText("Desconocido", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Observado: Sin dato", { exact: true })).toBeVisible();
  await expect(page.locator(".executive-report-card").getByText(/Fecha de corte:/)).toBeVisible();
  await expect(page.getByRole("img", { name: "Historial de análisis por estado" })).toBeVisible();
});

test("el centro de IA consulta el historial sin iniciar trabajos al abrirlo", async ({ page }) => {
  const apiObserver = await installApi(page);
  const { analysisWrites } = apiObserver;
  await page.goto("/analysis");

  await expect(page.getByRole("heading", { name: "Centro de análisis de IA" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Análisis realizados", exact: true })).toBeVisible();
  await expect(page.getByText("run-current", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Nuevo análisis" })).toBeDisabled();
  await expect(page.getByText("run-current", { exact: true })).toBeVisible();
  await expect(page.getByText("Sin motivos registrados", { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(0);
  expect(apiObserver.analysisJobListReads).toBe(0);
});

test("una etapa desconocida no se presenta como omitida y una omisión requiere registro explícito", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis?run_id=run-current");
  await page.getByText("Información técnica del análisis").click();

  const pipeline = page.getByRole("region", { name: "Etapas del pipeline" });
  const card = (name: string) => pipeline.getByRole("article").filter({
    has: page.getByRole("heading", { name, exact: true }),
  });
  await expect(card("Verificación de hallazgos").getByText("No registrado; estado desconocido", { exact: true })).toBeVisible();
  await expect(card("Elaboración del informe").getByText("Omitida explícitamente", { exact: true })).toBeVisible();
  await expect(page.getByText("Fuentes almacenadas consultadas", { exact: true })).toBeVisible();
  await expect(page.getByText("local · fixture-monitor", { exact: true })).toBeVisible();
});

test("pipeline stage states follow recorded public records", async ({ page }) => {
  await installApi(page);
  await page.route("**/api/v1/runs/run-current/stages**", async (route) => {
    await route.fulfill({ json: {
      ...stageRecords,
      items: [
        { ...stageRecords.items[0], status: "completed", recorded: true },
        { ...stageRecords.items[1], status: "failed", recorded: true },
        { ...stageRecords.items[2], status: "skipped", recorded: true },
        { ...stageRecords.items[3], status: "unknown", recorded: true },
        { ...stageRecords.items[4], status: "skipped", recorded: false },
        { ...stageRecords.items[5], status: "completed", recorded: true },
      ],
    } });
  });
  await page.goto("/analysis/run-current");
  await page.getByText("Información técnica del análisis").click();
  const pipeline = page.getByRole("region", { name: "Etapas del pipeline" });
  const card = (name: string) => pipeline.getByRole("article").filter({
    has: page.getByRole("heading", { name, exact: true }),
  });
  await expect(card("Recopilación de fuentes").getByText("Completada", { exact: true })).toBeVisible();
  await expect(card("Seguimiento del proyecto").getByText("Fallida", { exact: true })).toBeVisible();
  await expect(card("Propuestas generadas").getByText("Omitida explícitamente", { exact: true })).toBeVisible();
  await expect(card("Revisión de calidad").getByText("Desconocida", { exact: true })).toBeVisible();
  await expect(card("Verificación de hallazgos").getByText("No registrado; estado desconocido", { exact: true })).toBeVisible();
  await expect(card("Elaboración del informe").getByText("Completada", { exact: true })).toBeVisible();
  await expect(card("Elaboración del informe").getByText("Omitida explícitamente", { exact: true })).toHaveCount(0);
});

test("la calidad conserva hallazgos candidatos, verificaciones y fragmentos como texto", async ({ page }) => {
  await installApi(page);
  await page.goto("/quality");

  await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  await expect(page.getByText("Hallazgo candidato", { exact: true })).toBeVisible();
  await expect(page.locator(".finding-list").getByText("Evidencia insuficiente", { exact: true })).toBeVisible();
  await expect(page.getByText(/CI.*no disponible/i)).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  await expect(page.getByRole("dialog", { name: /Hallazgo candidato/ })).toBeVisible();
  await expect(page.getByText("El fragmento mostrado es parcial.", { exact: true })).toBeVisible();
  await expect(page.locator("pre")).toContainText("<script>window.__findingXss = true</script>");
  expect(await page.evaluate(() => (window as Window & { __findingXss?: boolean }).__findingXss)).toBeUndefined();
});

test("la preparación y la confirmación son pasos protegidos independientes con consentimiento remoto", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["running", "completed"],
  });
  const { analysisWrites } = apiObserver;
  await page.goto("/analysis");
  await expect(page.getByRole("button", { name: "Nuevo análisis" })).toBeEnabled();
  expect(analysisWrites).toHaveLength(0);

  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await expect(page.getByText("La revisión no contacta servicios en la nube.", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /uso de servicios en la nube/i })).not.toBeChecked();
  const confirmButton = page.getByRole("button", { name: "Confirmar análisis" });
  await expect(confirmButton).toBeDisabled();
  expect(analysisWrites).toHaveLength(1);
  expect(analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
  expect(JSON.parse(analysisWrites[0].body ?? "{}")).toEqual({ request_analysis: true });
  expect(analysisWrites[0].csrfToken).toBe("csrf-fixture");

  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();
  await expect(page.locator(".active-job-panel").getByText("Completado", { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(2);
  expect(analysisWrites[1].path).toBe("/api/v1/projects/hormigestio/analysis/confirm");
  expect(JSON.parse(analysisWrites[1].body ?? "{}")).toEqual({
    preparation_id: "preparation-fixture-12345678",
    configuration_fingerprint: "a".repeat(64),
    confirm_analysis: true,
    remote_consent: true,
  });
  expect(analysisWrites[1].csrfToken).toBe("csrf-fixture");
  expect(analysisWrites[1].idempotencyKey).toBeTruthy();
  expect(apiObserver.jobPollRequests).toBe(2);
  await page.waitForTimeout(350);
  expect(apiObserver.jobPollRequests).toBe(2);
});

test("la falta de una clave de IA permite confirmar el análisis mixto sin omitir consentimiento remoto", async ({ page }) => {
  const fallbackReason = "Sin clave para el proveedor de IA; la revisión se realizará localmente.";
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    preparePayload: {
      eligible: true,
      ready: true,
      execution_mode: "mixed",
      fallback_reasons: [fallbackReason],
      remote: {
        required: true,
        server_allowed: true,
        availability: "not_checked",
        providers: [
          { stage: "source_collection", provider: "trello", model: "not_applicable", transport: "remote", credential_status: "configured" },
          { stage: "quality", provider: "gemini", model: "gemini-2.5-flash", transport: "remote", credential_status: "missing" },
          { stage: "quality", provider: "local", model: "fixture-quality", transport: "local", credential_status: "not_required" },
        ],
      },
    },
    analysisJobPayload: { execution_mode: "mixed", fallback_reasons: [fallbackReason] },
    jobPollStatuses: ["running", "completed"],
  });
  const { analysisWrites } = apiObserver;
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  await expect(page.getByText("Modo de ejecución: Mixto", { exact: true })).toBeVisible();
  await expect(page.getByText(fallbackReason, { exact: true })).toBeVisible();
  const preparationFacts = page.locator(".preparation-facts");
  await expect(preparationFacts.getByText("Fuentes que se consultarán", { exact: true })).toBeVisible();
  await expect(preparationFacts.getByText("Trello", { exact: true })).toBeVisible();
  await page.getByText("Detalles técnicos del preflight").click();
  await expect(page.locator(".remote-preflight").getByText("Recopilación de fuentes: Trello", { exact: true })).toBeVisible();
  await expect(page.getByText(/not_applicable/)).toHaveCount(0);
  await expect(page.getByText("Gemini · gemini-2.5-flash")).toBeVisible();
  await expect(page.getByText("Acceso: No disponible")).toBeVisible();
  const confirmButton = page.getByRole("button", { name: "Confirmar análisis" });
  await expect(confirmButton).toBeDisabled();
  const consent = page.getByRole("checkbox", { name: /uso de servicios en la nube/i });
  await expect(consent).not.toBeChecked();
  await consent.check();
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();

  const activeJob = page.getByRole("region", { name: "Análisis del proyecto HORMIGESTION" });
  await expect(activeJob.getByText("Completado", { exact: true })).toBeVisible();
  await expect(activeJob.getByText("Modo de ejecución", { exact: true })).toBeVisible();
  await expect(activeJob.getByText("Mixto", { exact: true })).toBeVisible();
  await expect(activeJob.getByText("Alternativas aplicadas", { exact: true })).toBeVisible();
  await expect(activeJob.getByText(fallbackReason, { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(2);
  expect(analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
  expect(analysisWrites[0].csrfToken).toBe("csrf-fixture");
  expect(JSON.parse(analysisWrites[0].body ?? "{}")).toEqual({ request_analysis: true });
  expect(analysisWrites[1].path).toBe("/api/v1/projects/hormigestio/analysis/confirm");
  expect(JSON.parse(analysisWrites[1].body ?? "{}")).toMatchObject({ confirm_analysis: true, remote_consent: true });
  expect(analysisWrites[1].csrfToken).toBe("csrf-fixture");
  expect(analysisWrites[1].idempotencyKey).toBeTruthy();
  expect(apiObserver.jobPollRequests).toBe(2);
});

test("una preparación genuinamente no elegible nunca habilita la confirmación", async ({ page }) => {
  const { analysisWrites } = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    preparePayload: { eligible: false, ready: false, execution_mode: "sandbox" },
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  await expect(page.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  await expect(page.getByText(/La revisión aún no cumple las condiciones/)).toBeVisible();
  expect(analysisWrites.map((write) => write.path)).toEqual(["/api/v1/projects/hormigestio/analysis/prepare"]);
});

test("la interfaz no inicia trabajo si la sesión carece de la capacidad de preparación", async ({ page }) => {
  const { analysisWrites } = await installApi(page, { authenticated: true, capabilities: ["operator_session"] });
  await page.goto("/analysis");

  await expect(page.getByRole("button", { name: "Nuevo análisis" })).toBeDisabled();
  await expect(page.getByText(/no tiene permiso para revisar un nuevo análisis/)).toBeVisible();
  expect(analysisWrites).toHaveLength(0);
});

test("la capacidad de preparación no sustituye la capacidad de confirmación", async ({ page }) => {
  const { analysisWrites } = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();

  await expect(page.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  await expect(page.locator(".preparation-result").getByText(/Tu sesión no tiene permiso para confirmar/)).toBeVisible();
  expect(analysisWrites.map((write) => write.path)).toEqual(["/api/v1/projects/hormigestio/analysis/prepare"]);
});

test("un rechazo de autorización del servidor se muestra y no crea un trabajo", async ({ page }) => {
  const { analysisWrites } = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    prepareStatus: 403,
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  await expect(page.getByRole("heading", { name: "No se pudo preparar el análisis" })).toBeVisible();
  await expect(page.getByText("La preparación no está autorizada por el servidor.", { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(1);
  expect(analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
});

test("un reintento de confirmación conserva la misma clave de idempotencia", async ({ page }) => {
  const { analysisWrites } = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    confirmStatuses: [503, 202],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.getByRole("heading", { name: "No se pudo confirmar el análisis" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /uso de servicios en la nube/i })).toBeDisabled();
  await page.getByRole("button", { name: "Intentar de nuevo" }).click();

  await expect(page.locator(".active-job-panel").getByText("En cola", { exact: true })).toBeVisible();
  const confirmations = analysisWrites.filter((write) => write.path.endsWith("/analysis/confirm"));
  expect(confirmations).toHaveLength(2);
  expect(confirmations[0].idempotencyKey).toBeTruthy();
  expect(confirmations[1].idempotencyKey).toBe(confirmations[0].idempotencyKey);
  expect(JSON.parse(confirmations[1].body ?? "{}")).toEqual(JSON.parse(confirmations[0].body ?? "{}"));
});

test("el polling de trabajos se detiene en el estado terminal fallido", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["failed"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.locator(".active-job-panel").getByText("Fallido", { exact: true })).toBeVisible();
  await expect(page.getByText("El trabajo no se completó correctamente. Revisa el historial o solicita ayuda al administrador del proyecto.", { exact: true })).toBeVisible();
  // El polling se detiene: el contador deja de crecer en el estado terminal.
  const settled = apiObserver.jobPollRequests;
  await page.waitForTimeout(1200);
  expect(apiObserver.jobPollRequests).toBe(settled);
});

test("el polling de trabajos se detiene en el estado terminal interrumpido", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["interrupted"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await page.getByRole("checkbox", { name: /uso de servicios en la nube/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.locator(".active-job-panel").getByText("Interrumpido", { exact: true })).toBeVisible();
  await expect(page.getByText("El trabajo no se completó correctamente. Revisa el historial o solicita ayuda al administrador del proyecto.", { exact: true })).toBeVisible();
  // El polling se detiene: el contador deja de crecer en el estado terminal.
  const settled = apiObserver.jobPollRequests;
  await page.waitForTimeout(1200);
  expect(apiObserver.jobPollRequests).toBe(settled);
});

test("conecta dashboard, filtros URL y comparación basada en capturas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
  await expect(page.getByText("VISTA GENERAL · HORMIGESTION")).toBeVisible();
  const totalMetric = page.getByRole("link", { name: /Tareas registradas/ });
  await expect(totalMetric.getByText("26", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Tareas", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks/);
  await page.getByLabel("Buscar tareas", { exact: true }).fill("Implementar API");
  await page.getByLabel("Estado").selectOption("en_curso");
  await page.getByLabel("ID de responsable").fill("operator-1");
  await page.getByLabel("Fase o lista").fill("Construcción");
  await page.getByLabel("Solo con alertas").click();
  await expect(page.getByLabel("Solo con alertas")).toBeChecked();
  await expect(page).toHaveURL(/q=Implementar/);
  await expect(page).toHaveURL(/state=en_curso/);
  await expect(page).toHaveURL(/assignee_id=operator-1/);
  await expect(page).toHaveURL(/list_name=Construcci%C3%B3n/);
  await expect(page).toHaveURL(/critical_only=true/);

  await page.getByRole("button", { name: /Implementar API de pagos/ }).click();
  await expect(page.getByRole("dialog", { name: /Implementar API de pagos/ })).toBeVisible();
  await expect(page.getByText("Prueba observada en el tablero").first()).toBeVisible();
  await page.getByRole("button", { name: "Ver cambios" }).click();
  await expect(page.getByRole("deletion")).toHaveText("Pendiente");
  await expect(page.locator(".diff-after")).toHaveText("En curso");
});

test("el inicio de sesión informa errores de credenciales sin habilitar acciones de gestor", async ({ page }) => {
  await installApi(page, { loginStatus: 401 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await page.getByRole("button", { name: "Abrir navegación" }).click();
  await expect(page.getByRole("link", { name: "Propuestas", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cerrar navegación" }).click();
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await page.getByLabel("Correo electrónico").fill(managerCredentials.email);
  await page.getByLabel("Contraseña").fill("incorrecta");
  await page.getByRole("button", { name: "Iniciar Sesión como Gestor" }).click();

  await expect(page.getByRole("alert")).toHaveText("Correo electrónico o contraseña incorrectos. Verifique sus credenciales de Gestor del Proyecto.");
  await expect(page.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sincronizar proyecto" })).toHaveCount(0);
  const stored = await page.evaluate(() => Object.values(localStorage));
  expect(stored).not.toContain("incorrecta");
});

test("credenciales desconocidas o incorrectas no desbloquean el área de gestor", async ({ page }) => {
  await installApi(page, { requireLogin: true });
  await page.goto("/");

  for (const credentials of [
    { email: "cualquiera@test.com", password: "wrong-password" },
    { email: managerCredentials.email, password: "wrong-password" },
  ]) {
    await page.getByLabel("Correo electrónico").fill(credentials.email);
    await page.getByLabel("Contraseña").fill(credentials.password);
    await page.getByRole("button", { name: "Iniciar Sesión como Gestor" }).click();
    await expect(page.getByRole("alert")).toHaveText("Correo electrónico o contraseña incorrectos. Verifique sus credenciales de Gestor del Proyecto.");
    await expect(page.getByRole("heading", { name: "Acceso de operador" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sincronizar proyecto" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Propuestas", exact: true })).toHaveCount(0);
  }

  const missingPasswordStatus = await page.evaluate(async (email) => {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    return response.status;
  }, managerCredentials.email);
  expect(missingPasswordStatus).toBe(401);
});

test("explica una API no disponible en vez de inventar datos", async ({ page }) => {
  await installApi(page, { projectStatus: 503 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /No se pudo cargar|Error al cargar/ })).toBeVisible();
  await expect(page.getByText(/No hay datos|0 tareas/)).toHaveCount(0);
});

test("la sesión usa la identidad verificada del servidor y cierra con protección CSRF", async ({ page }) => {
  await installApi(page);
  await page.addInitScript(() => {
    try { sessionStorage.setItem("gestionpro_operator_email", "falsified@example.org"); } catch {}
  });
  await page.goto("/");

  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await expect(page.getByLabel("Correo electrónico")).toHaveAttribute("type", "email");
  await expect(page.getByLabel("Correo electrónico")).toHaveAttribute("required", "");
  await expect(page.getByLabel("Contraseña")).toHaveAttribute("required", "");
  await page.getByLabel("Correo electrónico").fill(managerCredentials.email);
  await page.getByLabel("Contraseña").fill(managerCredentials.password);
  await page.getByRole("button", { name: "Iniciar Sesión como Gestor" }).click();
  await expect(page.getByRole("banner").getByText(managerCredentials.email, { exact: true })).toBeVisible();
  await expect(page.getByText("falsified@example.org", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
  await expect(page.getByText("Consulta de solo lectura")).toBeVisible();

  const stored = await page.evaluate(() => Object.values(sessionStorage));
  expect(stored).not.toContain(managerCredentials.email);
});

test("ordena y pagina tareas actualizando la consulta persistente", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks");

  await page.getByRole("button", { name: "Tarea" }).click();
  await expect(page).toHaveURL(/sort=title/);
  await expect(page).toHaveURL(/direction=asc/);
  await page.getByRole("button", { name: "Tarea" }).click();
  await expect(page).toHaveURL(/direction=desc/);

  await page.getByRole("button", { name: "Página siguiente" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText(/Mostrando página 2 de 2/)).toBeVisible();
});

test("cambia el tema y conecta las rutas de gobierno sin crear acciones decorativas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Cambiar a modo oscuro" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => localStorage.getItem("h5-theme"))).toBe("dark");
  await expect(page.getByRole("link", { name: /Propuestas, módulo pendiente/ })).toHaveCount(0);
  await page.getByRole("link", { name: "Propuestas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Propuestas", exact: true })).toBeVisible();
  await expect(page.getByText("Pendiente de integración")).toHaveCount(0);
});

test("pagina la cartera de proyectos usando la respuesta del backend", async ({ page }) => {
  await installApi(page, { projectPagination: true });
  await page.goto("/projects");

  await expect(page.getByRole("heading", { name: "Proyectos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "HORMIGESTION", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Página siguiente de proyectos" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByRole("heading", { name: "Proyecto secundario" })).toBeVisible();
});

test("expone desconocidos como desconocidos cuando falta evidencia de responsable y prioridad", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?state=desconocido");

  await expect(page.locator(".task-table").getByText("Desconocido", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Ver detalle de Validar sincronización", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Validar sincronización" })).toBeVisible();
  await expect(page.getByText("No disponible en la fuente")).toBeVisible();
  await expect(page.getByText("No observado", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("La respuesta no incluye información de evidencia para esta tarea.")).toBeVisible();
});

test("el foco de ruta no dibuja un marco negro y el enlace de salto conserva navegación por teclado", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const outlineStyle = await page.locator("#main-content").evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outlineStyle).toBe("none");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Saltar al contenido principal" })).toBeFocused();
  await page.keyboard.press("Enter");
  const main = page.locator("#main-content");
  await expect(main).toBeFocused();
  const focusTreatment = await main.evaluate((element) => getComputedStyle(element).boxShadow);
  expect(focusTreatment).not.toBe("none");
});

test("configuración distingue metadatos reales de preferencias locales y no expone secretos", async ({ page }) => {
  const apiMutations: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/") && !["GET", "HEAD"].includes(request.method())) {
      apiMutations.push(`${request.method()} ${path}`);
    }
  });
  await installApi(page);
  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "Configuración", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preferencias de interfaz" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Proyecto seleccionado" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "HORMIGESTION", exact: true })).toBeVisible();
  await expect(page.getByText("trello", { exact: true })).toBeVisible();
  await expect(page.getByText("Escrituras externas deshabilitadas", { exact: true })).toBeVisible();
  await expect(page.getByText("fixture", { exact: true })).toBeVisible();

  await page.getByRole("radio", { name: "Oscuro" }).check();
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("dark");
  const credentialLikeKeys = await page.evaluate(() => Object.keys(localStorage).filter((key) => /token|secret|credential/i.test(key)));
  expect(credentialLikeKeys).toEqual([]);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  expect(apiMutations).toEqual([]);
});

test("las preferencias de configuración funcionan con teclado y en ancho móvil", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");

  const lightChoice = page.getByRole("radio", { name: "Claro" });
  const darkChoice = page.getByRole("radio", { name: "Oscuro" });
  await lightChoice.focus();
  await page.keyboard.press("ArrowRight");
  await expect(darkChoice).toBeChecked();
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("dark");
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.client);
});

test("la revisión determinista informa sus límites y la decisión protegida produce un resultado sandbox explícito", async ({ page }) => {
  const technicalReview = {
    review_id: "technical-review-001",
    technical_status: "ready",
    technical_errors: [],
    dry_run: { status: "SIMULATED", reasons: [], field: "due_date", before: proposalDetail.current_value, after: proposalDetail.proposed_value, live_state_changed: false },
    human_approval_status: "required",
    execution_authorized: false,
    recorded_at: "2026-01-12T10:20:00-05:00",
  };
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: ["operator_session", "governance:proposal_execute", "audit:read"],
    proposalFixture: { ...proposalDetail, technical_review: technicalReview },
  });
  await page.goto("/proposals");
  await expect(page.getByRole("heading", { name: "Propuestas", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Abrir propuesta para Fecha de vencimiento" }).click();
  await expect(page.getByRole("heading", { name: "Revisión técnica lista", exact: true })).toBeVisible();
  await expect(page.getByText("Decisión pendiente", { exact: true })).toBeVisible();
  await expect(page.getByText("No autorizada", { exact: true })).toBeVisible();
  await expect(page.getByText(/No es una revisión independiente de código ni una evaluación de un parche/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /Aprobar para revisión local|Autorizar simulación segura|Simular propuesta autorizada/ })).toHaveCount(0);
  await page.getByLabel("Motivo de la decisión").fill("El cambio se acepta con base en la revisión técnica.");
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toBeVisible();

  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0].path).toBe("/api/v1/projects/hormigestio/proposals/proposal-date-001/execute");
  expect(observer.governanceRequests[0].csrfToken).toBe("csrf-fixture");
  expect(observer.governanceRequests[0].idempotencyKey).toBeTruthy();
  expect(JSON.parse(observer.governanceRequests[0].body ?? "{}")).toEqual({
    decision: "approve_and_execute",
    reason: "El cambio se acepta con base en la revisión técnica.",
  });
  expect(observer.governanceRequests.some((item) => /\/review$|dry-run-authorization|\/dry-runs$/.test(item.path))).toBe(false);
});

test("la sesión de consulta no ofrece decisiones falsas ni controles de propuesta", async ({ page }) => {
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session"] });
  await page.goto("/proposals?proposal_id=proposal-date-001");
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Modificar el campo recomendado", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toHaveCount(0);
  expect(observer.governanceRequests).toHaveLength(0);
});

test("un rechazo HTTP del servidor se muestra y no activa una decisión local", async ({ page }) => {
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: ["operator_session", "governance:proposal_execute", "audit:read"],
    proposalExecutionStatus: 403,
  });
  await page.goto("/proposals?proposal_id=proposal-date-001");
  await page.getByLabel("Motivo de la decisión").fill("El servidor rechazó esta ejecución.");
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("La ejecución no está disponible por el servidor.");
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0].path).toBe("/api/v1/projects/hormigestio/proposals/proposal-date-001/execute");
  expect(observer.governanceRequests.some((item) => /\/review$|dry-run-authorization|\/dry-runs$/.test(item.path))).toBe(false);
});

test("los informes protegen Markdown, separan la decisión humana y descargan solo artefactos del backend", async ({ page }) => {
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("beacon.invalid")) externalRequests.push(request.url());
  });
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "reports:decide"] });
  await page.goto("/reports");
  await expect(page.getByRole("heading", { name: "Informes", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Abrir informe report-001" }).click();
  await expect(page.getByRole("heading", { name: "Informe report-001" })).toBeVisible();
  await expect(page.getByText("Resumen almacenado del informe.", { exact: true })).toBeVisible();
  await expect(page.getByText("No se registró CI.", { exact: true })).toBeVisible();
  await expect(page.locator(".safe-markdown img")).toHaveCount(0);
  await expect(page.locator(".safe-markdown [onerror]")).toHaveCount(0);
  const unsafeLink = page.locator(".safe-markdown a").filter({ hasText: "Enlace peligroso" });
  await expect(unsafeLink).toHaveCount(1);
  expect(await unsafeLink.getAttribute("href")).toBeNull();
  const safeLink = page.locator(".safe-markdown a").filter({ hasText: "Documentación" });
  await expect(safeLink).toHaveAttribute("href", "https://example.invalid/docs");
  await expect(safeLink).toHaveAttribute("target", "_blank");
  await expect(safeLink).toHaveAttribute("rel", /noopener noreferrer/);
  expect(externalRequests).toEqual([]);

  const markdownDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar Markdown" }).click();
  expect((await markdownDownload).suggestedFilename()).toBe("report-report-001.md");
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar JSON" }).click();
  expect((await jsonDownload).suggestedFilename()).toBe("report-report-001.json");

  const decision = page.getByRole("combobox", { name: "Decisión humana del informe" });
  await decision.selectOption("changes_requested");
  const reason = page.getByRole("textbox", { name: "Motivo de la decisión" });
  await expect(page.getByRole("button", { name: "Registrar decisión humana" })).toBeDisabled();
  await reason.fill("Aclarar la evidencia de cobertura.");
  await page.getByRole("button", { name: "Registrar decisión humana" }).click();
  const decisionDialog = page.getByRole("dialog", { name: "Confirmar decisión humana" });
  await decisionDialog.getByRole("button", { name: "Confirmar decisión humana" }).click();
  await expect(page.locator(".report-detail-heading").getByText("Cambios solicitados", { exact: true })).toBeVisible();
  expect(observer.reportDecisionRequests).toHaveLength(1);
  expect(observer.reportDecisionRequests[0].path).toBe("/api/v1/projects/hormigestio/reports/report-001/decision");
  expect(observer.reportDecisionRequests[0].csrfToken).toBe("csrf-fixture");
  expect(JSON.parse(observer.reportDecisionRequests[0].body ?? "{}")).toEqual({ expected_report_version: 1, expected_human_decision: "pending", decision: "changes_requested", reason: "Aclarar la evidencia de cobertura." });
  expect(observer.reportDecisionRequests[0].idempotencyKey).toBeTruthy();
  expect(observer.downloadRequests).toEqual(["report:markdown", "report:json"]);
  expect(observer.governanceRequests).toHaveLength(0);
});

test("las listas de propuestas e informes no convierten disponibilidad desconocida en una bandeja vacía", async ({ page }) => {
  await installApi(page, { proposalAvailability: "unknown", reportAvailability: "unknown" });
  await page.goto("/proposals");
  await expect(page.getByText("Disponibilidad de propuestas desconocida", { exact: true })).toBeVisible();
  await page.goto("/reports");
  await expect(page.getByText("Disponibilidad de informes desconocida", { exact: true })).toBeVisible();
});

test("calendario muestra el outbox real y exige preparación, confirmación e idempotencia para un ciclo local", async ({ page }) => {
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: ["operator_session", "weekly_cycle:prepare", "weekly_cycle:confirm"],
  });
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Calendario y reportes semanales" })).toBeVisible();
  await expect(page.getByText("Disparador automático: no verificado", { exact: true })).toBeVisible();
  await expect(page.getByText("America/Guayaquil", { exact: true })).toBeVisible();
  await expect(page.getByText("DELIVERED", { exact: true })).toBeVisible();
  expect(observer.weeklyCycleRequests).toHaveLength(0);

  await page.getByRole("link", { name: "Abrir entrega delivery-001" }).click();
  await expect(page.getByRole("heading", { name: "Entrega delivery-001" })).toBeVisible();
  await expect(page.getByText("Cuerpo ligado al recibo persistido.", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar recibo Markdown" }).click();
  expect((await download).suggestedFilename()).toBe("outbox-delivery-001.md");

  await page.getByRole("button", { name: "Preparar ciclo manual" }).click();
  const prepareDialog = page.getByRole("dialog", { name: "Preparar ciclo semanal local" });
  await prepareDialog.getByRole("button", { name: "Comprobar elegibilidad" }).click();
  await expect(page.getByRole("heading", { name: "Preparación del ciclo" })).toBeVisible();
  const consent = page.getByRole("checkbox", { name: "Confirmo ejecutar el ciclo local mediante el canal SQLite, sin llamadas remotas ni entregas externas" });
  await expect(consent).not.toBeChecked();
  await consent.check();
  await page.getByRole("button", { name: "Confirmar ciclo semanal" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Confirmar ciclo semanal" });
  await confirmDialog.getByRole("button", { name: "Ejecutar ciclo local" }).click();
  await expect(page.getByText("Trabajo completado", { exact: true })).toBeVisible();
  await expect(page.getByText("Omitido: la ventana aún no está vencida", { exact: true })).toBeVisible();
  await expect(page.getByText("Ejecutado", { exact: true })).toHaveCount(0);

  expect(observer.weeklyCycleRequests.map((item) => item.path)).toEqual([
    "/api/v1/projects/hormigestio/schedule/manual-cycle/prepare",
    "/api/v1/projects/hormigestio/schedule/manual-cycle/confirm",
  ]);
  expect(observer.weeklyCycleRequests.every((item) => item.csrfToken === "csrf-fixture")).toBe(true);
  expect(observer.weeklyCycleRequests[0].idempotencyKey).toBeNull();
  expect(observer.weeklyCycleRequests[1].idempotencyKey).toBeTruthy();
  expect(JSON.parse(observer.weeklyCycleRequests[0].body ?? "{}")).toEqual({ request_cycle: true });
  expect(JSON.parse(observer.weeklyCycleRequests[1].body ?? "{}")).toEqual({ preparation_id: "cycle-preparation-001", configuration_fingerprint: "c".repeat(64), confirm_cycle: true, remote_consent: false });
  expect(observer.downloadRequests).toEqual(["outbox:markdown"]);
});

test("un ciclo manual sin elegibilidad no permite confirmación", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["weekly_cycle:prepare", "weekly_cycle:confirm"], cyclePrepareEligible: false });
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Preparar ciclo manual" }).click();
  await page.getByRole("dialog", { name: "Preparar ciclo semanal local" }).getByRole("button", { name: "Comprobar elegibilidad" }).click();
  await expect(page.getByText("El servidor no autorizó la preparación del ciclo.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar ciclo semanal" })).toBeDisabled();
});

test("auditoría protegida filtra eventos y conserva autoría desconocida", async ({ page }) => {
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "audit:read"] });
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Auditoría y actividad" })).toBeVisible();
  await expect(page.getByText("Sesión local verificada: operador-local", { exact: true })).toBeVisible();
  await expect(page.getByText("Actor no verificado", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Tipo de evento" }).fill("proposal_review");
  await page.getByRole("textbox", { name: "Desde (ISO 8601 con zona)" }).fill("2026-01-12T00:00:00-05:00");
  await page.getByRole("textbox", { name: "Hasta (ISO 8601 con zona)" }).fill("2026-01-12T23:59:59-05:00");
  await page.getByRole("button", { name: "Aplicar filtros de auditoría" }).click();
  await expect(page).toHaveURL(/event_type=proposal_review/);
  await expect(page).toHaveURL(/since=2026-01-12T00%3A00%3A00-05%3A00/);
  await expect(page.getByRole("link", { name: "Ver evento audit-legacy-002" })).toBeVisible();
  await page.getByRole("link", { name: "Ver evento audit-legacy-002" }).click();
  await expect(page.getByRole("heading", { name: "Evento audit-legacy-002" })).toBeVisible();
  await expect(page.getByText("Actor no verificado", { exact: true }).last()).toBeVisible();
  await expect(page.getByText(/"captured": true/)).toBeVisible();
  expect(observer.moduleReads.some((path) => path.includes("event_type=proposal_review") && path.includes("since=2026-01-12"))).toBe(true);
});

test("lecturas de auditoría y configuración no se consultan sin sus capabilities del servidor", async ({ page }) => {
  const observer = await installApi(page);
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Auditoría y actividad" })).toBeVisible();
  await expect(page.getByText("Se requiere una sesión local con la capacidad audit:read.", { exact: true })).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByText("La configuración protegida requiere la capacidad configuration:read.", { exact: true })).toBeVisible();
  expect(observer.moduleReads.some((path) => path.includes("/audit"))).toBe(false);
  expect(observer.moduleReads.some((path) => path.includes("/configuration"))).toBe(false);
});

test("configuración protegida revela presencia y metadatos permitidos, nunca credenciales ni controles de edición", async ({ page }) => {
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "configuration:read"] });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Configuración general del proyecto" })).toBeVisible();
  await expect(page.getByText("org-fixture/gestion-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("board-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("Credencial Trello: configurada", { exact: true })).toBeVisible();
  await expect(page.getByText("gemini-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("policy-v2", { exact: true })).toBeVisible();
  await expect(page.getByText("America/Guayaquil", { exact: true })).toBeVisible();
  await expect(page.getByText("Ningún campo de configuración es editable desde esta interfaz.", { exact: true })).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Guardar configuración|Editar|Crear proyecto/ })).toHaveCount(0);
  const serializedUi = await page.locator("main").innerText();
  expect(serializedUi).not.toContain("api_token=");
  expect(serializedUi).not.toContain("secret-token");
  expect(observer.moduleReads.some((path) => path.endsWith("/configuration"))).toBe(true);
});

test("la página principal es el login cuando no hay sesión activa", async ({ page }) => {
  await installApi(page, { requireLogin: true });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Acceso de operador" })).toBeVisible();
  await expect(page.getByLabel("Correo electrónico")).toBeVisible();
  await expect(page.getByLabel("Correo electrónico")).toHaveAttribute("type", "email");
  await expect(page.getByLabel("Correo electrónico")).toHaveAttribute("required", "");
  await expect(page.getByLabel("Contraseña")).toBeVisible();
  await expect(page.getByLabel("Contraseña")).toHaveAttribute("required", "");
  await expect(page.getByRole("button", { name: "Iniciar Sesión como Gestor" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ingresar en Modo Consulta (Solo lectura)" })).toBeVisible();
});

test("inicia sesión desde la página principal con las credenciales verificadas del gestor", async ({ page }) => {
  expect(managerCredentials).toEqual({ email: "gestor@hormigestion.ec", password: "HormiGestion2026!" });
  await installApi(page, { requireLogin: true });
  await page.goto("/");

  await page.getByLabel("Correo electrónico").fill(managerCredentials.email);
  await page.getByLabel("Contraseña").fill(managerCredentials.password);
  await page.getByRole("button", { name: "Iniciar Sesión como Gestor" }).click();

  await expect(page.getByRole("banner").getByText(managerCredentials.email, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();

  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByRole("heading", { name: "Acceso de operador" })).toBeVisible();
});

test("el modo consulta desde la página principal permite explorar en solo lectura", async ({ page }) => {
  await installApi(page, { requireLogin: true });
  await page.goto("/");

  await page.getByRole("button", { name: "Ingresar en Modo Consulta (Solo lectura)" }).click();
  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
  await expect(page.getByText("Consulta de solo lectura")).toBeVisible();
  await expect(page.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
});

