import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

const frontendOrigin = "http://127.0.0.1:4173";
const apiRoot = `${frontendOrigin}/api/v1`;
const projectId = "HORMIGESTION";
const operatorToken = "g1-demo-operator-token-never-use-in-production";
const sourceDatabase = fileURLToPath(new URL("../../.runtime/agente.db", import.meta.url));
const taskId = "6a9582c16a2c16f6106609e2";
const evidenceId = "trello-task-6a9582c16a2c16f6106609e2";
const currentDue = "2026-10-19T17:00:00-05:00";
const proposedDue = "2026-10-20T17:00:00-05:00";
const decisionReason = "Validacion controlada G1: caso de prueba, sin efecto operativo en Trello.";

function sha256(file: string): string {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function allowedOrigin(value: string): boolean {
  const url = new URL(value);
  return url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port === "4173";
}

async function installNetworkGuard(page: Page): Promise<string[]> {
  const unexpected: string[] = [];
  page.on("request", (request: Request) => {
    if (!allowedOrigin(request.url())) {
      unexpected.push(request.url());
    }
  });
  return unexpected;
}

async function login(context: BrowserContext) {
  const response = await context.request.post(`${apiRoot}/auth/login`, {
    data: { token: operatorToken },
    headers: { Origin: frontendOrigin },
  });
  expect(response.status()).toBe(200);
  const session = await response.json() as { authenticated: boolean; csrf_token: string };
  expect(session.authenticated).toBe(true);
  expect(session.csrf_token).toBeTruthy();
  return session;
}

test("controlled G1 validation over an isolated copy of the real HORMIGESTION data", async ({ page, context }) => {
  const sourceHashBefore = sha256(sourceDatabase);
  const unexpected = await installNetworkGuard(page);
  await login(context);

  // 1. Analysis prepared and confirmed from the browser.
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();
  await expect(page.getByRole("heading", { name: "Qué se va a revisar" })).toBeVisible();
  await expect(page.getByText("Reglas de propuestas cargadas")).toBeVisible();

  const confirmed = page.waitForResponse(
    (response) => response.url().includes("/analysis/confirm") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  const confirmation = await confirmed;
  expect(confirmation.status()).toBe(202);
  const jobId = ((await confirmation.json()) as { job: { job_id: string } }).job.job_id;

  let status = "queued";
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const polled = await context.request.get(`${apiRoot}/analysis/jobs/${jobId}`);
    expect(polled.status()).toBe(200);
    status = ((await polled.json()) as { status: string }).status;
    if (status === "completed" || status === "failed") {
      break;
    }
    await page.waitForTimeout(500);
  }
  expect(status).toBe("completed");

  // 2. The proposal appears in the review inbox.
  await page.goto("/proposals");
  await expect(page.getByRole("heading", { name: "Bandeja de revisión" })).toBeVisible();
  await expect(page.getByText("1 propuestas")).toBeVisible();
  const proposalLink = page.getByRole("link", { name: /Abrir propuesta para/ }).first();
  await expect(proposalLink).toBeVisible();
  await proposalLink.click();

  // 3. Review: the exact task, the evidence, the current and the proposed value.
  await expect(page.getByRole("heading", { name: "Revisión técnica lista" })).toBeVisible();
  await expect(page.getByText("Valor actual", { exact: true })).toBeVisible();
  await expect(page.getByText("Cambio recomendado", { exact: true })).toBeVisible();
  const taskLink = page.getByRole("link", { name: /Abrir tarea/ });
  await expect(taskLink).toHaveAttribute("href", new RegExp(encodeURIComponent(taskId)));
  await page.getByText("Ver evidencia y datos técnicos de la propuesta").click();
  await expect(page.getByText(evidenceId)).toBeVisible();

  const listed = await context.request.get(`${apiRoot}/projects/${projectId}/proposals?page_size=100`);
  expect(listed.status()).toBe(200);
  const collection = await listed.json() as {
    total: number;
    pending_actionable_count: number | null;
    items: Array<{
      proposal_id: string;
      task_id: string;
      field: string;
      current_value: string;
      proposed_value: string;
      execution_status: string;
    }>;
  };
  expect(collection.total).toBe(1);
  expect(collection.pending_actionable_count).toBe(1);
  const proposal = collection.items[0];
  expect(proposal.task_id).toBe(taskId);
  expect(proposal.field).toBe("due_date");
  expect(proposal.current_value).toBe(currentDue);
  expect(proposal.proposed_value).toBe(proposedDue);
  expect(proposal.execution_status).toBe("not_executed");

  const beforeDecision = await context.request.get(`${apiRoot}/projects/${projectId}/proposals/${proposal.proposal_id}`);
  expect(beforeDecision.status()).toBe(200);
  const before = await beforeDecision.json() as {
    evidence: Array<{ evidence_id: string; category: string }>;
    technical_review: { technical_status: string; execution_authorized: boolean };
    review_status: string;
    authorization_status: string;
    execution_status: string;
  };
  expect(before.evidence.map((item) => item.evidence_id)).toContain(evidenceId);
  expect(before.evidence[0].category).toBe("task");
  expect(before.technical_review.technical_status).toBe("ready");
  expect(before.technical_review.execution_authorized).toBe(false);
  expect(before.review_status).toBe("pending");
  expect(before.authorization_status).toBe("not_authorized");
  expect(before.execution_status).toBe("not_executed");

  // 4. Decision: the manager approves with a reason.
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Aprobar y ejecutar" }).click();

  // 5. Sandbox result: simulated, and explicitly not a live change.
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live")).toBeVisible();
  await expect(page.getByText(/estado: SIMULATED/)).toBeVisible();

  const receipt = await context.request.get(`${apiRoot}/projects/${projectId}/proposals/${proposal.proposal_id}`);
  expect(receipt.status()).toBe(200);
  const detail = await receipt.json() as {
    review_status: string;
    execution_status: string;
    latest_receipt: { status: string; execution_mode: string } | null;
    decision_history: Array<{ decision: string; actor_id: string | null; reason: string | null }>;
  };
  expect(detail.review_status).toBe("approved_offline");
  expect(detail.execution_status).toBe("EXECUTED");
  expect(detail.latest_receipt?.status).toBe("EXECUTED");
  expect(detail.latest_receipt?.execution_mode).toBe("sandbox");
  expect(detail.decision_history[0].decision).toBe("approve_and_execute");
  expect(detail.decision_history[0].reason).toBe(decisionReason);

  // 6. The receipt and the history survive a reload.
  await page.getByText("Ver decisiones, relaciones y datos de auditoría disponibles").click();
  await expect(page.getByRole("heading", { name: "Recibo durable" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Historial de decisiones humanas" })).toBeVisible();
  await expect(page.getByText(decisionReason).first()).toBeVisible();
  await page.reload();
  await page.getByText("Ver decisiones, relaciones y datos de auditoría disponibles").click();
  await expect(page.getByRole("heading", { name: "Recibo durable" })).toBeVisible();
  await expect(page.getByText(decisionReason).first()).toBeVisible();

  // 7. Audit: the execution event and the security event are persisted.
  await page.goto(`/audit?event_type=proposal_execution`);
  await expect(page.getByText("proposal_execution").first()).toBeVisible();
  await expect(page.getByText(/Intento de ejecución registrado/).first()).toBeVisible();
  await page.goto(`/audit?event_type=security`);
  await expect(page.getByText(/web:proposal_execute/).first()).toBeVisible();

  // 8. The project database and the Trello board are untouched.
  expect(sha256(sourceDatabase)).toBe(sourceHashBefore);
  expect(unexpected).toEqual([]);
});
