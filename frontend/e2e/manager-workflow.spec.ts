import { expect, test } from "@playwright/test";
import { installApi, managerCredentials } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === "http://127.0.0.1:4173") return route.continue();
    return route.abort();
  });
});

test("Phase 1: dashboard conserva diez KPI y muestra las dos acciones en el encabezado", async ({ page }) => {
  await installApi(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Centro de Decisiones del Gestor (Requieren tu Autorización)" })).toBeVisible();
  await expect(page.locator(".metric-grid .metric-card")).toHaveCount(10);
  const header = page.getByRole("banner");
  await expect(header.getByRole("button", { name: "Sincronizar proyecto" })).toBeVisible();
  await expect(header.getByRole("button", { name: "Enviar correo" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Flujo de agentes" })).toHaveCount(0);
});

test("Phase 1: acción rápida exige consentimiento remoto y no confirma al preparar", async ({ page }) => {
  const backend = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare", "analysis:confirm"] });
  await page.goto("/dashboard");
  const header = page.getByRole("banner");
  await header.getByRole("button", { name: "Sincronizar proyecto" }).click();
  const dialog = page.getByRole("dialog", { name: "Sincronizar y analizar proyecto" });
  expect(backend.analysisWrites).toHaveLength(0);
  await dialog.getByRole("button", { name: "Preparar análisis" }).click();
  await expect(dialog.getByLabel("Autorizo el uso de servicios en la nube")).toBeVisible();
  expect(backend.analysisWrites).toHaveLength(1);
  await expect(dialog.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  await dialog.getByLabel("Autorizo el uso de servicios en la nube").check();
  await dialog.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect.poll(() => backend.analysisWrites.length).toBe(2);
  expect(JSON.parse(backend.analysisWrites[1].body!)).toMatchObject({ confirm_analysis: true, remote_consent: true });
  expect(backend.analysisWrites[1].csrfToken).toBeTruthy();
  expect(backend.analysisWrites[1].idempotencyKey).toBeTruthy();
});

test("el correo verificado aparece como destinatario y cambiarlo no condiciona el análisis", async ({ page }) => {
  const backend = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
  });
  await page.goto("/dashboard");
  const header = page.getByRole("banner");
  await header.getByRole("button", { name: "Enviar correo" }).click();
  const emailDialog = page.getByRole("dialog", { name: "Enviar correo al gestor" });
  await expect(emailDialog.getByLabel("Correo del gestor")).toHaveValue(managerCredentials.email);
  await page.keyboard.press("Escape");

  await header.getByRole("button", { name: "Sincronizar proyecto" }).click();
  const syncDialog = page.getByRole("dialog", { name: "Sincronizar y analizar proyecto" });
  const prepare = syncDialog.getByRole("button", { name: "Preparar análisis" });
  await expect(prepare).toBeEnabled();
  await prepare.click();
  await expect.poll(() => backend.analysisWrites.length).toBe(1);
});

test("Phase 1: capacidad de preparar no autoriza confirmar", async ({ page }) => {
  const backend = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.goto("/dashboard");
  const header = page.getByRole("banner");
  await header.getByRole("button", { name: "Sincronizar proyecto" }).click();
  const dialog = page.getByRole("dialog", { name: "Sincronizar y analizar proyecto" });
  expect(backend.analysisWrites).toHaveLength(0);
  await dialog.getByRole("button", { name: "Preparar análisis" }).click();
  await expect(dialog.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  expect(backend.analysisWrites).toHaveLength(1);
});

test("Phase 1: contraste visual y ancho responsive de secciones en ambos temas", async ({ page }, testInfo) => {
  await installApi(page);
  await page.goto("/dashboard");
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(value => document.documentElement.setAttribute("data-theme", value), theme);
      await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`phase1-dashboard-${theme}-${width}.png`), fullPage: true });
    }
  }
});
