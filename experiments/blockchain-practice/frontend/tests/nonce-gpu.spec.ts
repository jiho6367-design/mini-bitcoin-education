import { createHash } from "node:crypto";
import { expect, test, type Page } from "./team-test";

const defaultMessage = "You are the best of best!!!!";
const defaultHash = "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010";
const unicodeHash = "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad";
const hash = (message: string, nonce: string) => createHash("sha256").update(message + nonce, "utf8").digest("hex");
const count = async (page: Page, testId: string) => BigInt((await page.getByTestId(testId).innerText()).replace(/[^0-9]/g, "") || "0");
const nonce = async (page: Page) => (await page.getByTestId("nonce-value").innerText()).replaceAll(",", "").trim();
const start = (page: Page) => page.getByRole("button", { name: "탐색 시작", exact: true }).click();

async function ready(page: Page) {
  await expect(page.getByRole("heading", { name: "넌스 찾기", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "연산 방식", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "탐색 시작", exact: true })).toBeEnabled();
}

async function settings(page: Page) {
  const details = page.locator("details").filter({ has: page.getByText("추가 설정", { exact: true }) });
  if (await details.getAttribute("open") === null) await details.locator("summary").click();
}

async function found(page: Page, message: string, difficulty: number) {
  await expect(page.getByTestId("nonce-status")).toHaveText("조건에 맞는 넌스를 찾았습니다", { timeout: 45_000 });
  const resultNonce = await nonce(page);
  const resultHash = hash(message, resultNonce);
  expect(resultHash.startsWith("0".repeat(difficulty))).toBe(true);
  await expect(page.getByTestId("nonce-hash")).toHaveText(resultHash);
  await expect(page.getByRole("button", { name: "탐색 시작", exact: true })).toBeEnabled();
  return { nonce: resultNonce, hash: resultHash };
}

async function replaceWorkerPrefix(page: Page, prefix: string) {
  // Only this isolated development test's worker response is changed. Browser
  // permissions, driver settings and WebGPU flags are never modified.
  await page.route("**/education-assets/assets/hashcash.worker-*.js", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: prefix + "\n" + await response.text() });
  });
}

test("실제 하드웨어 GPU에서 원본 SHA-256 결과를 찾고 재검증·저장한다", async ({ page }, info) => {
  const errors: string[] = [];
  const mutations: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    if (request.url().includes("/api/") && !["GET", "HEAD"].includes(request.method())) mutations.push(request.url());
  });
  await page.goto("/hashcash");
  await ready(page);
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("gpu");
  await start(page);
  expect(await found(page, defaultMessage, 3)).toEqual({ nonce: "3055", hash: defaultHash });
  await expect(page.getByTestId("nonce-backend")).toHaveText("GPU");
  expect(await count(page, "nonce-gpu-checked")).toBeGreaterThan(0n);
  expect(await count(page, "nonce-cpu-checked")).toBe(0n);
  await expect(page.getByTestId("nonce-device")).not.toHaveText("");
  await page.getByRole("button", { name: "해시 다시 검증", exact: true }).click();
  await expect(page.getByTestId("nonce-verification")).toContainText("일치");
  await page.screenshot({ path: info.outputPath("actual-gpu-default-proof.png"), fullPage: true });

  await page.getByRole("textbox", { name: "메시지", exact: true }).fill("한글🙂");
  await start(page);
  expect(await found(page, "한글🙂", 3)).toEqual({ nonce: "980", hash: unicodeHash });
  await expect(page.getByTestId("nonce-backend")).toHaveText("GPU");
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(2);
  await page.reload();
  await ready(page);
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(2);
  await expect(page.getByTestId("nonce-history")).toContainText(defaultHash);
  await expect(page.getByTestId("nonce-history")).toContainText(unicodeHash);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});

