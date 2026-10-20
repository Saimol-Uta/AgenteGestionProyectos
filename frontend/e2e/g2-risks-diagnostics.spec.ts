import { expect, test } from "@playwright/test";
import { installApi, project } from "./fixtures";

test("el dashboard identifica las tareas afectadas por cada riesgo y lleva a su detalle", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const risks = page.getByRole("region", { name: "Alertas de la captura" });
  await expect(risks).toBeVisible();
  await expect(risks.getByText("Alertas de la tarea calculadas sobre la captura actual")).toBeVisible();
  await expect(risks.getByText("Tareas no completadas cuyo vencimiento es anterior al corte de la captura.")).toBeVisible();
  await expect(risks.getByText("Tareas no completadas cuyo vencimiento cae dentro de los 7 días posteriores al corte.")).toBeVisible();

  const taskLink = risks.getByRole("link", { name: "Implementar API de pagos" });
  await expect(taskLink).toHaveAttribute("href", "/tasks?task=task-1");

  await taskLink.click();
  await expect(page).toHaveURL(/task=task-1/);
  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();
});

test("el detalle de la tarea muestra la clasificación, su fundamento y la trazabilidad de la captura", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();

  const riskSection = dialog.locator("section").filter({ hasText: "¿Por qué requiere atención?" }).first();
  await expect(riskSection.getByText("Vencida", { exact: true })).toBeVisible();
  await expect(riskSection.getByText("Tareas no completadas cuyo vencimiento es anterior al corte de la captura.")).toBeVisible();
  await expect(riskSection.getByText("Trazabilidad: captura capture-current · identificación risk-fixture-overdue")).toBeVisible();
  await expect(riskSection).not.toContainText("Implementar API de pagos");

  await expect(dialog.getByRole("heading", { name: "Evidencias de Trello" })).toBeVisible();
  await expect(dialog.getByText("trello", { exact: true })).toBeVisible();
});

test("el detalle de la tarea declara la ausencia cuando no hay riesgo determinista", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-2");

  const dialog = page.getByRole("dialog", { name: "Validar sincronización" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();
  await expect(dialog.getByText("Esta tarea no tiene alertas en la captura consultada.")).toBeVisible();
});

test("el detalle de la ejecución explica por qué no hubo propuestas y conserva los códigos técnicos", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis/run-current");
  await page.getByText("Información técnica del análisis").click();

  await expect(page.getByRole("heading", { name: "Propuestas generadas" })).toBeVisible();
  await expect(page.getByText("No se encontraron candidatos válidos", { exact: true })).toBeVisible();
  await expect(page.getByText("Las reglas se aplicaron a la captura y no produjeron ningún candidato válido.")).toBeVisible();
  await expect(page.getByText("Códigos técnicos de auditoría (1)")).toBeVisible();

  await page.getByText("Códigos técnicos de auditoría (1)").click();
  await expect(page.getByText("unknown_task", { exact: true })).toBeVisible();
});

test("la bandeja de propuestas distingue la ausencia de propuestas de la ausencia de pendientes", async ({ page }) => {
  await installApi(page, {
    emptyProposals: true,
    projectsPayload: { items: [{ ...project, latest_run_id: "run-current" }], total: 1, page: 1, page_size: 100 },
  });
  await page.goto("/proposals");

  await expect(page.getByRole("heading", { name: "No hay propuestas registradas" })).toBeVisible();
  const diagnosticLink = page.getByRole("link", { name: "Ver el diagnóstico de la última ejecución" });
  await expect(diagnosticLink).toHaveAttribute("href", "/analysis/run-current");
  await expect(page.getByText(/No hay propuestas pendientes de decisión/)).toHaveCount(0);
});

test("la bandeja de propuestas informa cuando no hay pendientes pero sí propuestas en otros estados", async ({ page }) => {
  await installApi(page, { proposalPendingActionableCount: 0 });
  await page.goto("/proposals");

  await expect(page.getByText("No hay propuestas pendientes de decisión: las 1 propuestas registradas se encuentran en otros estados.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No hay propuestas registradas" })).toHaveCount(0);
});
