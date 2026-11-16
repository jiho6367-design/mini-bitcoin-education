import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import base from "./playwright.team.config";

const workspace = resolve(fileURLToPath(new URL("..", import.meta.url)), "../..");
const run = randomUUID();
const servers = Array.isArray(base.webServer) ? base.webServer : [base.webServer];

export default defineConfig({
  ...base,
  timeout: 45_000,
  testMatch: "**/classroom-easy-start.spec.ts",
  outputDir: resolve(workspace, "work/team-easy/browser-results"),
  reporter: [["list"], ["html", { open: "never", outputFolder: resolve(workspace, "work/team-easy/browser-report") }]],
  projects: base.projects?.map((project, index) => ({
    ...project,
    use: { ...project.use, baseURL: `http://127.0.0.1:${4186 + index}` },
  })),
  webServer: servers.map((server, index) => {
    if (!server) throw new Error("쉬운 시작 검사 서버 설정이 필요합니다");
    const port = 4186 + index;
    return {
      ...server,
      env: {
        ...server.env,
        PORT: String(port),
        KAKAO_PRACTICE_DB: resolve(workspace, `work/team-easy/${run}-${port}-basic.sqlite3`),
        TEAM_EDUCATION_DB: resolve(workspace, `work/team-easy/${run}-${port}-education.sqlite3`),
      },
      url: `http://127.0.0.1:${port}/api/education/health`,
    };
  }),
});
