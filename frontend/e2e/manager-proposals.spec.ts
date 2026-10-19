import { expect, test, type Page } from "@playwright/test";
import { assertNoBrowserNetworkAttempts, assigneeProposalDetail, installApi, installBrowserNetworkGuard, priorityProposalDetail, proposalDetail } from "./fixtures";
import type { ProposalDecisionHistoryEntry, ProposalDetail } from "../src/api/types";

const capability = "governance:proposal_execute";
const path = "/proposals?proposal_id=proposal-date-001";
const decisionReason = "El cambio está respaldado por la evidencia revisada.";
const readyProposal: ProposalDetail = {
  ...proposalDetail,
  technical_review: {
    review_id: "technical-review-001",
    technical_status: "ready",
    technical_errors: [],
    dry_run: { status: "SIMULATED", reasons: [], field: "due_date", before: proposalDetail.current_value, after: proposalDetail.proposed_value, live_state_changed: false },
    human_approval_status: "required",
    execution_authorized: false,
    recorded_at: "2026-01-12T10:20:00-05:00",
  },
};
const blockedStaleProposal: ProposalDetail = {
  ...readyProposal,
  freshness: "stale",
  technical_eligibility: "ineligible",
  technical_errors: ["capture_stale"],
  technical_review: { ...readyProposal.technical_review!, technical_status: "blocked", technical_errors: ["capture_stale"] },
};
const dryRunAuthorizedProposal: ProposalDetail = {
  ...readyProposal,
  authorization_status: "authorized_dry_run",
  decision_history: [{
    event_id: "dry-run-auth-001",
    decision: "authorize_dry_run",
    proposal_id: readyProposal.proposal_id,
    related_proposal_id: null,
    actor_id: "gestora-e2e-fixture",
    actor_verification: "verified_local_session",
    reason: "Prueba local autorizada; decisión de ejecución pendiente.",
    recorded_at: "2026-01-12T10:25:00-05:00",
    execution_mode: "sandbox",
    execution_status: null,
    verification_status: null,
    reasons: [],
    audit_event_id: "dry-run-audit-001",
  }],
};
const offlineApprovalHistory: ProposalDecisionHistoryEntry = {
  event_id: "offline-approval-001",
  decision: "approve_offline",
  proposal_id: readyProposal.proposal_id,
  related_proposal_id: null,
  actor_id: "gestora-e2e-fixture",
  actor_verification: "asserted_offline",
  reason: "Aprobación offline ya registrada.",
  recorded_at: "2026-01-12T10:25:00-05:00",
  execution_mode: null,
  execution_status: null,
  verification_status: null,
  reasons: [],
  audit_event_id: "offline-approval-audit-001",
};
const approvedOfflineProposal: ProposalDetail = {
  ...readyProposal,
  review_status: "approved_offline",
  decision_history: [offlineApprovalHistory],
};
const approvedOfflineWithoutMatchingHistory: ProposalDetail = {
  ...approvedOfflineProposal,
  decision_history: [],
};
const unknownDecisionHistoryProposal: ProposalDetail = {
  ...dryRunAuthorizedProposal,
  decision_history: dryRunAuthorizedProposal.decision_history?.map((entry) => ({ ...entry, decision: "unknown" })),
};

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

async function open(page: Page, options: Parameters<typeof installApi>[1] = {}) {
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: [capability, "audit:read"],
    proposalFixture: readyProposal,
    ...options,
  });
  await page.goto(path);
  return observer;
}

async function confirmDecision(page: Page, buttonName: string, dialogName: string, confirmName: string) {
  await page.getByRole("button", { name: buttonName, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: dialogName });
  await dialog.getByRole("button", { name: confirmName, exact: true }).click();
  return dialog;
}

