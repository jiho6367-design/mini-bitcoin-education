import { defineConfig } from "vitest/config";

export default defineConfig({
  server: { host: "127.0.0.1", port: 5186, proxy: { "/api/hashcash": "http://127.0.0.1:9186" } },
  preview: { host: "127.0.0.1", port: 5186, proxy: { "/api/hashcash": "http://127.0.0.1:9186" } },
  test: { include: ["src/**/*.test.ts"], coverage: { provider: "v8" } },
});