test("GPU가 uint64 끝값과 55바이트 단일 블록을 정확히 계산한다", async ({ page }) => {
  await page.goto("/hashcash");
  await ready(page);
  await settings(page);
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("gpu");
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await page.getByLabel("시작 넌스", { exact: true }).fill("18446744073709551615");
  for (const message of ["", "a".repeat(35), "한글🙂\n".repeat(3)]) {
    await page.getByRole("textbox", { name: "메시지", exact: true }).fill(message);
    await start(page);
    await expect(page.getByTestId("nonce-status")).toContainText(/범위|끝/);
    await expect(page.getByTestId("nonce-backend")).toHaveText("GPU");
    expect(await nonce(page)).toBe("18446744073709551615");
    await expect(page.getByTestId("nonce-hash")).toHaveText(hash(message, "18446744073709551615"));
    expect(await count(page, "nonce-checked")).toBe(1n);
    expect(await count(page, "nonce-gpu-checked")).toBe(1n);
  }
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(0);
});

test("CPU+GPU가 각자 실제 계산하고 중지·재시작해 유효한 해답을 찾는다", async ({ page }, info) => {
  await page.goto("/hashcash");
  await ready(page);
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("hybrid");
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await start(page);
  await expect(page.getByTestId("nonce-backend")).toHaveText("CPU+GPU", { timeout: 30_000 });
  await expect.poll(() => count(page, "nonce-cpu-checked"), { timeout: 30_000 }).toBeGreaterThan(0n);
  await expect.poll(() => count(page, "nonce-gpu-checked"), { timeout: 30_000 }).toBeGreaterThan(0n);
  await page.getByRole("button", { name: "중지", exact: true }).click();
  await expect(page.getByTestId("nonce-status")).toHaveText("탐색을 중지했습니다");
  const stopped = await page.getByTestId("nonce-checked").textContent() ?? "";
  await page.waitForTimeout(300);
  await expect(page.getByTestId("nonce-checked")).toHaveText(stopped);
  await page.getByLabel("목표 난이도", { exact: true }).fill("4");
  await start(page);
  await found(page, defaultMessage, 4);
  await expect(page.getByTestId("nonce-backend")).toHaveText("CPU+GPU");
  await page.getByRole("button", { name: "해시 다시 검증", exact: true }).click();
  await expect(page.getByTestId("nonce-verification")).toContainText("일치");
  await page.screenshot({ path: info.outputPath("actual-hybrid-proof.png"), fullPage: true });
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(1);
});

test("WebGPU가 없는 기기는 자동·GPU·동시 모드 모두 CPU로 전환하고 이유를 보여준다", async ({ page }) => {
  await replaceWorkerPrefix(page, 'Object.defineProperty(navigator,"gpu",{value:undefined,configurable:true});');
  await page.goto("/hashcash");
  await ready(page);
  for (const mode of ["auto", "gpu", "hybrid"]) {
    await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption(mode);
    await start(page);
    expect(await found(page, defaultMessage, 3)).toEqual({ nonce: "3055", hash: defaultHash });
    await expect(page.getByTestId("nonce-backend")).toHaveText("CPU");
    await expect(page.getByTestId("nonce-engine-reason")).toContainText(/GPU|지원|사용/);
    expect(await count(page, "nonce-cpu-checked")).toBeGreaterThan(0n);
    expect(await count(page, "nonce-gpu-checked")).toBe(0n);
  }
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(3);
});