test("una propuesta bloqueada y obsoleta puede rechazarse, pero no aprobarse ni modificarse", async ({ page }) => {
  const observer = await open(page, { proposalFixture: blockedStaleProposal });

  await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Aprobar y ejecutar|Modificar y ejecutar/ })).toHaveCount(0);
  await expect(page.locator(".proposal-condition-strip").getByText("Obsoleta", { exact: true })).toBeVisible();
  await page.getByLabel("Motivo de la decisión").fill("La propuesta usa una captura obsoleta.");
  await page.getByRole("button", { name: "Rechazar propuesta", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar rechazo de propuesta" });
  await dialog.getByRole("button", { name: "Rechazar propuesta", exact: true }).click();

  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0]).toMatchObject({
    path: "/api/v1/projects/hormigestio/proposals/proposal-date-001/execute",
    body: JSON.stringify({ decision: "reject", reason: "La propuesta usa una captura obsoleta." }),
    csrfToken: "csrf-fixture",
    origin: new URL(page.url()).origin,
  });
  expect(observer.governanceRequests[0].idempotencyKey).toBeTruthy();
});

test("una autorización de prueba local no se confunde con una ejecución ni bloquea la decisión del gestor", async ({ page }) => {
  const observer = await open(page, { proposalFixture: dryRunAuthorizedProposal });
  await expect(page.getByText("Autorizada para prueba local", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toBeVisible();
  await expect(page.getByLabel("Motivo de la decisión")).toBeVisible();
  await page.locator(".proposal-history-details > summary").click();
  await expect(page.locator(".proposal-history-list")).toContainText("Autorización de prueba local");
  await expect(page.getByText("No hay un recibo de ejecución conocido.", { exact: true })).toBeVisible();
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toBeEnabled();
  expect(observer.governanceRequests).toHaveLength(0);
});

test("una aprobación offline conocida permite ejecutar solo tras una nueva confirmación del gestor", async ({ page }) => {
  const observer = await open(page, { proposalFixture: approvedOfflineProposal });
  const executeButton = page.getByRole("button", { name: "Aprobar y ejecutar", exact: true });

  await expect(page.getByText("Aprobación offline registrada", { exact: true })).toBeVisible();
  await expect(executeButton).toBeVisible();
  await expect(executeButton).toBeDisabled();
  expect(observer.governanceRequests).toHaveLength(0);

  await page.getByLabel("Motivo de la decisión").fill("Confirmar la ejecución después de revisar la aprobación offline.");
  await expect(executeButton).toBeEnabled();
  await executeButton.click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toBeVisible();
  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0]).toMatchObject({
    path: "/api/v1/projects/hormigestio/proposals/proposal-date-001/execute",
    body: JSON.stringify({ decision: "approve_and_execute", reason: "Confirmar la ejecución después de revisar la aprobación offline." }),
    csrfToken: "csrf-fixture",
    origin: new URL(page.url()).origin,
  });
  expect(observer.governanceRequests[0].idempotencyKey).toBeTruthy();
});

test("approved_offline sin un evento approve_offline coincidente no habilita ejecución", async ({ page }) => {
  await open(page, { proposalFixture: approvedOfflineWithoutMatchingHistory });

  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
  await expect(page.getByText("La aprobación offline no está confirmada en el historial protegido; no se ofrece ejecutar.", { exact: true })).toBeVisible();
});

