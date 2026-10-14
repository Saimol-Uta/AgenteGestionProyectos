#!/usr/bin/env node
/** T2/T3 integration using the real API and its explicitly disposable injected-executor fixture. */
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.H5_WEB_URL ?? "http://127.0.0.1:5173";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Loopback only.");
const output = new URL("../.runtime/h5-browser/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1050 }, reducedMotion: "reduce" });
  const project = await context.request.get("/api/v1/projects/H5-TEST");
  if (project.status() !== 200 || (await project.json()).source_mode !== "fixtures") throw new Error("Disposable fixture required.");
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  const analysisMutations = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    if (request.method() === "POST" && /\/analysis\/(prepare|confirm)$/.test(new URL(request.url()).pathname)) analysisMutations.push(request.url());
  });
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("No se pudo cargar");
  await page.screenshot({ path: fileURLToPath(new URL("executive-fixture-light.png", output)), fullPage: true, animations: "disabled" });
  console.log("PASS executive dashboard through actual backend resources");

  await page.goto("/analysis?run_id=run-current", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Ejecución run-current", exact: true })).toBeVisible();
  const verifier = page.getByRole("article").filter({ has: page.getByText("verifier", { exact: true }) });
  await expect(verifier).toContainText("skipped");
  await expect(page.getByRole("button", { name: "Preparar análisis", exact: true })).toBeDisabled();
  if (analysisMutations.length !== 0) throw new Error("Analysis invoked by consultation.");
  await page.screenshot({ path: fileURLToPath(new URL("analysis-fixture-light.png", output)), fullPage: true, animations: "disabled" });
  console.log("PASS historical run, persisted omitted verifier, and no analysis on consultation");

  await page.getByRole("link", { name: "Revisar calidad y hallazgos", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate-001", exact: true }).click();
  const detail = page.getByRole("dialog");
  await expect(detail).toContainText("Fragmento parcial");
  await expect(detail).toContainText("El fragmento parcial no permite confirmar");
  await expect(detail).not.toContainText("fixture-evidence-secret-do-not-return");
  await page.screenshot({ path: fileURLToPath(new URL("finding-fixture-light.png", output)), fullPage: true, animations: "disabled" });
  await page.keyboard.press("Escape");
  console.log("PASS candidate finding, insufficient evidence, partial persisted snippet and redaction");

  await page.goto("/analysis", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Iniciar sesión", exact: true }).click();
  const login = page.getByRole("dialog");
  await login.getByLabel("Token de operador", { exact: true }).fill("h5-fixture-operator-only-never-use-in-production");
  await login.getByRole("button", { name: "Verificar y continuar", exact: true }).click();
  await expect(login).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Preparar análisis", exact: true })).toBeEnabled();
  const [preparedResponse] = await Promise.all([
    page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/analysis/prepare")),
    page.getByRole("button", { name: "Preparar análisis", exact: true }).click(),
  ]);
  if (preparedResponse.status() !== 200) throw new Error("Actual preparation rejected.");
  const prepared = await preparedResponse.json();
  if (!prepared.eligible || prepared.remote.required || prepared.remote.providers.some(provider => provider.provider !== "fake_simulated")) {
    throw new Error("Refusing confirmation: this is not an isolated fake-provider plan.");
  }
  const before = await (await context.request.get("/api/v1/analysis/jobs?project_id=H5-TEST")).json();
  if (before.total !== 0) throw new Error("Fixture ledger was not empty before explicit confirmation.");
  const [confirmed] = await Promise.all([
    page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/analysis/confirm")),
    page.getByRole("button", { name: "Confirmar análisis", exact: true }).click(),
  ]);
  if (confirmed.status() !== 202) throw new Error("Actual confirmation rejected.");
  const accepted = await confirmed.json();
  await expect(page.getByRole("status").filter({ hasText: /^Trabajo de análisis: completado$/ })).toBeVisible();
  const job = await (await context.request.get(`/api/v1/analysis/jobs/${accepted.job.job_id}`)).json();
  if (job.status !== "completed" || job.run_id !== "run-current" || job.remote_consent !== false) throw new Error("Unexpected persisted fixture job result.");
  if (analysisMutations.filter(url => url.endsWith("/analysis/confirm")).length !== 1) throw new Error("Confirmation submitted more than once.");
  await page.screenshot({ path: fileURLToPath(new URL("confirmed-fixture-job.png", output)), fullPage: true, animations: "disabled" });
  if (errors.length) throw new Error(errors.join("\n"));
  console.log("PASS real HTTP explicit preparation, confirmation, durable terminal job and UI polling");
  console.log("INTELLIGENCE_INTEGRATION_OK: injected fixture executor only; no engine pipeline, provider or external source invoked.");
} finally {
  await browser.close();
}