test("실제 GPU 작업 중 장치를 잃으면 완료 계산량을 보존하고 CPU로 이어간다", async ({ page }) => {
  await replaceWorkerPrefix(page, `
    (() => {
      const gpu = navigator.gpu;
      if (!gpu) return;
      let device;
      let destroyed = false;
      const requestAdapter = gpu.requestAdapter.bind(gpu);
      Object.defineProperty(gpu, "requestAdapter", { value: async (...args) => {
        const adapter = await requestAdapter(...args);
        if (!adapter) return adapter;
        const requestDevice = adapter.requestDevice.bind(adapter);
        Object.defineProperty(adapter, "requestDevice", { value: async (...deviceArgs) => {
          device = await requestDevice(...deviceArgs);
          return device;
        }});
        return adapter;
      }});
      const post = globalThis.postMessage.bind(globalThis);
      globalThis.postMessage = (...args) => {
        post(...args);
        const message = args[0];
        if (!destroyed && device && message?.type === "progress" && message.backend === "gpu" && BigInt(message.gpuChecked || "0") > 0n) {
          destroyed = true;
          setTimeout(() => device.destroy(), 0);
        }
      };
    })();
  `);
  await page.goto("/hashcash");
  await ready(page);
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("gpu");
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await start(page);
  await expect.poll(() => count(page, "nonce-gpu-checked"), { timeout: 30_000 }).toBeGreaterThan(0n);
  await expect(page.getByTestId("nonce-backend")).toHaveText("CPU", { timeout: 30_000 });
  await expect.poll(() => count(page, "nonce-cpu-checked"), { timeout: 30_000 }).toBeGreaterThan(0n);
  await expect(page.getByTestId("nonce-engine-reason")).toContainText(/GPU/);
  await page.getByRole("button", { name: "중지", exact: true }).click();
  await expect(page.getByTestId("nonce-status")).toHaveText("탐색을 중지했습니다");
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(0);
});

test("GPU 입력 범위를 넘는 긴 메시지는 내용 그대로 CPU에서 계산한다", async ({ page }) => {
  const message = "공백과 줄바꿈\n".repeat(4);
  await page.goto("/hashcash");
  await ready(page);
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("gpu");
  await page.getByRole("textbox", { name: "메시지", exact: true }).fill(message);
  await page.getByLabel("목표 난이도", { exact: true }).fill("1");
  await start(page);
  await found(page, message, 1);
  await expect(page.getByTestId("nonce-backend")).toHaveText("CPU");
  await expect(page.getByTestId("nonce-engine-reason")).toContainText(/메시지|35/);
  expect(await count(page, "nonce-gpu-checked")).toBe(0n);
});

for (const mode of ["python-native", "c-native"]) {
  test(`${mode} PC 엔진을 사용할 수 없는 웹에서는 실제 CPU로 전환한다`, async ({ page }) => {
    const starts: string[] = [];
    await page.route("**/api/hashcash-native/capabilities", route => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ available: false, reason: "Python·C 모드는 PC에서 실행할 때 사용할 수 있습니다" }),
    }));
    page.on("request", request => {
      if (request.url().includes("/api/hashcash-native/start")) starts.push(request.url());
    });
    await page.goto("/hashcash");
    await ready(page);
    await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption(mode);
    await start(page);
    expect(await found(page, defaultMessage, 3)).toEqual({ nonce: "3055", hash: defaultHash });
    await expect(page.getByTestId("nonce-backend")).toHaveText("CPU");
    await expect(page.getByTestId("nonce-engine-reason")).toContainText(/PC/);
    expect(await count(page, "nonce-cpu-checked")).toBeGreaterThan(0n);
    expect(await count(page, "nonce-gpu-checked")).toBe(0n);
    expect(starts).toEqual([]);
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 휴대폰 화면에서 GPU 선택과 이전 CPU 기록을 유지한다`, async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((record) => {
      localStorage.setItem("team-hashcash-history-v1", JSON.stringify([record]));
    }, {
      id: "saved-before-gpu", participantName: "이전 참가자", message: defaultMessage,
      difficulty: 3, startNonce: "0", nonce: "3055", hash: defaultHash,
      checked: "3056", elapsedMs: 100, createdAt: "2026-11-17T00:00:00.000Z",
    });
    await page.goto("/hashcash");
    await ready(page);
    await page.getByLabel("화면 테마", { exact: true }).selectOption(theme);
    await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("gpu");
    await expect(page.getByTestId("nonce-history-record")).toHaveCount(1);
    await expect(page.getByTestId("nonce-history-record")).toContainText("이전 참가자");
    await start(page);
    expect(await found(page, defaultMessage, 3)).toEqual({ nonce: "3055", hash: defaultHash });
    await expect(page.getByTestId("nonce-backend")).toHaveText("GPU");
    await expect(page.getByTestId("nonce-history-record")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`gpu-${theme}-390.png`), fullPage: true });
  });
}
