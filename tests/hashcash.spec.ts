import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

test("UTF-8 nonce search, independent verification, progress, cancel and restart", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("● 로컬 연산 서버 연결됨")).toBeVisible();
  await page.getByLabel("메시지", { exact: false }).fill("한글🙂");
  await page.getByLabel("CPU 작업 수").fill("1");
  await page.getByLabel("연산 장치").selectOption("auto");
  await page.getByRole("button", { name: "탐색 시작" }).click();
  await expect(page.getByText("조건을 만족하는 nonce 발견", { exact: true })).toBeVisible();
  if (process.env.HASHCASH_EXPECT_GPU === "1") {
    await expect(page.getByRole("region", { name: "탐색 관찰" }).getByText(/^GPU · /)).toBeVisible();
    await expect(page.getByRole("region", { name: "탐색 관찰" }).getByText("CUDA 가용성·SHA-256 자체 검증 통과", { exact: true })).toBeVisible();
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
    await expect(page.getByRole("region", { name: "탐색 관찰" }).getByText(/^GPU · /)).toBeVisible();
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

test("found proofs persist across reload with participant and difficulty filters", async ({ page }) => {
  const suffix = Date.now().toString();
  const alice = `기록 검사 가 ${suffix}`;
  const bob = `기록 검사 나 ${suffix}`;
  await page.goto("/");
  await expect(page.getByText("● 로컬 연산 서버 연결됨")).toBeVisible();

  async function findAndSave(name: string, message: string, difficulty: number) {
    await page.getByLabel("기록 이름", { exact: true }).fill(name);
    await page.getByLabel("메시지", { exact: false }).fill(message);
    await page.getByLabel("목표 난이도", { exact: false }).fill(String(difficulty));
    await page.getByLabel("연산 장치").selectOption("cpu");
    await page.getByLabel("CPU 작업 수").fill("1");
    await page.getByLabel("시작 nonce").fill("0");
    await page.getByRole("button", { name: "탐색 시작" }).click();
    await expect(page.getByText("조건을 만족하는 nonce 발견", { exact: true })).toBeVisible();
    await expect.poll(async () => {
      const { job } = await (await page.request.get("/api/hashcash/status")).json();
      return job?.participantName === name && job?.recordId && job?.finished;
    }).toBeTruthy();
    await expect(page.getByRole("button", { name: "탐색 시작" })).toBeEnabled();
  }

  await findAndSave(alice, "한글🙂", 3);
  await findAndSave(bob, "Pyeongtaek University!", 1);
  await findAndSave(alice, "한글🙂", 2);
  await page.reload();
  await expect(page.getByLabel("기록 이름", { exact: true })).toHaveValue(alice);
  const history = page.getByTestId("history-panel");
  await history.getByLabel("기록 이름 필터").selectOption(alice);
  await expect(history.getByTestId("history-record")).toHaveCount(2);
  await history.getByLabel("기록 난이도 필터").selectOption("3");
  const record = history.getByTestId("history-record");
  await expect(record).toHaveCount(1);
  await expect(record.getByTestId("history-nonce")).toHaveText("980");
  await record.getByRole("button", { name: "기록 해시 검증", exact: true }).click();
  await expect(record.getByText("저장된 메시지로 다시 계산한 해시가 일치합니다.")).toBeVisible();
  await record.getByRole("button", { name: "조건 불러오기" }).click();
  await expect(page.getByLabel("메시지", { exact: false })).toHaveValue("한글🙂");
  await expect(page.getByLabel("목표 난이도", { exact: false })).toHaveValue("3");
  await expect(page.getByLabel("기록 이름", { exact: true })).toHaveValue(alice);

  await history.getByLabel("기록 난이도 필터").selectOption("");
  const downloaded = page.waitForEvent("download");
  await history.getByRole("button", { name: "JSON 내보내기" }).click();
  const download = await downloaded;
  const file = await download.path();
  expect(file).not.toBeNull();
  const exported = JSON.parse(await readFile(file!, "utf8"));
  expect(exported.records).toHaveLength(2);
  expect(exported.records.every((entry: { participantName: string; nonce: string; hash: string; message: string }) =>
    entry.participantName === alice && typeof entry.nonce === "string" &&
    createHash("sha256").update(entry.message + entry.nonce, "utf8").digest("hex") === entry.hash,
  )).toBeTruthy();

  await history.getByLabel("기록 이름 필터").selectOption(bob);
  await expect(history.getByTestId("history-record")).toHaveCount(1);
  await history.getByLabel("입력 조건으로 한정").check();
  await expect(history.getByTestId("history-record-count")).toHaveText("총 0건");
  await expect(history.getByTestId("history-record")).toHaveCount(0);
  await history.getByLabel("입력 조건으로 한정").uncheck();
  await expect(history.getByTestId("history-record")).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: `work/hashcash-${test.info().project.name}-history-mobile.png`, fullPage: true });
});

test("failed history save preserves the proof and retry unlocks a new search", async ({ page }) => {
  const hash = "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad";
  let savedId: string | null = null;
  const job = {
    id: "retry-history-job", status: "found", backend: "CPU", reason: "finished",
    message: "한글🙂", difficulty: 3, participantName: "저장 재시도", startNonce: "0",
    checked: "981", lastNonce: "980", lastHash: hash, elapsed: 0.2,
    hashesPerSecond: 4905, finished: true, error: null,
    result: { nonce: "980", hash, verified: true },
  };
  function snapshot() { return { ...job, recordId: savedId, saveError: savedId ? null : "저장 폴더를 사용할 수 없습니다." }; }
  await page.route("**/api/hashcash/health", (route) => route.fulfill({ json: { cpuCount: 1 } }));
  await page.route("**/api/hashcash/status", (route) => route.fulfill({ json: { job: snapshot() } }));
  await page.route(/\/api\/hashcash\/history(?:\?.*)?$/, (route) => route.fulfill({
    json: { participants: [job.participantName], total: savedId ? 1 : 0, records: savedId ? [{
      id: savedId, jobId: job.id, participantName: job.participantName,
      message: job.message, difficulty: job.difficulty, nonce: "980", hash,
      backend: job.backend, checked: job.checked, elapsed: job.elapsed,
      createdAt: "2026-10-02T17:00:00.000Z",
    }] : [] },
  }));
  await page.route("**/api/hashcash/history/save", (route) => {
    expect(route.request().postDataJSON()).toEqual({ jobId: job.id });
    savedId = "saved-after-retry";
    return route.fulfill({ json: { job: snapshot() } });
  });
  await page.goto("/");
  await expect(page.getByTestId("display-hash")).toHaveText(hash);
  await expect(page.getByRole("button", { name: "탐색 시작" })).toBeDisabled();
  await page.getByRole("button", { name: "기록 저장 다시 시도" }).click();
  await expect(page.getByText("저장 재시도의 난이도 3 기록으로 저장 완료했습니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "탐색 시작" })).toBeEnabled();
  await expect(page.getByTestId("display-hash")).toHaveText(hash);
  await expect(page.getByTestId("history-panel").getByTestId("history-record")).toHaveCount(1);
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
