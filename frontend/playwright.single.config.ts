import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// 화면 개발 서버 없이, 빌드된 화면(dist)을 함께 제공하는 Python 백엔드 하나만으로
// 같은 시나리오를 실행한다. 시나리오가 사용하는 주소(4173)에서 백엔드를 실행한다.
// 먼저 화면을 빌드해야 하므로 `npm run test:e2e:single`로 실행한다.
export default defineConfig({
  ...base,
  webServer: {
    command: "uv run mini-bitcoin-backend --port 4173",
    cwd: "../backend",
    url: "http://127.0.0.1:4173/api/health",
    // 같은 포트에 남아 있는 개발 서버를 백엔드로 착각하지 않도록 항상 새로 시작한다.
    reuseExistingServer: false,
  },
});
