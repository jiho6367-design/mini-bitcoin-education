import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";

const flaskDirectory = fileURLToPath(new URL("..", import.meta.url));
const workspaceDirectory = resolve(flaskDirectory, "../..");
const databaseRun = randomUUID();
const windowsPython = resolve(flaskDirectory, ".venv/Scripts/python.exe");
const pythonExecutable = existsSync(windowsPython)
  ? windowsPython
  : resolve(flaskDirectory, ".venv/bin/python");
const legacyEnvironment = Object.fromEntries(
  Object.keys(process.env).filter(key => key.startsWith("MINI_")).map(key => [key, ""]),
);

export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/classroom-team.spec.ts", "**/classroom-guide.spec.ts", "**/classroom-balances.spec.ts", "**/classroom-setup-mining.spec.ts"],
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  projects: [
    {
      name: "chrome",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        launchOptions: process.env.TEAM_CHROME_PATH
          ? { executablePath: process.env.TEAM_CHROME_PATH }
          : undefined,
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      name: "edge",
      use: {
        ...devices["Desktop Edge"],
        channel: "msedge",
        viewport: { width: 1440, height: 1000 },
      },
    },
  ],
  webServer: {
    command: `"${pythonExecutable}" -B app.py`,
    cwd: flaskDirectory,
    env: {
      ...legacyEnvironment,
      PORT: "4173",
      KAKAO_PRACTICE_DB: resolve(workspaceDirectory, `work/team-merge/e2e-${databaseRun}-basic.sqlite3`),
      TEAM_EDUCATION_DB: resolve(workspaceDirectory, `work/team-merge/e2e-${databaseRun}-education.sqlite3`),
      TEAM_TEACHER_SECRET: "classroom-test-only",
    },
    url: "http://127.0.0.1:4173/api/education/health",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
