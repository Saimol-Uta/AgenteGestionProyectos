import fs from "node:fs";
import path from "node:path";
import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

const frontendOrigin = "http://127.0.0.1:4173";
const apiRoot = `${frontendOrigin}/api/v1`;
const projectId = "P";
const actorId = "gestora-e2e-fixture";
const operatorToken = "t2-fixture-operator-token-never-use-in-production";
const decisionReason = "Aprobar en sandbox tras revisar la evidencia de la propuesta.";
const proposalPath = (proposalId: string) => `/proposals?proposal_id=${encodeURIComponent(proposalId)}`;

type ProposalCollection = {
  total: number;
  pending_actionable_count: number | null;
  items: Array<{ proposal_id: string }>;
};

type ProposalDetail = {
  proposal_id: string;
  decision_history: Array<{
    decision: string;
    event_id: string;
    actor_id: string | null;
    actor_verification: string;
    reason: string | null;
    recorded_at: string | null;
    execution_mode: string | null;
    execution_status: string | null;
    verification_status: string | null;
    audit_event_id: string | null;
  }>;
  latest_receipt: {
    execution_id: string;
    audit_event_id: string | null;
    status: string;
    execution_mode: string | null;
    verification_status: string;
  } | null;
};

type AuditEvent = {
  event_id: string;
  event_type: string;
  occurred_at: string;
  outcome: string;
  summary: string;
  actor_id: string | null;
  actor_verification: string;
  details?: Record<string, unknown>;
};

function scratchPath(name: string): string {
  const root = path.resolve(process.env.REQ08_T6_SCRATCH_ROOT ?? "");
  const candidate = path.resolve(process.env[name] ?? "");
  const relative = path.relative(root, candidate);
  if (!root || !candidate || !relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`${name} must be a unique path inside the authorized T6 scratch root.`);
  }
  return candidate;
}

function appendGuardEvent(name: string, event: Record<string, unknown>) {
  fs.appendFileSync(scratchPath(name), `${JSON.stringify(event)}\n`, { encoding: "utf8", flag: "a" });
}

function allowedBrowserOrigin(value: string): boolean {
  const url = new URL(value);
  return url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port === "4173";
}

const unexpectedBrowserRequests = new WeakMap<Page, string[]>();

async function installBrowserNetworkGuard(page: Page) {
  const unexpected: string[] = [];
  unexpectedBrowserRequests.set(page, unexpected);
  appendGuardEvent("REQ08_T6_BROWSER_GUARD_LOG", {
    event: "browser-network-guard-active",
    pid: process.pid,
    allowed_origin: frontendOrigin,
    service_workers: "blocked",
    host_resolver: "127.0.0.1 only",
  });
  await page.route("**/*", (route) => allowedBrowserOrigin(route.request().url())
    ? route.continue()
    : route.abort("blockedbyclient"));
  await page.routeWebSocket("**/*", (route) => {
    const url = new URL(route.url());
    if (url.protocol === "ws:" && url.hostname === "127.0.0.1" && url.port === "4173") {
      route.connectToServer();
    } else {
      appendGuardEvent("REQ08_T6_BROWSER_NETWORK_LOG", { event: "blocked-websocket", target: url.origin });
      route.close();
    }
  });
  page.on("request", (request: Request) => {
    if (allowedBrowserOrigin(request.url())) return;
    const url = new URL(request.url());
    const target = `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`;
    unexpected.push(target);
    appendGuardEvent("REQ08_T6_BROWSER_NETWORK_LOG", {
      event: "blocked-or-unexpected-browser-request",
      method: request.method(),
      target,
      pid: process.pid,
    });
  });
  page.on("websocket", (socket) => {
    const url = new URL(socket.url());
    if (url.protocol === "ws:" && url.hostname === "127.0.0.1" && url.port === "4173") return;
    const target = `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`;
    unexpected.push(target);
    appendGuardEvent("REQ08_T6_BROWSER_NETWORK_LOG", {
      event: "blocked-or-unexpected-browser-websocket",
      target,
      pid: process.pid,
    });
  });
}

