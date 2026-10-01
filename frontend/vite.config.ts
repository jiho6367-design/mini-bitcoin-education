import { defineConfig } from "vitest/config";

// 화면은 같은 출처의 /api만 호출하고, 개발·미리보기 서버가 Python 백엔드로 전달한다.
const proxy = {
  "/api": {
    target: process.env.MINI_BITCOIN_API_URL ?? "http://127.0.0.1:8000",
  },
};

export default defineConfig({
  server: { proxy },
  preview: { proxy },
  test: { include: ["src/**/*.test.ts"], coverage: { provider: "v8" } },
});
