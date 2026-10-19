import { expect, test, type Page } from "@playwright/test";
import { installApi, proposalDetail } from "./fixtures";

const risksRegion = (page: Page) => page.getByRole("region", { name: "Alertas de la captura" });
const riskOrigin = (page: Page) => page.getByRole("region", { name: "Riesgo que motivó la acción" });

test("el dashboard enlaza cada riesgo con las propuestas que lo motivaron y su estado", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const risks = risksRegion(page);
  await expect(risks).toBeVisible();

  const linkedRisk = risks.locator(".risk-item").filter({ hasText: "anterior al corte de la captura" });
  await expect(linkedRisk.getByText("Propuestas vinculadas (1):")).toBeVisible();
  const proposalLink = linkedRisk.getByRole("link", { name: "proposal-date-001" });
  await expect(proposalLink).toHaveAttribute("href", "/proposals?proposal_id=proposal-date-001");
  await expect(linkedRisk.getByText("Fecha de vencimiento", { exact: true })).toBeVisible();
  await expect(linkedRisk.getByText("Decisión pendiente", { exact: true })).toBeVisible();
  await expect(linkedRisk.getByText("Sin ejecución registrada", { exact: true })).toBeVisible();

  await expect(risks.getByText("Una propuesta aprobada o ejecutada no demuestra que el riesgo haya desaparecido")).toBeVisible();
});

test("el dashboard declara la ausencia de propuesta vinculada y muestra el diagnóstico persistido", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const risks = risksRegion(page);
  const riskWithoutProposal = risks.locator(".risk-item").filter({ hasText: "7 días posteriores al corte" });
  await expect(riskWithoutProposal.getByText("No hay una propuesta vinculada a este riesgo")).toBeVisible();
  await expect(riskWithoutProposal.getByText("No se encontraron candidatos válidos", { exact: true })).toBeVisible();
  await expect(riskWithoutProposal.getByText("Las reglas se aplicaron a la captura y no produjeron ningún candidato válido.")).toBeVisible();
});

test("el detalle de tarea acepta el deep link con captura y muestra la propuesta vinculada al riesgo", async ({ page }) => {
  const observer = await installApi(page);
  await page.goto("/tasks?task=task-1&capture_id=capture-current");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  expect(observer.taskDetailReads.some((read) => read.includes("capture_id=capture-current"))).toBe(true);

  const riskSection = dialog.locator("section").filter({ hasText: "¿Por qué requiere atención?" }).first();
  await expect(riskSection.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();
  const actionsSection = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await expect(actionsSection.getByRole("link", { name: "proposal-date-001" })).toHaveAttribute("href", "/proposals?proposal_id=proposal-date-001");
  await expect(actionsSection.getByText("Decisión pendiente", { exact: true })).toBeVisible();
  await expect(actionsSection.getByText("Responde a: Tareas no completadas cuyo vencimiento es anterior al corte de la captura.")).toBeVisible();

  await page.getByRole("button", { name: "Cerrar detalle de tarea" }).click();
  await expect(page).not.toHaveURL(/task=task-1/);
  await expect(page).not.toHaveURL(/capture_id=capture-current/);
});

test("el detalle de tarea de otra captura presenta la propuesta vinculada como histórica", async ({ page }) => {
  const observer = await installApi(page);
  await page.goto("/tasks?task=task-1&capture_id=capture-previous");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  expect(observer.taskDetailReads.some((read) => read.includes("capture_id=capture-previous"))).toBe(true);

  const actionsSection = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await expect(actionsSection.getByText("Histórica", { exact: true })).toBeVisible();
});

test("el detalle de tarea declara la ausencia de propuesta vinculada al riesgo", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-3&capture_id=capture-current");

  const dialog = page.getByRole("dialog", { name: "Validar sincronización 1" });
  await expect(dialog).toBeVisible();
  const actionsSection = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await expect(actionsSection.getByText("No hay una propuesta vinculada a esta tarea")).toBeVisible();
});

test("el detalle de la propuesta declara el riesgo que la motivó y enlaza su tarea y su evidencia", async ({ page }) => {
  await installApi(page);
  await page.goto("/proposals?proposal_id=proposal-date-001");

  const section = riskOrigin(page);
  await expect(section.getByRole("heading", { name: "Riesgo que motivó la acción" })).toBeVisible();
  await expect(section.getByText("Vencida", { exact: true })).toBeVisible();
  await expect(section.getByText("risk-fixture-overdue", { exact: true })).toBeVisible();
  await expect(section.getByRole("link", { name: "Abrir tarea de origen" })).toHaveAttribute("href", "/tasks?task=task-1&capture_id=capture-current");
  await expect(section.getByRole("link", { name: "Ver la evidencia de la propuesta" })).toHaveAttribute("href", "#proposal-evidence");
  await expect(section.getByText("Una propuesta aprobada o ejecutada no demuestra que el riesgo haya desaparecido")).toBeVisible();
});

test("una propuesta sin riesgo determinista se muestra como tal y no inventa un motivo", async ({ page }) => {
  await installApi(page, { proposalFixture: { ...proposalDetail, risk_refs: [] } });
  await page.goto("/proposals?proposal_id=proposal-date-001");

  const section = riskOrigin(page);
  await expect(section.getByText("Esta propuesta no está asociada a un riesgo determinista.")).toBeVisible();
  await expect(section).not.toContainText("risk-fixture-overdue");
});

test("una referencia de riesgo no verificable en la captura no se presenta como válida", async ({ page }) => {
  await installApi(page, {
    proposalFixture: {
      ...proposalDetail,
      risk_refs: [{ risk_id: "risk-fixture-historical", kind: "overdue", task_id: "task-1", capture_id: "capture-previous", resolved: false }],
    },
  });
  await page.goto("/proposals?proposal_id=proposal-date-001");

  const section = riskOrigin(page);
  await expect(section.getByText("No verificable en la captura vigente")).toBeVisible();
});

test("la bandeja de propuestas marca el tipo de riesgo de cada propuesta con riesgo", async ({ page }) => {
  await installApi(page);
  await page.goto("/proposals");

  const inboxItem = page.getByRole("link", { name: "Abrir propuesta para Fecha de vencimiento" });
  await expect(inboxItem.getByText("Vencida", { exact: true })).toBeVisible();
});

test("la navegación recorre riesgo, tarea y propuesta sin perder el origen", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const risks = risksRegion(page);
  await risks.getByRole("link", { name: "Implementar API de pagos" }).click();
  await expect(page).toHaveURL(/task=task-1/);

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  const actionsSection = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await actionsSection.getByRole("link", { name: "proposal-date-001" }).click();
  await expect(page).toHaveURL(/proposal_id=proposal-date-001/);
  await expect(page.getByRole("heading", { name: "Riesgo que motivó la acción" })).toBeVisible();
  await expect(page.getByText("risk-fixture-overdue", { exact: true })).toBeVisible();
});
