import { defineConfig } from "vitest/config";
import process from "node:process";

const hashcashPort = Number(process.env.HASHCASH_PORT ?? "5186");
const hashcashApi = `http://127.0.0.1:${Number(process.env.HASHCASH_API_PORT ?? "9186")}`;

export default defineConfig({
  server: { host: "127.0.0.1", port: hashcashPort, proxy: { "/api/hashcash": hashcashApi } },
  preview: { host: "127.0.0.1", port: hashcashPort, proxy: { "/api/hashcash": hashcashApi } },
  test: { include: ["src/**/*.test.ts"], coverage: { provider: "v8" } },
});
