import { expect, test } from "@playwright/test";
import { assigneeProposalDetail, findingDetail, installApi, priorityProposalDetail, proposalDetail } from "./fixtures";

const capabilities = ["governance:proposal_execute", "quality:decide", "notifications:email_send"];
const phase2Finding = {
  ...findingDetail,
  suggested_code: "export function backendSuggestion(): string { return 'review me'; }",
  improvement_summary: "La sugerencia proviene de la respuesta del backend.",
};

const approvedFinding = {
  decision_status: "approved",
  suggested_code: "export function reviewedSuggestion(): string { return 'approved'; }",
  improvement_summary: "Valida el valor antes de usarlo.",
  branch_name: "ai-fix/Q2-reviewed",
  branch_url: "https://github.com/org/project/tree/ai-fix/Q2-reviewed",
  commit_sha_applied: "c".repeat(40),
  execution_mode: "sandbox",
};

test("quality approval uses the backend suggestion and reports its sandbox result without claiming a GitHub branch", async ({ page }) => {
  const requests: Array<{ body: Record<string, unknown>; csrf: string | undefined; key: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/runs/run-current/findings/finding-candidate**", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: phase2Finding });
    requests.push({
      body: route.request().postDataJSON(),
      csrf: route.request().headers()["x-csrf-token"],
      key: route.request().headers()["idempotency-key"],
    });
    await route.fulfill({ json: approvedFinding });
  });

  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".quality-code-after code")).toContainText("backendSuggestion");
  await expect(dialog.getByRole("button", { name: "Editar código sugerido", exact: true })).toHaveCount(0);
  await dialog.getByLabel("Rama separada de verificación").fill("ai-fix/Q2-reviewed");
  await dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true }).click();

  await expect(dialog.locator(".quality-decision-result")).toContainText("Aprobada");
  await expect(dialog.locator(".quality-decision-result")).toContainText("Local");
  await expect(dialog.getByText(new RegExp(`c{40}`))).toBeVisible();
  await expect(dialog.getByText("Valida el valor antes de usarlo.", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("link", { name: /GitHub|Pull Request/i })).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ csrf: "csrf-fixture", body: {
    decision: "approve",
    target_branch: "ai-fix/Q2-reviewed",
  } });
  expect(requests[0].body).not.toHaveProperty("suggested_code_override");
  expect(requests[0].key).toBeTruthy();
});

const editableProposals = [
  { name: "fecha límite", field: "due_date", proposal: proposalDetail, originalValue: "22 ene 2026, 5:00 p. m." },
  { name: "prioridad", field: "priority", proposal: priorityProposalDetail, originalValue: "high" },
  { name: "responsables", field: "assignee_ids", proposal: assigneeProposalDetail, originalValue: "operator-2" },
] as const;

