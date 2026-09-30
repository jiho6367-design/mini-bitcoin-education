import { expect, test } from "@playwright/test";
import { calculateNonce, validPow } from "../src/core/block";
import type { Block } from "../src/core/types";
import {
  MINING_SAMPLE_LIMIT,
  type MiningCommand,
  type MiningUpdate,
} from "../src/mining/protocol";

// Worker 계산/메시지 경계만 검사하는 고정 헤더. 체인 수락은 learning.spec에서 검사한다.
const candidate: Block = {
  sessionId: "1".repeat(64),
  height: 1,
  previousHash: "0".repeat(64),
  transactionsHash: "2".repeat(64),
  timestamp: 1000,
  difficulty: 4,
  nonce: 0,
  hash: "",
  transactions: [],
};
while ([0, 1, 2].some((n) => validPow(calculateNonce(candidate, n).hash, 4)))
  candidate.timestamp++;

test("실제 Worker의 단계·일시정지·잘못된 작업 ID·중지·재개", async ({
  page,
}) => {
  await page.goto("/");
  const updates = await page.evaluate(async (block) => {
    const worker = new Worker("/src/mining/miner.worker.ts", {
      type: "module",
    });
    const next = (cmd: MiningCommand): Promise<MiningUpdate> =>
      new Promise((resolve) => {
        worker.onmessage = (e: MessageEvent<MiningUpdate>) => resolve(e.data);
        worker.postMessage(cmd);
      });
    const first = await next({
      type: "start",
      jobId: "one",
      candidate: block,
      step: true,
    });
    const paused = await next({ type: "pause", jobId: "one" });
    worker.postMessage({ type: "step", jobId: "stale" });
    const second = await next({ type: "step", jobId: "one" });
    const stopped = await next({ type: "stop", jobId: "one" });
    const afterStop: MiningUpdate[] = [];
    worker.onmessage = (e: MessageEvent<MiningUpdate>) =>
      afterStop.push(e.data);
    worker.postMessage({ type: "resume", jobId: "one" });
    await new Promise((resolve) => setTimeout(resolve, 150));
    const restarted = await next({
      type: "start",
      jobId: "two",
      candidate: { ...block, difficulty: 1 },
      step: true,
    });
    const found =
      restarted.type === "found"
        ? restarted
        : await new Promise<MiningUpdate>((resolve) => {
            worker.onmessage = (e: MessageEvent<MiningUpdate>) => {
              if (e.data.type === "found") resolve(e.data);
            };
            worker.postMessage({ type: "resume", jobId: "two" });
          });
    worker.terminate();
    return { first, paused, second, stopped, afterStop, found };
  }, candidate);
  expect(updates.first.type).toBe("paused");
  expect(updates.first.attempts).toBe(1);
  expect(updates.first.block?.hash).toBe(calculateNonce(candidate, 0).hash);
  expect(updates.first.samples).toEqual([
    { nonce: 0, hash: calculateNonce(candidate, 0).hash, passed: false },
  ]);
  expect(updates.first.mode).toBe("fast");
  expect(updates.first.observationMs).toBe(0);
  expect(updates.paused.attempts).toBe(1);
  expect(updates.second.attempts).toBe(2);
  expect(updates.second.block?.nonce).toBe(1);
  expect(updates.second.elapsedMs).toBeGreaterThanOrEqual(
    updates.first.elapsedMs,
  );
  expect(updates.stopped.type).toBe("stopped");
  expect(updates.afterStop).toEqual([]);
  expect(updates.found.jobId).toBe("two");
  expect(validPow(updates.found.block!.hash, 1)).toBe(true);
});

