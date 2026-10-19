import { expect, test, type Page, type Route } from "@playwright/test";
import { assertNoBrowserNetworkAttempts, findingDetail, installApi, installBrowserNetworkGuard } from "./fixtures";

const capability = "quality:decide";
const backendSuggestion = "export function validateInput(value: string): string { return value.trim(); }";
const backendExplanation = "El backend propone normalizar el valor antes de usarlo.";
async function json(route: Route, value: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

async function openFinding(page: Page, detail: unknown = findingDetail, options: Parameters<typeof installApi>[1] = {}) {
  await installApi(page, options);
  await page.route("**/api/v1/runs/run-current/findings/finding-candidate", async (route) => {
    await json(route, detail);
  });
  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  return page.getByRole("dialog");
}

test("la vista de consulta no fabrica sugerencias ni decisiones locales", async ({ page }) => {
  const dialog = await openFinding(page);

  await expect(dialog.getByRole("heading", { name: "Código actual observado" })).toBeVisible();
  await expect(dialog.locator(".quality-code-before code")).toContainText("<script>window.__findingXss");
  await expect(dialog.getByText("El backend no informó un ejemplo de código ni una explicación.", { exact: true })).toBeVisible();
  await expect(dialog.locator(".quality-code-after code")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /Aprobar|Rechazar|Solicitar cambios|Editar código/i })).toHaveCount(0);
  await expect(dialog.getByLabel("Rama separada de verificación")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__findingXss)).toBeUndefined();
  expect(await page.evaluate(() => Object.keys(localStorage).filter((key) => /quality|finding/i.test(key)))).toEqual([]);
});

test("un estado de decisión ausente se presenta como desconocido y no habilita acciones", async ({ page }) => {
  const detail = { ...findingDetail, verification_status: "confirmed", suggested_code: backendSuggestion, improvement_summary: backendExplanation };
  const dialog = await openFinding(page, detail, { authenticated: true, capabilities: [capability] });

  await expect(dialog.getByText("El estado de decisión es desconocido; no se ofrecen decisiones protegidas.", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Rechazar sugerencia", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Solicitar cambios", exact: true })).toHaveCount(0);
});

test("un estado de decisión explícitamente desconocido no habilita acciones protegidas", async ({ page }) => {
  const detail = { ...findingDetail, verification_status: "confirmed", decision_status: "unknown", suggested_code: backendSuggestion, improvement_summary: backendExplanation };
  const dialog = await openFinding(page, detail, { authenticated: true, capabilities: [capability] });

  await expect(dialog.getByText("El estado de decisión es desconocido; no se ofrecen decisiones protegidas.", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Rechazar sugerencia", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Solicitar cambios", exact: true })).toHaveCount(0);
});

for (const scenario of [
  { verification: "confirmed", decision: "request_changes" as const, label: "Solicitar cambios", formLabel: "Cambios solicitados", reason: "Solicitar una validación explícita del valor." },
  { verification: "insufficient", decision: "reject" as const, label: "Rechazar sugerencia", formLabel: "Motivo del rechazo", reason: "No existe evidencia suficiente para este parche." },
] as const) {
  test(`una verificación ${scenario.verification} no desbloquea aprobación; ${scenario.label.toLowerCase()} sigue disponible`, async ({ page }) => {
    const current = {
      ...findingDetail,
      verification_status: scenario.verification,
      suggested_code: backendSuggestion,
      improvement_summary: backendExplanation,
      decision_status: "pending",
    };
    const writes: Array<{ path: string; body: Record<string, unknown>; csrf: string | undefined; key: string | undefined; origin: string | undefined }> = [];
    await installApi(page, { authenticated: true, capabilities: [capability] });
    await page.route("**/api/v1/runs/run-current/findings/finding-candidate**", async (route) => {
      const request = route.request();
      if (request.method() === "GET") return json(route, current);
      const body = request.postDataJSON() as Record<string, unknown>;
      if (body.decision === "approve") {
        await json(route, { error: { code: "revision_independiente_requerida", message: "Aprobación bloqueada: falta revisión independiente del parche exacto." } }, 409);
        return;
      }
      writes.push({
        path: new URL(request.url()).pathname,
        body,
        csrf: request.headers()["x-csrf-token"],
        key: request.headers()["idempotency-key"],
        origin: request.headers().origin,
      });
      await json(route, {
        decision_status: scenario.decision === "reject" ? "rejected" : "changes_requested",
        suggested_code: backendSuggestion,
        improvement_summary: backendExplanation,
        branch_name: null,
        branch_url: null,
        commit_sha_applied: null,
        execution_mode: "sandbox",
      });
    });

    await page.goto("/quality");
    await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(".quality-code-after code")).toHaveText(backendSuggestion);
    const approve = dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true });
    await expect(approve).toBeDisabled();
    await expect(dialog.getByText("Aprobación bloqueada: falta revisión independiente del parche exacto.", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel("Rama separada de verificación")).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: /Editar código/i })).toHaveCount(0);
    await expect(dialog.getByText(/la verificación del defecto.*no es una revisión independiente/i)).toBeVisible();

    await dialog.getByRole("button", { name: scenario.label, exact: true }).click();
    await dialog.getByLabel(scenario.formLabel).fill(scenario.reason);
    await dialog.getByRole("button", { name: scenario.decision === "reject" ? "Confirmar rechazo" : "Enviar solicitud de cambios", exact: true }).click();

    await expect(dialog.locator(".quality-decision-result")).toContainText(scenario.decision === "reject" ? "Rechazada" : "Cambios solicitados");
    await expect(dialog.locator(".quality-decision-result")).not.toContainText("Local");
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      path: "/api/v1/runs/run-current/findings/finding-candidate/decision",
      body: { decision: scenario.decision, reason: scenario.reason },
      csrf: "csrf-fixture",
      origin: new URL(page.url()).origin,
    });
    expect(writes[0].key).toBeTruthy();
    expect(writes[0].body).not.toHaveProperty("target_branch");
    expect(writes[0].body).not.toHaveProperty("suggested_code_override");
    expect(writes[0].body.decision).not.toBe("approve");
  });
}

test("una evidencia parcial no habilita rama ni aprobación, pero deja consultar la sugerencia", async ({ page }) => {
  const partialFinding = {
    ...findingDetail,
    suggested_code: backendSuggestion,
    improvement_summary: backendExplanation,
    decision_status: "pending",
    code_excerpt: { ...findingDetail.code_excerpt, evidence_scope: "snippet", is_partial: true },
  };
  const dialog = await openFinding(page, partialFinding, { authenticated: true, capabilities: [capability] });
  const approve = dialog.getByRole("button", { name: "Aprobar y crear rama", exact: true });

  await expect(approve).toBeDisabled();
  await expect(dialog.locator(".quality-code-after code")).toHaveText(backendSuggestion);
  await expect(dialog.getByText("Aprobación bloqueada: falta revisión independiente del parche exacto.", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Rama separada de verificación")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Rechazar sugerencia", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Solicitar cambios", exact: true })).toBeVisible();
  await expect(dialog.getByText("El fragmento mostrado es parcial.", { exact: true })).toBeVisible();
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
      await dialog.getByRole("heading", { name: "Código actual observado" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`quality-${theme}-${width}.png`) });
    }
  }
});
