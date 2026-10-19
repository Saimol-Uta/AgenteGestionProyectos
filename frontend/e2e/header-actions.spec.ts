import { expect, test } from "@playwright/test";
import { analysisJob, analysisPreparation, installApi, managerCredentials, project, secondProject } from "./fixtures";

const syncTitle = "Sincronizar y analizar proyecto";
const emailTitle = "Enviar correo al gestor";

// The browser may reach only the locally hosted Vite app; installApi mocks its API.
test.beforeEach(async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === "http://127.0.0.1:4173") return route.continue();
    return route.abort();
  });
});

test("header actions replace the repeated workflow card and remain reachable on desktop and mobile", async ({ page }, testInfo) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm", "notifications:email_send", "audit:read"],
    projectsPayload: { items: [project, secondProject], total: 2, page: 1, page_size: 100 },
  });

  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    for (const path of ["/", "/reports", "/analysis"]) {
      await page.goto(path);
      const header = page.getByRole("banner");
      await expect(header.getByRole("button", { name: "Sincronizar proyecto" })).toBeVisible();
      await expect(header.getByRole("button", { name: "Enviar correo" })).toBeVisible();
      await expect(header.getByRole("button", { name: /Cambiar a modo/ })).toBeVisible();
      await expect(header.getByRole("button", { name: "Cerrar sesión" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await expect(page.getByRole("region", { name: "Flujo de agentes" })).toHaveCount(0);
      await expect(page.getByText("ASISTENTE DE SEGUIMIENTO Y CONTROL", { exact: true })).toHaveCount(0);

      if (width === 390) {
        await expect(header.getByRole("button", { name: "Abrir navegación" })).toBeVisible();
        await expect(header.getByRole("button", { name: "Abrir búsqueda global" })).toBeVisible();
        await header.getByRole("button", { name: "Abrir navegación" }).click();
        const navigation = page.getByRole("dialog", { name: "Navegación principal" });
        await expect(navigation.getByLabel("Proyecto activo")).toBeVisible();
        await navigation.getByRole("button", { name: "Cerrar navegación" }).click();
      } else {
        await expect(header.getByRole("search")).toBeVisible();
        await expect(header.getByLabel("Proyecto activo")).toBeVisible();
      }

      if (path === "/analysis") {
        await expect(page.getByRole("heading", { name: "Centro de análisis de IA" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toHaveCount(0);
        await expect(page.getByRole("heading", { name: "Resumen consolidado del proyecto" })).toHaveCount(0);
        await expect(page.getByRole("heading", { name: "Preparar un análisis" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Historial de análisis" })).toBeVisible();
        await expect(page.getByRole("link", { name: "Abrir ejecución run-current" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Trabajos recientes" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Sin trabajos asíncronos" })).toBeVisible();
      }

      if (path === "/" || path === "/analysis") {
        for (const theme of ["light", "dark"] as const) {
          await page.evaluate((value) => document.documentElement.setAttribute("data-theme", value), theme);
          const view = path === "/analysis" ? "analysis" : "dashboard";
          await page.screenshot({
            path: testInfo.outputPath(`page-${view}-${theme}-${width}.png`),
            fullPage: true,
            animations: "disabled",
          });

          if (path === "/analysis") {
            const layout = await page.locator(".analysis-page").evaluate((root) => {
              const bounds = (element: HTMLElement) => {
                const rect = element.getBoundingClientRect();
                return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
              };
              const describe = (element: HTMLElement | null) => {
                if (!element) return null;
                const computed = window.getComputedStyle(element);
                return {
                  text: (element.innerText ?? "").replace(/\s+/g, " ").trim().slice(0, 240),
                  bounds: bounds(element),
                  computed: {
                    display: computed.display,
                    position: computed.position,
                    zIndex: computed.zIndex,
                    opacity: computed.opacity,
                    backgroundColor: computed.backgroundColor,
                    color: computed.color,
                    marginBlockStart: computed.marginBlockStart,
                    marginBlockEnd: computed.marginBlockEnd,
                    paddingBlockStart: computed.paddingBlockStart,
                    paddingBlockEnd: computed.paddingBlockEnd,
                    lineHeight: computed.lineHeight,
                    overflow: computed.overflow,
                    transform: computed.transform,
                  },
                };
              };
              const intersects = (first: ReturnType<typeof bounds>, second: ReturnType<typeof bounds>) =>
                first.left < second.right - 1 && first.right > second.left + 1
                && first.top < second.bottom - 1 && first.bottom > second.top + 1;
              const headingElements = Array.from(root.querySelectorAll<HTMLElement>("h1, h2, h3, h4"))
                .filter((element) => element.getClientRects().length > 0);
              const headingOverlaps: string[] = [];
              for (let first = 0; first < headingElements.length; first += 1) {
                for (let second = first + 1; second < headingElements.length; second += 1) {
                  if (intersects(bounds(headingElements[first]), bounds(headingElements[second]))) {
                    headingOverlaps.push(`${headingElements[first].innerText} <> ${headingElements[second].innerText}`);
                  }
                }
              }
              const sectionTitleBodyOverlaps: string[] = [];
              const sectionHeadingBodyOverlaps: string[] = [];
              const sectionTitleIntroOverlaps: string[] = [];
              const panels = Array.from(root.querySelectorAll<HTMLElement>(":scope > .panel"));
              const sections = panels.map((panel) => {
                const heading = panel.querySelector<HTMLElement>(":scope > .section-heading");
                const title = heading?.querySelector<HTMLElement>("h2, h3, h4") ?? null;
                const body = Array.from(panel.children).find((child) => child !== heading) as HTMLElement | undefined;
                if (title && body && intersects(bounds(title), bounds(body))) sectionTitleBodyOverlaps.push(`${title.innerText} <> ${body.innerText}`);
                if (heading && body && bounds(body).top < bounds(heading).bottom - 1) sectionHeadingBodyOverlaps.push(`${title?.innerText ?? panel.className} <> ${body.innerText}`);
                const titleDescription = heading?.querySelector<HTMLElement>(".section-subtitle") ?? null;
                if (title && titleDescription && intersects(bounds(title), bounds(titleDescription))) sectionTitleIntroOverlaps.push(`${title.innerText} <> ${titleDescription.innerText}`);
                return {
                  label: title?.innerText ?? panel.className,
                  panel: describe(panel),
                  heading: describe(heading ?? null),
                  title: describe(title),
                  firstBodyChild: describe(body ?? null),
                  headingToBodyGap: heading && body ? bounds(body).top - bounds(heading).bottom : null,
                };
              });
              const panelOverlaps: string[] = [];
              const panelGaps = panels.slice(0, -1).map((panel, index) => ({
                before: panel.className,
                after: panels[index + 1].className,
                gap: bounds(panels[index + 1]).top - bounds(panel).bottom,
              }));
              for (let first = 0; first < panels.length; first += 1) {
                for (let second = first + 1; second < panels.length; second += 1) {
                  if (intersects(bounds(panels[first]), bounds(panels[second]))) panelOverlaps.push(`${panels[first].className} <> ${panels[second].className}`);
                }
              }
              const pageTitle = root.querySelector<HTMLElement>(".page-heading-row h1");
              const pageDescription = root.querySelector<HTMLElement>(".page-heading-row .page-subtitle");
              const pageTitleDescriptionOverlap = pageTitle && pageDescription && intersects(bounds(pageTitle), bounds(pageDescription));
              const layers = [
                { name: "topbar", element: document.querySelector<HTMLElement>(".topbar") },
                { name: "sidebar", element: document.querySelector<HTMLElement>(".desktop-sidebar") },
                { name: "analysis-page", element: root as HTMLElement },
                { name: "page-heading", element: root.querySelector<HTMLElement>(".page-heading-row") },
                { name: "preparation-panel", element: root.querySelector<HTMLElement>(".analysis-preparation-panel") },
                { name: "run-history-panel", element: root.querySelector<HTMLElement>(".run-history-section") },
                { name: "job-history-panel", element: root.querySelector<HTMLElement>(".job-history-section") },
              ].map(({ name, element }) => ({ name, details: describe(element) }));
              return {
                viewport: { width: window.innerWidth, height: window.innerHeight, documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight },
                pageTitle: describe(pageTitle),
                pageDescription: describe(pageDescription),
                pageTitleDescriptionGap: pageTitle && pageDescription ? bounds(pageDescription).top - bounds(pageTitle).bottom : null,
                sections,
                headings: headingElements.map((element) => describe(element)),
                panelGaps,
                overlaps: { headings: headingOverlaps, sectionTitleBody: sectionTitleBodyOverlaps, sectionHeadingBody: sectionHeadingBodyOverlaps, sectionTitleIntro: sectionTitleIntroOverlaps, panels: panelOverlaps, pageTitleDescription: Boolean(pageTitleDescriptionOverlap) },
                layers,
                openDialogOrOverlayCount: document.querySelectorAll(".dialog-overlay, .header-action-dialog").length,
              };
            });
            await testInfo.attach(`analysis-layout-${theme}-${width}.json`, {
              body: JSON.stringify(layout, null, 2),
              contentType: "application/json",
            });
            expect(layout.viewport.documentWidth).toBeLessThanOrEqual(width);
            expect(layout.overlaps.headings).toEqual([]);
            expect(layout.overlaps.sectionTitleBody).toEqual([]);
            expect(layout.overlaps.sectionHeadingBody).toEqual([]);
            expect(layout.overlaps.sectionTitleIntro).toEqual([]);
            expect(layout.overlaps.panels).toEqual([]);
            expect(layout.panelGaps.every((item) => item.gap >= 0)).toBe(true);
            expect(layout.overlaps.pageTitleDescription).toBe(false);
            expect(layout.openDialogOrOverlayCount).toBe(0);
          } else {
            for (const action of ["sync", "email"] as const) {
              const label = action === "sync" ? "Sincronizar proyecto" : "Enviar correo";
              const title = action === "sync" ? syncTitle : emailTitle;
              await header.getByRole("button", { name: label }).click();
              await expect(page.getByRole("dialog", { name: title })).toBeVisible();
              await page.screenshot({
                path: testInfo.outputPath(`header-${theme}-${width}-${action}.png`),
                animations: "disabled",
              });
              await page.keyboard.press("Escape");
            }
          }
        }
      }
      if (path === "/analysis") {
        await page.getByRole("link", { name: "Abrir ejecución run-current", exact: true }).click();
        await expect(page).toHaveURL(/\/analysis\/run-current$/);
        await expect(page.getByRole("heading", { name: "Repositorios del proyecto" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Resumen consolidado del proyecto" })).toBeVisible();
      }
    }
  }
});

test("opening synchronization is read-only until preparation and remote-consent confirmation", async ({ page }) => {
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
  });
  await page.goto("/");

  const header = page.getByRole("banner");
  const trigger = header.getByRole("button", { name: "Sincronizar proyecto" });
  await trigger.click();
  let dialog = page.getByRole("dialog", { name: syncTitle });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Preparar análisis" })).toBeVisible();
  expect(api.analysisWrites).toHaveLength(0);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  dialog = page.getByRole("dialog", { name: syncTitle });
  await expect(dialog.getByRole("button", { name: "Preparar análisis" })).toBeVisible();
  expect(api.analysisWrites).toHaveLength(0);

  await dialog.getByRole("button", { name: "Preparar análisis" }).click();
  await expect.poll(() => api.analysisWrites.length).toBe(1);
  expect(api.analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
  expect(api.analysisWrites[0].csrfToken).toBe("csrf-fixture");
  const consent = dialog.getByRole("checkbox", { name: /uso de servicios en la nube/i });
  await expect(consent).not.toBeChecked();
  const confirm = dialog.getByRole("button", { name: "Confirmar análisis" });
  await expect(confirm).toBeDisabled();
  await consent.check();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => api.analysisWrites.length).toBe(2);
  expect(api.analysisWrites[1].path).toBe("/api/v1/projects/hormigestio/analysis/confirm");
  expect(JSON.parse(api.analysisWrites[1].body ?? "{}")).toMatchObject({
    preparation_id: "preparation-fixture-12345678",
    configuration_fingerprint: "a".repeat(64),
    confirm_analysis: true,
    remote_consent: true,
  });
  expect(api.analysisWrites[1].csrfToken).toBe("csrf-fixture");
  expect(api.analysisWrites[1].idempotencyKey).toBeTruthy();
});

test("running analysis remains inspectable from the header while polling continues with the dialog closed", async ({ page }) => {
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
  });
  let jobStatus: "running" | "completed" = "running";
  let jobPollRequests = 0;
  let completedPollObserved = false;
  await page.route("**/api/v1/analysis/jobs/job-current", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    jobPollRequests += 1;
    const status = jobStatus;
    await route.fulfill({ json: {
      ...analysisJob,
      status,
      updated_at: status === "running" ? "2026-01-12T10:11:00-05:00" : "2026-01-12T10:12:00-05:00",
      run_id: status === "completed" ? "run-current" : null,
      error_code: null,
    } });
    if (status === "completed") completedPollObserved = true;
  });
  await page.goto("/");
  const header = page.getByRole("banner");
  const trigger = header.getByRole("button", { name: "Sincronizar proyecto" });

  await trigger.click();
  let dialog = page.getByRole("dialog", { name: syncTitle });
  await dialog.getByRole("button", { name: "Preparar análisis" }).click();
  const consent = dialog.getByRole("checkbox", { name: /uso de servicios en la nube/i });
  await consent.check();
  await dialog.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect.poll(() => jobPollRequests, { timeout: 10_000 }).toBeGreaterThan(0);
  expect(api.analysisWrites.map((write) => write.path)).toEqual([
    "/api/v1/projects/hormigestio/analysis/prepare",
    "/api/v1/projects/hormigestio/analysis/confirm",
  ]);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeEnabled();
  await trigger.click();
  dialog = page.getByRole("dialog", { name: syncTitle });
  await expect(dialog.getByText(/Estado del análisis: En curso/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Análisis en curso…", exact: true })).toBeDisabled();
  expect(api.analysisWrites).toHaveLength(2);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  jobStatus = "completed";
  await expect.poll(() => jobPollRequests, { timeout: 10_000 }).toBeGreaterThan(1);
  await expect.poll(() => completedPollObserved, { timeout: 10_000 }).toBe(true);
  await expect(dialog).toHaveCount(0);
  await trigger.click();
  dialog = page.getByRole("dialog", { name: syncTitle });
  await expect(dialog.getByText(/Estado del análisis: Completado/)).toBeVisible({ timeout: 10_000 });
  await expect(dialog.getByRole("button", { name: "Preparar análisis" })).toBeEnabled();
  expect(api.analysisWrites).toHaveLength(2);
});

test("email modal opening and recipient confirmation do not send; reopening during a send never duplicates it", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["notifications:email_send"],
  });
  const deliveries: Array<{ recipient: string; csrf: string | undefined; key: string | undefined }> = [];
  let releaseDelivery!: () => void;
  let requestStarted: (() => void) | null = null;
  let responseFinished: (() => void) | null = null;
  const delayedDelivery = new Promise<void>((resolve) => { releaseDelivery = resolve; });
  const started = new Promise<void>((resolve) => { requestStarted = resolve; });
  const finished = new Promise<void>((resolve) => { responseFinished = resolve; });
  await page.route("**/api/v1/projects/hormigestio/notifications/email", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const body = route.request().postDataJSON() as { recipient_email: string };
    deliveries.push({
      recipient: body.recipient_email,
      csrf: route.request().headers()["x-csrf-token"],
      key: route.request().headers()["idempotency-key"],
    });
    requestStarted?.();
    await delayedDelivery;
    await route.fulfill({ json: {
      delivery_id: "delivery-header-action-001",
      delivery_channel: "local_outbox_simulated_email",
      recipient_email: body.recipient_email,
      html_preview_url: null,
      status: "DELIVERED",
    } });
    responseFinished?.();
  });
  await page.goto("/");

  const header = page.getByRole("banner");
  const trigger = header.getByRole("button", { name: "Enviar correo" });
  await trigger.click();
  let dialog = page.getByRole("dialog", { name: emailTitle });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Correo del gestor")).toHaveValue(managerCredentials.email);
  expect(deliveries).toHaveLength(0);

  await dialog.getByRole("button", { name: "Enviar correo", exact: true }).click();
  await expect(dialog.getByText(`Confirma el envío a ${managerCredentials.email}`, { exact: true })).toBeVisible();
  expect(deliveries).toHaveLength(0);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await trigger.click();
  dialog = page.getByRole("dialog", { name: emailTitle });
  await expect(dialog.getByText(`Confirma el envío a ${managerCredentials.email}`, { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Enviar correo", exact: true }).click();
  await dialog.getByRole("button", { name: `Confirmar envío a ${managerCredentials.email}`, exact: true }).click();
  await started;
  expect(deliveries).toEqual([{ recipient: managerCredentials.email, csrf: "csrf-fixture", key: expect.any(String) }]);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await trigger.click();
  dialog = page.getByRole("dialog", { name: emailTitle });
  const sendAgain = dialog.getByRole("button", { name: "Enviar correo", exact: true });
  await expect(sendAgain).toBeDisabled();
  releaseDelivery();
  await finished;
  await expect(sendAgain).toBeEnabled();
  expect(deliveries).toHaveLength(1);
});

test("a late preparation cannot populate another project, and session changes discard email confirmation state", async ({ page }) => {
  await installApi(page, {
    capabilities: ["analysis:prepare", "analysis:confirm", "notifications:email_send"],
    projectsPayload: { items: [project, secondProject], total: 2, page: 1, page_size: 100 },
  });
  let releasePreparation!: () => void;
  let requestStarted: (() => void) | null = null;
  let responseFinished: (() => void) | null = null;
  const delayedPreparation = new Promise<void>((resolve) => { releasePreparation = resolve; });
  const started = new Promise<void>((resolve) => { requestStarted = resolve; });
  const finished = new Promise<void>((resolve) => { responseFinished = resolve; });
  let prepareCalls = 0;
  await page.route("**/api/v1/projects/hormigestio/analysis/prepare", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    prepareCalls += 1;
    requestStarted?.();
    await delayedPreparation;
    await route.fulfill({ json: analysisPreparation });
    responseFinished?.();
  });
  await page.goto("/");

  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await page.getByLabel("Correo electrónico").fill(managerCredentials.email);
  await page.getByLabel("Contraseña").fill(managerCredentials.password);
  await page.getByRole("button", { name: "Iniciar Sesión como Gestor" }).click();
  const header = page.getByRole("banner");
  await expect(header.getByRole("button", { name: "Cerrar sesión" })).toBeVisible();
  await header.getByRole("button", { name: "Sincronizar proyecto" }).click();
  let syncDialog = page.getByRole("dialog", { name: syncTitle });
  await syncDialog.getByRole("button", { name: "Preparar análisis" }).click();
  await started;
  await page.keyboard.press("Escape");
  await expect(syncDialog).toHaveCount(0);
  await page.getByLabel("Proyecto activo").first().selectOption(secondProject.id);

  await header.getByRole("button", { name: "Sincronizar proyecto" }).click();
  syncDialog = page.getByRole("dialog", { name: syncTitle });
  await expect(syncDialog.getByRole("button", { name: "Preparar análisis" })).toBeVisible();
  releasePreparation();
  await finished;
  await expect(syncDialog.getByRole("button", { name: "Preparar análisis" })).toBeVisible();
  await expect(syncDialog.getByRole("checkbox", { name: /uso de servicios en la nube/i })).toHaveCount(0);
  expect(prepareCalls).toBe(1);

  await page.keyboard.press("Escape");
  await header.getByRole("button", { name: "Enviar correo" }).click();
  const emailDialog = page.getByRole("dialog", { name: emailTitle });
  await expect(emailDialog.getByLabel("Correo del gestor")).toHaveValue(managerCredentials.email);
  await emailDialog.getByRole("button", { name: "Enviar correo", exact: true }).click();
  await expect(emailDialog.getByText(`Confirma el envío a ${managerCredentials.email}`, { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(emailDialog).toHaveCount(0);

  await header.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(header.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
  await header.getByRole("button", { name: "Enviar correo" }).click();
  const guestEmailDialog = page.getByRole("dialog", { name: emailTitle });
  await expect(guestEmailDialog.getByLabel("Correo del gestor")).toHaveValue("");
  await expect(guestEmailDialog.getByText(/Confirma el envío a/)).toHaveCount(0);
});
