import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appDir = dirname(fileURLToPath(import.meta.url));
const repoDir = resolve(appDir, "../..");
const localPython = join(
  appDir,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const python =
  process.env.FLASK_PYTHON ??
  (existsSync(localPython) ? localPython : "python");

export default defineConfig({
  testDir: "./tests/browser",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  outputDir: join(repoDir, "work/kakao-overhaul/test-results"),
  reporter: [
    ["list"],
    [
      "html",
      {
        open: "never",
        outputFolder: join(repoDir, "work/kakao-overhaul/browser-report"),
      },
    ],
  ],
  use: {
    baseURL: "http://127.0.0.1:5010",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chrome", use: { channel: "chrome" } },
    { name: "edge", use: { channel: "msedge" } },
  ],
  webServer: {
    command: `"${python}" -B app.py`,
    cwd: appDir,
    env: {
      PORT: "5010",
      KAKAO_PRACTICE_DB: join(repoDir, "work/kakao-overhaul/e2e.sqlite3"),
    },
    url: "http://127.0.0.1:5010",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