async function getJson<T>(context: BrowserContext, pathName: string): Promise<T> {
  const response = await context.request.get(`${apiRoot}${pathName}`);
  expect(response.status()).toBe(200);
  return await response.json() as T;
}

function displayedDate(value: string): string {
  return new Intl.DateTimeFormat("es-EC", {
    timeZone: "America/Guayaquil",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function assertAuditEventVisible(page: Page, event: AuditEvent, expectedActor?: string) {
  await expect(page.getByRole("heading", { name: "Auditoría y actividad", exact: true })).toBeVisible();
  await expect(page.locator(".audit-detail-summary")).toHaveText(event.summary);
  const grid = page.locator(".audit-detail-grid");
  await expect(grid).toContainText(event.outcome);
  await expect(grid).toContainText(displayedDate(event.occurred_at));
  if (expectedActor) await expect(grid).toContainText(`Sesión local verificada: ${expectedActor}`);
  await expect(page.locator(".audit-details-json")).toContainText(decisionReason);
}

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  expect(unexpectedBrowserRequests.get(page) ?? []).toEqual([]);
});

test("Dashboard completa una decisión real en sandbox y enlaza su referencia pública de auditoría", async ({
  page,
  context,
  request,
}) => {
  const healthResponse = await context.request.get(`${apiRoot}/system/health`);
  expect(healthResponse.status()).toBe(200);
  const health = await healthResponse.json() as { external_writes_enabled: boolean };
  expect(health.external_writes_enabled).toBe(false);

  const login = await context.request.post(`${apiRoot}/auth/login`, {
    headers: { Origin: frontendOrigin },
    data: { token: operatorToken },
  });
  expect(login.status()).toBe(200);
  const session = await login.json() as { authenticated: boolean; actor: string; csrf_token: string; capabilities: string[] };
  expect(session.authenticated).toBe(true);
  expect(session.actor).toBe(actorId);
  expect(session.capabilities).toContain("governance:proposal_execute");
  expect(session.capabilities).toContain("audit:read");
  expect(session.csrf_token).toBeTruthy();
  const sessionCookies = await context.cookies(`${frontendOrigin}/api/v1`);
  expect(sessionCookies.map(({ name, path: cookiePath, httpOnly }) => ({ name, path: cookiePath, httpOnly }))).toContainEqual({
    name: "h5_operator_session",
    path: "/api/v1",
    httpOnly: true,
  });

  const initialCollection = await getJson<ProposalCollection>(context, `/projects/${projectId}/proposals?page_size=100`);
  expect(initialCollection.total).toBe(1);
  expect(initialCollection.pending_actionable_count).toBe(1);
  const proposalId = initialCollection.items[0]?.proposal_id;
  expect(proposalId).toBeTruthy();

  let executionPosts = 0;
  let postedBody: Record<string, unknown> | undefined;
  let postedHeaders: Record<string, string> | undefined;
  page.on("request", (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (browserRequest.method() === "POST"
      && url.pathname === `/api/v1/projects/${projectId}/proposals/${proposalId}/execute`) {
      executionPosts += 1;
      postedBody = browserRequest.postDataJSON() as Record<string, unknown>;
      postedHeaders = browserRequest.headers();
    }
  });

  await page.goto("/");
  const dashboardDecision = page.locator(".decision-trello");
  await expect(dashboardDecision.locator(".decision-count")).toHaveText("1");
  await expect(dashboardDecision).toContainText("1 propuestas están pendientes de tu decisión");
  await dashboardDecision.getByRole("link", { name: "Revisar propuestas", exact: true }).click();
  await expect(page).toHaveURL(/\/proposals(?:\?.*)?$/);
  await page.getByRole("link", { name: /Abrir propuesta para/ }).click();
  await expect(page.getByRole("heading", { name: "Revisión técnica lista", exact: true })).toBeVisible();
  const conditions = page.locator(".proposal-business-step").filter({ hasText: "4 · CONDICIONES TÉCNICAS" });
  await expect(conditions).toContainText("Elegible");
  await expect(conditions).toContainText("Actual");

  const approve = page.getByRole("button", { name: "Aprobar y ejecutar", exact: true });
  await expect(approve).toBeDisabled();
  await page.getByLabel("Motivo de la decisión").fill(decisionReason);
  await expect(approve).toBeEnabled();
  await approve.click();
  const dialog = page.getByRole("dialog", { name: "Confirmar aprobación y ejecución" });
  const executionResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST"
      && url.pathname === `/api/v1/projects/${projectId}/proposals/${proposalId}/execute`;
  });
  await dialog.getByRole("button", { name: "Aprobar y ejecutar", exact: true }).click();
  const executionResponse = await executionResponsePromise;
  expect(executionResponse.status()).toBe(200);
  const receipt = await executionResponse.json() as {
    decision: string;
    execution_mode: string;
    execution: {
      execution_id: string;
      status: string;
      result_status: string;
      verification_status: string;
      live_state_changed: boolean;
    };
  };
  expect(receipt.decision).toBe("approve_and_execute");
  expect(receipt.execution_mode).toBe("sandbox");
  expect(receipt.execution.status).toBe("SIMULATED");
  expect(receipt.execution.result_status).toBe("EXECUTED");
  expect(receipt.execution.verification_status).toBe("verified");
  expect(receipt.execution.live_state_changed).toBe(false);
  expect(receipt.execution.execution_id).toBeTruthy();
  await expect(page.locator(".proposal-demo-result")).toContainText("Resultado sandbox registrado · no acredita cambios live");
  await expect(page.locator(".proposal-demo-result")).toContainText("SIMULATED");

  expect(executionPosts).toBe(1);
  expect(postedBody).toEqual({ decision: "approve_and_execute", reason: decisionReason });
  expect(postedHeaders?.origin).toBe(frontendOrigin);
  expect(postedHeaders?.["x-csrf-token"]).toBe(session.csrf_token);
  expect(postedHeaders?.["idempotency-key"]).toBeTruthy();

  const detailBeforeReload = await getJson<ProposalDetail>(
    context, `/projects/${projectId}/proposals/${proposalId}`,
  );
  expect(detailBeforeReload.latest_receipt?.execution_id).toBe(receipt.execution.execution_id);
  expect(detailBeforeReload.latest_receipt?.status).toBe("EXECUTED");
  expect(detailBeforeReload.latest_receipt?.execution_mode).toBe("sandbox");
  expect(detailBeforeReload.decision_history).toHaveLength(1);
  const decision = detailBeforeReload.decision_history[0];
  expect(decision).toMatchObject({
    decision: "approve_and_execute",
    actor_id: actorId,
    actor_verification: "verified_local_session",
    reason: decisionReason,
    execution_mode: "sandbox",
    execution_status: "EXECUTED",
    verification_status: "verified",
  });
  expect(decision.recorded_at).toBeTruthy();
  const auditEventId = decision.audit_event_id;
  expect(auditEventId).toBeTruthy();
  expect(detailBeforeReload.latest_receipt?.audit_event_id).toBe(auditEventId);

  const securityEventsResponse = await context.request.get(
    `${apiRoot}/projects/${projectId}/audit?event_type=security&page_size=100`,
  );
  expect(securityEventsResponse.status()).toBe(200);
  const securityEvents = await securityEventsResponse.json() as { items: AuditEvent[] };
  const securityEvent = securityEvents.items.find((event) => event.event_id === auditEventId);
  expect(securityEvent).toMatchObject({
    event_id: auditEventId,
    event_type: "security",
    outcome: "granted",
    actor_id: actorId,
    actor_verification: "verified_local_session",
  });
  const securityDetailResponse = await context.request.get(
    `${apiRoot}/projects/${projectId}/audit/${encodeURIComponent(auditEventId!)}`,
  );
  expect(securityDetailResponse.status()).toBe(200);
  const securityDetail = await securityDetailResponse.json() as AuditEvent;
  expect(securityDetail).toMatchObject({
    event_id: auditEventId,
    actor_id: actorId,
    outcome: "granted",
    details: { human_reason: decisionReason, permission: "web:proposal_execute", decision: "granted" },
  });
  expect(securityDetail.occurred_at).toBeTruthy();

  const anonymousSecurityRead = await request.get(
    `${apiRoot}/projects/${projectId}/audit/${encodeURIComponent(auditEventId!)}`,
  );
  expect(anonymousSecurityRead.status()).toBe(401);

  const executionEventsResponse = await context.request.get(
    `${apiRoot}/projects/${projectId}/audit?event_type=proposal_execution&page_size=100`,
  );
  expect(executionEventsResponse.status()).toBe(200);
  const executionEvents = await executionEventsResponse.json() as { total: number; items: AuditEvent[] };
  expect(executionEvents.total).toBe(1);
  const executionEvent = executionEvents.items.find((event) => event.event_id === receipt.execution.execution_id);
  expect(executionEvent).toMatchObject({
    event_id: receipt.execution.execution_id,
    event_type: "proposal_execution",
    outcome: "EXECUTED",
  });
  const executionDetailResponse = await context.request.get(
    `${apiRoot}/projects/${projectId}/audit/${encodeURIComponent(receipt.execution.execution_id)}`,
  );
  expect(executionDetailResponse.status()).toBe(200);
  const executionDetail = await executionDetailResponse.json() as AuditEvent;
  expect(executionDetail).toMatchObject({
    event_id: receipt.execution.execution_id,
    outcome: "EXECUTED",
    details: { status: "EXECUTED", verification_status: "verified", human_reason: decisionReason },
  });

  await page.reload();
  await expect(page.locator(".proposal-decision-trail")).toContainText(
    "Resultado local confirmado; no acredita cambios en Trello live.",
  );
  const detailAfterReload = await getJson<ProposalDetail>(
    context, `/projects/${projectId}/proposals/${proposalId}`,
  );
  expect(detailAfterReload.latest_receipt?.execution_id).toBe(receipt.execution.execution_id);
  expect(detailAfterReload.decision_history).toHaveLength(1);
  expect(executionPosts).toBe(1);

  const finalCollection = await getJson<ProposalCollection>(context, `/projects/${projectId}/proposals?page_size=100`);
  expect(finalCollection.pending_actionable_count).toBe(0);
  await page.locator(".sidebar-navigation").getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page.locator(".decision-trello .decision-count")).toHaveText("0");
  await expect(page.locator(".decision-trello")).toContainText("No hay propuestas pendientes que requieran tu decisión.");

  await page.goto(proposalPath(proposalId!));
  await expect(page.locator(".proposal-history-details > summary")).toBeVisible();
  await page.locator(".proposal-history-details > summary").click();
  const history = page.locator(".proposal-history-list");
  await expect(history).toContainText(actorId);
  await expect(history).toContainText(decisionReason);
  await expect(history).toContainText(displayedDate(decision.recorded_at!));
  await expect(history).toContainText("Ejecución registrada");
  await expect(history).toContainText("Sandbox; no acredita una escritura live");

  await page.goto(`/audit?event_id=${encodeURIComponent(auditEventId!)}`);
  await assertAuditEventVisible(page, securityDetail, actorId);
  await page.goto(`/audit?event_id=${encodeURIComponent(receipt.execution.execution_id)}`);
  await assertAuditEventVisible(page, executionDetail);
  await expect(page.locator(".audit-detail-grid")).toContainText("EXECUTED");

  await page.goto(proposalPath(proposalId!));
  await page.locator(".proposal-history-details > summary").click();
  const contextualAuditLink = page.getByRole("link", { name: "Ver evento en Auditoría", exact: true });
  await expect(contextualAuditLink).toHaveAttribute(
    "href", `/audit?event_id=${encodeURIComponent(auditEventId!)}`,
  );
  await contextualAuditLink.click();
  await expect(page).toHaveURL(new RegExp(`/audit\\?event_id=${encodeURIComponent(auditEventId!)}`));
  await assertAuditEventVisible(page, securityDetail, actorId);
  expect(executionPosts).toBe(1);
});