for (const scenario of editableProposals) {
  test(`modified proposal execution changes only the proposed ${scenario.name} and reports sandbox honestly`, async ({ page }) => {
    const requests: Array<{ body: Record<string, unknown>; csrf: string | undefined; key: string | undefined }> = [];
    await installApi(page, { authenticated: true, capabilities, proposalFixture: scenario.proposal });
    await page.route("**/api/v1/projects/hormigestio/proposals/proposal-date-001/execute", async (route) => {
      requests.push({
        body: route.request().postDataJSON(),
        csrf: route.request().headers()["x-csrf-token"],
        key: route.request().headers()["idempotency-key"],
      });
      await route.fulfill({ json: {
        proposal_id: "proposal-date-001",
        decision: "modify_and_execute",
        execution: { status: "SIMULATED", verification_status: "verified", task_id: "task-1" },
        execution_mode: "sandbox",
      } });
    });

    await page.goto("/proposals?proposal_id=proposal-date-001");
    await page.getByRole("button", { name: "Modificar antes de Aprobar", exact: true }).click();
    const controls = {
      due_date: page.getByLabel("Nueva fecha límite"),
      priority: page.getByLabel("Nueva prioridad"),
      assignee_ids: page.getByLabel("Nuevo responsable"),
    };
    await expect(controls[scenario.field]).toBeVisible();
    for (const [field, control] of Object.entries(controls)) {
      if (field !== scenario.field) await expect(control).toHaveCount(0);
    }
    await expect(page.getByText("Solo se enviará al backend el campo de esta propuesta; el servidor vuelve a validarlo antes de ejecutar.", { exact: true })).toBeVisible();
    if (scenario.field === "due_date") await controls.due_date.fill("2026-02-20");
    else if (scenario.field === "priority") await controls.priority.selectOption("low");
    else await controls.assignee_ids.fill("gestor-demo, operador-2");
    const reason = "El gestor aprueba el cambio de un solo campo propuesto.";
    await page.getByLabel("Motivo de la decisión").fill(reason);
    await page.getByRole("button", { name: "Aprobar y Aplicar en Trello", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirmar modificación y ejecución" });
    await dialog.getByRole("button", { name: "Modificar y ejecutar", exact: true }).click();

    await expect(page.getByText("Local · Trello sin cambios", { exact: true })).toBeVisible();
    await expect(page.getByText(/Verificación: verified/)).toBeVisible();
    await expect(page.locator(".proposal-value-diff")).toContainText(scenario.originalValue);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ csrf: "csrf-fixture" });
    const fieldPayload = scenario.field === "due_date" ? { modified_due_date: "2026-02-20" }
      : scenario.field === "priority" ? { modified_priority: "low" }
        : { modified_assignee_ids: ["gestor-demo", "operador-2"] };
    expect(requests[0].body).toEqual({
      decision: "modify_and_execute",
      ...fieldPayload,
      reason,
    });
    expect(requests[0].key).toBeTruthy();
  });
}

test("quality request-changes decision sends its required reason to the backend", async ({ page }) => {
  const requests: Array<{ body: Record<string, unknown>; key: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/runs/run-current/findings/finding-candidate**", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: phase2Finding });
    requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    await route.fulfill({ json: {
      decision_status: "changes_requested",
      suggested_code: phase2Finding.suggested_code,
      improvement_summary: "Aclara el flujo de validación.",
      branch_name: null,
      branch_url: null,
      commit_sha_applied: null,
      execution_mode: "sandbox",
    } });
  });
  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Solicitar cambios", exact: true }).click();
  await dialog.getByLabel("Cambios solicitados").fill("Agregar una validación para cadenas vacías.");
  await dialog.getByRole("button", { name: "Enviar solicitud de cambios", exact: true }).click();
  await expect(dialog.locator(".quality-decision-result")).toContainText("Cambios solicitados");
  await expect(dialog.locator(".quality-decision-result")).toContainText("Local");
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ body: { decision: "request_changes", reason: "Agregar una validación para cadenas vacías." } });
  expect(requests[0].key).toBeTruthy();
});

