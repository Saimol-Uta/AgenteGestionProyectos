import { expect, test, type Page } from "@playwright/test";
import { assertNoBrowserNetworkAttempts, installApi, installBrowserNetworkGuard, proposalSummary } from "./fixtures";

type ProposalCollectionOverrides = {
  availability?: "available" | "unknown";
  total?: number;
  pending_actionable_count?: number | null;
  omitPendingActionableCount?: boolean;
};

async function openDashboard(page: Page, overrides: ProposalCollectionOverrides = {}) {
  await installApi(page);
  await page.route("**/api/v1/projects/hormigestio/proposals**", async (route) => {
    const url = new URL(route.request().url());
    const { omitPendingActionableCount, ...collectionOverrides } = overrides;
    const collection: Record<string, unknown> = {
      availability: "available",
      items: [proposalSummary],
      total: 25,
      page: Number(url.searchParams.get("page") ?? 1),
      page_size: Number(url.searchParams.get("page_size") ?? 1),
      ...collectionOverrides,
    };
    if (omitPendingActionableCount) delete collection.pending_actionable_count;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(collection) });
  });
  await page.goto("/dashboard");
}

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

test("el Dashboard muestra las propuestas accionables pendientes sin alterar Quality ni los informes", async ({ page }) => {
  await openDashboard(page, { pending_actionable_count: 2 });

  const decisions = page.locator(".manager-decision-grid");
  const proposalCard = decisions.locator(".decision-trello");
  const qualityCard = decisions.locator(".decision-quality");
  const reportCard = decisions.locator(".decision-report");

  await expect(qualityCard.locator(".decision-count")).toHaveText("2");
  await expect(qualityCard).toContainText("1 confirmados por el Revisor");
  await expect(reportCard.locator(".decision-report-state")).toHaveText("Informe disponible");
  await expect(reportCard).toContainText("Decisión: pendiente de revisión.");

  await expect(proposalCard.locator(".decision-count")).toHaveText("2");
  await expect(proposalCard).toContainText("2 propuestas están pendientes de tu decisión.");
  await expect(page.locator('a[href="/proposals"] .nav-decision-badge')).toHaveText("2");
  await expect(page.locator('a[href="/quality"] .nav-decision-badge')).toHaveText("2");

  await proposalCard.getByRole("link", { name: "Revisar propuestas" }).click();
  await expect(page).toHaveURL(/\/proposals$/);
});

test("un conteo conocido de cero indica que no hay propuestas que requieran decisión", async ({ page }) => {
  await openDashboard(page, { pending_actionable_count: 0 });

  const proposalCard = page.locator(".decision-trello");
  await expect(proposalCard.locator(".decision-count")).toHaveText("0");
  await expect(proposalCard).toContainText("No hay propuestas pendientes que requieran tu decisión.");
});

test("un conteo pending_actionable_count nulo permanece no disponible", async ({ page }) => {
  await openDashboard(page, { pending_actionable_count: null });

  const proposalCard = page.locator(".decision-trello");
  await expect(proposalCard.locator(".decision-count")).toHaveText("—");
  await expect(proposalCard).toContainText("No se pudo confirmar cuántas propuestas esperan tu decisión.");
});

test("un conteo pending_actionable_count ausente permanece no disponible", async ({ page }) => {
  await openDashboard(page, { omitPendingActionableCount: true });

  const proposalCard = page.locator(".decision-trello");
  await expect(proposalCard.locator(".decision-count")).toHaveText("—");
  await expect(proposalCard).toContainText("No se pudo confirmar cuántas propuestas esperan tu decisión.");
});

test("una colección de propuestas no disponible no se interpreta como cero", async ({ page }) => {
  await openDashboard(page, { availability: "unknown", total: 0, pending_actionable_count: null });

  const proposalCard = page.locator(".decision-trello");
  await expect(proposalCard.locator(".decision-count")).toHaveText("—");
  await expect(proposalCard).toContainText("No se pudo confirmar cuántas propuestas esperan tu decisión.");
});