test("저속 학습은 실제 Nonce 하나씩 계산하고 관찰 대기·일시정지를 구분한다", async ({
  page,
}) => {
  await page.goto("/");
  const updates = await page.evaluate(async (block) => {
    const worker = new Worker("/src/mining/miner.worker.ts", {
      type: "module",
    });
    const received: MiningUpdate[] = [];
    let pending: ((update: MiningUpdate) => void) | undefined;
    worker.onmessage = (event: MessageEvent<MiningUpdate>) => {
      received.push(event.data);
      pending?.(event.data);
    };
    const next = (cmd: MiningCommand): Promise<MiningUpdate> =>
      new Promise((resolve) => {
        pending = (update) => {
          if (update.jobId !== cmd.jobId) return;
          pending = undefined;
          resolve(update);
        };
        worker.postMessage(cmd);
      });
    const first = await next({
      type: "start",
      jobId: "slow",
      candidate: block,
      step: false,
      mode: "learning",
    });
    const paused = await next({ type: "pause", jobId: "slow" });
    const countWhenPaused = received.length;
    await new Promise((resolve) => setTimeout(resolve, 300));
    const countAfterPause = received.length;
    const stillPaused = await next({ type: "pause", jobId: "slow" });
    worker.postMessage({ type: "resume", jobId: "old-job" });
    const second = await next({ type: "step", jobId: "slow" });
    const resumed = await next({ type: "resume", jobId: "slow" });
    const afterWait = await new Promise<MiningUpdate>((resolve) => {
      pending = (update) => {
        pending = undefined;
        resolve(update);
      };
    });
    const stopped = await next({ type: "stop", jobId: "slow" });
    const countWhenStopped = received.length;
    worker.postMessage({ type: "resume", jobId: "slow" });
    await new Promise((resolve) => setTimeout(resolve, 300));
    const countAfterStop = received.length;
    worker.terminate();
    return {
      first,
      paused,
      stillPaused,
      second,
      resumed,
      afterWait,
      stopped,
      countWhenPaused,
      countAfterPause,
      countWhenStopped,
      countAfterStop,
    };
  }, candidate);
  expect(updates.first.type).toBe("progress");
  expect(updates.first.attempts).toBe(1);
  expect(updates.first.mode).toBe("learning");
  expect(updates.first.samples[0].hash).toBe(calculateNonce(candidate, 0).hash);
  expect(updates.countAfterPause).toBe(updates.countWhenPaused);
  expect(updates.stillPaused.observationMs).toBe(updates.paused.observationMs);
  expect(updates.stillPaused.elapsedMs).toBe(updates.paused.elapsedMs);
  expect(updates.second.attempts).toBe(2);
  expect(updates.second.samples.map((sample) => sample.nonce)).toEqual([0, 1]);
  expect(updates.second.observationMs).toBe(updates.paused.observationMs);
  expect(updates.resumed.attempts).toBe(3);
  expect(updates.afterWait.attempts).toBe(4);
  expect(updates.afterWait.observationMs).toBeGreaterThan(
    updates.resumed.observationMs,
  );
  expect(updates.afterWait.elapsedMs).toBeGreaterThanOrEqual(
    updates.resumed.elapsedMs,
  );
  for (const sample of updates.afterWait.samples) {
    const actual = calculateNonce(candidate, sample.nonce);
    expect(sample.hash).toBe(actual.hash);
    expect(sample.passed).toBe(validPow(actual.hash, candidate.difficulty));
  }
  expect(updates.stopped.type).toBe("stopped");
  expect(updates.countAfterStop).toBe(updates.countWhenStopped);
});

test("빠른 첫 묶음의 성공도 실제 실패·성공 표본을 제한된 개수로 보존한다", async ({
  page,
}) => {
  // 확률적 시간 제한 대신, 고정 헤더에서 첫 성공 Nonce가 12 이상인 후보를 고른다.
  const block = { ...candidate, difficulty: 1 };
  while (
    Array.from({ length: MINING_SAMPLE_LIMIT }, (_, n) => n).some((n) =>
      validPow(calculateNonce(block, n).hash, 1),
    )
  )
    block.timestamp++;
  let expectedNonce = MINING_SAMPLE_LIMIT;
  while (!validPow(calculateNonce(block, expectedNonce).hash, 1))
    expectedNonce++;
  await page.goto("/");
  const found = await page.evaluate(async (input) => {
    const worker = new Worker("/src/mining/miner.worker.ts", {
      type: "module",
    });
    const result = await new Promise<MiningUpdate>((resolve) => {
      worker.onmessage = (event: MessageEvent<MiningUpdate>) => {
        if (event.data.type === "found") resolve(event.data);
      };
      worker.postMessage({
        type: "start",
        jobId: "fast",
        candidate: input,
        step: false,
      });
    });
    worker.terminate();
    return result;
  }, block);
  expect(found.attempts).toBe(expectedNonce + 1);
  expect(found.samples).toHaveLength(MINING_SAMPLE_LIMIT);
  expect(found.samples.at(-1)?.passed).toBe(true);
  expect(found.samples.slice(0, -1).every((sample) => !sample.passed)).toBe(
    true,
  );
  expect(found.samples[0].nonce).toBe(expectedNonce - MINING_SAMPLE_LIMIT + 1);
  for (const sample of found.samples) {
    expect(sample.hash).toBe(calculateNonce(block, sample.nonce).hash);
    expect(sample.passed).toBe(validPow(sample.hash, block.difficulty));
  }
});

