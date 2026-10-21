import { test, expect } from "@playwright/test";

test("real React API SQLite comment sandbox: edit, approve, receipt and reload", async ({ page }) => {
  const outside: string[] = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
      outside.push(url.origin); await route.abort(); return;
    }
    await route.continue();
  });
  const login = await page.request.post("/api/v1/auth/login", {
    headers: { Origin: "http://127.0.0.1:4173" }, data: { token: "comment-sandbox-operator-token-long-enough" },
  });
  expect(login.ok()).toBeTruthy();
  const proposals = await page.request.get("/api/v1/projects/HORMIGESTION/proposals");
  const item = (await proposals.json()).items[0];
  expect(item.field).toBe("comment");
  await page.goto(`/proposals?proposal_id=${item.proposal_id}`);
  await expect(page.getByRole("heading", { name: "Comentario de seguimiento" }).first()).toBeVisible();
  await page.getByText("Ver evidencia y datos técnicos de la propuesta", { exact: true }).click();
  await expect(page.getByText("Confirmación humana:", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Modificar el campo recomendado", exact: true }).click();
  const modified = item.proposed_value + "\nAdjuntar resultados de las pruebas de los criterios existentes.";
  await page.getByRole("textbox", { name: "Comentario de seguimiento", exact: true }).fill(modified);
  await page.getByLabel("Motivo de la decisión", { exact: true }).fill("Revisión humana simulada del texto efectivo");
  await page.getByRole("button", { name: /Aprobar y ejecutar/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Modificar y ejecutar", exact: true }).click();
  await expect(page.getByText("Resultado sandbox registrado", { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Resultado local confirmado; no acredita cambios en Trello live.")).toBeVisible();
  await page.getByText("Ver decisiones, relaciones y datos de auditoría disponibles", { exact: true }).click();
  await expect(page.getByText("sandbox-comment-1", { exact: true })).toBeVisible();
  const detail = await page.request.get(`/api/v1/projects/HORMIGESTION/proposals/${item.proposal_id}`);
  const data = await detail.json();
  expect(data.latest_receipt.execution_mode).toBe("sandbox");
  expect(data.latest_receipt.verification_status).toBe("verified");
  expect(data.decision_history.some((event: { decision: string }) => event.decision === "modify_and_execute")).toBeTruthy();
  expect(outside).toEqual([]);
  await page.screenshot({ path: process.env.COMMENT_SANDBOX_ROOT + "/comment-receipt.png", fullPage: true });
});
