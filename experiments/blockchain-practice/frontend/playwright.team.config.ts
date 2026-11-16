import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import base from "./playwright.config";

const originalServer = Array.isArray(base.webServer) ? base.webServer[0] : base.webServer;
if (!originalServer) throw new Error("팀 실습 테스트 서버 설정이 필요합니다");
const workspace = resolve(fileURLToPath(new URL("..", import.meta.url)), "../..");
const run = randomUUID();
const server = (port: number, browser: string) => ({
  ...originalServer,
  env: {
    ...originalServer.env,
    PORT: String(port),
    KAKAO_PRACTICE_DB: resolve(workspace, `work/team-merge/e2e-team-${run}-${browser}-basic.sqlite3`),
    TEAM_EDUCATION_DB: resolve(workspace, `work/team-merge/e2e-team-${run}-${browser}-education.sqlite3`),
  },
  url: `http://127.0.0.1:${port}/api/education/health`,
});

// Each browser receives its own persistent hub; teacher-mode suites use another DB.
export default defineConfig({
  ...base,
  testIgnore: [],
  testMatch: "**/classroom-team.spec.ts",
  outputDir: resolve(workspace, "work/team-merge/team-playwright-results"),
  reporter: [["list"], ["html", { open: "never", outputFolder: resolve(workspace, "work/team-merge/team-playwright-report") }]],
  projects: base.projects?.map((project, i) => ({
    ...project,
    use: { ...project.use, baseURL: `http://127.0.0.1:${4174 + i}` },
  })),
  webServer: [server(4174, "chrome"), server(4175, "edge")],
});
