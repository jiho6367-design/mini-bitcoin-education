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

const server = (port: number, browser: string) => ({
  command: `"${pythonExecutable}" -B app.py`,
  cwd: flaskDirectory,
  env: {
    ...legacyEnvironment,
    PORT: String(port),
    KAKAO_PRACTICE_DB: resolve(workspaceDirectory, `work/team-merge/e2e-${databaseRun}-${browser}-basic.sqlite3`),
    TEAM_EDUCATION_DB: resolve(workspaceDirectory, `work/team-merge/e2e-${databaseRun}-${browser}-education.sqlite3`),
    TEAM_TEACHER_SECRET: "classroom-test-only",
  },
  url: `http://127.0.0.1:${port}/api/education/health`,
  reuseExistingServer: false,
  timeout: 30_000,
});

export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/classroom-team.spec.ts", "**/classroom-guide.spec.ts", "**/classroom-balances.spec.ts", "**/classroom-setup-mining.spec.ts", "**/classroom-easy-start.spec.ts"],
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
        baseURL: "http://127.0.0.1:4173",
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
        baseURL: "http://127.0.0.1:4176",
        channel: "msedge",
        viewport: { width: 1440, height: 1000 },
      },
    },
  ],
  // A persisted default team room must not change the other browser's teacher entry.
  webServer: [server(4173, "chrome"), server(4176, "edge")],
});
