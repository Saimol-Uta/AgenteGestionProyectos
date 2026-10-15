#!/usr/bin/env node
/** Real HTTP/browser proof of the unified workflow, against a fresh fixture only. */
import { createRequire } from "node:module";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const help = "Usage: node scripts/check_web_governance.mjs [--help]\n"
  + "Runs the unified workflow check against a fresh local fixture.\n"
  + "Set H5_WEB_URL to the loopback frontend URL (default http://127.0.0.1:5173).\n"
  + "Requires fixture-mode API; never contacts live providers.\n";
if (process.argv.length === 3 && process.argv[2] === "--help") {
  process.stdout.write(help);
  process.exit(0);
}
if (process.argv.length !== 2) {
  process.stderr.write(help);
  process.exit(2);
}
const base = new URL(process.env.H5_WEB_URL ?? "http://127.0.0.1:5173");
if (!["http:", "https:"].includes(base.protocol)
    || !["127.0.0.1", "localhost"].includes(base.hostname)
    || base.username || base.password || base.pathname !== "/" || base.search || base.hash) {
  throw new Error("A loopback frontend origin without credentials or path is required.");
}
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const projectId = "H5-TEST";
const root = `/api/v1/projects/${projectId}`;
const output = new URL("../.runtime/h5-browser/", import.meta.url);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ baseURL: base.origin, viewport: { width: 1440, height: 1050 }, reducedMotion: "reduce" });
  const errors = [], external = [], mutations = [];
  await context.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (["http:", "https:"].includes(url.protocol) && !["127.0.0.1", "localhost"].includes(url.hostname)) {
      external.push(url.origin);
      await route.abort();
    } else {
      await route.continue(); // Local API responses are never mocked.
    }
  });
  const get = async path => {
    if (!path.startsWith("/api/v1/")) throw new Error("API-relative paths required.");
    const response = await context.request.get(path);
    if (response.status() !== 200) throw new Error(`${path}: HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
  };
  const health = await get("/api/v1/system/health");
  const project = await get(root);
  if (project.id !== projectId || project.source_mode !== "fixtures" || health.external_writes_enabled !== false) {
    throw new Error("Refusing anything except the controlled fixture with external writes disabled.");
  }
  const historyBefore = await get(`${root}/history?page_size=100`);
  const captures = [...historyBefore.items].sort((a, b) => Date.parse(a.captured_at) - Date.parse(b.captured_at));
  if (captures.length < 2) throw new Error("At least two fresh comparable captures are required.");
  const [older, current] = captures.slice(-2);
  const comparison = await get(`${root}/compare?before=${encodeURIComponent(older.capture_id)}&after=${encodeURIComponent(current.capture_id)}`);
  const executive = await get(`${root}/executive-summary`);
  if (!Array.isArray(executive.capture_series) || executive.capture_series.length < 2) {
    throw new Error("Historical chart data is missing.");
  }
  const login = await context.request.post("/api/v1/auth/login", {
    headers: { Origin: base.origin },
    data: { token: "h5-fixture-operator-only-never-use-in-production" },
  });
  if (login.status() !== 200) throw new Error(`Fixture login: HTTP ${login.status()}`);
  const session = await login.json();
  const required = ["governance:proposal_execute", "quality:decide", "notifications:email_send"];
  if (!session.authenticated || !session.csrf_token || required.some(capability => !session.capabilities.includes(capability))) {
    throw new Error("Unified server-derived fixture capabilities required.");
  }
  const retired = ["governance:proposal_review", "governance:dry_run_authorization", "governance:dry_run"];
  if (retired.some(capability => session.capabilities.includes(capability))) throw new Error("Retired manual capability advertised.");
  const post = async (path, data, key) => {
    mutations.push(path);
    const response = await context.request.post(path, {
      headers: { Origin: base.origin, "X-CSRF-Token": session.csrf_token, "Idempotency-Key": key }, data,
    });
    if (response.status() !== 200) throw new Error(`${path}: HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
  };
  const runs = await get(`/api/v1/runs?project_id=${projectId}&page_size=100`);
  const run = runs.items.find(item => item.run_id === project.latest_run_id);
  if (!run) throw new Error("A fresh persisted pipeline run is required.");
  const detail = await get(`/api/v1/runs/${encodeURIComponent(run.run_id)}`);
  if (!detail.report_id) throw new Error("Pipeline must have produced a report.");
  const report = await get(`${root}/reports/${encodeURIComponent(detail.report_id)}`);
  if (report.report_id !== detail.report_id) throw new Error("Report is not bound to the generated run.");
  const findings = await get(`/api/v1/runs/${encodeURIComponent(run.run_id)}/findings`);
  const candidate = findings.items.find(item => item.file_path?.endsWith(".py"));
  if (!candidate) throw new Error("Fresh fixture requires a supported complete-source code finding.");
  const findingPath = `/api/v1/runs/${encodeURIComponent(run.run_id)}/findings/${encodeURIComponent(candidate.finding_id)}`;
  const finding = await get(findingPath);
  if (!finding.suggested_code?.trim() || !finding.improvement_summary?.trim() || finding.decision_status !== "pending") {
    throw new Error("A fresh backend suggestion is required; no local or legacy fallback is accepted.");
  }
  const targetBranch = `verify/unified-smoke-${randomUUID().slice(0, 8)}`;
  const codeKey = `code-${randomUUID()}`;
  const codeBody = { decision: "approve", target_branch: targetBranch, reason: "Unified complete-source fixture approval." };
  const code = await post(`${findingPath}/decision`, codeBody, codeKey);
  const codeReplay = await post(`${findingPath}/decision`, codeBody, codeKey);
  if (code.decision_status !== "approved" || code.execution_mode !== "sandbox" || code.branch_name !== targetBranch
      || !code.commit_sha_applied || codeReplay.reused !== true || codeReplay.commit_sha_applied !== code.commit_sha_applied) {
    throw new Error("Code approval must return the actual approved sandbox receipt and durable replay.");
  }
  const proposals = await get(`${root}/proposals?page_size=100`);
  const proposal = proposals.items.find(item => item.capture_id === current.capture_id && item.execution_status === "not_executed");
  if (!proposal) throw new Error("A fresh current-capture proposal is required.");
  const taskPath = `${root}/tasks/${encodeURIComponent(proposal.task_id)}`;
  const taskBefore = await get(taskPath);
  const proposalReason = "Unified controlled proposal approval; no remote provider call.";
  const executionPath = `${root}/proposals/${encodeURIComponent(proposal.proposal_id)}/execute`;
  const executionBody = { decision: "approve_and_execute", reason: proposalReason };
  const executionKey = `proposal-${randomUUID()}`;
  const execution = await post(executionPath, executionBody, executionKey);
  const executionReplay = await post(executionPath, executionBody, executionKey);
  if (execution.execution?.status !== "SIMULATED" || execution.execution?.result_status !== "EXECUTED"
      || execution.execution?.verification_status !== "verified" || execution.execution?.live_state_changed !== false
      || execution.execution_mode !== "sandbox" || executionReplay.replayed !== true) {
    throw new Error("Unified proposal must execute only in sandbox and return its verified durable receipt.");
  }
  const audit = await get(`${root}/audit?page_size=100`);
  let reasonRetained = false;
  for (const event of audit.items.filter(item => item.resource_id === proposal.proposal_id)) {
    const eventDetail = await get(`${root}/audit/${encodeURIComponent(event.event_id)}`);
    if (event.actor_verification === "verified_local_session" && JSON.stringify(eventDetail.details).includes(proposalReason)) {
      reasonRetained = true;
      break;
    }
  }
  if (!reasonRetained) throw new Error("Human approval reason is not recoverable through protected public audit.");
  expect(await get(taskPath)).toEqual(taskBefore);
  expect(await get(`${root}/history?page_size=100`)).toEqual(historyBefore);
  await mkdir(output, { recursive: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", error => errors.push(error.message));
  const screenshot = async name => page.screenshot({ path: fileURLToPath(new URL(name, output)), fullPage: true, animations: "disabled" });
  await page.goto("/dashboard", { waitUntil: "networkidle" });
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator(".recharts-wrapper").first()).toBeVisible();
  await screenshot("unified-dashboard.png");
  await page.goto(`/quality?run_id=${encodeURIComponent(run.run_id)}&finding_id=${encodeURIComponent(candidate.finding_id)}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: `Revisar hallazgo ${candidate.finding_id}`, exact: true }).click();
  await expect(page.getByText(targetBranch, { exact: true }).first()).toBeVisible();
  await screenshot("unified-code-approved.png");
  await page.goto(`/proposals?proposal_id=${encodeURIComponent(proposal.proposal_id)}`, { waitUntil: "networkidle" });
  await expect(page.locator("main")).toBeVisible();
  await screenshot("unified-proposal-approved.png");
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
  const proof = {
    dataset: "fresh unified controlled fixture", api_mocked: false,
    capture_ids: [older.capture_id, current.capture_id], comparison,
    capture_series_length: executive.capture_series.length,
    run_id: run.run_id, report_id: detail.report_id,
    code, proposal: execution, human_reason_recoverable: reasonRetained,
    immutable_task_and_capture_history_unchanged: true,
    external_browser_requests: external, page_errors: errors, mutations,
    live_github_trello_smtp_verified: false,
  };
  await writeFile(new URL("../.runtime/h5-governance-proof.json", import.meta.url), JSON.stringify(proof, null, 2));
  console.log("UNIFIED_GOVERNANCE_OK: real local HTTP/browser, two captures, automatic review, approved sandbox code/branch receipt, verified sandbox proposal and durable human reason; no live providers.");
} finally {
  await browser.close();
}
