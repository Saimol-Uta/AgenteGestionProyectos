import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

const frontendOrigin = "http://127.0.0.1:4173";
const apiRoot = `${frontendOrigin}/api/v1`;
const projectId = "G1WEB";
const operatorToken = "g1-browser-operator-token-never-use-in-production";
const taskId = "T-G1WEB";

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

test("a web analysis publishes the owner rules as a reviewable proposal", async ({ page, context }) => {
  const unexpected = await installNetworkGuard(page);
  await login(context);

  await page.goto("/analysis");
  await page.getByRole("button", { name: "Nuevo análisis" }).click();

  await expect(
    page.getByRole("heading", { name: "Qué se va a revisar" }),
  ).toBeVisible();
  await expect(page.getByText("Reglas de propuestas cargadas")).toBeVisible();
  await expect(
    page.getByText("El análisis puede publicar propuestas correctivas a partir de las reglas del propietario."),
  ).toBeVisible();

  const confirmed = page.waitForResponse(
    (response) => response.url().includes("/analysis/confirm") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  const confirmation = await confirmed;
  expect(confirmation.status()).toBe(202);
  const jobId = ((await confirmation.json()) as { job: { job_id: string } }).job.job_id;

  let status = "queued";
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const polled = await context.request.get(`${apiRoot}/analysis/jobs/${jobId}`);
    expect(polled.status()).toBe(200);
    status = ((await polled.json()) as { status: string }).status;
    if (status === "completed" || status === "failed") {
      break;
    }
    await page.waitForTimeout(500);
  }
  expect(status).toBe("completed");

  await page.goto("/proposals");
  await expect(page.getByRole("heading", { name: "Bandeja de revisión" })).toBeVisible();
  await expect(page.getByText("1 propuestas")).toBeVisible();
  const proposalLink = page.getByRole("link", { name: /Abrir propuesta para/ }).first();
  await expect(proposalLink).toBeVisible();
  await proposalLink.click();
  const taskLink = page.getByRole("link", { name: /Abrir tarea/ });
  await expect(taskLink).toBeVisible();
  await expect(taskLink).toHaveAttribute("href", new RegExp(encodeURIComponent(taskId)));

  const detail = await context.request.get(
    `${apiRoot}/projects/${projectId}/proposals?page_size=100`,
  );
  expect(detail.status()).toBe(200);
  const collection = await detail.json() as {
    total: number;
    pending_actionable_count: number | null;
    items: Array<{ task_id: string; field: string; execution_status: string }>;
  };
  expect(collection.total).toBe(1);
  expect(collection.pending_actionable_count).toBe(1);
  expect(collection.items[0].task_id).toBe(taskId);
  expect(collection.items[0].field).toBe("due_date");
  expect(collection.items[0].execution_status).toBe("not_executed");

  expect(unexpected).toEqual([]);
});
