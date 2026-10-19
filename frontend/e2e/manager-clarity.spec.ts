import { expect, test } from "@playwright/test";
import { history, installApi, summary, tasks } from "./fixtures";

/**
 * Manager clarity over the Trello surfaces: who is responsible, which signal is really
 * active, which alert is about this task, how the evidence of a card is grouped and which
 * corrective action answers the alert.
 */

const clarityTasks = {
  items: [
    {
      ...tasks[0],
      assignee_ids: ["operator-1"],
      assignee_names: {},
      observed_fields: ["title", "state", "assignee_ids", "due_date"],
    },
    {
      ...tasks[1],
      id: "task-unassigned",
      title: "Tarea sin responsable",
      assignee_ids: [],
      observed_fields: ["title", "state", "assignee_ids"],
    },
    {
      ...tasks[1],
      id: "task-unobserved",
      title: "Tarea sin evidencia de responsable",
      assignee_ids: [],
      observed_fields: null,
    },
  ],
  total: 3,
  page: 1,
  page_size: 25,
};

const sharedRisk = {
  risk_id: "risk-clarity-overdue",
  project_id: "hormigestio",
  capture_id: "capture-current",
  kind: "overdue" as const,
  task_ids: ["task-1", "task-3"],
  task_count: 2,
  summary: "2 tarea(s) vencida(s) no completada(s).",
  basis: "Tareas no completadas cuyo vencimiento es anterior al corte de la captura.",
  source: "deterministic" as const,
  cutoff_datetime: "2026-01-11T23:59:59-05:00",
  task_titles: {
    "task-1": "Implementar API de pagos",
    "task-3": "Validar sincronización 1",
  },
  proposals: [],
};

function taskDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...tasks[0],
    capture_id: "capture-current",
    risks: [sharedRisk],
    evidence: [],
    history: [],
    ...overrides,
  };
}

test("el responsable distingue sin asignar, no observado y nombre no disponible", async ({ page }) => {
  await installApi(page, { tasksPayload: clarityTasks });
  await page.goto("/tasks");

  const rows = page.locator(".task-table tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).getByText("Nombre no disponible")).toBeVisible();
  await expect(rows.nth(1).getByText("Sin asignar")).toBeVisible();
  await expect(rows.nth(2).getByText("No observado")).toBeVisible();
  await expect(rows.nth(0)).not.toContainText("operator-1");
});

test("el identificador técnico del responsable queda solo en la información avanzada", async ({ page }) => {
  await installApi(page, { tasksPayload: clarityTasks, taskDetailPayload: {
    ...tasks[0],
    assignee_ids: ["operator-1"],
    assignee_names: {},
    observed_fields: ["title", "state", "assignee_ids", "due_date"],
    capture_id: "capture-current",
    evidence: [],
    history: [],
    risks: [],
  } });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Nombre no disponible")).toBeVisible();
  await expect(dialog.getByText("Campos informados")).toHaveCount(0);
  // The identifier stays hidden until the advanced section is opened.
  await expect(dialog.getByText("operator-1")).not.toBeVisible();

  await dialog.getByText("Información técnica de la tarea").click();
  await expect(dialog.getByText("operator-1")).toBeVisible();
  await expect(dialog.getByText("Campos observados:")).toBeVisible();
});

test("el responsable se muestra con su nombre cuando el origen lo resolvió", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks");

  const row = page.locator(".task-table tbody tr").filter({
    has: page.getByRole("button", { name: "Ver detalle de Implementar API de pagos", exact: true }),
  });
  await expect(row.getByText("Ada Lovelace")).toBeVisible();
  await expect(row).not.toContainText("operator-1");

  await row.getByRole("button", { name: "Ver detalle de Implementar API de pagos", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog.getByText("Ada Lovelace")).toBeVisible();
  await expect(dialog.getByText("Nombre no disponible")).toHaveCount(0);

  await dialog.getByText("Información técnica de la tarea").click();
  await expect(dialog.getByText("operator-1")).toBeVisible();
});

test("un responsable sin nombre resuelto convive con el que sí lo tiene", async ({ page }) => {
  await installApi(page, {
    tasksPayload: {
      items: [
        {
          ...tasks[0],
          id: "task-mixed-names",
          title: "Tarea con dos responsables",
          assignee_ids: ["operator-1", "operator-2"],
          assignee_names: { "operator-1": "Ada Lovelace" },
        },
      ],
      total: 1, page: 1, page_size: 25,
    },
  });
  await page.goto("/tasks");

  const row = page.locator(".task-table tbody tr").first();
  await expect(row.getByText("Ada Lovelace, Nombre no disponible")).toBeVisible();
});

test("la prioridad ausente se comunica sin inventar un valor", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("No disponible en la fuente")).toBeVisible();
  await expect(dialog.getByText(/La fuente no informa prioridad para esta tarea/)).toBeVisible();
  await expect(dialog.getByText("Prioridad desconocida")).toHaveCount(0);
});