test("첫 실제 해시의 성공을 그대로 인정하고 새 작업에서 이전 타이머를 제거한다", async ({
  page,
}) => {
  const winner = { ...candidate, difficulty: 1 };
  while (!validPow(calculateNonce(winner, 0).hash, 1)) winner.timestamp++;
  await page.goto("/");
  const result = await page.evaluate(
    async ({ old, fresh }) => {
      const worker = new Worker("/src/mining/miner.worker.ts", {
        type: "module",
      });
      const next = (cmd: MiningCommand): Promise<MiningUpdate> =>
        new Promise((resolve) => {
          worker.onmessage = (event: MessageEvent<MiningUpdate>) =>
            resolve(event.data);
          worker.postMessage(cmd);
        });
      await next({
        type: "start",
        jobId: "old",
        candidate: old,
        step: false,
        mode: "learning",
      });
      const found = await next({
        type: "start",
        jobId: "new",
        candidate: fresh,
        step: false,
        mode: "learning",
      });
      const late: MiningUpdate[] = [];
      worker.onmessage = (event: MessageEvent<MiningUpdate>) =>
        late.push(event.data);
      worker.postMessage({ type: "pause", jobId: "old" });
      worker.postMessage({ type: "step", jobId: "old" });
      await new Promise((resolve) => setTimeout(resolve, 350));
      worker.terminate();
      return { found, late };
    },
    { old: candidate, fresh: winner },
  );
  expect(result.found.type).toBe("found");
  expect(result.found.jobId).toBe("new");
  expect(result.found.attempts).toBe(1);
  expect(result.found.observationMs).toBe(0);
  expect(result.found.samples).toEqual([
    { nonce: 0, hash: calculateNonce(winner, 0).hash, passed: true },
  ]);
  expect(result.late).toEqual([]);
});

test("초기화 이후 도착하는 이전 Worker 결과를 무시한다", async ({ page }) => {
  // 종료 이후 found 이벤트가 도착하는 최악의 순서를 강제로 재현한다.
  await page.addInitScript(() => {
    class DelayedWorker {
      onmessage: ((event: { data: unknown }) => void) | null = null;
      onerror = null;
      last: { jobId: string; candidate: unknown } | null = null;
      postMessage(cmd: { type: string; jobId: string; candidate: unknown }) {
        if (cmd.type === "start") {
          this.last = cmd;
          this.onmessage?.({
            data: {
              type: "paused",
              jobId: cmd.jobId,
              attempts: 1,
              elapsedMs: 1,
              block: cmd.candidate,
            },
          });
        }
      }
      terminate() {
        if (!this.last) return;
        const value = this.last;
        setTimeout(
          () =>
            this.onmessage?.({
              data: {
                type: "found",
                jobId: value.jobId,
                attempts: 999,
                elapsedMs: 999,
                block: value.candidate,
              },
            }),
          250,
        );
      }
    }
    Object.defineProperty(window, "Worker", { value: DelayedWorker });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
  for (const name of ["1. 거래 작성", "2. 전자서명", "3. 검증·전파"])
    await page.getByRole("button", { name, exact: true }).click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: /채굴/ })
    .click();
  await page
    .getByRole("button", { name: "한 번 계산하기", exact: true })
    .click();
  await expect(page.getByTestId("attempts")).toHaveText("1");
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).click();
  await page.getByRole("button", { name: "초기화 확인", exact: true }).click();
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: /채굴/ })
    .click();
  // 지연된 이벤트가 실행될 기회를 명시적으로 준 후 새 세션의 상태를 검사한다.
  await page.waitForTimeout(350);
  await expect(page.getByTestId("attempts")).toHaveText("0");
  await expect(page.getByText("● 준비", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
