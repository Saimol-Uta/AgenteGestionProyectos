import { test, expect } from "@playwright/test";
import { installApi, outboxDetail, project, secondProject } from "./fixtures";

const panelName = "Configuración de Entrega por Correo Electrónico al Gestor";

test("recipient is validated, persisted across pages, and isolated by project", async ({ page }) => {
  await installApi(page);
  await page.route(/\/api\/v1\/projects(?:\?.*)?$/, route => route.fulfill({ json: { items: [project, secondProject], total: 2, page: 1, page_size: 100 } }));
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: panelName });
  await expect(panel).toBeVisible();
  const recipient = panel.getByLabel("Correo del gestor");
  await expect(recipient).toHaveValue("");
  await recipient.fill("invalid");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await expect(recipient).toHaveAttribute("aria-invalid", "true");
  await recipient.fill("gestor@example.org");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await expect(panel.getByText("Preferencia local guardada", { exact: true })).toBeVisible();
  await page.goto("/reports");
  await expect(page.getByLabel("Correo del gestor")).toHaveValue("gestor@example.org");
  await page.goto("/calendar");
  await expect(page.getByLabel("Correo del gestor")).toHaveValue("gestor@example.org");
  await page.getByLabel("Proyecto activo", { exact: true }).first().selectOption("proyecto-secundario");
  await expect(page.getByLabel("Correo del gestor")).toHaveValue("");
  await page.getByLabel("Proyecto activo", { exact: true }).first().selectOption("hormigestio");
  await expect(page.getByLabel("Correo del gestor")).toHaveValue("gestor@example.org");
});

test("manual notification produces an executive preview without any delivery request", async ({ page }) => {
  await installApi(page);
  const writes: string[] = [];
  page.on("request", request => { if (request.url().includes("/api/") && request.method() !== "GET") writes.push(request.url()); });
  await page.goto("/reports");
  const panel = page.getByRole("region", { name: panelName });
  const previewButton = panel.getByRole("button", { name: "Enviar Notificación por Correo al Gestor" });
  await expect(previewButton).toBeDisabled();
  await panel.getByLabel("Correo del gestor").fill("gestor@example.org");
  await panel.getByRole("button", { name: "Guardar correo local" }).click();
  await previewButton.click();
  const preview = page.getByRole("article", { name: "Vista previa de correo ejecutivo" });
  await expect(preview).toContainText("gestor@example.org");
  await expect(preview).toContainText("Agente IA de Gestión");
  await expect(preview).toContainText("Asunto");
  await expect(preview).toContainText("Avance del proyecto");
  await expect(preview).toContainText("Riesgos y retrasos");
  await expect(preview).toContainText("Acciones recomendadas");
  await expect(preview).toContainText("No enviado");
  await expect(panel).toContainText("Para enviar se requiere sesión local y notifications:email_send; la vista previa local no entrega correo.");
  expect(writes).toEqual([]);
});

test("outbox retains the real local receipt and downloads rather than claiming inbox delivery", async ({ page }) => {
  const api = await installApi(page, { authenticated: true });
  await page.goto("/calendar?delivery_id=delivery-001");
  const detail = page.getByRole("region", { name: "Detalle del recibo" });
  await expect(detail.getByRole("article", { name: "Correo ejecutivo del recibo" })).toBeVisible();
  await expect(detail).toContainText("DELIVERED");
  await expect(detail).toContainText("sqlite");
  await expect(detail).toContainText("no acredita entrega en una bandeja de correo");
  await expect(detail).toContainText("Destinatario no informado por el servidor");
  await detail.getByRole("button", { name: "Descargar recibo Markdown" }).click();
  await detail.getByRole("button", { name: "Descargar recibo JSON" }).click();
  await expect.poll(() => api.downloadRequests).toEqual(["outbox:markdown", "outbox:json"]);
});

test("stored receiver remains distinct from the local email preference and unsafe markdown stays inert", async ({ page }) => {
  await installApi(page, { authenticated: true });
  await page.route("**/schedule/outbox/delivery-001", route => route.fulfill({ json: { ...outboxDetail, message: { ...outboxDetail.message, recipient: "stored@example.org", body_markdown: "<script>window.__emailXss=true</script>\n\n![remote](https://example.org/tracker.png)" } } }));
  await page.goto("/calendar?delivery_id=delivery-001");
  await page.getByLabel("Correo del gestor").fill("local@example.org");
  await page.getByRole("button", { name: "Guardar correo local" }).click();
  const detail = page.getByRole("region", { name: "Detalle del recibo" });
  await expect(detail).toContainText("stored@example.org");
  await expect(detail).not.toContainText("local@example.org");
  await expect(detail.locator("img, script")).toHaveCount(0);
  expect(await page.evaluate(() => "__emailXss" in window)).toBe(false);
});

test("email preferences do not unlock protected backend configuration or weekly actions", async ({ page }) => {
  const api = await installApi(page, { authenticated: true, capabilities: [] });
  await page.goto("/settings");
  await expect(page.getByRole("region", { name: panelName })).toBeVisible();
  await expect(page.getByText("La configuración protegida requiere la capacidad configuration:read.")).toBeVisible();
  expect(api.moduleReads.filter(read => read.includes("/configuration"))).toEqual([]);
  await page.goto("/calendar");
  await expect(page.getByRole("button", { name: "Preparar ciclo manual" })).toHaveCount(0);
  expect(api.weeklyCycleRequests).toEqual([]);
});

for (const channel of ["file", "console"] as const) {
  test(`DELIVERED on ${channel} is not represented as email inbox delivery`, async ({ page }) => {
    await installApi(page, { authenticated: true });
    await page.route("**/schedule/outbox/delivery-001", route => route.fulfill({ json: { receipt: { ...outboxDetail.receipt, channel }, message: { ...outboxDetail.message, channel } } }));
    await page.goto("/calendar?delivery_id=delivery-001");
    const email = page.getByRole("article", { name: "Correo ejecutivo del recibo" });
    await expect(email).toContainText(`${channel} · DELIVERED`);
    await expect(email).toContainText("no acredita entrega en una bandeja de correo");
  });
}

test("email panels remain contained in both themes on mobile and desktop", async ({ page }, testInfo) => {
  await installApi(page, { authenticated: true });
  await page.goto("/settings");
  for (const theme of ["light", "dark"] as const) {
    await page.getByRole("radio", { name: theme === "light" ? "Claro Superficies luminosas" : "Oscuro Contraste de baja luz" }).check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      const panel = page.getByRole("region", { name: panelName });
      await panel.getByLabel("Correo del gestor").fill("gestor@example.org");
      await panel.getByRole("button", { name: "Guardar correo local" }).click();
      await panel.getByRole("button", { name: "Enviar Notificación por Correo al Gestor" }).click();
      const email = panel.getByRole("article", { name: "Vista previa de correo ejecutivo" });
      await expect(email).toBeVisible();
      expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      expect(await email.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await panel.screenshot({ path: testInfo.outputPath(`email-${theme}-${width}.png`) });
    }
  }
});