test("una tarea próxima a vencer no se presenta también como crítica", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?page=2");

  const row = page.locator(".task-table tbody tr").filter({
    has: page.getByRole("button", { name: "Ver detalle de Validar sincronización 1", exact: true }),
  });
  await expect(row.getByText("Próxima", { exact: true })).toBeVisible();
  await expect(row.getByText("Crítica", { exact: true })).toHaveCount(0);

  await row.getByRole("button", { name: "Ver detalle de Validar sincronización 1", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Validar sincronización 1" });
  await expect(dialog.getByText("Próxima a vencer", { exact: true }).first()).toBeVisible();
  await expect(dialog.getByText("Crítica", { exact: true })).toHaveCount(0);
});

test("la alerta explica la condición, la fecha de referencia y el motivo, y separa esta tarea del resto", async ({ page }) => {
  await installApi(page, { taskDetailPayload: taskDetail() });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  const alerts = dialog.locator("section").filter({ hasText: "¿Por qué requiere atención?" }).first();
  await expect(alerts.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();
  await expect(alerts.getByText("Condición observada", { exact: true })).toBeVisible();
  await expect(alerts.getByText("Fecha de referencia", { exact: true })).toBeVisible();
  await expect(alerts.getByText("Motivo", { exact: true })).toBeVisible();
  await expect(alerts.getByText("2 tarea(s) vencida(s) no completada(s).")).toBeVisible();
  await expect(alerts.getByText("Tareas no completadas cuyo vencimiento es anterior al corte de la captura.")).toBeVisible();
  await expect(alerts.getByText("Trazabilidad: captura capture-current · identificación risk-clarity-overdue")).toBeVisible();
});

test("las tareas con la misma alerta se muestran por nombre y permiten abrir su detalle", async ({ page }) => {
  await installApi(page, { taskDetailPayload: taskDetail() });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  const alerts = dialog.locator("section").filter({ hasText: "¿Por qué requiere atención?" }).first();
  await expect(alerts.getByText("Otras 1 tarea(s) con la misma alerta:")).toBeVisible();

  const otherTask = alerts.getByRole("button", { name: "Validar sincronización 1" });
  await expect(otherTask).toBeVisible();
  await expect(alerts).not.toContainText("task-3");
  await otherTask.click();
  await expect(page.getByRole("dialog", { name: "Validar sincronización 1" })).toBeVisible();
});

test("las observaciones de la misma tarjeta se agrupan y no se presentan como cambios", async ({ page }) => {
  await installApi(page, {
    taskDetailPayload: taskDetail({
      evidence: [
        {
          provenance: { id: "evidence-a", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-09T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
        {
          provenance: { id: "evidence-b", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-10T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
        {
          provenance: { id: "evidence-c", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-11T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
      ],
    }),
  });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".evidence-group")).toHaveCount(1);
  await expect(dialog.getByText("Observación más reciente:")).toBeVisible();
  await expect(dialog.getByText("Historial de observaciones (3)")).toBeVisible();
  await expect(dialog.getByText("ninguna diferencia comprobada entre capturas: los datos observados no cambiaron.")).toBeVisible();
  await expect(dialog.getByText("Cambios comprobados")).toHaveCount(0);
});

test("con una sola captura no se afirma que los datos no cambiaron", async ({ page }) => {
  await installApi(page, {
    historyPayload: { items: [history.items[0]], total: 1, page: 1, page_size: 25 },
    taskDetailPayload: taskDetail({
      evidence: [
        {
          provenance: { id: "evidence-a", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-09T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
        {
          provenance: { id: "evidence-b", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-10T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
      ],
    }),
  });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog.getByText("observaciones de la misma captura; hace falta otra captura para comprobar cambios.")).toBeVisible();
});

test("el historial de cambios solo lista diferencias verificadas entre capturas", async ({ page }) => {
  await installApi(page, { taskDetailPayload: taskDetail() });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog.getByRole("heading", { name: "Historial de cambios" })).toBeVisible();
  await expect(dialog.getByText("No se verificaron cambios entre las capturas disponibles.")).toBeVisible();
});

test("los cambios comprobados se destacan con su campo y su valor anterior y nuevo", async ({ page }) => {
  await installApi(page, {
    taskDetailPayload: taskDetail({
      history: [
        {
          id: "change-clarity-1",
          task_id: "task-1",
          kind: "field_changed",
          field: "due_date",
          before: "2026-01-10T12:00:00-05:00",
          after: "2026-01-12T12:00:00-05:00",
          previous_evidence_ids: [],
          current_evidence_ids: ["evidence-a"],
          observed_at: "2026-01-12T10:00:00-05:00",
          capture_id: "capture-current",
          previous_capture_id: "capture-previous",
          current_capture_id: "capture-current",
          actor: null,
        },
      ],
      evidence: [
        {
          provenance: { id: "evidence-a", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-11T10:00:00-05:00" },
          category: "task",
          summary: "Vencimiento observado en la captura completa de tareas.",
        },
      ],
    }),
  });
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  await expect(dialog.getByText("Cambios comprobados (1):")).toBeVisible();
  await expect(dialog.getByText("Fecha de vencimiento").first()).toBeVisible();
});

test("el detalle ofrece la acción correctiva vinculada a la alerta de la tarea", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-1");

  const dialog = page.getByRole("dialog", { name: "Implementar API de pagos" });
  const actions = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await expect(actions.getByRole("heading", { name: "Acciones correctivas" })).toBeVisible();
  await expect(actions.getByRole("link", { name: "proposal-date-001" })).toHaveAttribute("href", "/proposals?proposal_id=proposal-date-001");
  await expect(actions.getByText("Decisión pendiente", { exact: true })).toBeVisible();
});

test("sin propuesta vinculada el detalle declara la ausencia y reutiliza el diagnóstico", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?task=task-3");

  const dialog = page.getByRole("dialog", { name: "Validar sincronización 1" });
  const actions = dialog.locator("section").filter({ hasText: "Acciones correctivas" }).first();
  await expect(actions.getByText("No hay una propuesta vinculada a esta tarea")).toBeVisible();
  await expect(actions.getByText("No se encontraron candidatos válidos", { exact: true })).toBeVisible();
});

test("el dashboard muestra las alertas con nombre de tarea y sin categorías solapadas", async ({ page }) => {
  await installApi(page, { summaryPayload: { ...summary, risks: [sharedRisk] } });
  await page.goto("/");

  const alerts = page.getByRole("region", { name: "Alertas de la captura" });
  await expect(alerts.getByRole("link", { name: "Implementar API de pagos" })).toBeVisible();
  await expect(alerts.getByRole("link", { name: "Validar sincronización 1" })).toBeVisible();
  await expect(alerts.getByText("Fecha de referencia:")).toBeVisible();
  await expect(page.getByRole("img", { name: "Señales de alerta por tipo" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Requieren atención/ })).toHaveAttribute("href", "/tasks?critical_only=true");
});
