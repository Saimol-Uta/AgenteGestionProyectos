import { expect, test } from "@playwright/test";
import { installApi } from "./fixtures";

test("Phase 1: dashboard conserva diez KPI y presenta el centro y los tres agentes", async ({ page }) => {
  await installApi(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Centro de Decisiones del Gestor (Requieren tu Autorización)" })).toBeVisible();
  await expect(page.locator(".metric-grid .metric-card")).toHaveCount(10);
  const workflow = page.getByRole("region", { name: "Flujo de agentes" });
  await expect(workflow.getByText("Agente Ejecutor", { exact: true })).toBeVisible();
  await expect(workflow.getByText("Agente Revisor", { exact: true })).toBeVisible();
  await expect(workflow.getByText("Agente Aprobador", { exact: true })).toBeVisible();
  await expect(workflow.getByRole("button", { name: "Sincronizar y Analizar Proyecto" })).toBeDisabled();
});

test("Phase 1: acción rápida exige consentimiento remoto y no confirma al preparar", async ({ page }) => {
  const backend = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare", "analysis:confirm"] });
  await page.goto("/dashboard");
  const workflow = page.getByRole("region", { name: "Flujo de agentes" });
  await workflow.getByRole("button", { name: "Sincronizar y Analizar Proyecto" }).click();
  await expect(workflow.getByLabel("Autorizo el análisis con proveedores remotos")).toBeVisible();
  expect(backend.analysisWrites).toHaveLength(1);
  await expect(workflow.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  await workflow.getByLabel("Autorizo el análisis con proveedores remotos").check();
  await workflow.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect.poll(() => backend.analysisWrites.length).toBe(2);
  expect(JSON.parse(backend.analysisWrites[1].body!)).toMatchObject({ confirm_analysis: true, remote_consent: true });
  expect(backend.analysisWrites[1].csrfToken).toBeTruthy();
  expect(backend.analysisWrites[1].idempotencyKey).toBeTruthy();
});

test("Phase 1: capacidad de preparar no autoriza confirmar", async ({ page }) => {
  const backend = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.goto("/dashboard");
  const workflow = page.getByRole("region", { name: "Flujo de agentes" });
  await workflow.getByRole("button", { name: "Sincronizar y Analizar Proyecto" }).click();
  await expect(workflow.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  expect(backend.analysisWrites).toHaveLength(1);
});

test("Phase 1: contraste visual y ancho responsive de barra y decisiones", async ({ page }) => {
  await installApi(page);
  await page.goto("/dashboard");
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(value => document.documentElement.setAttribute("data-theme", value), theme);
      await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `/tmp/phase1-dashboard-${theme}-${width}.png`, fullPage: true });
    }
  }
});
