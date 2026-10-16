import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";

const historyTestDirectory = resolve("work/hashcash-e2e", randomUUID());
const frontendPort = process.env.HASHCASH_E2E_PORT ?? "5188";
const apiPort = process.env.HASHCASH_E2E_API_PORT ?? "9188";
const baseURL = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./tests",
  testMatch: "hashcash.spec.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: "list",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    { name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome", viewport: { width: 1440, height: 1050 } } },
    { name: "edge", use: { ...devices["Desktop Edge"], channel: "msedge", viewport: { width: 1440, height: 1050 } } },
  ],
  webServer: {
    command: "npm run hashcash", url: baseURL, reuseExistingServer: false,
    env: { HASHCASH_DATA_DIR: historyTestDirectory, HASHCASH_PORT: frontendPort, HASHCASH_API_PORT: apiPort },
  },
});