test("proposal rejection sends the human reason to the execution endpoint", async ({ page }) => {
  const requests: Array<{ body: Record<string, unknown>; key: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/projects/hormigestio/proposals/proposal-date-001/execute", async (route) => {
    requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    await route.fulfill({ json: {
      proposal_id: "proposal-date-001",
      decision: "reject",
      execution: { status: "rejected", verification_status: "not_required" },
      execution_mode: "sandbox",
    } });
  });
  await page.goto("/proposals?proposal_id=proposal-date-001");
  await page.getByLabel("Motivo de la decisión").fill("La evidencia del bloqueo no está vigente.");
  await page.getByRole("button", { name: "Rechazar Propuesta", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar rechazo de propuesta" });
  await dialog.getByRole("button", { name: "Rechazar propuesta", exact: true }).click();
  await expect(page.getByText("Local · Trello sin cambios", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ body: { decision: "reject", reason: "La evidencia del bloqueo no está vigente." } });
  expect(requests[0].key).toBeTruthy();
});

test("manager email confirms the recipient, sends once, and shows the protected message preview", async ({ page }) => {
  const requests: Array<{ body: Record<string, unknown>; csrf: string | undefined; key: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/projects/hormigestio/notifications/email", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    requests.push({
      body: route.request().postDataJSON(),
      csrf: route.request().headers()["x-csrf-token"],
      key: route.request().headers()["idempotency-key"],
    });
    await route.fulfill({ json: {
      delivery_id: "delivery-email-001",
      delivery_channel: "smtp_email_sent",
      recipient_email: "gestor@example.org",
      html_preview_url: "/api/v1/projects/hormigestio/notifications/email/delivery-email-001/preview",
      status: "DELIVERED",
    } });
  });
  await page.route("**/api/v1/projects/hormigestio/notifications/email/delivery-email-001/preview", async (route) => {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body><h1>Reporte enviado por correo</h1><p>Destinatario confirmado</p></body></html>" });
  });

  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Configuración de Entrega por Correo Electrónico al Gestor" });
  await panel.getByLabel("Correo del gestor").fill("gestor@example.org");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await panel.getByRole("button", { name: "Enviar Alerta/Reporte al Correo Ahora" }).click();
  await expect(panel.getByText("Confirma el envío a gestor@example.org", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Confirmar envío a gestor@example.org", exact: true }).click();

  await expect(panel.getByText("Aceptado por el servicio de correo", { exact: true })).toBeVisible();
  await expect(panel.getByText("Registrado", { exact: true })).toBeVisible();
  const preview = panel.getByTitle("Vista previa del mensaje");
  await expect(preview).toBeVisible();
  await expect(preview.contentFrame().getByRole("heading", { name: "Reporte enviado por correo" })).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ csrf: "csrf-fixture", body: { recipient_email: "gestor@example.org" } });
  expect(requests[0].key).toBeTruthy();
});

test("global agent workflow opens the reusable delivery panel on demand", async ({ page }) => {
  await installApi(page);
  await page.goto("/");
  await page.getByText("Enviar alerta / reporte al gestor", { exact: true }).click();
  const panel = page.getByRole("region", { name: "Configuración de Entrega por Correo Electrónico al Gestor" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("button", { name: "Enviar Notificación por Correo al Gestor", exact: true })).toBeDisabled();
});

test("a locally saved email is never presented as inbox delivery", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/projects/hormigestio/notifications/email", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({ json: {
      delivery_id: "delivery-local-001",
      delivery_channel: "local_outbox_simulated_email",
      recipient_email: "gestor@example.org",
      html_preview_url: null,
      status: "DELIVERED",
    } });
  });
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Configuración de Entrega por Correo Electrónico al Gestor" });
  await panel.getByLabel("Correo del gestor").fill("gestor@example.org");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await panel.getByRole("button", { name: "Enviar Alerta/Reporte al Correo Ahora" }).click();
  await panel.getByRole("button", { name: "Confirmar envío a gestor@example.org", exact: true }).click();
  await expect(panel.getByText("Guardado localmente · no enviado", { exact: true })).toBeVisible();
  await expect(panel.getByText(/no se envió a una bandeja de correo/i)).toBeVisible();
});


test("an unconfirmed email delivery is never presented as successful", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities });
  await page.route("**/api/v1/projects/hormigestio/notifications/email", route => route.fulfill({ json: {
    delivery_id: "delivery-failed-001", delivery_channel: "smtp_email_failed",
    recipient_email: "gestor@example.org", html_preview_url: null, status: "FAILED",
  } }));
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Configuración de Entrega por Correo Electrónico al Gestor" });
  await panel.getByLabel("Correo del gestor").fill("gestor@example.org");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await panel.getByRole("button", { name: "Enviar Alerta/Reporte al Correo Ahora" }).click();
  await panel.getByRole("button", { name: "Confirmar envío a gestor@example.org", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("No se confirmó el envío.");
  await expect(panel.getByText("Entrega no confirmada", { exact: true })).toBeVisible();
  await expect(panel.getByText("Aceptado por el servicio de correo", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("Guardado localmente · no enviado", { exact: true })).toHaveCount(0);
});
