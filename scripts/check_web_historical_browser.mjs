#!/usr/bin/env node
/** Authentic historical browser consultation. Never logs in or submits a mutation. */
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.H5_WEB_URL ?? "http://127.0.0.1:5174";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Loopback only.");
const baseline = JSON.parse(await readFile(new URL("../.runtime/h5-authentic-browser-before.json", import.meta.url), "utf8"));
const output = new URL("../.runtime/h5-browser/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const proof = { completed: false, dataset: "authentic HORMIGESTION", api_intercepted: false, mutations: [], external_browser_requests: [], page_errors: [], checks: [], limitations: [] };
try {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1050 }, reducedMotion: "reduce" });
  const get = async path => {
    const response = await context.request.get(path);
    if (response.status() !== 200) throw new Error(`${path}: HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
  };
  const api = "/api/v1/projects/HORMIGESTION";
  const project = await get(api);
  if (project.source_mode === "fixtures") throw new Error("Authentic data required; refusing fixture substitution.");
  const session = await get("/api/v1/auth/session");
  if (session.authenticated) throw new Error("Historical demonstration must be unprivileged.");
  proof.metrics = await get(api + "/summary");
  const history = await get(api + "/history");
  const runs = await get("/api/v1/runs?project_id=HORMIGESTION&page_size=100");
  const reports = await get(api + "/reports?page_size=100");
  const proposals = await get(api + "/proposals");
  proof.counts = { captures: history.total, runs: runs.total, reports: reports.total, proposals: proposals.total };
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => proof.page_errors.push(error.message));
  page.on("request", request => {
    if (request.method() === "POST") proof.mutations.push(new URL(request.url()).pathname);
    const url = new URL(request.url());
    if (["http:", "https:"].includes(url.protocol) && !["127.0.0.1", "localhost"].includes(url.hostname)) proof.external_browser_requests.push(request.url());
  });
  const screenshot = async name => page.screenshot({ path: fileURLToPath(new URL(name, output)), fullPage: true, animations: "disabled" });
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Resumen del proyecto", exact: true })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("No se pudo cargar");
  await screenshot("authentic-dashboard-light.png");
  await page.getByRole("button", { name: "Cambiar a modo oscuro", exact: true }).click();
  await screenshot("authentic-dashboard-dark.png");
  proof.checks.push("dashboard, deterministic backend metrics, light/dark");

  await page.goto("/tasks?critical_only=true", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Solo críticas", { exact: true })).toBeChecked();
  await page.getByRole("button", { name: /^Ver detalle de / }).first().click();
  const detail = page.getByRole("dialog");
  await expect(detail).toBeVisible();
  await screenshot("authentic-critical-task.png");
  if (history.items.length >= 2) {
    const before = history.items.at(-1).capture_id;
    const after = history.items[0].capture_id;
    // Task history defaults to oldest→latest. Selecting its current value emits no new GET.
    const [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/compare") && new URL(response.url()).searchParams.get("before") === before && new URL(response.url()).searchParams.get("after") === after),
      detail.getByRole("button", { name: "Comparar capturas", exact: true }).click(),
    ]);
    await expect(detail.getByLabel("Captura anterior")).toHaveValue(before);
    await expect(detail.getByLabel("Captura posterior")).toHaveValue(after);
    if (response.status() === 409) {
      const body = await response.json();
      if (body.error?.code !== "comparacion_no_disponible") throw new Error("Unexpected comparison refusal");
      await expect(detail.getByText(body.error.message, { exact: true })).toBeVisible();
      await expect(detail.locator("del")).toHaveCount(0);
      proof.limitations.push("Oldest-to-newest capture comparison unavailable (409); no synthetic diff rendered.");
    } else if (response.status() !== 200) throw new Error(`Unexpected comparison status: ${response.status()}`);
    await screenshot("authentic-comparison-boundary.png");
  }
  await page.keyboard.press("Escape");
  proof.checks.push("critical task filter/detail and honest comparison boundary");

  let chosenRun = runs.items[0], chosenFinding = null;
  for (const run of runs.items) {
    const findings = await get(`/api/v1/runs/${encodeURIComponent(run.run_id)}/findings`);
    if (findings.items.length) { chosenRun = run; chosenFinding = findings.items[0]; break; }
  }
  if (!chosenRun) throw new Error("No actual historical run; no fake run is substituted.");
  await page.goto(`/analysis?run_id=${encodeURIComponent(chosenRun.run_id)}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: `Ejecución ${chosenRun.run_id}`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Preparar análisis", exact: true })).toBeDisabled();
  await screenshot("authentic-analysis.png");
  proof.run_id = chosenRun.run_id;
  if (chosenFinding) {
    await page.goto(`/quality?run_id=${encodeURIComponent(chosenRun.run_id)}`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: `Revisar hallazgo ${chosenFinding.finding_id}`, exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await screenshot("authentic-finding.png");
    proof.finding_id = chosenFinding.finding_id;
    await page.keyboard.press("Escape");
  } else proof.limitations.push("No persisted finding available; no substitute generated.");
  proof.checks.push("actual historical run, persisted stages and available finding");

  const report = reports.items.find(row => row.run_id === chosenRun.run_id) ?? reports.items[0];
  if (!report) throw new Error("No actual report available; no substitute generated.");
  await page.goto(`/reports?report_id=${encodeURIComponent(report.report_id)}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: `Informe ${report.report_id}`, exact: true })).toBeVisible();
  await expect(page.locator(".safe-markdown script, .safe-markdown img")).toHaveCount(0);
  await screenshot("authentic-report.png");
  proof.report_id = report.report_id;
  await page.goto("/proposals", { waitUntil: "networkidle" });
  if (proposals.availability === "available" && proposals.total === 0) {
    await expect(page.getByText("No hay propuestas en este filtro", { exact: true })).toBeVisible();
    proof.limitations.push("No authentic proposal stored; authorized simulation demonstrated separately on disposable fixtures.");
  }
  await screenshot("authentic-proposals-empty.png");
  await page.goto("/audit", { waitUntil: "networkidle" });
  await expect(page.getByText("Se requiere una sesión local con la capacidad audit:read.", { exact: true })).toBeVisible();
  proof.limitations.push("Protected audit/configuration/manual actions remain unavailable in unprivileged historical demo.");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "networkidle" });
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error("Authentic dashboard mobile overflow");
  await screenshot("authentic-dashboard-mobile.png");
  if (proof.mutations.length || proof.external_browser_requests.length || proof.page_errors.length) throw new Error(JSON.stringify(proof));
  proof.checks.push("actual report, honest empty proposals, protected audit, mobile");
  proof.completed = true;
} finally {
  await browser.close();
  const after = JSON.parse(execFileSync(root + ".venv/bin/python", ["-c", "import json; from scripts.check_web_history import historical_fingerprints; print(json.dumps(historical_fingerprints()))"], { cwd: root, encoding: "utf8" }));
  expect(after).toEqual(baseline);
  proof.historical_files_unchanged = Object.keys(baseline).length;
  await writeFile(new URL("../.runtime/h5-authentic-browser-proof.json", import.meta.url), JSON.stringify(proof, null, 2));
}
console.log("AUTHENTIC_BROWSER_OK: HORMIGESTION actual history, no POST/external browser requests, all historical fingerprints unchanged.");
