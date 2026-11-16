import { defineConfig } from "vitest/config";

// 개발 시에도 블록체인 실습실 서버 하나의 실습 API를 사용한다
const proxy = {
  "/api/education": {
    ws: true,
    target: process.env.BLOCKCHAIN_PRACTICE_API_URL
      ?? process.env.KAKAO_FLASK_API_URL
      ?? "http://127.0.0.1:5000",
  },
};

export default defineConfig({
  base: "/education-assets/",
  server: { proxy },
  preview: { proxy },
  test: { include: ["src/**/*.test.ts"], coverage: { provider: "v8" } },
});
