import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const frontendOrigin = "http://127.0.0.1:4173";
const backendHealth = "http://127.0.0.1:4174/api/v1/system/health";
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR;
const frontendDirectory = fileURLToPath(new URL("..", import.meta.url));

if (!outputDir) throw new Error("PLAYWRIGHT_OUTPUT_DIR must point inside the fresh T6 scratch run.");

export default defineConfig({
  testDir: ".",
  testMatch: "req08-decision-workflow.spec.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: "list",
  outputDir,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: frontendOrigin,
    trace: "off",
    serviceWorkers: "block",
    launchOptions: {
      args: [
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-default-apps",
        "--no-first-run",
        "--no-default-browser-check",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      ],
    },
  },
  webServer: [
    {
      command: "python ../tests/req08_browser_server.py",
      cwd: frontendDirectory,
      url: backendHealth,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev -- --host=127.0.0.1 --port=4173 --strictPort",
      cwd: frontendDirectory,
      url: frontendOrigin,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
