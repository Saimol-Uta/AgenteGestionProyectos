import { expect, test } from "@playwright/test";
import { installApi, project, secondProject } from "./fixtures";

/**
 * Initial project selection: the manager's project opens by default when the
 * session may access it, every other authorized project stays selectable.
 */

const testingProject = { ...project, id: "h5-test", name: "H5-TEST" };
const preferredProject = { ...project, id: "hormigestio", name: "HORMIGESTION" };

test("el proyecto preferido se selecciona por defecto cuando la sesion lo permite", async ({ page }) => {
  await installApi(page, {
    projectsPayload: { items: [testingProject, preferredProject], total: 2, page: 1, page_size: 100 },
  });
  await page.goto("/tasks");

  const selector = page.locator(".topbar").getByLabel("Proyecto activo");
  await expect(selector).toHaveValue(preferredProject.id);
  await expect(page.getByText("SEGUIMIENTO · HORMIGESTION")).toBeVisible();
});

test("si el proyecto preferido no esta disponible se usa el primer proyecto autorizado", async ({ page }) => {
  const other = { ...project, id: "proyecto-otro", name: "Proyecto otro" };
  await installApi(page, {
    projectsPayload: { items: [other], total: 1, page: 1, page_size: 100 },
  });
  await page.goto("/tasks");

  const selector = page.locator(".topbar").getByLabel("Proyecto activo");
  await expect(selector).toHaveValue(other.id);
  await expect(page.getByText("SEGUIMIENTO · Proyecto otro")).toBeVisible();
});

test("el selector permite cambiar entre los proyectos autorizados", async ({ page }) => {
  await installApi(page, {
    projectsPayload: { items: [testingProject, preferredProject, secondProject], total: 3, page: 1, page_size: 100 },
  });
  await page.goto("/tasks");

  const selector = page.locator(".topbar").getByLabel("Proyecto activo");
  await expect(selector).toHaveValue(preferredProject.id);

  await selector.selectOption(testingProject.id);
  await expect(selector).toHaveValue(testingProject.id);
  await expect(page.getByText("SEGUIMIENTO · H5-TEST")).toBeVisible();

  await selector.selectOption(secondProject.id);
  await expect(selector).toHaveValue(secondProject.id);
  await expect(page.getByText("SEGUIMIENTO · Proyecto secundario")).toBeVisible();
});
