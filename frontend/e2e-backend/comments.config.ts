import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const cwd = fileURLToPath(new URL("..", import.meta.url));
export default defineConfig({
  testDir: ".", testMatch: "comment-circuit.spec.ts", workers: 1, retries: 0,
  reporter: "list", use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4173", serviceWorkers: "block" },
  webServer: [
    { command: "../.venv/bin/python ../tests/comment_sandbox_server.py", cwd,
      url: "http://127.0.0.1:4178/api/v1/system/health", reuseExistingServer: false,
      env: { EXTERNAL_WRITES_ENABLED: "false", ALLOW_REMOTE_AI: "false", PYTHON_DOTENV_DISABLED: "true" } },
    { command: "npm run dev -- --host=127.0.0.1 --port=4173 --strictPort", cwd,
      url: "http://127.0.0.1:4173", reuseExistingServer: false,
      env: { H5_API_PROXY_TARGET: "http://127.0.0.1:4178" } },
  ],
});
