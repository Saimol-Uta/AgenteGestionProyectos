import crypto from "node:crypto";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

const frontendOrigin = "http://127.0.0.1:4173";
const apiRoot = `${frontendOrigin}/api/v1`;
const projectId = "HORMIGESTION";
const operatorToken = "g3-demo-operator-token-never-use-in-production";
const sourceDatabase = fileURLToPath(new URL("../../.runtime/agente.db", import.meta.url));
const taskId = "6a9582c16a2c16f6106609e2";
const unknownTaskId = "6a958243ac18e7ebdc471d37";
const decisionReason = "Validacion controlada G3: caso de prueba, sin efecto operativo en Trello.";

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

async function runAnalysis(context: BrowserContext, csrfToken: string) {
  const prepared = await context.request.post(`${apiRoot}/analysis/prepare`, {
    data: { project_id: projectId, request_analysis: true },
    headers: { Origin: frontendOrigin, "X-CSRF-Token": csrfToken },
  });
  expect(prepared.status()).toBe(200);
  const preparation = await prepared.json() as {
    preparation_id: string;
    configuration_fingerprint: string;
  };

  const confirmed = await context.request.post(`${apiRoot}/analysis/confirm`, {
    data: {
      project_id: projectId,
      preparation_id: preparation.preparation_id,
      configuration_fingerprint: preparation.configuration_fingerprint,
      confirm_analysis: true,
      remote_consent: false,
    },
    headers: {
      Origin: frontendOrigin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": "g3-demo-analysis-0001",
    },
  });
  expect(confirmed.status()).toBe(202);
  const jobId = ((await confirmed.json()) as { job: { job_id: string } }).job.job_id;

  let status = "queued";
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const polled = await context.request.get(`${apiRoot}/analysis/jobs/${jobId}`);
    expect(polled.status()).toBe(200);
    status = ((await polled.json()) as { status: string }).status;
    if (status === "completed" || status === "failed") {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  expect(status).toBe("completed");
}

test("controlled G3 validation: risk, task, proposal, review and decision over isolated real data", async ({ page, context }) => {
  const sourceHashBefore = sha256(sourceDatabase);
  const unexpected = await installNetworkGuard(page);
  const session = await login(context);
  await runAnalysis(context, session.csrf_token);

  // 1. The stored proposal really carries the deterministic risk of its capture.
  const listed = await context.request.get(`${apiRoot}/projects/${projectId}/proposals?page_size=100`);
  expect(listed.status()).toBe(200);
  const collection = await listed.json() as {
    total: number;
    items: Array<{
      proposal_id: string;
      task_id: string;
      field: string;
      risk_refs: Array<{
        risk_id: string;
        kind: string;
        task_id: string;
        capture_id: string;
        resolved: boolean;
      }>;
    }>;
  };
  expect(collection.total).toBe(1);
  const proposal = collection.items[0];
  expect(proposal.task_id).toBe(taskId);
  expect(proposal.risk_refs.length).toBe(1);
  expect(proposal.risk_refs[0].task_id).toBe(taskId);
  expect(proposal.risk_refs[0].resolved).toBe(true);
  expect(proposal.risk_refs[0].risk_id.startsWith("risk-")).toBe(true);

  // 2. The summary binds that proposal to the risk and leaves the other risk alone.
  const summaryResponse = await context.request.get(`${apiRoot}/projects/${projectId}/summary`);
  expect(summaryResponse.status()).toBe(200);
  const summary = await summaryResponse.json() as {
    capture_id: string;
    risks: Array<{
      risk_id: string;
      kind: string;
      basis: string;
      task_ids: string[];
      task_titles?: Record<string, string>;
      cutoff_datetime?: string | null;
      proposals: Array<{ proposal_id: string; review_status: string; execution_status: string }>;
    }>;
    proposals_context: {
      run_id: string;
      capture_id: string | null;
      diagnostic: { outcome: string; saved: number };
    } | null;
  };
  // The rule answers the deadline risk the stored capture really presents, so
  // the expectation follows the data instead of a date that could expire.
  const deadlineRisk = summary.risks.find(
    (risk) => risk.task_ids.includes(taskId) && risk.kind !== "unknown_state",
  );
  expect(deadlineRisk).toBeTruthy();
  expect(proposal.risk_refs[0].kind).toBe(deadlineRisk?.kind);
  expect(deadlineRisk?.task_ids).toContain(taskId);
  expect(deadlineRisk?.task_titles?.[taskId]).toBeTruthy();
  expect(deadlineRisk?.cutoff_datetime).toBeTruthy();
  expect(deadlineRisk?.proposals.map((link) => link.proposal_id)).toEqual([proposal.proposal_id]);
  expect(deadlineRisk?.proposals[0].review_status).toBe("pending");
  expect(deadlineRisk?.proposals[0].execution_status).toBe("not_executed");
  const unknown = summary.risks.find((risk) => risk.kind === "unknown_state");
  expect(unknown?.task_ids).toContain(unknownTaskId);
  expect(unknown?.proposals).toEqual([]);
  expect(summary.proposals_context?.diagnostic.outcome).toBe("saved");

  // 3. Dashboard: the risk shows the proposal that answers it, and the other one admits it has none.
  await page.goto("/");
  const risks = page.getByRole("region", { name: "Alertas de la captura" });
  await expect(risks).toBeVisible();
  await expect(risks.getByText(deadlineRisk?.basis ?? "")).toBeVisible();
  await expect(risks.getByText(proposal.proposal_id)).toBeVisible();
  const unknownRiskItem = risks.locator("li.risk-item").filter({
    has: page.locator(`a[href*="task=${unknownTaskId}"]`),
  });
  await expect(unknownRiskItem.getByText("No hay una propuesta vinculada a este riesgo")).toBeVisible();

  // 4. The risk leads to its task, and the task shows the same classification.
  const taskLink = risks.locator(`a[href*="task=${taskId}"]`).first();
  await expect(taskLink).toHaveAttribute("href", new RegExp(`task=${taskId}`));
  await expect(taskLink).toHaveText(deadlineRisk?.task_titles?.[taskId] ?? "");
  await taskLink.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "¿Por qué requiere atención?" })).toBeVisible();
  await expect(drawer.getByText(deadlineRisk?.kind === "overdue" ? "Vencida" : "Próxima a vencer", { exact: true }).first()).toBeVisible();
  await expect(drawer.getByText(proposal.proposal_id).first()).toBeVisible();

  // 5. The proposal reports the risk that motivated it and links back to its task.
  await page.goto("/proposals");
  await expect(page.getByRole("heading", { name: "Bandeja de revisión" })).toBeVisible();
  await page.getByRole("link", { name: /Abrir propuesta para/ }).first().click();
  await expect(page.getByRole("heading", { name: "Riesgo que motivó la acción" })).toBeVisible();
  await expect(page.getByText(deadlineRisk?.kind === "overdue" ? "Vencida" : "Próxima a vencer").first()).toBeVisible();
  const originLink = page.locator(`a[href*="task=${taskId}"]`).first();
  await expect(originLink).toHaveAttribute("href", new RegExp(`task=${taskId}`));
  await expect(originLink).toHaveAttribute("href", /capture_id=/);

  // 6. The executive detail of REQ-08 still works: decision, sandbox result and receipt.
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await page.getByRole("button", { name: "Aprobar y ejecutar" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Aprobar y ejecutar" }).click();
  await expect(page.getByText("Resultado sandbox registrado · no acredita cambios live")).toBeVisible();
  const receipt = await context.request.get(`${apiRoot}/projects/${projectId}/proposals/${proposal.proposal_id}`);
  expect(receipt.status()).toBe(200);
  const detail = await receipt.json() as {
    review_status: string;
    execution_status: string;
    risk_refs: Array<{ kind: string; resolved: boolean }>;
    latest_receipt: { status: string; execution_mode: string } | null;
  };
  expect(detail.review_status).toBe("approved_offline");
  expect(detail.execution_status).toBe("EXECUTED");
  expect(detail.latest_receipt?.execution_mode).toBe("sandbox");
  expect(detail.risk_refs[0].kind).toBe(deadlineRisk?.kind);
  expect(detail.risk_refs[0].resolved).toBe(true);

  // 7. The source database and the board are untouched.
  expect(sha256(sourceDatabase)).toBe(sourceHashBefore);
  expect(unexpected).toEqual([]);
});
