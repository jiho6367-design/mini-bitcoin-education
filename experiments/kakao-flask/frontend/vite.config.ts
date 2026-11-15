import { defineConfig } from "vitest/config";

// 개발 시에도 팀 Flask 서버 하나의 통합 실습 API를 사용한다
const proxy = {
  "/api/education": {
    ws: true,
    target: process.env.KAKAO_FLASK_API_URL ?? "http://127.0.0.1:5000",
  },
};

export default defineConfig({
  base: "/education-assets/",
  server: { proxy },
  preview: { proxy },
  test: { include: ["src/**/*.test.ts"], coverage: { provider: "v8" } },
});
