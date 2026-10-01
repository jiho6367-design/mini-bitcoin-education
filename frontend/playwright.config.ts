import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  // 설치된 Windows 브라우저의 첫 실행과 두 블록 시나리오까지 포함한 제한이다.
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
  // 테스트 전용 포트로 Python 백엔드와 화면 개발 서버를 함께 시작한다.
  webServer: [
    {
      command: "uv run mini-bitcoin-backend --port 8173",
      cwd: "../backend",
      url: "http://127.0.0.1:8173/api/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev -- --port 4173 --strictPort",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: !process.env.CI,
      env: { MINI_BITCOIN_API_URL: "http://127.0.0.1:8173" },
    },
  ],
});
