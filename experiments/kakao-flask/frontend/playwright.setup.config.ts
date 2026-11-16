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
  testMatch: "**/classroom-setup-mining.spec.ts",
  outputDir: resolve(workspace, "work/team-setup/browser-results"),
  reporter: [["list"], ["html", { open: "never", outputFolder: resolve(workspace, "work/team-setup/browser-report") }]],
  projects: base.projects?.map((project, index) => ({
    ...project,
    use: { ...project.use, baseURL: `http://127.0.0.1:${4184 + index}` },
  })),
  webServer: servers.map((server, index) => {
    if (!server) throw new Error("새 실습·단일 채굴 검사 서버 설정이 필요합니다");
    const port = 4184 + index;
    return {
      ...server,
      env: {
        ...server.env,
        PORT: String(port),
        KAKAO_PRACTICE_DB: resolve(workspace, `work/team-setup/${run}-${port}-basic.sqlite3`),
        TEAM_EDUCATION_DB: resolve(workspace, `work/team-setup/${run}-${port}-education.sqlite3`),
      },
      url: `http://127.0.0.1:${port}/api/education/health`,
    };
  }),
});
