#!/usr/bin/env node
/** Real-browser foundation checks. Requires the explicitly disposable fixture API, no interception. */
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.H5_WEB_URL ?? "http://127.0.0.1:5173";
const selectedURL = new URL(base);
if (!["127.0.0.1", "localhost"].includes(selectedURL.hostname)) {
  throw new Error("This check only accepts a loopback web server.");
}
const output = new URL("../.runtime/h5-browser/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    baseURL: base, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce",
  });
  const response = await context.request.get("/api/v1/projects/H5-TEST");
  if (response.status() !== 200) {
    throw new Error(`Fixture API unavailable (HTTP ${response.status()}). Start the API and verify /api/v1/system/health before this check.`);
  }
  if ((await response.json()).source_mode !== "fixtures") {
    throw new Error("Refusing to run fixture session checks against non-fixture data.");
  }
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
  await page.screenshot({ path: fileURLToPath(new URL("dashboard-fixture-light.png", output)), fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Cambiar a modo oscuro" }).click();
  await page.screenshot({ path: fileURLToPath(new URL("dashboard-fixture-dark.png", output)), fullPage: true, animations: "disabled" });
  console.log("PASS real API dashboard and both themes");

  await page.goto("/tasks?critical_only=true", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Solo críticas", { exact: true })).toBeChecked();
  await page.getByRole("button", { name: /Ver detalle de Tarea crítica/ }).click();
  const detail = page.getByRole("dialog");
  await expect(detail).toBeVisible();
  await expect(detail).not.toContainText("fixture-evidence-secret-do-not-return");
  await expect(detail).toContainText("Autor: no determinado");
  await detail.getByRole("button", { name: "Comparar capturas", exact: true }).click();
  await expect(detail.getByLabel("Captura anterior")).toBeVisible();
  await expect(detail.locator("del").first()).toHaveText("Tarea anterior");
  await page.screenshot({ path: fileURLToPath(new URL("task-diff-fixture-dark.png", output)), fullPage: true, animations: "disabled" });
  await page.keyboard.press("Escape");
  console.log("PASS real API critical task, evidence and observed capture diff");

  await page.getByRole("button", { name: "Iniciar sesión", exact: true }).click();
  const login = page.getByRole("dialog");
  const fixtureToken = "h5-fixture-operator-only-never-use-in-production";
  await login.getByLabel("Token de operador", { exact: true }).fill(fixtureToken);
  // The public form submit label differs from the topbar's login trigger.
  await login.getByRole("button", { name: "Verificar y continuar", exact: true }).click();
  await expect(login).not.toBeVisible();
  const session = await (await context.request.get("/api/v1/auth/session")).json();
  if (!session.authenticated || session.actor !== "operador-fixture") throw new Error("Server session was not verified.");
  const cookies = await context.cookies();
  if (!cookies.some((cookie) => cookie.name === "h5_operator_session" && cookie.httpOnly && cookie.sameSite === "Strict")) {
    throw new Error("Missing HttpOnly/SameSite=Strict session cookie.");
  }
  if (await page.evaluate((token) => JSON.stringify(localStorage).includes(token), fixtureToken)) {
    throw new Error("Operator credential leaked to localStorage.");
  }
  await page.getByRole("button", { name: "Cerrar sesión", exact: true }).click();
  await expect(page.getByRole("button", { name: "Iniciar sesión", exact: true })).toBeVisible();
  const loggedOut = await (await context.request.get("/api/v1/auth/session")).json();
  if (loggedOut.authenticated) throw new Error("Server session was not revoked.");
  console.log("PASS server-verified operator, HttpOnly cookie, no persisted token, CSRF logout and revocation");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "networkidle" });
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) {
    throw new Error("Mobile page overflows the viewport.");
  }
  await page.getByRole("button", { name: "Abrir navegación", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.screenshot({ path: fileURLToPath(new URL("dashboard-fixture-mobile.png", output)), fullPage: true, animations: "disabled" });
  if (errors.length) throw new Error(errors.join("\n"));
  console.log("PASS real API mobile navigation without overflow or page errors");
  console.log("FOUNDATION_INTEGRATION_OK (controlled fixture, not authentic history)");
} finally {
  await browser.close();
}
