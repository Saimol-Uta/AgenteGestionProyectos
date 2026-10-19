import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const frontendOrigin = "http://127.0.0.1:4173";
const backendHealth = "http://127.0.0.1:4175/api/v1/system/health";
const frontendDirectory = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  testDir: ".",
  testMatch: "g3-risk-proposal-chain.spec.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: "list",
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
        "--disable-default-browser-check",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      ],
    },
  },
  webServer: [
    {
      command: "../.venv/bin/python ../tests/g3_hormigestion_demo_server.py",
      cwd: frontendDirectory,
      url: backendHealth,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        EXTERNAL_WRITES_ENABLED: "false",
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHON_DOTENV_DISABLED: "true",
      },
    },
    {
      command: "npm run dev -- --host=127.0.0.1 --port=4173 --strictPort",
      cwd: frontendDirectory,
      url: frontendOrigin,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        H5_API_PROXY_TARGET: "http://127.0.0.1:4175",
      },
    },
  ],
});
