#!/usr/bin/env node
/** Actual HTTP/browser T4/T5 proof. Refuses anything except the disposable fixture. */
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.H5_WEB_URL ?? "http://127.0.0.1:5173";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Loopback only.");
const output = new URL("../.runtime/h5-browser/", import.meta.url);
const downloadDirectory = fileURLToPath(new URL("../.runtime/h5-downloads/", import.meta.url)).replace(/\/$/, "");
await mkdir(output, { recursive: true });
await mkdir(downloadDirectory, { recursive: true });
const root = "/api/v1/projects/H5-TEST";
const browser = await chromium.launch({ headless: true, downloadsPath: downloadDirectory });
try {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1050 }, reducedMotion: "reduce" });
  const get = async path => {
    const response = await context.request.get(path);
    if (response.status() !== 200) throw new Error(`${path}: HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
  };
  const project = await get(root);
  if (project.source_mode !== "fixtures" || !project.name.includes("fixture controlada")) throw new Error("Disposable demonstration fixture required.");
  const beforeTask = await get(`${root}/tasks/task-001`);
  const beforeHistory = await get(`${root}/history`);
  const beforeProposal = await get(`${root}/proposals/proposal-date-001`);
  if (beforeProposal.review_status !== "pending") throw new Error("A fresh fixture is required; proposal already reviewed.");
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [], external = [], mutations = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    const url = new URL(request.url());
    if (["http:", "https:"].includes(url.protocol) && !["127.0.0.1", "localhost"].includes(url.hostname)) external.push(request.url());
    if (request.method() === "POST") mutations.push(url.pathname);
  });
  const screenshot = async name => page.screenshot({ path: fileURLToPath(new URL(name, output)), fullPage: true, animations: "disabled" });
  const confirm = async (suffix, locator, expectedStatus = 200) => {
    const [response] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(suffix)), locator.click(),
    ]);
    if (response.status() !== expectedStatus) throw new Error(`${suffix}: HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
  };
  const download = async (label, extension, parseJson = false) => {
    const event = page.waitForEvent("download");
    await page.getByRole("button", { name: label, exact: true }).click();
    const result = await event;
    if (!result.suggestedFilename().endsWith(extension)) throw new Error("Unexpected download filename");
    const path = await result.path();
    if (!path?.startsWith(downloadDirectory + "/")) throw new Error("Download escaped the project test directory");
    const content = await readFile(path, "utf8");
    if (/<script|javascript:|attacker\.invalid|fixture-evidence-secret-do-not-return/i.test(content)) throw new Error("Unsafe report download");
    if (parseJson) JSON.parse(content);
    return result.suggestedFilename();
  };
  await page.goto("/reports?report_id=report-current", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Informe report-current", exact: true })).toBeVisible();
  await expect(page.locator(".safe-markdown img, .safe-markdown script, .safe-markdown [onerror]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Registrar decisión humana", exact: true })).toHaveCount(0);
  const reportFiles = [await download("Descargar Markdown", ".md"), await download("Descargar JSON", ".json", true)];
  await screenshot("report-fixture-light.png");
  console.log("PASS real stored report, sanitized Markdown and actual Markdown/JSON downloads");

  await page.getByRole("button", { name: "Iniciar sesión", exact: true }).click();
  const login = page.getByRole("dialog");
  await login.getByLabel("Token de operador", { exact: true }).fill("h5-fixture-operator-only-never-use-in-production");
  await login.getByRole("button", { name: "Verificar y continuar", exact: true }).click();
  await expect(login).not.toBeVisible();
  const session = await get("/api/v1/auth/session");
  if (!session.authenticated || session.actor !== "operador-fixture") throw new Error("Fixture identity was not verified by server");
  const beforeJobs = await get("/api/v1/analysis/jobs?project_id=H5-TEST");

  await page.goto("/proposals", { waitUntil: "networkidle" });
  await page.getByRole("link", { name: "Abrir propuesta proposal-date-001", exact: true }).click();
  await expect(page.getByText("Valor observado", { exact: true })).toBeVisible();
  await expect(page.getByText("Valor propuesto", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Aprobar para revisión local", exact: true }).click();
  const reviewed = await confirm("/review", page.getByRole("dialog", { name: "Confirmar aprobación de revisión local" }).getByRole("button", { name: "Registrar aprobación offline", exact: true }));
  if (reviewed.proposal.review_status !== "approved_offline" || reviewed.proposal.authorization_status !== "not_authorized") throw new Error("Review granted authorization implicitly");
  await page.getByRole("button", { name: "Autorizar simulación segura", exact: true }).click();
  const authorized = await confirm("/dry-run-authorization", page.getByRole("dialog", { name: "Autorizar simulación", exact: true }).getByRole("button", { name: "Autorizar simulación", exact: true }));
  if (authorized.proposal.authorization_status !== "authorized_dry_run") throw new Error("Separate authorization missing");
  await page.getByRole("button", { name: "Simular propuesta autorizada", exact: true }).click();
  const simulated = await confirm("/dry-runs", page.getByRole("dialog", { name: "Confirmar simulación local" }).getByRole("button", { name: "Simular sin escrituras", exact: true }));
  if (simulated.dry_run.status !== "SIMULATED" || simulated.dry_run.live_state_changed || simulated.dry_run.real_execution_allowed) throw new Error("Unsafe or unsuccessful core simulation");
  await expect(page.getByText("SIMULATED", { exact: true })).toBeVisible();
  await expect(page.getByText("Estado en vivo: Sin cambios", { exact: true })).toBeVisible();
  await expect(page.getByText("Ejecución real permitida: No", { exact: true })).toBeVisible();
  expect(await get(`${root}/tasks/task-001`)).toEqual(beforeTask);
  expect(await get(`${root}/history`)).toEqual(beforeHistory);
  if ((await get(`${root}/reports/report-current`)).human_decision !== "pending") throw new Error("Proposal review changed report decision");
  await page.getByRole("button", { name: "Cambiar a modo oscuro", exact: true }).click();
  await screenshot("proposal-simulated-fixture-dark.png");
  console.log("PASS separate review/authorization/core dry-run with unchanged tasks/captures/report decision");

  await page.goto("/calendar", { waitUntil: "networkidle" });
  await expect(page.getByText("Disparador automático: no verificado", { exact: true })).toBeVisible();
  const receiptsBefore = await get(`${root}/schedule/outbox`);
  const receipt = receiptsBefore.items.find(item => item.artifact_available);
  if (!receipt) throw new Error("No stored downloadable outbox receipt");
  await page.getByRole("link", { name: `Abrir entrega ${receipt.delivery_id}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: `Entrega ${receipt.delivery_id}`, exact: true })).toBeVisible();
  const outboxFile = await download("Descargar recibo Markdown", ".md");
  await page.getByRole("button", { name: "Preparar ciclo manual", exact: true }).click();
  const prepared = await confirm("/manual-cycle/prepare", page.getByRole("dialog", { name: "Preparar ciclo semanal local" }).getByRole("button", { name: "Comprobar elegibilidad", exact: true }));
  if (!prepared.eligible || prepared.remote.required || prepared.remote.server_allowed) throw new Error("Weekly cycle is not eligible/local-only");
  const consent = page.getByRole("checkbox", { name: "Confirmo ejecutar el ciclo local mediante el canal SQLite, sin llamadas remotas ni entregas externas", exact: true });
  await expect(consent).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Confirmar ciclo semanal", exact: true })).toBeDisabled();
  await consent.check();
  await page.getByRole("button", { name: "Confirmar ciclo semanal", exact: true }).click();
  const accepted = await confirm("/manual-cycle/confirm", page.getByRole("dialog", { name: "Confirmar ciclo semanal", exact: true }).getByRole("button", { name: "Ejecutar ciclo local", exact: true }), 202);
  await expect(page.getByRole("heading", { name: "Estado del ciclo", exact: true })).toBeVisible();
  await expect(page.getByText("Trabajo completado", { exact: true }).first()).toBeVisible();
  const job = await get(`${root}/schedule/manual-cycle/jobs/${accepted.job.job_id}`);
  if (job.status !== "completed" || job.outcome !== "EXECUTED" || job.remote_consent) throw new Error(`Unexpected weekly cycle: ${JSON.stringify(job)}`);
  expect((await get(`${root}/schedule/outbox`)).total).toBe(receiptsBefore.total + 1);
  expect((await get("/api/v1/analysis/jobs?project_id=H5-TEST")).total).toBe(beforeJobs.total);
  await screenshot("weekly-cycle-fixture-dark.png");
  console.log("PASS actual two-phase weekly core scheduler, SQLite receipt, terminal polling; no new analysis");

  await page.goto("/audit", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Auditoría y actividad", exact: true })).toBeVisible();
  await expect(page.getByText("Actor no verificado", { exact: true }).first()).toBeVisible();
  const events = await get(`${root}/audit`);
  const event = events.items.find(row => row.actor_verification === "verified_local_session" && row.resource_id === "proposal-date-001");
  if (!event) throw new Error("Missing linked verified-session proposal audit");
  await page.getByRole("textbox", { name: "Tipo de evento", exact: true }).fill(event.event_type);
  await page.getByRole("button", { name: "Aplicar filtros de auditoría", exact: true }).click();
  await page.getByRole("link", { name: `Ver evento ${event.event_id}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: `Evento ${event.event_id}`, exact: true })).toBeVisible();
  await screenshot("audit-event-fixture-dark.png");
  await page.goto("/settings", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Configuración general del proyecto", exact: true })).toBeVisible();
  await expect(page.getByText("Ningún campo de configuración es editable desde esta interfaz.", { exact: true })).toBeVisible();
  await expect(page.locator('main input[type="password"]')).toHaveCount(0);
  await screenshot("configuration-fixture-dark.png");

  await page.goto("/reports?report_id=report-current", { waitUntil: "networkidle" });
  await page.getByRole("combobox", { name: "Decisión humana del informe", exact: true }).selectOption("changes_requested");
  await expect(page.getByRole("button", { name: "Registrar decisión humana", exact: true })).toBeDisabled();
  await page.getByRole("textbox", { name: "Motivo de la decisión", exact: true }).fill("Revisión controlada de fixture, sin aprobación de propuestas ni ejecución externa.");
  await page.getByRole("button", { name: "Registrar decisión humana", exact: true }).click();
  const decision = await confirm("/decision", page.getByRole("dialog", { name: "Confirmar decisión humana", exact: true }).getByRole("button", { name: "Confirmar decisión humana", exact: true }));
  await expect(page.locator(".report-detail-heading").getByText("Cambios solicitados", { exact: true })).toBeVisible();
  if (decision.report.human_decision !== "changes_requested" || !decision.audit_event_id) throw new Error("Report decision/audit link missing");
  const afterProposal = await get(`${root}/proposals/proposal-date-001`);
  if (afterProposal.execution_status !== "not_executed") throw new Error("Report decision enabled proposal execution");
  expect(await get(`${root}/tasks/task-001`)).toEqual(beforeTask);
  await screenshot("report-decision-fixture-dark.png");

  const mobileRoutes = ["/proposals?proposal_id=proposal-date-001", "/reports?report_id=report-current", "/calendar", "/audit", "/settings"];
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of mobileRoutes) {
    await page.goto(route, { waitUntil: "networkidle" });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error(`Mobile overflow: ${route}`);
  }
  await screenshot("configuration-fixture-mobile.png");
  if (external.length || errors.length) throw new Error(JSON.stringify({ external, errors }));
  if (mutations.some(path => /execute|trello/.test(path))) throw new Error("External mutation route requested");
  const proof = { dataset: "disposable controlled fixture", api_intercepted: false, reportFiles, outboxFile,
    proposal: { review: reviewed.proposal.review_status, authorization: authorized.proposal.authorization_status, simulation: simulated.dry_run },
    weekly: job, report_decision: decision.report.human_decision, verified_audit_event: event.event_id,
    task_and_captures_unchanged: true, external_browser_requests: external, page_errors: errors, mutations, mobile_routes: mobileRoutes };
  await writeFile(new URL("../.runtime/h5-governance-proof.json", import.meta.url), JSON.stringify(proof, null, 2));
  console.log("GOVERNANCE_INTEGRATION_OK: real HTTP, core simulation/weekly cycle, report decision/downloads, verified audit, read-only settings and mobile; no external mutations.");
} finally { await browser.close(); }
