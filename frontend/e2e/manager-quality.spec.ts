import { expect, test, type Page } from "@playwright/test";
import { findingDetail, installApi } from "./fixtures";

const capability = "quality:decide";
const backendSuggestion = "export function validateInput(value: string): string { return value.trim(); }";
const backendExplanation = "El backend propone normalizar el valor antes de usarlo.";

async function openFinding(page: Page, detail: unknown = findingDetail, options: Parameters<typeof installApi>[1] = {}) {
  await installApi(page, options);
  await page.route("**/runs/run-current/findings/finding-candidate", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail) });
  });
  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  return page.getByRole("dialog");
}

test("la vista de consulta no fabrica sugerencias ni decisiones locales", async ({ page }) => {
  const dialog = await openFinding(page);

  await expect(dialog.getByRole("heading", { name: "Código Actual" })).toBeVisible();
  await expect(dialog.locator(".quality-code-before code")).toContainText("<script>window.__findingXss");
  await expect(dialog.getByText("El backend no informó un ejemplo de código ni una explicación.", { exact: true })).toBeVisible();
  await expect(dialog.locator(".quality-code-after code")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios|Editar código/i })).toHaveCount(0);
  await expect(dialog.getByLabel("Rama separada de verificación")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__findingXss)).toBeUndefined();
  expect(await page.evaluate(() => Object.keys(localStorage).filter((key) => /quality|finding/i.test(key)))).toEqual([]);
});

test("la aprobación usa solo la sugerencia del backend y conserva el estado aprobado tras recargar", async ({ page }) => {
  const current = {
    ...findingDetail,
    suggested_code: backendSuggestion,
    improvement_summary: backendExplanation,
  };
  const writes: Array<{ path: string; body: Record<string, unknown>; csrf: string | undefined; key: string | undefined; origin: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities: [capability] });
  await page.route("**/api/v1/runs/run-current/findings/finding-candidate**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({ json: current });
    writes.push({
      path: new URL(request.url()).pathname,
      body: request.postDataJSON(),
      csrf: request.headers()["x-csrf-token"],
      key: request.headers()["idempotency-key"],
      origin: request.headers().origin,
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    const result = {
      decision_status: "approved",
      suggested_code: backendSuggestion,
      improvement_summary: backendExplanation,
      branch_name: "quality/finding-candidate",
      branch_url: null,
      commit_sha_applied: "c".repeat(40),
      execution_mode: "sandbox",
    };
    Object.assign(current, result);
    await route.fulfill({ json: result });
  });

  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".quality-code-after code")).toHaveText(backendSuggestion);
  await expect(dialog.getByText(backendExplanation, { exact: true })).toBeVisible();
  await dialog.getByLabel("Rama separada de verificación").fill("quality/finding-candidate");
  const approve = dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true });
  await approve.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });

  await expect(dialog.locator(".quality-decision-result")).toContainText("Aprobada");
  await expect(dialog.locator(".quality-decision-result")).toContainText("sandbox");
  await expect(dialog.locator(".quality-decision-result")).toContainText("quality/finding-candidate");
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    path: "/api/v1/runs/run-current/findings/finding-candidate/decision",
    body: { decision: "approve", target_branch: "quality/finding-candidate" },
    csrf: "csrf-fixture",
    origin: new URL(page.url()).origin,
  });
  expect(writes[0].key).toBeTruthy();
  expect(writes[0].body).not.toHaveProperty("suggested_code_override");
  await expect(dialog.getByText(/simulado|Commit simulado/i)).toHaveCount(0);

  await page.reload();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const refreshed = page.getByRole("dialog");
  await expect(refreshed.locator(".quality-decision-result")).toContainText("Aprobada");
  await expect(refreshed.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios/i })).toHaveCount(0);
  expect(writes).toHaveLength(1);
});

test("el rechazo de fuente del backend se muestra sin aprobación de respaldo", async ({ page }) => {
  const partialFinding = {
    ...findingDetail,
    suggested_code: backendSuggestion,
    improvement_summary: backendExplanation,
    code_excerpt: { ...findingDetail.code_excerpt, evidence_scope: "snippet", is_partial: true },
  };
  const writes: Array<{ body: Record<string, unknown>; csrf: string | undefined; key: string | undefined }> = [];
  await installApi(page, { authenticated: true, capabilities: [capability] });
  await page.route("**/api/v1/runs/run-current/findings/finding-candidate**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({ json: partialFinding });
    writes.push({ body: request.postDataJSON(), csrf: request.headers()["x-csrf-token"], key: request.headers()["idempotency-key"] });
    await route.fulfill({ status: 409, json: { error: {
      code: "fuente_no_disponible",
      message: "No hay un fragmento original verificable para crear la rama.",
    } } });
  });

  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true }).click();

  await expect(dialog.getByRole("alert")).toContainText("No hay un fragmento original verificable para crear la rama.");
  await expect(dialog.locator(".quality-decision-result")).not.toContainText(/Aprobada|Rechazada|Cambios solicitados/);
  await expect(dialog.getByText(/simulado|Commit simulado/i)).toHaveCount(0);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ body: { decision: "approve" }, csrf: "csrf-fixture" });
  expect(writes[0].key).toBeTruthy();
  expect(await page.evaluate(() => Object.keys(localStorage).filter((key) => /quality|finding/i.test(key)))).toEqual([]);
});

test("la sesión sin capability conserva calidad en modo de consulta", async ({ page }) => {
  const detail = { ...findingDetail, suggested_code: backendSuggestion, improvement_summary: backendExplanation };
  const dialog = await openFinding(page, detail, { authenticated: true, capabilities: ["operator_session"] });
  await expect(dialog.locator(".quality-code-after code")).toHaveText(backendSuggestion);
  await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios|Editar código/i })).toHaveCount(0);
  await expect(dialog.getByText(/El servidor no concedió quality:decide/)).toBeVisible();
});

test("calidad mantiene el contraste semántico y el ancho móvil en ambos temas", async ({ page }, testInfo) => {
  const detail = { ...findingDetail, suggested_code: backendSuggestion, improvement_summary: backendExplanation };
  await openFinding(page, detail);
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      const dialog = page.getByRole("dialog");
      const bounds = await dialog.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await dialog.getByRole("heading", { name: "Código Actual" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`quality-${theme}-${width}.png`) });
    }
  }
});
