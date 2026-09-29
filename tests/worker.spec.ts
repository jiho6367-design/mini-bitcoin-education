import { expect, test } from "@playwright/test";
import { calculateNonce, validPow } from "../src/core/block";
import type { Block } from "../src/core/types";
import type { MiningCommand, MiningUpdate } from "../src/mining/protocol";

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
