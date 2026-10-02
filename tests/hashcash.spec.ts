import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";

test("UTF-8 nonce search, independent verification, progress, cancel and restart", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("● 로컬 연산 서버 연결됨")).toBeVisible();
  await page.getByLabel("메시지", { exact: false }).fill("한글🙂");
  await page.getByLabel("CPU 작업 수").fill("1");
  await page.getByLabel("연산 장치").selectOption("auto");
  await page.getByRole("button", { name: "탐색 시작" }).click();
  await expect(page.getByText("조건을 만족하는 nonce 발견", { exact: true })).toBeVisible();
  if (process.env.HASHCASH_EXPECT_GPU === "1") {
    await expect(page.getByText(/^GPU · /)).toBeVisible();
    await expect(page.getByText("CUDA 가용성·SHA-256 자체 검증 통과", { exact: true })).toBeVisible();
  }
  await expect(page.getByTestId("display-hash")).toHaveText("00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad");
  await page.getByRole("button", { name: "해시 다시 검증" }).click();
  await expect(page.getByText("브라우저 SHA-256 재계산도 일치합니다.")).toBeVisible();
  await page.screenshot({ path: `work/hashcash-${test.info().project.name}-light.png`, fullPage: true });
  await page.getByLabel("목표 난이도", { exact: false }).fill("64");
  await page.getByLabel("연산 장치").selectOption(process.env.HASHCASH_EXPECT_GPU === "1" ? "auto" : "cpu");
  await page.getByRole("button", { name: "탐색 시작" }).click();
  await expect(page.getByText("조건을 찾는 중", { exact: true })).toBeVisible();
  await expect(page.getByTestId("display-hash")).not.toContainText("해시 결과가 여기에");
  if (process.env.HASHCASH_EXPECT_GPU === "1") {
    await expect(page.getByText(/^GPU · /)).toBeVisible();
  }
  await page.getByRole("button", { name: "중지", exact: true }).click();
  await expect(page.getByText("탐색 중지됨", { exact: true })).toBeVisible();
  await page.getByLabel("메시지", { exact: false }).fill("Pyeongtaek University!");
  await page.getByLabel("목표 난이도", { exact: false }).fill("3");
  await page.getByLabel("연산 장치").selectOption("cpu");
  await page.getByRole("button", { name: "탐색 시작" }).click();
  await expect(page.getByText("조건을 만족하는 nonce 발견", { exact: true })).toBeVisible();
  await expect(page.getByTestId("display-hash")).toHaveText("00028e62333a32b5b6bdf41418f42725f8c21d84dc0b7ff60e4480cc11a8677b");
  await page.getByLabel("화면 테마").selectOption("dark");
  await page.screenshot({ path: `work/hashcash-${test.info().project.name}-dark.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "해시 다시 검증" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: `work/hashcash-${test.info().project.name}-mobile.png`, fullPage: true });
});

test("a delayed previous status cannot replace a new job or round its nonce", async ({ page }) => {
  const oldHash = "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad";
  const oldJob = {
    id: "previous-job", status: "found", backend: "CPU", reason: "finished",
    message: "한글🙂", difficulty: 3, startNonce: "0", checked: "981",
    lastNonce: "980", lastHash: oldHash, elapsed: 0.1, hashesPerSecond: 9810,
    error: null, finished: true, result: { nonce: "980", hash: oldHash, verified: true },
  };
  const message = " exact message\n한글🙂 ";
  const nonce = "9007199254740993";
  const sampleHash = createHash("sha256").update(message + nonce, "utf8").digest("hex");
  const newJob = {
    ...oldJob, id: "current-job", status: "running", message, difficulty: 64,
    startNonce: nonce, checked: "1", lastNonce: nonce, lastHash: sampleHash,
    finished: false, result: null,
  };
  let armDelay = false;
  let holdingPrevious = false;
  let started = false;
  let releasePrevious = () => {};
  let releaseFuture = () => {};
  let signalHeld = () => {};
  const previousReleased = new Promise<void>((resolve) => { releasePrevious = resolve; });
  const futureReleased = new Promise<void>((resolve) => { releaseFuture = resolve; });
  const previousHeld = new Promise<void>((resolve) => { signalHeld = resolve; });

  await page.route("**/api/hashcash/health", (route) => route.fulfill({ json: { cpuCount: 1 } }));
  await page.route("**/api/hashcash/status", async (route) => {
    if (armDelay && !holdingPrevious && !started) {
      holdingPrevious = true;
      signalHeld();
      await previousReleased;
      await route.fulfill({ json: { job: oldJob } });
    } else {
      // Prevent another poll from concealing the stale response during checks.
      if (started) await futureReleased;
      await route.fulfill({ json: { job: started ? newJob : oldJob } });
    }
  });
  await page.route("**/api/hashcash/start", async (route) => {
    const payload = route.request().postDataJSON();
    expect(payload.message).toBe(message);
    expect(payload.startNonce).toBe(nonce);
    expect(typeof payload.startNonce).toBe("string");
    started = true;
    await route.fulfill({ json: { job: newJob } });
  });

  try {
    await page.goto("/");
    await expect(page.getByTestId("display-hash")).toHaveText(oldHash);
    await page.getByLabel("메시지", { exact: false }).fill(message);
    await page.getByLabel("시작 nonce").fill(nonce);
    await page.getByLabel("목표 난이도", { exact: false }).fill("64");
    armDelay = true;
    await previousHeld;
    await page.getByRole("button", { name: "탐색 시작" }).click();
    await expect(page.getByTestId("display-hash")).toHaveText(sampleHash);
    const responseReceived = page.waitForResponse((response) => response.url().endsWith("/api/hashcash/status"));
    releasePrevious();
    await responseReceived;
    // Two paint boundaries allow the resolved fetch and React commit to finish.
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByTestId("display-hash")).toHaveText(sampleHash);
    await expect(page.getByText("조건을 찾는 중", { exact: true })).toBeVisible();
    await expect(page.getByText(nonce, { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "탐색 시작" })).toBeDisabled();
    await expect(page.getByLabel("메시지", { exact: false })).toHaveValue(message);
    await expect(page.getByLabel("메시지", { exact: false })).toBeDisabled();
  } finally {
    releasePrevious();
    releaseFuture();
  }
});