test("una decisión desconocida en el historial mantiene la ejecución cerrada", async ({ page }) => {
  await open(page, { proposalFixture: unknownDecisionHistoryProposal });

  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
  await expect(page.getByText("El historial protegido contiene una decisión desconocida; no se ofrece ejecutar.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toBeVisible();
});

test("el Revisor técnico es automático y solo el gestor confirma una ejecución unificada", async ({ page }) => {
  const observer = await open(page);
  await expect(page.getByRole("heading", { name: "Revisión técnica lista", exact: true })).toBeVisible();
  await expect(page.getByText("Decisión pendiente", { exact: true })).toBeVisible();
  await expect(page.getByText("No autorizada", { exact: true })).toBeVisible();
  await expect(page.getByText(/autorización de ejecución aún pendiente/i)).toHaveCount(0);
  await expect(page.getByText(/Ver detalles técnicos de simulación y auditoría|Simulaciones persistidas|Revisión offline|Autorización de simulación/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Aprobar para revisión local|Autorizar simulación segura|Simular propuesta autorizada/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toBeDisabled();
  expect(observer.governanceRequests).toHaveLength(0);
});

test("approve-and-execute envía motivo, sesión same-origin y refresca la propuesta y auditoría", async ({ page }) => {
  const observer = await open(page, { capabilities: [capability, "audit:read"] });
  const initialProposalReads = observer.moduleReads.filter((item) => item.endsWith("/proposals/proposal-date-001")).length;
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toBeDisabled();
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await confirmDecision(page, "Aprobar y ejecutar", "Confirmar aprobación y ejecución", "Aprobar y ejecutar");

  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toBeVisible();
  await expect.poll(() => observer.moduleReads.filter((item) => item.endsWith("/proposals/proposal-date-001")).length).toBeGreaterThan(initialProposalReads);
  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0]).toMatchObject({
    path: "/api/v1/projects/hormigestio/proposals/proposal-date-001/execute",
    csrfToken: "csrf-fixture",
    body: JSON.stringify({ decision: "approve_and_execute", reason: decisionReason }),
    origin: new URL(page.url()).origin,
  });
  expect(observer.governanceRequests[0].idempotencyKey).toBeTruthy();
  expect(observer.governanceRequests.some((item) => /\/review$|dry-run-authorization|\/dry-runs$/.test(item.path))).toBe(false);

  await page.reload();
  await expect(page.getByText("Resultado local confirmado; no acredita cambios en Trello live.", { exact: true })).toBeVisible();
  await page.locator(".proposal-history-details > summary").click();
  await expect(page.locator(".proposal-history-list")).toContainText("Aprobación y ejecución");
  await page.locator(".proposal-history-list .proposal-raw-audit-details > summary").click();
  await expect(page.locator(".proposal-history-list")).toContainText("proposal-execution-proposal-date-001");

  await page.goto("/audit");
  await expect(page.getByRole("link", { name: "Ver evento audit-local-001" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ver evento proposal-execution-proposal-date-001" })).toBeVisible();
});

for (const scenario of [
  { name: "fecha límite", field: "due_date", proposal: readyProposal, label: "Nueva fecha límite", value: "2026-02-20", payload: { modified_due_date: "2026-02-20" } },
  { name: "prioridad", field: "priority", proposal: { ...readyProposal, ...priorityProposalDetail, technical_review: readyProposal.technical_review }, label: "Nueva prioridad", value: "low", payload: { modified_priority: "low" } },
  { name: "responsables", field: "assignee_ids", proposal: { ...readyProposal, ...assigneeProposalDetail, technical_review: readyProposal.technical_review }, label: "Nuevo responsable", value: "gestor-demo, operador-2", payload: { modified_assignee_ids: ["gestor-demo", "operador-2"] } },
] as const) {
  test(`modify-and-execute cambia solo el campo propuesto de ${scenario.name}`, async ({ page }) => {
    const observer = await open(page, { capabilities: [capability, "audit:read"], proposalFixture: scenario.proposal });
    await page.getByLabel("Motivo de la decisión").fill("Aplicar únicamente el ajuste indicado por el gestor.");
    await page.getByRole("button", { name: "Modificar el campo recomendado", exact: true }).click();
    const field = page.getByLabel(scenario.label);
    await expect(field).toBeVisible();
    if (scenario.field === "priority") await field.selectOption(scenario.value);
    else await field.fill(scenario.value);
    await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirmar modificación y ejecución" });
    await dialog.getByRole("button", { name: "Modificar y ejecutar", exact: true }).click();

    await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toBeVisible();
    expect(observer.governanceRequests).toHaveLength(1);
    expect(observer.governanceRequests[0].path).toBe("/api/v1/projects/hormigestio/proposals/proposal-date-001/execute");
    expect(JSON.parse(observer.governanceRequests[0].body!)).toEqual({
      decision: "modify_and_execute",
      ...scenario.payload,
      reason: "Aplicar únicamente el ajuste indicado por el gestor.",
    });
    expect(observer.governanceRequests[0].csrfToken).toBe("csrf-fixture");
    expect(observer.governanceRequests[0].idempotencyKey).toBeTruthy();
    await expect(page.locator(".proposal-single-comparison")).toContainText(scenario.value);
  });
}

test("la propuesta derivada enlaza el recibo y permite volver al original por la misma ruta", async ({ page }) => {
  const observer = await open(page);
  await page.getByLabel("Motivo de la decisión").fill("Ajustar únicamente la fecha recomendada.");
  await page.getByRole("button", { name: "Modificar el campo recomendado", exact: true }).click();
  await page.getByLabel("Nueva fecha límite").fill("2026-02-25");
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar modificación y ejecución" });
  await dialog.getByRole("button", { name: "Modificar y ejecutar", exact: true }).click();
  expect(JSON.parse(observer.governanceRequests[0].body!)).toEqual({
    decision: "modify_and_execute",
    modified_due_date: "2026-02-25",
    reason: "Ajustar únicamente la fecha recomendada.",
  });

  await page.locator(".proposal-history-details > summary").click();
  await page.getByRole("link", { name: "Abrir propuesta derivada 1", exact: true }).click();
  await expect(page).toHaveURL(/proposal_id=proposal-derived-001/);
  await expect(page.locator(".proposal-decision-trail")).toContainText("Resultado local confirmado; no acredita cambios en Trello live.");
  await page.locator(".proposal-history-details").evaluate((element) => { (element as HTMLDetailsElement).open = true; });
  await expect(page.locator(".proposal-history-list")).toContainText("Modificación y ejecución");
  await page.getByRole("link", { name: "Volver a la propuesta original", exact: true }).click();
  await expect(page).toHaveURL(/proposal_id=proposal-date-001/);
  await page.locator(".proposal-history-details").evaluate((element) => { (element as HTMLDetailsElement).open = true; });
  await expect(page.locator("#proposal-detail-title")).toHaveText("Fecha de vencimiento");
  await expect(page.getByRole("link", { name: "Abrir propuesta derivada 1", exact: true })).toBeVisible();
});

test("reject envía el motivo al mismo endpoint y no crea una decisión local", async ({ page }) => {
  const observer = await open(page);
  await page.getByLabel("Motivo de la decisión").fill("La evidencia de este cambio no es suficiente.");
  await page.getByRole("button", { name: "Rechazar propuesta", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar rechazo de propuesta" });
  await dialog.getByRole("button", { name: "Rechazar propuesta", exact: true }).click();

  await expect(page.getByText("Rechazo registrado por el backend", { exact: true })).toBeVisible();
  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0].path).toBe("/api/v1/projects/hormigestio/proposals/proposal-date-001/execute");
  expect(JSON.parse(observer.governanceRequests[0].body!)).toEqual({ decision: "reject", reason: "La evidencia de este cambio no es suficiente." });
  await expect(page.getByText(/guardado solo en este navegador|borrador aprobado solo/i)).toHaveCount(0);
});

test("sin audit:read, el historial y el recibo desconocidos no habilitan ejecución", async ({ page }) => {
  await open(page, { capabilities: [capability] });
  await expect(page.getByText(/El recibo no está disponible o su historial es desconocido/)).toBeVisible();
  await expect(page.getByText(/La relación entre propuestas es desconocida/)).toBeVisible();
  await page.locator(".proposal-history-details > summary").click();
  await expect(page.locator(".proposal-history-section").first()).toContainText("Disponibilidad del historial desconocida.");
  await expect(page.locator(".proposal-history-section").nth(1)).toContainText("La relación entre propuesta original y derivadas es desconocida");
  await expect(page.locator(".proposal-history-section").last()).toContainText("Disponibilidad del recibo desconocida");
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Modificar el campo recomendado", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toBeVisible();
});

test("la sesión de consulta no muestra controles de decisión ni persiste gobernanza local", async ({ page }) => {
  await open(page, { authenticated: false, capabilities: [] });
  await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Modificar el campo recomendado", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Motivo de la decisión")).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(localStorage).filter((key) => /proposal/i.test(key)))).toEqual([]);
});

test("el reintento de /execute reutiliza clave y cuerpo sin duplicar la escritura", async ({ page }) => {
  const requests: Array<{ body: Record<string, unknown>; key: string | undefined; csrf: string | undefined; origin: string | undefined }> = [];
  let committedWrites = 0;
  await open(page);
  await page.route("**/api/v1/projects/hormigestio/proposals/proposal-date-001/execute", async (route) => {
    const request = route.request();
    requests.push({
      body: request.postDataJSON(),
      key: request.headers()["idempotency-key"],
      csrf: request.headers()["x-csrf-token"],
      origin: request.headers().origin,
    });
    if (requests.length === 1) {
      await route.fulfill({ status: 503, json: { error: { code: "temporary_failure", message: "Respuesta temporal no disponible." } } });
      return;
    }
    committedWrites += 1;
    await route.fulfill({ json: {
      proposal_id: "proposal-date-001",
      decision: "approve_and_execute",
      execution: { status: "SIMULATED", verification_status: "verified" },
      execution_mode: "sandbox",
    } });
  });

  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  const confirm = dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true });
  await confirm.click();
  await expect(dialog.getByRole("alert")).toContainText("Respuesta temporal no disponible.");
  await confirm.click();

  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0].key).toBeTruthy();
  expect(requests[1].key).toBe(requests[0].key);
  expect(requests[0].body).toEqual(requests[1].body);
  expect(requests[0]).toMatchObject({
    body: { decision: "approve_and_execute", reason: decisionReason },
    csrf: "csrf-fixture",
    origin: new URL(page.url()).origin,
  });
  expect(committedWrites).toBe(1);
});

