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
  testMatch: "**/classroom-balances.spec.ts",
  outputDir: resolve(workspace, "work/team-balances/browser-results"),
  reporter: [["list"], ["html", { open: "never", outputFolder: resolve(workspace, "work/team-balances/browser-report") }]],
  projects: base.projects?.map((project, index) => ({
    ...project,
    use: { ...project.use, baseURL: `http://127.0.0.1:${4182 + index}` },
  })),
  webServer: servers.map((server, index) => {
    if (!server) throw new Error("잔액 안내 검사 서버 설정이 필요합니다");
    const port = 4182 + index;
    return {
      ...server,
      env: {
        ...server.env,
        PORT: String(port),
        KAKAO_PRACTICE_DB: resolve(workspace, `work/team-balances/${run}-${port}-basic.sqlite3`),
        TEAM_EDUCATION_DB: resolve(workspace, `work/team-balances/${run}-${port}-education.sqlite3`),
      },
      url: `http://127.0.0.1:${port}/api/education/health`,
    };
  }),
});