test("el backend puede rechazar una ejecución sin activar revisión o simulación de respaldo", async ({ page }) => {
  const observer = await open(page, { proposalExecutionStatus: 404 });
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(dialog.getByRole("alert")).toContainText("La ejecución no está disponible por el servidor.");
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
  expect(observer.governanceRequests).toHaveLength(1);
  expect(observer.governanceRequests[0].path).toBe("/api/v1/projects/hormigestio/proposals/proposal-date-001/execute");
  await expect(page.getByRole("button", { name: /Aprobar para revisión local|Autorizar simulación segura|Simular propuesta autorizada/ })).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(localStorage).filter((key) => /proposal/i.test(key)))).toEqual([]);
});

test("la interfaz informa live solo después de recibir el modo y la verificación del backend", async ({ page }) => {
  await open(page, { proposalExecutionBody: {
    proposal_id: "proposal-date-001",
    decision: "approve_and_execute",
    execution: { status: "EXECUTED", verification_status: "verified" },
    execution_mode: "live",
  } });
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await expect(dialog).toContainText("sandbox o live");
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(page.getByText("Resultado live informado por el backend", { exact: true })).toBeVisible();
  await expect(page.getByText(/Backend: live · EXECUTED · verificación verified/)).toBeVisible();
});

test("una verificación no confirmada no se presenta como éxito", async ({ page }) => {
  await open(page, { proposalExecutionBody: {
    proposal_id: "proposal-date-001",
    decision: "approve_and_execute",
    execution: { status: "EXECUTED", verification_status: "unknown" },
    execution_mode: "live",
  } });
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(dialog.getByRole("alert")).toContainText("no confirmó la ejecución (EXECUTED · verificación desconocida · modo live)");
  await expect(page.getByText("Resultado live informado por el backend", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
});

test("una respuesta con una decisión distinta no se presenta como resultado confirmado", async ({ page }) => {
  const observer = await open(page, { proposalExecutionBody: {
    proposal_id: "proposal-date-001",
    decision: "reject",
    execution: { status: "DENIED", verification_status: "not_required" },
    execution_mode: "sandbox",
  } });
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(dialog.getByRole("alert")).toContainText("La respuesta del backend no coincide con la decisión enviada");
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Rechazo registrado por el backend", { exact: true })).toHaveCount(0);
  expect(JSON.parse(observer.governanceRequests[0].body!)).toEqual({ decision: "approve_and_execute", reason: decisionReason });
});

test("un modo de ejecución desconocido no se presenta como resultado favorable", async ({ page }) => {
  await open(page, { proposalExecutionBody: {
    proposal_id: "proposal-date-001",
    decision: "approve_and_execute",
    execution: { status: "EXECUTED", verification_status: "verified" },
    execution_mode: "unknown",
  } });
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

  await expect(dialog.getByRole("alert")).toContainText("modo desconocido");
  await expect(page.getByText("Resultado live informado por el backend", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
});

for (const outcome of ["DENIED", "FAILED"] as const) {
  test(`una respuesta ${outcome} no se presenta como ejecución exitosa`, async ({ page }) => {
    await open(page, { proposalExecutionBody: {
      proposal_id: "proposal-date-001",
      decision: "approve_and_execute",
      execution: { status: outcome, verification_status: outcome === "DENIED" ? "not_required" : "failed", reason: `backend-${outcome.toLowerCase()}` },
      execution_mode: "sandbox",
    } });
    await page.getByLabel("Motivo de la decisión").fill(decisionReason);
    await page.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
    await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();

    await expect(dialog.getByRole("alert").filter({ hasText: outcome })).toBeVisible();
    await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Resultado live informado por el backend", { exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toBeEnabled();
  });
}

for (const readiness of [
  { name: "stale", proposal: { ...readyProposal, freshness: "stale" as const } },
  { name: "desconocida", proposal: { ...readyProposal, technical_review: null } },
  { name: "ineligible", proposal: { ...readyProposal, technical_eligibility: "ineligible" as const } },
  { name: "review bloqueado", proposal: { ...readyProposal, technical_review: { ...readyProposal.technical_review!, technical_status: "blocked" as const, technical_errors: ["source_incomplete"] } } },
]) {
  test(`no se ofrece ejecución cuando la respuesta del Revisor está ${readiness.name}`, async ({ page }) => {
    const observer = await open(page, { proposalFixture: readiness.proposal });
    await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Modificar el campo recomendado", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toBeVisible();
    expect(observer.governanceRequests).toHaveLength(0);
  });
}

for (const terminal of [
  { name: "rechazada", proposal: { ...readyProposal, review_status: "rejected" as const } },
  { name: "ejecutada", proposal: { ...readyProposal, execution_status: "EXECUTED" as const } },
  { name: "con ejecución desconocida", proposal: { ...readyProposal, execution_status: "unknown" as const } },
  { name: "consumida por una derivada", proposal: { ...readyProposal, derived_proposal_ids: ["proposal-derived-001"] } },
  { name: "decisión desconocida", proposal: { ...readyProposal, review_status: "unknown" as const } },
  { name: "autorización desconocida", proposal: { ...readyProposal, authorization_status: "unknown" as const } },
] as const) {
  test(`no se ofrecen decisiones para una propuesta ${terminal.name}`, async ({ page }) => {
    const observer = await open(page, { proposalFixture: terminal.proposal });
    await expect(page.getByRole("button", { name: "Aprobar y ejecutar", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Modificar el campo recomendado", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Rechazar propuesta", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Motivo de la decisión")).toHaveCount(0);
    expect(observer.governanceRequests).toHaveLength(0);
  });
}

for (const theme of ["light", "dark"]) {
  test(`la propuesta unificada mantiene ancho móvil y teclado en modo ${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page);
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    const modify = page.getByRole("button", { name: "Modificar el campo recomendado", exact: true });
    await modify.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Nueva fecha límite")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`proposal-${theme}.png`), fullPage: true });
  });
}

test("la navegación de propuestas conserva la identidad seleccionada en el historial del navegador", async ({ page }) => {
  await open(page, { proposalFixture: readyProposal });
  await page.goto("/proposals");
  await page.getByRole("link", { name: "Abrir propuesta para Fecha de vencimiento" }).click();
  await expect(page).toHaveURL(/proposal_id=proposal-date-001/);
  await page.goBack();
  await expect(page).toHaveURL(/\/proposals$/);
  await page.goForward();
  await expect(page).toHaveURL(/proposal_id=proposal-date-001/);
});
